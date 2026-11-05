"""Capacity, market-price, readiness, protection, and reduction checks."""

from __future__ import annotations

from hmt_crypto.risk.context import EvaluationContext, age_seconds, incomplete_reason, limit_outcome
from hmt_crypto.risk.exposure import strict_reduction
from hmt_crypto.risk.halt_checks import halt_check
from hmt_crypto.risk.math import amount_from_scaled, conservative_bps, scaled_amount, sum_amounts
from hmt_crypto.risk.models import (
    CheckKind,
    CheckOutcome,
    Completeness,
    PriceSource,
    RiskCheck,
    RiskEffect,
    RiskInputError,
    Side,
    Unit,
)
from hmt_crypto.risk.protection import protection_values


def _open_and_rate_checks(context: EvaluationContext) -> tuple[RiskCheck, RiskCheck]:
    policy, snapshot = context.policy, context.snapshot
    open_count = (
        len(snapshot.open_orders)
        + sum(item.open_order_slots for item in snapshot.reservations)
        + context.candidate_slots
    )
    if context.recon_ready:
        outcome, reason = limit_outcome(open_count <= policy.max_open_orders)
        open_check = context.check(
            CheckKind.MAX_OPEN_ORDERS,
            outcome,
            reason,
            open_count,
            policy.max_open_orders,
            Unit.ORDERS,
            snapshot.reconciliation.observed_at,
        )
    elif context.recon_reason == "STALE_EVIDENCE":
        open_check = context.check(
            CheckKind.MAX_OPEN_ORDERS,
            CheckOutcome.UNKNOWN,
            context.recon_reason,
            open_count,
            policy.max_open_orders,
            Unit.ORDERS,
            snapshot.reconciliation.observed_at,
        )
    else:
        open_check = context.check(
            CheckKind.MAX_OPEN_ORDERS,
            CheckOutcome.UNKNOWN,
            context.recon_reason,
        )
    rate_window = snapshot.rate_window
    relevant_reservations = tuple(
        item
        for item in snapshot.reservations
        if item.rate_window_start_at < context.rate_end
        and item.rate_window_end_at > context.rate_start
    )
    reservation_provenance_matches = all(
        item.rate_policy_version == policy.rate_policy_version
        and item.rate_policy_sha256 == policy.rate_policy_sha256
        for item in relevant_reservations
    )
    if rate_window.completeness is not Completeness.COMPLETE:
        rate_check = context.check(
            CheckKind.MAX_ORDER_RATE,
            CheckOutcome.UNKNOWN,
            incomplete_reason(rate_window.completeness),
        )
        return open_check, rate_check
    window_is_current = (
        rate_window.observed_at == snapshot.evaluated_at
        and rate_window.window_start_at == context.rate_start
        and rate_window.window_end_at == context.rate_end
    )
    provenance_matches = (
        rate_window.rate_policy_version == policy.rate_policy_version
        and rate_window.rate_policy_sha256 == policy.rate_policy_sha256
    )
    if not window_is_current or not provenance_matches or not reservation_provenance_matches:
        rate_check = context.check(
            CheckKind.MAX_ORDER_RATE,
            CheckOutcome.UNKNOWN,
            "STALE_EVIDENCE" if not window_is_current else "AMBIGUOUS_EVIDENCE",
            evidence_at=rate_window.observed_at,
        )
        return open_check, rate_check
    if rate_window.consumed_units is None:
        rate_check = context.check(
            CheckKind.MAX_ORDER_RATE,
            CheckOutcome.UNKNOWN,
            "MISSING_EVIDENCE",
        )
        return open_check, rate_check
    consumed = rate_window.consumed_units
    consumed += sum(item.pending_rate_units for item in relevant_reservations)
    rate_observed = consumed + 1
    outcome, reason = limit_outcome(rate_observed <= policy.max_order_rate)
    rate_check = context.check(
        CheckKind.MAX_ORDER_RATE,
        outcome,
        reason,
        rate_observed,
        policy.max_order_rate,
        Unit.REQUESTS,
        rate_window.observed_at,
    )
    return open_check, rate_check


def _price_check(context: EvaluationContext) -> tuple[RiskCheck, PriceSource]:
    policy, intent, snapshot = context.policy, context.intent, context.snapshot
    price, now = snapshot.price, snapshot.evaluated_at
    selected = None
    selected_at = None
    source = PriceSource.NONE
    if (
        price.completeness is Completeness.COMPLETE
        and price.mark_price is not None
        and price.mark_observed_at is not None
        and age_seconds(now, price.mark_observed_at) <= policy.max_mark_age_seconds
    ):
        selected, selected_at, source = (
            price.mark_price,
            price.mark_observed_at,
            PriceSource.MARK,
        )
    elif (
        policy.allow_last_price_fallback
        and price.completeness is Completeness.COMPLETE
        and price.last_price is not None
        and price.last_observed_at is not None
        and age_seconds(now, price.last_observed_at) <= policy.max_last_price_age_seconds
    ):
        selected, selected_at, source = (
            price.last_price,
            price.last_observed_at,
            PriceSource.LAST,
        )
    if selected is not None:
        collar = conservative_bps(intent.order_price, selected)
        if collar <= policy.price_collar_bps:
            return (
                context.check(
                    CheckKind.PRICE_COLLAR,
                    CheckOutcome.PASS,
                    "SATISFIED",
                    collar,
                    policy.price_collar_bps,
                    Unit.BASIS_POINTS,
                    selected_at,
                ),
                source,
            )
        return (
            context.check(
                CheckKind.PRICE_COLLAR,
                CheckOutcome.FAIL,
                "PRICE_OUTSIDE_COLLAR",
                collar,
                policy.price_collar_bps,
                Unit.BASIS_POINTS,
                selected_at,
            ),
            source,
        )
    if price.completeness is not Completeness.COMPLETE:
        return (
            context.check(
                CheckKind.PRICE_COLLAR,
                CheckOutcome.UNKNOWN,
                incomplete_reason(price.completeness),
            ),
            source,
        )
    stale = price.mark_price if price.mark_price is not None else price.last_price
    stale_at = price.mark_observed_at if price.mark_price is not None else price.last_observed_at
    if stale is None or stale_at is None:
        return (
            context.check(
                CheckKind.PRICE_COLLAR,
                CheckOutcome.UNKNOWN,
                "MISSING_EVIDENCE",
            ),
            source,
        )
    return (
        context.check(
            CheckKind.PRICE_COLLAR,
            CheckOutcome.UNKNOWN,
            "STALE_EVIDENCE",
            conservative_bps(intent.order_price, stale),
            policy.price_collar_bps,
            Unit.BASIS_POINTS,
            stale_at,
        ),
        source,
    )


def _duplicate_check(context: EvaluationContext) -> RiskCheck:
    reconciliation = context.snapshot.reconciliation
    active = {item.intent_key_sha256 for item in context.open_orders} | {
        item.intent_key_sha256 for item in context.snapshot.reservations
    }
    if context.recon_ready:
        if context.intent_key_sha256 in active:
            return context.check(
                CheckKind.DUPLICATE_ORDER,
                CheckOutcome.FAIL,
                "DUPLICATE_ORDER",
                evidence_at=reconciliation.observed_at,
            )
        return context.check(
            CheckKind.DUPLICATE_ORDER,
            CheckOutcome.PASS,
            "SATISFIED",
            evidence_at=reconciliation.observed_at,
        )
    return context.check(
        CheckKind.DUPLICATE_ORDER,
        CheckOutcome.UNKNOWN,
        context.recon_reason,
        evidence_at=(
            reconciliation.observed_at if context.recon_reason == "STALE_EVIDENCE" else None
        ),
    )


def _collateral_check(context: EvaluationContext) -> RiskCheck:
    policy, intent, snapshot = context.policy, context.intent, context.snapshot
    reserved = sum_amounts(tuple(item.collateral for item in snapshot.reservations))
    candidate = (
        sum_amounts(())
        if intent.risk_effect is RiskEffect.REDUCE_ONLY
        else context.candidate_collateral
    )
    required = sum_amounts((reserved, candidate))
    collateral_age = age_seconds(snapshot.evaluated_at, snapshot.collateral_observed_at)
    if not context.recon_ready:
        if context.recon_reason == "STALE_EVIDENCE":
            return context.check(
                CheckKind.COLLATERAL_MARGIN,
                CheckOutcome.UNKNOWN,
                context.recon_reason,
                required,
                snapshot.collateral_available,
                Unit.COLLATERAL_ASSET,
                snapshot.reconciliation.observed_at,
            )
        return context.check(
            CheckKind.COLLATERAL_MARGIN,
            CheckOutcome.UNKNOWN,
            context.recon_reason,
        )
    if collateral_age > policy.max_reconciliation_age_seconds:
        return context.check(
            CheckKind.COLLATERAL_MARGIN,
            CheckOutcome.UNKNOWN,
            "STALE_EVIDENCE",
            required,
            snapshot.collateral_available,
            Unit.COLLATERAL_ASSET,
            snapshot.collateral_observed_at,
        )
    if required <= snapshot.collateral_available:
        remaining = sum_amounts(
            (snapshot.collateral_available, amount_from_scaled(-scaled_amount(reserved)))
        )
        return context.check(
            CheckKind.COLLATERAL_MARGIN,
            CheckOutcome.PASS,
            "SATISFIED",
            candidate,
            remaining,
            Unit.COLLATERAL_ASSET,
            snapshot.collateral_observed_at,
        )
    return context.check(
        CheckKind.COLLATERAL_MARGIN,
        CheckOutcome.FAIL,
        "INSUFFICIENT_COLLATERAL",
        required,
        snapshot.collateral_available,
        Unit.COLLATERAL_ASSET,
        snapshot.collateral_observed_at,
    )


def _readiness_check(context: EvaluationContext) -> RiskCheck:
    snapshot, policy = context.snapshot, context.policy
    reconciliation = snapshot.reconciliation
    if (
        reconciliation.completeness is Completeness.COMPLETE
        and context.recon_age is not None
        and context.target_present
    ):
        if context.recon_age <= policy.max_reconciliation_age_seconds:
            ready_check = context.check(
                CheckKind.FRESHNESS_RECONCILIATION,
                CheckOutcome.PASS,
                "SATISFIED",
                context.recon_age,
                policy.max_reconciliation_age_seconds,
                Unit.SECONDS,
                reconciliation.observed_at,
            )
        else:
            ready_check = context.check(
                CheckKind.FRESHNESS_RECONCILIATION,
                CheckOutcome.UNKNOWN,
                "STALE_EVIDENCE",
                context.recon_age,
                policy.max_reconciliation_age_seconds,
                Unit.SECONDS,
                reconciliation.observed_at,
            )
    elif not context.target_present:
        ready_check = context.check(
            CheckKind.FRESHNESS_RECONCILIATION,
            CheckOutcome.UNKNOWN,
            "AMBIGUOUS_EVIDENCE",
        )
    elif reconciliation.completeness is Completeness.PARTIAL and context.recon_age is not None:
        ready_check = context.check(
            CheckKind.FRESHNESS_RECONCILIATION,
            CheckOutcome.FAIL,
            "RECONCILIATION_NOT_READY",
            context.recon_age,
            policy.max_reconciliation_age_seconds,
            Unit.SECONDS,
            reconciliation.observed_at,
        )
    else:
        ready_check = context.check(
            CheckKind.FRESHNESS_RECONCILIATION,
            CheckOutcome.UNKNOWN,
            incomplete_reason(reconciliation.completeness),
        )
    return ready_check


def _protection_and_reduction_checks(
    context: EvaluationContext,
) -> tuple[RiskCheck, RiskCheck]:
    intent, snapshot = context.intent, context.snapshot
    reconciliation = snapshot.reconciliation
    if intent.risk_effect is RiskEffect.REDUCE_ONLY:
        protection = context.check(
            CheckKind.PROTECTION,
            CheckOutcome.NOT_APPLICABLE,
            "NOT_APPLICABLE",
        )
    else:
        covered, required = protection_values(context)
        if context.recon_ready:
            if covered >= required:
                protection = context.check(
                    CheckKind.PROTECTION,
                    CheckOutcome.PASS,
                    "SATISFIED",
                    covered,
                    required,
                    Unit.CONTRACTS,
                    reconciliation.observed_at,
                )
            else:
                protection = context.check(
                    CheckKind.PROTECTION,
                    CheckOutcome.FAIL,
                    "PROTECTION_INADEQUATE",
                    covered,
                    required,
                    Unit.CONTRACTS,
                    reconciliation.observed_at,
                )
        elif context.recon_reason == "STALE_EVIDENCE":
            protection = context.check(
                CheckKind.PROTECTION,
                CheckOutcome.UNKNOWN,
                context.recon_reason,
                covered,
                required,
                Unit.CONTRACTS,
                reconciliation.observed_at,
            )
        else:
            protection = context.check(
                CheckKind.PROTECTION,
                CheckOutcome.UNKNOWN,
                context.recon_reason,
            )
    if intent.risk_effect is RiskEffect.RISK_INCREASE:
        reduction = context.check(
            CheckKind.REDUCE_ONLY_NO_REVERSAL,
            CheckOutcome.NOT_APPLICABLE,
            "NOT_APPLICABLE",
        )
        return protection, reduction
    signed = intent.contracts if intent.side is Side.BUY else -intent.contracts
    post = context.current_position + signed
    if not context.recon_ready:
        if context.recon_reason == "STALE_EVIDENCE":
            reduction = context.check(
                CheckKind.REDUCE_ONLY_NO_REVERSAL,
                CheckOutcome.UNKNOWN,
                context.recon_reason,
                post,
                context.current_position,
                Unit.CONTRACTS,
                reconciliation.observed_at,
            )
        else:
            reduction = context.check(
                CheckKind.REDUCE_ONLY_NO_REVERSAL,
                CheckOutcome.UNKNOWN,
                context.recon_reason,
            )
        return protection, reduction
    try:
        strict_reduction(context.current_position, intent.side, intent.contracts)
    except RiskInputError:
        reversal = (
            context.current_position != 0
            and post != 0
            and (context.current_position > 0) != (post > 0)
        )
        reduction = context.check(
            CheckKind.REDUCE_ONLY_NO_REVERSAL,
            CheckOutcome.FAIL,
            "POSITION_REVERSAL" if reversal else "LIMIT_EXCEEDED",
            post,
            context.current_position,
            Unit.CONTRACTS,
            reconciliation.observed_at,
        )
    else:
        reduction = context.check(
            CheckKind.REDUCE_ONLY_NO_REVERSAL,
            CheckOutcome.PASS,
            "SATISFIED",
            post,
            context.current_position,
            Unit.CONTRACTS,
            reconciliation.observed_at,
        )
    return protection, reduction


def control_checks(
    context: EvaluationContext,
) -> tuple[tuple[RiskCheck, ...], PriceSource]:
    """Compute canonical checks eight through sixteen."""
    open_check, rate_check = _open_and_rate_checks(context)
    price_check, price_source = _price_check(context)
    duplicate = _duplicate_check(context)
    collateral = _collateral_check(context)
    halt = halt_check(context)
    readiness = _readiness_check(context)
    protection, reduction = _protection_and_reduction_checks(context)
    return (
        (
            open_check,
            rate_check,
            price_check,
            duplicate,
            collateral,
            halt,
            readiness,
            protection,
            reduction,
        ),
        price_source,
    )
