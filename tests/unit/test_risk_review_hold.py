"""Adversarial regressions from the independent pure-risk review hold."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from test_risk_manager import NOW, checks_by_kind, intent, policy, snapshot

import hmt_crypto.risk.inputs as risk_inputs
import hmt_crypto.risk.models as risk_models
from hmt_crypto.risk.inputs import PositionRisk, ProtectionOrder
from hmt_crypto.risk.manager import RiskManager
from hmt_crypto.risk.models import (
    CheckKind,
    CheckOutcome,
    DecisionOutcome,
    RiskInputError,
    Side,
)


def test_existing_long_protection_requires_a_stop_trigger_below_fresh_market() -> None:
    unsafe = replace(snapshot().protective_orders[0], trigger_price=Decimal("110"))

    result = RiskManager().evaluate(policy(), intent(), snapshot(protective_orders=(unsafe,)))

    protection = checks_by_kind(result)[CheckKind.PROTECTION]
    assert result.outcome is DecisionOutcome.DENIED
    assert protection.outcome is CheckOutcome.FAIL  # type: ignore[attr-defined]
    assert protection.reason_code == "PROTECTION_INADEQUATE"  # type: ignore[attr-defined]


def test_each_products_trigger_uses_its_own_proven_market_reference() -> None:
    state = snapshot()
    eth_position = PositionRisk(
        product_id=2202,
        signed_contracts=1,
        notional_per_contract=Decimal("100"),
        effective_leverage=Decimal("2"),
    )
    eth_protection = ProtectionOrder(
        order_key="protect-eth",
        product_id=2202,
        side=Side.SELL,
        remaining_contracts=1,
        trigger_price=Decimal("900"),
        reference_price=Decimal("1000"),
        reduce_only=True,
        order_type=risk_models.ProtectionOrderType.STOP_MARKET,
        status=risk_models.ProtectionOrderStatus.ACTIVE,
        observed_at=state.reconciliation.observed_at,
        reconciliation_evidence_sha256=state.reconciliation.evidence_sha256,
        evidence_sha256="7" * 64,
    )
    portfolio = replace(
        state,
        positions=(*state.positions, eth_position),
        protective_orders=(*state.protective_orders, eth_protection),
    )

    safe = RiskManager().evaluate(policy(max_total_exposure=Decimal("2000")), intent(), portfolio)
    unsafe = RiskManager().evaluate(
        policy(max_total_exposure=Decimal("2000")),
        intent(),
        replace(
            portfolio,
            protective_orders=(
                *state.protective_orders,
                replace(eth_protection, trigger_price=Decimal("1100")),
            ),
        ),
    )

    assert checks_by_kind(safe)[CheckKind.PROTECTION].outcome is CheckOutcome.PASS  # type: ignore[attr-defined]
    assert checks_by_kind(unsafe)[CheckKind.PROTECTION].outcome is CheckOutcome.FAIL  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "changes",
    [
        {"reduce_only": False},
        {"order_type": "limit"},
        {"status": "inactive"},
    ],
)
def test_only_verified_reduce_only_active_stop_orders_count_as_protection(
    changes: dict[str, object],
) -> None:
    existing = snapshot().protective_orders[0]
    if changes.get("order_type") == "limit":
        changes["order_type"] = risk_models.ProtectionOrderType.LIMIT
    if changes.get("status") == "inactive":
        changes["status"] = risk_models.ProtectionOrderStatus.INACTIVE
    unverified = replace(existing, **changes)

    result = RiskManager().evaluate(policy(), intent(), snapshot(protective_orders=(unverified,)))

    protection = checks_by_kind(result)[CheckKind.PROTECTION]
    assert result.outcome is DecisionOutcome.DENIED
    assert protection.outcome is CheckOutcome.FAIL  # type: ignore[attr-defined]


def test_protective_order_identity_is_unique_and_provenance_matches_reconciliation() -> None:
    existing = snapshot().protective_orders[0]

    with pytest.raises(RiskInputError):
        snapshot(protective_orders=(existing, existing))

    mismatched = replace(existing, reconciliation_evidence_sha256="9" * 64)
    with pytest.raises(RiskInputError):
        snapshot(protective_orders=(mismatched,))

    stale_provenance = replace(existing, observed_at=NOW - timedelta(seconds=1))
    with pytest.raises(RiskInputError):
        snapshot(protective_orders=(stale_provenance,))


@pytest.mark.parametrize("completeness", ["MISSING", "PARTIAL", "AMBIGUOUS"])
def test_noncomplete_rate_window_evidence_is_unknown(completeness: str) -> None:
    state = snapshot()
    empty = risk_inputs.RateWindowSnapshot(
        completeness=getattr(risk_models.Completeness, completeness)
    )

    result = RiskManager().evaluate(policy(), intent(), replace(state, rate_window=empty))

    rate = checks_by_kind(result)[CheckKind.MAX_ORDER_RATE]
    assert result.outcome is DecisionOutcome.DENIED
    assert rate.outcome is CheckOutcome.UNKNOWN  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "changes",
    [
        {"observed_at": NOW + timedelta(seconds=9)},
        {"window_start_at": NOW - timedelta(minutes=1)},
        {"window_end_at": NOW + timedelta(minutes=2)},
    ],
)
def test_rate_window_must_be_current_and_match_exact_epoch_window(
    changes: dict[str, object],
) -> None:
    stale_or_wrong = replace(snapshot().rate_window, **changes)

    result = RiskManager().evaluate(policy(), intent(), snapshot(rate_window=stale_or_wrong))

    rate = checks_by_kind(result)[CheckKind.MAX_ORDER_RATE]
    assert result.outcome is DecisionOutcome.DENIED
    assert rate.outcome is CheckOutcome.UNKNOWN  # type: ignore[attr-defined]


def test_complete_current_zero_consumption_snapshot_proves_the_empty_window() -> None:
    zero = replace(snapshot().rate_window, consumed_units=0, cursor="consumption-ledger:zero")

    result = RiskManager().evaluate(policy(), intent(), snapshot(rate_window=zero))

    rate = checks_by_kind(result)[CheckKind.MAX_ORDER_RATE]
    assert rate.outcome is CheckOutcome.PASS  # type: ignore[attr-defined]
    assert rate.observed_integer == 2  # type: ignore[attr-defined]


def test_risk_increasing_open_order_leverage_is_included_in_maximum() -> None:
    overleveraged = replace(snapshot().open_orders[0], configured_leverage=Decimal("6"))

    result = RiskManager().evaluate(policy(), intent(), snapshot(open_orders=(overleveraged,)))

    leverage = checks_by_kind(result)[CheckKind.MAX_LEVERAGE]
    assert result.outcome is DecisionOutcome.DENIED
    assert leverage.outcome is CheckOutcome.FAIL  # type: ignore[attr-defined]
    assert leverage.observed_decimal == Decimal("6")  # type: ignore[attr-defined]


def test_risk_increasing_reservation_leverage_is_included_in_maximum() -> None:
    overleveraged = replace(snapshot().reservations[0], configured_leverage=Decimal("7"))

    result = RiskManager().evaluate(policy(), intent(), snapshot(reservations=(overleveraged,)))

    leverage = checks_by_kind(result)[CheckKind.MAX_LEVERAGE]
    assert result.outcome is DecisionOutcome.DENIED
    assert leverage.outcome is CheckOutcome.FAIL  # type: ignore[attr-defined]
    assert leverage.observed_decimal == Decimal("7")  # type: ignore[attr-defined]


def test_only_risk_increasing_pending_facts_carry_configured_leverage() -> None:
    open_order = snapshot().open_orders[0]
    reservation = snapshot().reservations[0]

    with pytest.raises(RiskInputError):
        replace(open_order, configured_leverage=None)
    with pytest.raises(RiskInputError):
        replace(reservation, configured_leverage=None)
    assert replace(open_order, reduce_only=True, configured_leverage=None).reduce_only
    assert (
        replace(
            reservation,
            risk_effect=risk_models.RiskEffect.REDUCE_ONLY,
            configured_leverage=None,
            worst_case_loss=Decimal("0"),
            collateral=Decimal("0"),
        ).configured_leverage
        is None
    )


def test_duplicate_identity_uses_owner_and_economics_not_caller_key() -> None:
    manager = RiskManager()
    first = manager.evaluate(policy(), intent(intent_key="caller-a"), snapshot())
    retried = manager.evaluate(policy(), intent(intent_key="caller-b"), snapshot())
    other_owner = manager.evaluate(
        policy(), intent(intent_key="caller-a", intent_owner="strategy-beta"), snapshot()
    )
    changed_economics = manager.evaluate(
        policy(), intent(intent_key="caller-a", leverage=Decimal("3")), snapshot()
    )

    assert retried.intent_key_sha256 == first.intent_key_sha256
    assert other_owner.intent_key_sha256 != first.intent_key_sha256
    assert changed_economics.intent_key_sha256 != first.intent_key_sha256

    active = replace(snapshot().open_orders[0], intent_key_sha256=first.intent_key_sha256)
    duplicate = manager.evaluate(
        policy(),
        intent(intent_key="caller-b"),
        snapshot(open_orders=(active,)),
    )
    check = checks_by_kind(duplicate)[CheckKind.DUPLICATE_ORDER]
    assert check.outcome is CheckOutcome.FAIL  # type: ignore[attr-defined]


def test_reservation_plan_binds_policy_intent_product_effect_and_input_context() -> None:
    baseline = RiskManager().evaluate(policy(), intent(), snapshot())
    plan = baseline.reservation
    assert plan is not None
    assert plan.product_id == 1101
    assert plan.risk_effect is risk_models.RiskEffect.RISK_INCREASE
    assert plan.intent_key_sha256 == baseline.intent_key_sha256
    assert plan.input_context_sha256
    assert plan.policy_version == 1
    assert plan.policy_sha256 == "a" * 64
    assert plan.configured_leverage == Decimal("2")

    changed_policy = (
        RiskManager()
        .evaluate(policy(version=2, policy_sha256="8" * 64), intent(), snapshot())
        .reservation
    )
    changed_context = (
        RiskManager()
        .evaluate(
            policy(),
            intent(),
            snapshot(collateral_evidence_sha256="9" * 64),
        )
        .reservation
    )
    assert changed_policy is not None
    assert changed_context is not None
    assert changed_policy.reservation_plan_sha256 != plan.reservation_plan_sha256
    assert changed_context.reservation_plan_sha256 != plan.reservation_plan_sha256
    assert changed_context.input_context_sha256 != plan.input_context_sha256


def test_manager_revalidates_corrupted_top_level_and_nested_facts() -> None:
    scenarios: list[tuple[object, object, object]] = []

    corrupted_policy = policy()
    object.__setattr__(corrupted_policy, "max_position_contracts", True)
    scenarios.append((corrupted_policy, intent(), snapshot()))

    corrupted_intent = intent()
    object.__setattr__(corrupted_intent, "intent_owner", 7)
    scenarios.append((policy(), corrupted_intent, snapshot()))

    corrupted_position_state = snapshot()
    object.__setattr__(corrupted_position_state.positions[0], "signed_contracts", True)
    scenarios.append((policy(), intent(), corrupted_position_state))

    corrupted_open_state = snapshot()
    object.__setattr__(corrupted_open_state.open_orders[0], "configured_leverage", None)
    scenarios.append((policy(), intent(), corrupted_open_state))

    corrupted_reservation_state = snapshot()
    object.__setattr__(corrupted_reservation_state.reservations[0], "rate_policy_version", True)
    scenarios.append((policy(), intent(), corrupted_reservation_state))

    corrupted_rate_state = snapshot()
    reconstructed_source = str.__new__(
        risk_models.RateWindowSource,
        risk_models.RateWindowSource.CONSUMPTION_LEDGER.value,
    )
    object.__setattr__(corrupted_rate_state.rate_window, "source", reconstructed_source)
    scenarios.append((policy(), intent(), corrupted_rate_state))

    corrupted_protection_state = snapshot()
    object.__setattr__(corrupted_protection_state.protective_orders[0], "reduce_only", 1)
    scenarios.append((policy(), intent(), corrupted_protection_state))

    corrupted_accounting_state = snapshot()
    reconstructed_completeness = str.__new__(
        risk_models.Completeness,
        risk_models.Completeness.COMPLETE.value,
    )
    object.__setattr__(
        corrupted_accounting_state.accounting,
        "completeness",
        reconstructed_completeness,
    )
    scenarios.append((policy(), intent(), corrupted_accounting_state))

    for raw_policy, raw_intent, raw_snapshot in scenarios:
        with pytest.raises(RiskInputError):
            RiskManager().evaluate(raw_policy, raw_intent, raw_snapshot)  # type: ignore[arg-type]


def test_missing_owned_field_is_normalized_to_risk_input_error() -> None:
    corrupted = intent()
    object.__delattr__(corrupted, "intent_owner")

    with pytest.raises(RiskInputError):
        RiskManager().evaluate(policy(), corrupted, snapshot())


def test_hostile_corruption_is_rejected_without_invoking_callbacks() -> None:
    calls = 0

    class Explosive:
        def _explode(self, *_args: object) -> object:
            nonlocal calls
            calls += 1
            raise AssertionError("hostile callback ran")

        __bool__ = _explode
        __eq__ = _explode
        __hash__ = _explode
        __iter__ = _explode
        __le__ = _explode
        __lt__ = _explode
        __str__ = _explode

    corrupted = policy()
    object.__setattr__(corrupted, "max_position_contracts", Explosive())

    with pytest.raises(RiskInputError):
        RiskManager().evaluate(corrupted, intent(), snapshot())
    assert calls == 0


def test_manager_outputs_owned_canonical_decimal_values() -> None:
    result = RiskManager().evaluate(
        policy(), intent(leverage=Decimal("2.000000000000000000")), snapshot()
    )

    assert result.reservation is not None
    assert result.reservation.configured_leverage is not None
    assert result.reservation.configured_leverage.as_tuple().exponent == 0
