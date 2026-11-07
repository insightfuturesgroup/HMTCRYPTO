"""Mandatory pure RiskManager decisions and evidence."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

import hmt_crypto.risk.inputs as risk_inputs
import hmt_crypto.risk.models as risk_models
from hmt_crypto.risk.inputs import (
    AssetRef,
    DailyAccounting,
    HaltSnapshot,
    OpenOrderRisk,
    PositionRisk,
    PriceSnapshot,
    ProductTruth,
    ProtectionOrder,
    ReconciliationSnapshot,
    ReservationRisk,
    RiskIntent,
    RiskPolicy,
    RiskSnapshot,
)
from hmt_crypto.risk.manager import RiskManager
from hmt_crypto.risk.models import (
    CatalogueStatus,
    CheckKind,
    CheckOutcome,
    Completeness,
    DecisionOutcome,
    HaltCause,
    MarketStatus,
    OperationKind,
    PriceSource,
    RiskEffect,
    RiskInputError,
    Side,
)

NOW = datetime(2026, 11, 4, 12, tzinfo=UTC)
HASH = "a" * 64


def policy(**changes: object) -> RiskPolicy:
    values: dict[str, object] = {
        "version": 1,
        "policy_sha256": HASH,
        "reference_asset": AssetRef(asset_id=1, symbol="USDT"),
        "collateral_asset": AssetRef(asset_id=2, symbol="USDT"),
        "max_position_contracts": 10,
        "max_total_exposure": Decimal("1000"),
        "max_leverage": Decimal("5"),
        "max_loss_per_trade": Decimal("50"),
        "max_daily_loss": Decimal("100"),
        "max_open_orders": 5,
        "max_order_rate": 10,
        "rate_window_seconds": 60,
        "rate_policy_version": 1,
        "rate_policy_sha256": "b" * 64,
        "price_collar_bps": Decimal("100"),
        "max_catalogue_age_seconds": 60,
        "max_mark_age_seconds": 5,
        "allow_last_price_fallback": False,
        "max_last_price_age_seconds": 5,
        "max_reconciliation_age_seconds": 30,
        "max_accounting_age_seconds": 30,
        "max_halt_age_seconds": 30,
        "daily_reset_second_utc": 0,
        "collateral_buffer_bps": Decimal("0"),
    }
    values.update(changes)
    return RiskPolicy(**values)  # type: ignore[arg-type]


def intent(**changes: object) -> RiskIntent:
    values: dict[str, object] = {
        "product_id": 1101,
        "operation": OperationKind.PLACE_ORDER,
        "target_order_key": None,
        "side": Side.BUY,
        "contracts": 2,
        "risk_effect": RiskEffect.RISK_INCREASE,
        "order_price": Decimal("100"),
        "leverage": Decimal("2"),
        "stop_price": Decimal("90"),
        "loss_fee_reserve": Decimal("0"),
        "planned_protection_contracts": 2,
        "intent_owner": "strategy-alpha",
        "intent_key": "strategy:btc:2026-11-04T12:00:00Z",
    }
    values.update(changes)
    return RiskIntent(**values)  # type: ignore[arg-type]


def accounting(**changes: object) -> DailyAccounting:
    values: dict[str, object] = {
        "completeness": Completeness.COMPLETE,
        "observed_at": NOW,
        "window_start_at": datetime(2026, 11, 4, tzinfo=UTC),
        "baseline_observed_at": datetime(2026, 11, 4, tzinfo=UTC),
        "baseline_equity": Decimal("1000"),
        "current_equity": Decimal("995"),
        "realized_pnl": Decimal("-3"),
        "unrealized_pnl": Decimal("-2"),
        "unresolved_loss_reserve": Decimal("1"),
        "ledger_cursor_start": "ledger-reset",
        "ledger_cursor_end": "ledger-now",
        "ledger_evidence_sha256": "c" * 64,
        "included_reservation_keys": (),
    }
    values.update(changes)
    return DailyAccounting(**values)  # type: ignore[arg-type]


def snapshot(**changes: object) -> RiskSnapshot:
    reservation = ReservationRisk(
        reservation_key="d" * 64,
        intent_key_sha256="e" * 64,
        product_id=1101,
        side=Side.BUY,
        risk_effect=RiskEffect.RISK_INCREASE,
        contracts=1,
        notional_per_contract=Decimal("100"),
        configured_leverage=Decimal("2"),
        worst_case_loss=Decimal("10"),
        collateral=Decimal("10"),
        open_order_slots=1,
        pending_rate_units=1,
        rate_policy_version=1,
        rate_policy_sha256="b" * 64,
        rate_window_start_at=NOW,
        rate_window_end_at=NOW + timedelta(minutes=1),
    )
    values: dict[str, object] = {
        "evaluated_at": NOW + timedelta(seconds=10),
        "product": ProductTruth(
            product_id=1101,
            catalogue_status=CatalogueStatus.ENABLED,
            market_status=MarketStatus.TRADABLE,
            contract_multiplier=Decimal("1"),
            max_leverage=Decimal("10"),
            catalogue_observed_at=NOW,
            market_observed_at=NOW,
            evidence_sha256="f" * 64,
        ),
        "price": PriceSnapshot(
            product_id=1101,
            completeness=Completeness.COMPLETE,
            mark_price=Decimal("100"),
            mark_observed_at=NOW + timedelta(seconds=9),
            last_price=Decimal("100"),
            last_observed_at=NOW + timedelta(seconds=9),
            evidence_sha256="1" * 64,
        ),
        "positions": (
            PositionRisk(
                product_id=1101,
                signed_contracts=3,
                notional_per_contract=Decimal("100"),
                effective_leverage=Decimal("2"),
            ),
        ),
        "open_orders": (
            OpenOrderRisk(
                order_key="order-existing",
                intent_key_sha256="2" * 64,
                product_id=1101,
                side=Side.BUY,
                remaining_contracts=1,
                notional_per_contract=Decimal("100"),
                reduce_only=False,
                configured_leverage=Decimal("2"),
            ),
        ),
        "reservations": (reservation,),
        "rate_window": risk_inputs.RateWindowSnapshot(
            completeness=Completeness.COMPLETE,
            source=risk_models.RateWindowSource.CONSUMPTION_LEDGER,
            cursor="consumption-ledger:2",
            observed_at=NOW + timedelta(seconds=10),
            window_start_at=NOW,
            window_end_at=NOW + timedelta(minutes=1),
            consumed_units=2,
            rate_policy_version=1,
            rate_policy_sha256="b" * 64,
            evidence_sha256="6" * 64,
        ),
        "protective_orders": (
            ProtectionOrder(
                order_key="protect-existing",
                product_id=1101,
                side=Side.SELL,
                remaining_contracts=3,
                trigger_price=Decimal("90"),
                reference_price=Decimal("100"),
                reduce_only=True,
                order_type=risk_models.ProtectionOrderType.STOP_MARKET,
                status=risk_models.ProtectionOrderStatus.ACTIVE,
                observed_at=NOW,
                reconciliation_evidence_sha256="3" * 64,
                evidence_sha256="5" * 64,
            ),
        ),
        "reconciliation": ReconciliationSnapshot(
            completeness=Completeness.COMPLETE,
            observed_at=NOW,
            evidence_sha256="3" * 64,
        ),
        "accounting": accounting(),
        "halt": HaltSnapshot(
            completeness=Completeness.COMPLETE,
            source=risk_models.HaltSource.DURABLE_HALT_LEDGER,
            observed_at=NOW,
            read_at=NOW + timedelta(seconds=10),
            revision=1,
            evidence_sha256="8" * 64,
            active_causes=(),
        ),
        "collateral_available": Decimal("1000"),
        "collateral_observed_at": NOW,
        "collateral_evidence_sha256": "4" * 64,
    }
    values.update(changes)
    return RiskSnapshot(**values)  # type: ignore[arg-type]


def checks_by_kind(result: object) -> dict[CheckKind, object]:
    assert hasattr(result, "checks")
    return {check.kind: check for check in result.checks}  # type: ignore[attr-defined]


def test_all_sixteen_checks_are_computed_and_approved_with_canonical_evidence() -> None:
    manager = RiskManager()
    result = manager.evaluate(policy(), intent(), snapshot())
    repeated = manager.evaluate(policy(), intent(), snapshot())

    assert result.outcome is DecisionOutcome.APPROVED
    assert tuple(check.kind for check in result.checks) == tuple(CheckKind)
    assert len(result.checks) == 16
    assert all(
        check.outcome is CheckOutcome.PASS
        for check in result.checks
        if check.kind is not CheckKind.REDUCE_ONLY_NO_REVERSAL
    )
    assert result.checks[-1].outcome is CheckOutcome.NOT_APPLICABLE
    assert result.price_source is PriceSource.MARK
    assert result == repeated
    for digest in (
        result.intent_key_sha256,
        result.check_set_sha256,
        result.decision_sha256,
        *(check.input_set_sha256 for check in result.checks),
        *(check.evidence_sha256 for check in result.checks),
    ):
        assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")

    observed = checks_by_kind(result)
    assert observed[CheckKind.MAX_POSITION].observed_integer == 7  # type: ignore[attr-defined]
    assert observed[CheckKind.TOTAL_EXPOSURE].observed_decimal == Decimal("700")  # type: ignore[attr-defined]
    assert observed[CheckKind.MAX_LOSS_PER_TRADE].observed_decimal == Decimal("20")  # type: ignore[attr-defined]
    assert observed[CheckKind.MAX_DAILY_LOSS].observed_decimal == Decimal("36")  # type: ignore[attr-defined]
    assert observed[CheckKind.MAX_OPEN_ORDERS].observed_integer == 3  # type: ignore[attr-defined]
    assert observed[CheckKind.MAX_ORDER_RATE].observed_integer == 4  # type: ignore[attr-defined]
    assert observed[CheckKind.COLLATERAL_MARGIN].observed_decimal == Decimal("100")  # type: ignore[attr-defined]
    assert observed[CheckKind.COLLATERAL_MARGIN].limit_decimal == Decimal("990")  # type: ignore[attr-defined]
    assert observed[CheckKind.PROTECTION].observed_integer == 5  # type: ignore[attr-defined]

    plan = result.reservation
    assert plan is not None
    assert plan.reserved_incremental_gross_exposure == Decimal("200")
    assert plan.reserved_worst_case_loss == Decimal("20")
    assert plan.reserved_collateral == Decimal("100")
    assert plan.reserved_open_order_slots == 1
    assert plan.reserved_pending_rate_units == 1
    assert len(plan.reservation_plan_sha256) == 64
    assert result.halt_directive is None


@pytest.mark.parametrize(
    "kind,policy_changes,intent_changes,snapshot_changes",
    [
        (
            CheckKind.PRODUCT_CATALOGUE,
            {},
            {},
            {"product": replace(snapshot().product, catalogue_status=CatalogueStatus.UNSUPPORTED)},
        ),
        (
            CheckKind.MARKET_TRADABILITY,
            {},
            {},
            {"product": replace(snapshot().product, market_status=MarketStatus.CLOSED)},
        ),
        (CheckKind.MAX_POSITION, {"max_position_contracts": 6}, {}, {}),
        (CheckKind.TOTAL_EXPOSURE, {"max_total_exposure": Decimal("699")}, {}, {}),
        (CheckKind.MAX_LEVERAGE, {"max_leverage": Decimal("1")}, {}, {}),
        (CheckKind.MAX_LOSS_PER_TRADE, {"max_loss_per_trade": Decimal("19")}, {}, {}),
        (CheckKind.MAX_DAILY_LOSS, {"max_daily_loss": Decimal("35")}, {}, {}),
        (CheckKind.MAX_OPEN_ORDERS, {"max_open_orders": 2}, {}, {}),
        (CheckKind.MAX_ORDER_RATE, {"max_order_rate": 3}, {}, {}),
        (CheckKind.PRICE_COLLAR, {}, {"order_price": Decimal("102")}, {}),
        (
            CheckKind.COLLATERAL_MARGIN,
            {},
            {},
            {"collateral_available": Decimal("109")},
        ),
        (
            CheckKind.KILL_HALT,
            {},
            {},
            {"halt": replace(snapshot().halt, active_causes=(HaltCause.KILL_SWITCH,))},
        ),
        (
            CheckKind.FRESHNESS_RECONCILIATION,
            {},
            {},
            {
                "reconciliation": replace(
                    snapshot().reconciliation, completeness=Completeness.PARTIAL
                )
            },
        ),
        (
            CheckKind.PROTECTION,
            {},
            {"planned_protection_contracts": 1},
            {},
        ),
    ],
)
def test_each_numeric_or_state_limit_denies_at_the_first_unsafe_value(
    kind: CheckKind,
    policy_changes: dict[str, object],
    intent_changes: dict[str, object],
    snapshot_changes: dict[str, object],
) -> None:
    result = RiskManager().evaluate(
        policy(**policy_changes), intent(**intent_changes), snapshot(**snapshot_changes)
    )
    check = checks_by_kind(result)[kind]
    assert result.outcome is DecisionOutcome.DENIED
    assert check.outcome in {CheckOutcome.FAIL, CheckOutcome.UNKNOWN}  # type: ignore[attr-defined]


def test_duplicate_key_is_derived_by_manager_and_active_duplicate_denies() -> None:
    manager = RiskManager()
    candidate = intent()
    first = manager.evaluate(policy(), candidate, snapshot())
    duplicate = OpenOrderRisk(
        order_key="other-order",
        intent_key_sha256=first.intent_key_sha256,
        product_id=1101,
        side=Side.SELL,
        remaining_contracts=1,
        notional_per_contract=Decimal("100"),
        reduce_only=False,
        configured_leverage=Decimal("2"),
    )
    state = snapshot(open_orders=(*snapshot().open_orders, duplicate))
    result = manager.evaluate(policy(), candidate, state)
    check = checks_by_kind(result)[CheckKind.DUPLICATE_ORDER]
    assert result.outcome is DecisionOutcome.DENIED
    assert check.outcome is CheckOutcome.FAIL  # type: ignore[attr-defined]
    assert check.reason_code == "DUPLICATE_ORDER"  # type: ignore[attr-defined]


def test_stale_mark_fails_closed_unless_explicit_fresh_last_fallback_is_enabled() -> None:
    state = snapshot(
        price=replace(
            snapshot().price,
            mark_observed_at=NOW,
            last_observed_at=NOW + timedelta(seconds=9),
        )
    )
    denied = RiskManager().evaluate(policy(), intent(), state)
    stale = checks_by_kind(denied)[CheckKind.PRICE_COLLAR]
    assert denied.outcome is DecisionOutcome.DENIED
    assert stale.outcome is CheckOutcome.UNKNOWN  # type: ignore[attr-defined]
    assert stale.reason_code == "STALE_EVIDENCE"  # type: ignore[attr-defined]

    approved = RiskManager().evaluate(policy(allow_last_price_fallback=True), intent(), state)
    assert approved.outcome is DecisionOutcome.APPROVED
    assert approved.price_source is PriceSource.LAST


def test_daily_loss_adds_only_unaccounted_reservations_and_candidate_loss() -> None:
    state = snapshot()
    reservation_key = state.reservations[0].reservation_key
    covered = snapshot(accounting=accounting(included_reservation_keys=(reservation_key,)))
    result = RiskManager().evaluate(policy(), intent(), covered)
    check = checks_by_kind(result)[CheckKind.MAX_DAILY_LOSS]
    assert check.observed_decimal == Decimal("26")  # type: ignore[attr-defined]


def test_accounting_ambiguity_denies_new_risk_and_proposes_no_authorized_side_effect() -> None:
    state = snapshot(accounting=replace(accounting(), completeness=Completeness.AMBIGUOUS))
    result = RiskManager().evaluate(policy(), intent(), state)
    check = checks_by_kind(result)[CheckKind.MAX_DAILY_LOSS]
    assert result.outcome is DecisionOutcome.DENIED
    assert check.outcome is CheckOutcome.UNKNOWN  # type: ignore[attr-defined]
    assert check.reason_code == "AMBIGUOUS_EVIDENCE"  # type: ignore[attr-defined]
    assert result.halt_directive is not None
    assert result.halt_directive.cause is HaltCause.ACCOUNTING_UNKNOWN
    assert result.halt_directive.authorizes_side_effect is False


def test_actual_daily_loss_breach_proposes_a_non_authorizing_halt() -> None:
    state = snapshot(accounting=accounting(current_equity=Decimal("800")))
    result = RiskManager().evaluate(policy(), intent(), state)
    assert result.outcome is DecisionOutcome.DENIED
    assert result.halt_directive is not None
    assert result.halt_directive.cause is HaltCause.DAILY_LOSS
    assert result.halt_directive.authorizes_side_effect is False


def test_controlled_reduce_only_can_pass_during_halt_without_risk_reservations() -> None:
    reducing = intent(
        side=Side.SELL,
        contracts=2,
        risk_effect=RiskEffect.REDUCE_ONLY,
        stop_price=None,
        planned_protection_contracts=0,
    )
    state = snapshot(halt=replace(snapshot().halt, active_causes=(HaltCause.KILL_SWITCH,)))
    result = RiskManager().evaluate(policy(), reducing, state)
    checks = checks_by_kind(result)
    assert result.outcome is DecisionOutcome.APPROVED
    assert checks[CheckKind.KILL_HALT].outcome is CheckOutcome.PASS  # type: ignore[attr-defined]
    assert checks[CheckKind.REDUCE_ONLY_NO_REVERSAL].outcome is CheckOutcome.PASS  # type: ignore[attr-defined]
    for kind in (
        CheckKind.MAX_POSITION,
        CheckKind.TOTAL_EXPOSURE,
        CheckKind.MAX_LEVERAGE,
        CheckKind.MAX_LOSS_PER_TRADE,
        CheckKind.MAX_DAILY_LOSS,
        CheckKind.PROTECTION,
    ):
        assert checks[kind].outcome is CheckOutcome.NOT_APPLICABLE  # type: ignore[attr-defined]
    assert result.reservation is not None
    assert result.reservation.reserved_incremental_gross_exposure == 0
    assert result.reservation.reserved_worst_case_loss == 0
    assert result.reservation.reserved_collateral == 0


def test_reduce_only_reversal_is_denied_with_exact_pre_and_post_position() -> None:
    reversing = intent(
        side=Side.SELL,
        contracts=4,
        risk_effect=RiskEffect.REDUCE_ONLY,
        stop_price=None,
        planned_protection_contracts=0,
    )
    result = RiskManager().evaluate(policy(), reversing, snapshot())
    check = checks_by_kind(result)[CheckKind.REDUCE_ONLY_NO_REVERSAL]
    assert result.outcome is DecisionOutcome.DENIED
    assert check.outcome is CheckOutcome.FAIL  # type: ignore[attr-defined]
    assert check.reason_code == "POSITION_REVERSAL"  # type: ignore[attr-defined]
    assert check.limit_integer == 3  # type: ignore[attr-defined]
    assert check.observed_integer == -1  # type: ignore[attr-defined]


def test_modify_replaces_target_in_exposure_slot_and_duplicate_calculations() -> None:
    modifying = intent(
        operation=OperationKind.MODIFY_ORDER,
        target_order_key="order-existing",
    )
    result = RiskManager().evaluate(policy(), modifying, snapshot())
    checks = checks_by_kind(result)
    assert result.outcome is DecisionOutcome.APPROVED
    assert checks[CheckKind.MAX_POSITION].observed_integer == 6  # type: ignore[attr-defined]
    assert checks[CheckKind.TOTAL_EXPOSURE].observed_decimal == Decimal("600")  # type: ignore[attr-defined]
    assert checks[CheckKind.MAX_OPEN_ORDERS].observed_integer == 2  # type: ignore[attr-defined]
    assert result.reservation is not None
    assert result.reservation.reserved_open_order_slots == 0


def test_modify_with_missing_target_fails_closed_as_ambiguous_truth() -> None:
    modifying = intent(
        operation=OperationKind.MODIFY_ORDER,
        target_order_key="missing-order",
    )
    result = RiskManager().evaluate(policy(), modifying, snapshot())
    checks = checks_by_kind(result)
    assert result.outcome is DecisionOutcome.DENIED
    assert result.reservation is None
    assert (
        checks[CheckKind.FRESHNESS_RECONCILIATION].outcome is CheckOutcome.UNKNOWN  # type: ignore[attr-defined]
    )
    assert (
        checks[CheckKind.FRESHNESS_RECONCILIATION].reason_code == "AMBIGUOUS_EVIDENCE"  # type: ignore[attr-defined]
    )


def test_ambiguous_reconciliation_never_authorizes_state_dependent_checks() -> None:
    state = snapshot(
        reconciliation=replace(snapshot().reconciliation, completeness=Completeness.AMBIGUOUS)
    )
    result = RiskManager().evaluate(policy(), intent(), state)
    checks = checks_by_kind(result)
    assert result.outcome is DecisionOutcome.DENIED
    assert result.reservation is None
    for kind in (
        CheckKind.MAX_POSITION,
        CheckKind.TOTAL_EXPOSURE,
        CheckKind.MAX_OPEN_ORDERS,
        CheckKind.DUPLICATE_ORDER,
        CheckKind.COLLATERAL_MARGIN,
        CheckKind.FRESHNESS_RECONCILIATION,
        CheckKind.PROTECTION,
    ):
        assert checks[kind].outcome is CheckOutcome.UNKNOWN  # type: ignore[attr-defined]


def test_missing_stop_cannot_understate_trade_or_projected_daily_loss() -> None:
    result = RiskManager().evaluate(
        policy(),
        intent(stop_price=None, planned_protection_contracts=0),
        snapshot(),
    )
    checks = checks_by_kind(result)
    assert result.outcome is DecisionOutcome.DENIED
    assert checks[CheckKind.MAX_LOSS_PER_TRADE].outcome is CheckOutcome.UNKNOWN  # type: ignore[attr-defined]
    assert checks[CheckKind.MAX_DAILY_LOSS].outcome is CheckOutcome.UNKNOWN  # type: ignore[attr-defined]
    assert checks[CheckKind.PROTECTION].outcome is CheckOutcome.FAIL  # type: ignore[attr-defined]


def test_limits_are_inclusive_at_exact_position_exposure_loss_daily_and_collar_boundaries() -> None:
    result = RiskManager().evaluate(
        policy(
            max_position_contracts=7,
            max_total_exposure=Decimal("702"),
            max_loss_per_trade=Decimal("20"),
            max_daily_loss=Decimal("36"),
        ),
        intent(order_price=Decimal("101"), stop_price=Decimal("91")),
        snapshot(),
    )
    checks = checks_by_kind(result)
    assert result.outcome is DecisionOutcome.APPROVED
    for kind in (
        CheckKind.MAX_POSITION,
        CheckKind.TOTAL_EXPOSURE,
        CheckKind.MAX_LOSS_PER_TRADE,
        CheckKind.MAX_DAILY_LOSS,
        CheckKind.PRICE_COLLAR,
    ):
        assert checks[kind].outcome is CheckOutcome.PASS  # type: ignore[attr-defined]


def test_product_leverage_cap_is_enforced_even_when_policy_cap_is_higher() -> None:
    state = snapshot(product=replace(snapshot().product, max_leverage=Decimal("1")))
    result = RiskManager().evaluate(policy(max_leverage=Decimal("5")), intent(), state)
    check = checks_by_kind(result)[CheckKind.MAX_LEVERAGE]
    assert result.outcome is DecisionOutcome.DENIED
    assert check.outcome is CheckOutcome.FAIL  # type: ignore[attr-defined]
    assert check.limit_decimal == Decimal("1")  # type: ignore[attr-defined]


def test_rate_window_includes_current_start_and_excludes_prior_end() -> None:
    evaluated = NOW + timedelta(minutes=1)
    protection = replace(snapshot().protective_orders[0], observed_at=evaluated)
    state = snapshot(
        evaluated_at=evaluated,
        price=replace(
            snapshot().price,
            mark_observed_at=evaluated,
            last_observed_at=evaluated,
        ),
        reservations=(),
        rate_window=risk_inputs.RateWindowSnapshot(
            completeness=Completeness.COMPLETE,
            source=risk_models.RateWindowSource.CONSUMPTION_LEDGER,
            cursor="consumption-ledger:next-window",
            observed_at=evaluated,
            window_start_at=evaluated,
            window_end_at=evaluated + timedelta(minutes=1),
            consumed_units=1,
            rate_policy_version=1,
            rate_policy_sha256="b" * 64,
            evidence_sha256="7" * 64,
        ),
        protective_orders=(protection,),
        reconciliation=replace(snapshot().reconciliation, observed_at=evaluated),
        accounting=replace(accounting(), observed_at=evaluated),
        halt=replace(snapshot().halt, observed_at=evaluated, read_at=evaluated),
        collateral_observed_at=evaluated,
    )
    result = RiskManager().evaluate(policy(), intent(), state)
    check = checks_by_kind(result)[CheckKind.MAX_ORDER_RATE]
    assert check.observed_integer == 2  # type: ignore[attr-defined]
    assert check.outcome is CheckOutcome.PASS  # type: ignore[attr-defined]


def test_rate_consumption_with_mismatched_policy_proof_fails_closed() -> None:
    mismatched = replace(snapshot().rate_window, rate_policy_sha256="9" * 64)
    result = RiskManager().evaluate(policy(), intent(), snapshot(rate_window=mismatched))
    check = checks_by_kind(result)[CheckKind.MAX_ORDER_RATE]
    assert result.outcome is DecisionOutcome.DENIED
    assert check.outcome is CheckOutcome.UNKNOWN  # type: ignore[attr-defined]
    assert check.reason_code == "AMBIGUOUS_EVIDENCE"  # type: ignore[attr-defined]


def test_evaluation_digest_and_amounts_ignore_ambient_decimal_context() -> None:
    baseline = RiskManager().evaluate(policy(), intent(), snapshot())
    with localcontext() as context:
        context.prec = 2
        constrained = RiskManager().evaluate(policy(), intent(), snapshot())
    assert constrained == baseline


def test_reconstructed_or_callback_capable_enum_inputs_are_rejected_without_comparison() -> None:
    reconstructed = str.__new__(Side, "buy")
    with pytest.raises(RiskInputError):
        intent(side=reconstructed)

    class Explosive:
        def __eq__(self, other: object) -> bool:
            raise AssertionError("untrusted equality ran")

    with pytest.raises(RiskInputError):
        intent(side=Explosive())


def test_mutable_containers_and_future_observations_fail_before_evaluation() -> None:
    with pytest.raises(RiskInputError):
        snapshot(positions=list(snapshot().positions))
    future_price = replace(
        snapshot().price,
        mark_observed_at=snapshot().evaluated_at + timedelta(microseconds=1),
    )
    with pytest.raises(RiskInputError):
        snapshot(price=future_price)
    future_reservation = replace(
        snapshot().reservations[0],
        rate_window_start_at=snapshot().evaluated_at + timedelta(seconds=1),
        rate_window_end_at=snapshot().evaluated_at + timedelta(minutes=1),
    )
    with pytest.raises(RiskInputError):
        snapshot(reservations=(future_reservation,))


def test_any_input_change_changes_manager_owned_decision_evidence() -> None:
    first = RiskManager().evaluate(policy(), intent(), snapshot())
    changed = RiskManager().evaluate(
        policy(), intent(), snapshot(collateral_available=Decimal("999"))
    )
    assert first.decision_sha256 != changed.decision_sha256
    assert first.check_set_sha256 != changed.check_set_sha256
