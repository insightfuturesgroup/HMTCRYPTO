"""Validated derived values shared by pure risk check groups."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hmt_crypto.risk.evidence import canonical_digest
from hmt_crypto.risk.inputs import OpenOrderRisk, RiskIntent, RiskPolicy, RiskSnapshot
from hmt_crypto.risk.math import (
    amount_from_scaled,
    conservative_division,
    conservative_product,
    multiply_amount,
    scaled_amount,
    sum_amounts,
    with_bps_buffer,
)
from hmt_crypto.risk.models import (
    CheckKind,
    CheckOutcome,
    Completeness,
    MetricKind,
    OperationKind,
    RiskCheck,
    RiskInputError,
    Unit,
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def age_seconds(now: datetime, observed: datetime) -> int:
    """Return a conservative whole-second age without float arithmetic."""
    delta = now - observed
    micros = (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    if micros < 0:
        raise RiskInputError() from None
    return (micros + 999_999) // 1_000_000


def rate_window(now: datetime, seconds: int) -> tuple[datetime, datetime]:
    """Return the deterministic epoch-aligned order-rate window."""
    delta = now - _EPOCH
    elapsed = delta.days * 86_400 + delta.seconds
    start = _EPOCH + timedelta(seconds=(elapsed // seconds) * seconds)
    return start, start + timedelta(seconds=seconds)


def limit_outcome(passes: bool) -> tuple[CheckOutcome, str]:
    """Map one inclusive limit comparison to persistence-compatible evidence."""
    if passes:
        return CheckOutcome.PASS, "SATISFIED"
    return CheckOutcome.FAIL, "LIMIT_EXCEEDED"


def incomplete_reason(completeness: Completeness) -> str:
    """Map raw truth quality to an UNKNOWN reason."""
    if completeness is Completeness.MISSING:
        return "MISSING_EVIDENCE"
    return "AMBIGUOUS_EVIDENCE"


def make_check(
    *,
    kind: CheckKind,
    outcome: CheckOutcome,
    reason: str,
    observed: Decimal | int | None,
    limit: Decimal | int | None,
    unit: Unit,
    evidence_at: datetime | None,
    context_sha256: str,
) -> RiskCheck:
    """Build one canonical, manager-owned immutable check result."""
    observed_decimal: Decimal | None = None
    limit_decimal: Decimal | None = None
    observed_integer: int | None = None
    limit_integer: int | None = None
    if type(observed) is Decimal and type(limit) is Decimal:
        metric = MetricKind.DECIMAL
        observed_decimal, limit_decimal = observed, limit
    elif type(observed) is int and type(limit) is int:
        metric = MetricKind.INTEGER
        observed_integer, limit_integer = observed, limit
    elif observed is None and limit is None:
        metric = MetricKind.NONE
        unit = Unit.NONE
    else:
        raise RiskInputError() from None
    input_sha = canonical_digest(
        {
            "kind": "risk_check_input_v1",
            "check_kind": kind,
            "context": context_sha256,
        }
    )
    evidence = {
        "check_kind": kind,
        "outcome": outcome,
        "reason_code": reason,
        "metric_kind": metric,
        "observed_decimal": observed_decimal,
        "limit_decimal": limit_decimal,
        "observed_integer": observed_integer,
        "limit_integer": limit_integer,
        "unit": unit,
        "evidence_observed_at": evidence_at,
        "input_set_sha256": input_sha,
    }
    return RiskCheck(
        kind=kind,
        outcome=outcome,
        reason_code=reason,
        metric_kind=metric,
        observed_decimal=observed_decimal,
        limit_decimal=limit_decimal,
        observed_integer=observed_integer,
        limit_integer=limit_integer,
        unit=unit,
        evidence_observed_at=evidence_at,
        input_set_sha256=input_sha,
        evidence_sha256=canonical_digest(evidence),
    )


@dataclass(frozen=True, slots=True)
class EvaluationContext:
    """Derived values with no repository, clock, exchange, or config authority."""

    policy: RiskPolicy
    intent: RiskIntent
    snapshot: RiskSnapshot
    context_sha256: str
    intent_key_sha256: str
    open_orders: tuple[OpenOrderRisk, ...]
    target_present: bool
    current_position: int
    candidate_per_contract: Decimal
    candidate_exposure: Decimal
    candidate_loss: Decimal | None
    candidate_collateral: Decimal
    recon_age: int | None
    recon_ready: bool
    recon_reason: str
    rate_start: datetime
    rate_end: datetime
    candidate_slots: int

    def check(
        self,
        kind: CheckKind,
        outcome: CheckOutcome,
        reason: str,
        observed: Decimal | int | None = None,
        limit: Decimal | int | None = None,
        unit: Unit = Unit.NONE,
        evidence_at: datetime | None = None,
    ) -> RiskCheck:
        """Build one check bound to this exact input set."""
        return make_check(
            kind=kind,
            outcome=outcome,
            reason=reason,
            observed=observed,
            limit=limit,
            unit=unit,
            evidence_at=evidence_at,
            context_sha256=self.context_sha256,
        )


def build_context(
    policy: RiskPolicy, intent: RiskIntent, snapshot: RiskSnapshot
) -> EvaluationContext:
    """Validate exact contract ownership and derive candidate amounts."""
    if (
        type(policy) is not RiskPolicy
        or type(intent) is not RiskIntent
        or type(snapshot) is not RiskSnapshot
        or intent.product_id != snapshot.product.product_id
    ):
        raise RiskInputError() from None
    context_sha = canonical_digest(
        {
            "kind": "risk_evaluation_input_v1",
            "policy": policy,
            "intent": intent,
            "snapshot": snapshot,
        }
    )
    intent_key_sha = canonical_digest(
        {
            "kind": "risk_intent_key_v1",
            "intent_owner": intent.intent_owner,
            "product_id": intent.product_id,
            "operation": intent.operation,
            "target_order_key": intent.target_order_key,
            "side": intent.side,
            "contracts": intent.contracts,
            "risk_effect": intent.risk_effect,
            "order_price": intent.order_price,
            "leverage": intent.leverage,
            "stop_price": intent.stop_price,
            "loss_fee_reserve": intent.loss_fee_reserve,
            "planned_protection_contracts": intent.planned_protection_contracts,
        }
    )
    open_orders = tuple(
        item
        for item in snapshot.open_orders
        if not (
            intent.operation is OperationKind.MODIFY_ORDER
            and item.order_key == intent.target_order_key
        )
    )
    target_present = intent.operation is OperationKind.PLACE_ORDER or any(
        item.order_key == intent.target_order_key for item in snapshot.open_orders
    )
    current_position = next(
        (
            item.signed_contracts
            for item in snapshot.positions
            if item.product_id == intent.product_id
        ),
        0,
    )
    per_contract = conservative_product(intent.order_price, snapshot.product.contract_multiplier)
    exposure = multiply_amount(per_contract, intent.contracts)
    loss: Decimal | None = None
    if intent.stop_price is not None:
        difference = amount_from_scaled(
            abs(scaled_amount(intent.order_price) - scaled_amount(intent.stop_price))
        )
        loss_per_contract = conservative_product(difference, snapshot.product.contract_multiplier)
        loss = sum_amounts(
            (multiply_amount(loss_per_contract, intent.contracts), intent.loss_fee_reserve)
        )
    collateral = with_bps_buffer(
        conservative_division(exposure, intent.leverage), policy.collateral_buffer_bps
    )
    reconciliation = snapshot.reconciliation
    recon_age = (
        None
        if reconciliation.observed_at is None
        else age_seconds(snapshot.evaluated_at, reconciliation.observed_at)
    )
    recon_ready = (
        reconciliation.completeness is Completeness.COMPLETE
        and recon_age is not None
        and recon_age <= policy.max_reconciliation_age_seconds
        and target_present
    )
    recon_reason = (
        "AMBIGUOUS_EVIDENCE"
        if not target_present
        else (
            "STALE_EVIDENCE"
            if reconciliation.completeness is Completeness.COMPLETE
            else incomplete_reason(reconciliation.completeness)
        )
    )
    start, end = rate_window(snapshot.evaluated_at, policy.rate_window_seconds)
    return EvaluationContext(
        policy=policy,
        intent=intent,
        snapshot=snapshot,
        context_sha256=context_sha,
        intent_key_sha256=intent_key_sha,
        open_orders=open_orders,
        target_present=target_present,
        current_position=current_position,
        candidate_per_contract=per_contract,
        candidate_exposure=exposure,
        candidate_loss=loss,
        candidate_collateral=collateral,
        recon_age=recon_age,
        recon_ready=recon_ready,
        recon_reason=recon_reason,
        rate_start=start,
        rate_end=end,
        candidate_slots=1 if intent.operation is OperationKind.PLACE_ORDER else 0,
    )


__all__ = [
    "EvaluationContext",
    "age_seconds",
    "build_context",
    "incomplete_reason",
    "limit_outcome",
    "make_check",
]
