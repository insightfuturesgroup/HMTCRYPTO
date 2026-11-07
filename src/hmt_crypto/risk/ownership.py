"""Rebuild untrusted risk dataclasses into manager-owned canonical values."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from hmt_crypto.risk.inputs import (
    AssetRef,
    DailyAccounting,
    HaltSnapshot,
    OpenOrderRisk,
    PositionRisk,
    PriceSnapshot,
    ProductTruth,
    ProtectionOrder,
    RateWindowSnapshot,
    ReconciliationSnapshot,
    ReservationRisk,
    RiskIntent,
    RiskPolicy,
    RiskSnapshot,
)
from hmt_crypto.risk.models import RiskInputError

_Raw = TypeVar("_Raw")
_Owned = TypeVar("_Owned")


def _exact_tuple(
    value: object,
    expected: type[_Raw],
    owner: Callable[[_Raw], _Owned],
) -> tuple[_Owned, ...]:
    if type(value) is not tuple:
        raise RiskInputError() from None
    result: list[_Owned] = []
    for item in value:
        if type(item) is not expected:
            raise RiskInputError() from None
        result.append(owner(item))
    return tuple(result)


def _asset(value: object) -> AssetRef:
    if type(value) is not AssetRef:
        raise RiskInputError() from None
    raw = value
    return AssetRef(asset_id=raw.asset_id, symbol=raw.symbol)


def _policy(value: object) -> RiskPolicy:
    if type(value) is not RiskPolicy:
        raise RiskInputError() from None
    raw = value
    return RiskPolicy(
        version=raw.version,
        policy_sha256=raw.policy_sha256,
        reference_asset=_asset(raw.reference_asset),
        collateral_asset=_asset(raw.collateral_asset),
        max_position_contracts=raw.max_position_contracts,
        max_total_exposure=raw.max_total_exposure,
        max_leverage=raw.max_leverage,
        max_loss_per_trade=raw.max_loss_per_trade,
        max_daily_loss=raw.max_daily_loss,
        max_open_orders=raw.max_open_orders,
        max_order_rate=raw.max_order_rate,
        rate_window_seconds=raw.rate_window_seconds,
        rate_policy_version=raw.rate_policy_version,
        rate_policy_sha256=raw.rate_policy_sha256,
        price_collar_bps=raw.price_collar_bps,
        max_catalogue_age_seconds=raw.max_catalogue_age_seconds,
        max_mark_age_seconds=raw.max_mark_age_seconds,
        allow_last_price_fallback=raw.allow_last_price_fallback,
        max_last_price_age_seconds=raw.max_last_price_age_seconds,
        max_reconciliation_age_seconds=raw.max_reconciliation_age_seconds,
        max_accounting_age_seconds=raw.max_accounting_age_seconds,
        max_halt_age_seconds=raw.max_halt_age_seconds,
        daily_reset_second_utc=raw.daily_reset_second_utc,
        collateral_buffer_bps=raw.collateral_buffer_bps,
    )


def _intent(value: object) -> RiskIntent:
    if type(value) is not RiskIntent:
        raise RiskInputError() from None
    raw = value
    return RiskIntent(
        product_id=raw.product_id,
        operation=raw.operation,
        target_order_key=raw.target_order_key,
        side=raw.side,
        contracts=raw.contracts,
        risk_effect=raw.risk_effect,
        order_price=raw.order_price,
        leverage=raw.leverage,
        stop_price=raw.stop_price,
        loss_fee_reserve=raw.loss_fee_reserve,
        planned_protection_contracts=raw.planned_protection_contracts,
        intent_owner=raw.intent_owner,
        intent_key=raw.intent_key,
    )


def _accounting(raw: DailyAccounting) -> DailyAccounting:
    return DailyAccounting(
        completeness=raw.completeness,
        observed_at=raw.observed_at,
        window_start_at=raw.window_start_at,
        baseline_observed_at=raw.baseline_observed_at,
        baseline_equity=raw.baseline_equity,
        current_equity=raw.current_equity,
        realized_pnl=raw.realized_pnl,
        unrealized_pnl=raw.unrealized_pnl,
        unresolved_loss_reserve=raw.unresolved_loss_reserve,
        ledger_cursor_start=raw.ledger_cursor_start,
        ledger_cursor_end=raw.ledger_cursor_end,
        ledger_evidence_sha256=raw.ledger_evidence_sha256,
        included_reservation_keys=raw.included_reservation_keys,
    )


def _product(raw: ProductTruth) -> ProductTruth:
    return ProductTruth(
        product_id=raw.product_id,
        catalogue_status=raw.catalogue_status,
        market_status=raw.market_status,
        contract_multiplier=raw.contract_multiplier,
        max_leverage=raw.max_leverage,
        catalogue_observed_at=raw.catalogue_observed_at,
        market_observed_at=raw.market_observed_at,
        evidence_sha256=raw.evidence_sha256,
    )


def _price(raw: PriceSnapshot) -> PriceSnapshot:
    return PriceSnapshot(
        product_id=raw.product_id,
        completeness=raw.completeness,
        mark_price=raw.mark_price,
        mark_observed_at=raw.mark_observed_at,
        last_price=raw.last_price,
        last_observed_at=raw.last_observed_at,
        evidence_sha256=raw.evidence_sha256,
    )


def _position(raw: PositionRisk) -> PositionRisk:
    return PositionRisk(
        product_id=raw.product_id,
        signed_contracts=raw.signed_contracts,
        notional_per_contract=raw.notional_per_contract,
        effective_leverage=raw.effective_leverage,
    )


def _open_order(raw: OpenOrderRisk) -> OpenOrderRisk:
    return OpenOrderRisk(
        order_key=raw.order_key,
        intent_key_sha256=raw.intent_key_sha256,
        product_id=raw.product_id,
        side=raw.side,
        remaining_contracts=raw.remaining_contracts,
        notional_per_contract=raw.notional_per_contract,
        reduce_only=raw.reduce_only,
        configured_leverage=raw.configured_leverage,
    )


def _reservation(raw: ReservationRisk) -> ReservationRisk:
    return ReservationRisk(
        reservation_key=raw.reservation_key,
        intent_key_sha256=raw.intent_key_sha256,
        product_id=raw.product_id,
        side=raw.side,
        risk_effect=raw.risk_effect,
        contracts=raw.contracts,
        notional_per_contract=raw.notional_per_contract,
        configured_leverage=raw.configured_leverage,
        worst_case_loss=raw.worst_case_loss,
        collateral=raw.collateral,
        open_order_slots=raw.open_order_slots,
        pending_rate_units=raw.pending_rate_units,
        rate_policy_version=raw.rate_policy_version,
        rate_policy_sha256=raw.rate_policy_sha256,
        rate_window_start_at=raw.rate_window_start_at,
        rate_window_end_at=raw.rate_window_end_at,
    )


def _rate_window(raw: RateWindowSnapshot) -> RateWindowSnapshot:
    return RateWindowSnapshot(
        completeness=raw.completeness,
        source=raw.source,
        cursor=raw.cursor,
        observed_at=raw.observed_at,
        window_start_at=raw.window_start_at,
        window_end_at=raw.window_end_at,
        consumed_units=raw.consumed_units,
        rate_policy_version=raw.rate_policy_version,
        rate_policy_sha256=raw.rate_policy_sha256,
        evidence_sha256=raw.evidence_sha256,
    )


def _protection(raw: ProtectionOrder) -> ProtectionOrder:
    return ProtectionOrder(
        order_key=raw.order_key,
        product_id=raw.product_id,
        side=raw.side,
        remaining_contracts=raw.remaining_contracts,
        trigger_price=raw.trigger_price,
        reference_price=raw.reference_price,
        reduce_only=raw.reduce_only,
        order_type=raw.order_type,
        status=raw.status,
        observed_at=raw.observed_at,
        reconciliation_evidence_sha256=raw.reconciliation_evidence_sha256,
        evidence_sha256=raw.evidence_sha256,
    )


def _reconciliation(raw: ReconciliationSnapshot) -> ReconciliationSnapshot:
    return ReconciliationSnapshot(
        completeness=raw.completeness,
        observed_at=raw.observed_at,
        evidence_sha256=raw.evidence_sha256,
    )


def _halt(raw: HaltSnapshot) -> HaltSnapshot:
    return HaltSnapshot(
        completeness=raw.completeness,
        source=raw.source,
        observed_at=raw.observed_at,
        read_at=raw.read_at,
        revision=raw.revision,
        evidence_sha256=raw.evidence_sha256,
        active_causes=raw.active_causes,
    )


def _snapshot(value: object) -> RiskSnapshot:
    if type(value) is not RiskSnapshot:
        raise RiskInputError() from None
    raw = value
    if type(raw.product) is not ProductTruth:
        raise RiskInputError() from None
    if type(raw.price) is not PriceSnapshot:
        raise RiskInputError() from None
    if type(raw.rate_window) is not RateWindowSnapshot:
        raise RiskInputError() from None
    if type(raw.reconciliation) is not ReconciliationSnapshot:
        raise RiskInputError() from None
    if type(raw.accounting) is not DailyAccounting:
        raise RiskInputError() from None
    if type(raw.halt) is not HaltSnapshot:
        raise RiskInputError() from None
    return RiskSnapshot(
        evaluated_at=raw.evaluated_at,
        product=_product(raw.product),
        price=_price(raw.price),
        positions=_exact_tuple(raw.positions, PositionRisk, _position),
        open_orders=_exact_tuple(raw.open_orders, OpenOrderRisk, _open_order),
        reservations=_exact_tuple(raw.reservations, ReservationRisk, _reservation),
        rate_window=_rate_window(raw.rate_window),
        protective_orders=_exact_tuple(raw.protective_orders, ProtectionOrder, _protection),
        reconciliation=_reconciliation(raw.reconciliation),
        accounting=_accounting(raw.accounting),
        halt=_halt(raw.halt),
        collateral_available=raw.collateral_available,
        collateral_observed_at=raw.collateral_observed_at,
        collateral_evidence_sha256=raw.collateral_evidence_sha256,
    )


def own_risk_inputs(
    policy: object,
    intent: object,
    snapshot: object,
) -> tuple[RiskPolicy, RiskIntent, RiskSnapshot]:
    """Return deep manager-owned copies or one opaque fail-closed error."""
    try:
        return _policy(policy), _intent(intent), _snapshot(snapshot)
    except RiskInputError:
        raise
    except AttributeError:
        raise RiskInputError() from None


__all__ = ["own_risk_inputs"]
