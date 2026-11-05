"""Catalogue, exposure, leverage, loss, and accounting risk checks."""

from __future__ import annotations

from decimal import Decimal

from hmt_crypto.risk.context import EvaluationContext, age_seconds, incomplete_reason, limit_outcome
from hmt_crypto.risk.daily_loss import conservative_daily_loss, expected_window_start
from hmt_crypto.risk.evidence import canonical_digest
from hmt_crypto.risk.exposure import gross_exposure, worst_position_contracts
from hmt_crypto.risk.math import sum_amounts
from hmt_crypto.risk.models import (
    CatalogueStatus,
    CheckKind,
    CheckOutcome,
    Completeness,
    ExposureLeg,
    HaltCause,
    HaltDirective,
    MarketStatus,
    RiskCheck,
    RiskEffect,
    Side,
    Unit,
)


def _catalogue_checks(context: EvaluationContext) -> tuple[RiskCheck, RiskCheck]:
    policy, snapshot = context.policy, context.snapshot
    product, now = snapshot.product, snapshot.evaluated_at
    catalogue_age = age_seconds(now, product.catalogue_observed_at)
    if product.catalogue_status is CatalogueStatus.AMBIGUOUS:
        catalogue = context.check(
            CheckKind.PRODUCT_CATALOGUE, CheckOutcome.UNKNOWN, "AMBIGUOUS_EVIDENCE"
        )
    elif product.catalogue_status is CatalogueStatus.UNSUPPORTED:
        catalogue = context.check(
            CheckKind.PRODUCT_CATALOGUE,
            CheckOutcome.FAIL,
            "UNSUPPORTED_PRODUCT",
            evidence_at=product.catalogue_observed_at,
        )
    elif product.catalogue_status is CatalogueStatus.DISABLED:
        catalogue = context.check(
            CheckKind.PRODUCT_CATALOGUE,
            CheckOutcome.FAIL,
            "PRODUCT_DISABLED",
            evidence_at=product.catalogue_observed_at,
        )
    elif catalogue_age > policy.max_catalogue_age_seconds:
        catalogue = context.check(
            CheckKind.PRODUCT_CATALOGUE,
            CheckOutcome.UNKNOWN,
            "STALE_EVIDENCE",
            evidence_at=product.catalogue_observed_at,
        )
    else:
        catalogue = context.check(
            CheckKind.PRODUCT_CATALOGUE,
            CheckOutcome.PASS,
            "SATISFIED",
            evidence_at=product.catalogue_observed_at,
        )
    market_age = age_seconds(now, product.market_observed_at)
    if product.market_status is MarketStatus.AMBIGUOUS:
        market = context.check(
            CheckKind.MARKET_TRADABILITY,
            CheckOutcome.UNKNOWN,
            "AMBIGUOUS_EVIDENCE",
        )
    elif product.market_status is MarketStatus.CLOSED:
        market = context.check(
            CheckKind.MARKET_TRADABILITY,
            CheckOutcome.FAIL,
            "MARKET_NOT_TRADABLE",
            evidence_at=product.market_observed_at,
        )
    elif market_age > policy.max_catalogue_age_seconds:
        market = context.check(
            CheckKind.MARKET_TRADABILITY,
            CheckOutcome.UNKNOWN,
            "STALE_EVIDENCE",
            evidence_at=product.market_observed_at,
        )
    else:
        market = context.check(
            CheckKind.MARKET_TRADABILITY,
            CheckOutcome.PASS,
            "SATISFIED",
            evidence_at=product.market_observed_at,
        )
    return catalogue, market


def _dependent_limit(
    context: EvaluationContext,
    kind: CheckKind,
    observed: Decimal | int,
    limit: Decimal | int,
    unit: Unit,
) -> RiskCheck:
    reconciliation = context.snapshot.reconciliation
    if context.recon_ready:
        outcome, reason = limit_outcome(observed <= limit)
        return context.check(
            kind, outcome, reason, observed, limit, unit, reconciliation.observed_at
        )
    if context.recon_reason == "STALE_EVIDENCE":
        return context.check(
            kind,
            CheckOutcome.UNKNOWN,
            context.recon_reason,
            observed,
            limit,
            unit,
            reconciliation.observed_at,
        )
    return context.check(kind, CheckOutcome.UNKNOWN, context.recon_reason)


def _position_and_trade_checks(context: EvaluationContext) -> tuple[RiskCheck, ...]:
    intent, policy, snapshot = context.intent, context.policy, context.snapshot
    if intent.risk_effect is RiskEffect.REDUCE_ONLY:
        return tuple(
            context.check(kind, CheckOutcome.NOT_APPLICABLE, "NOT_APPLICABLE")
            for kind in (
                CheckKind.MAX_POSITION,
                CheckKind.TOTAL_EXPOSURE,
                CheckKind.MAX_LEVERAGE,
                CheckKind.MAX_LOSS_PER_TRADE,
            )
        )
    open_legs = tuple(
        ExposureLeg(
            item.side,
            item.remaining_contracts,
            item.notional_per_contract,
            item.reduce_only,
        )
        for item in context.open_orders
    )
    reservation_legs = tuple(
        ExposureLeg(
            item.side,
            item.contracts,
            item.notional_per_contract,
            item.risk_effect is RiskEffect.REDUCE_ONLY,
        )
        for item in snapshot.reservations
    )
    candidate_leg = ExposureLeg(intent.side, intent.contracts, context.candidate_per_contract)
    target_pairs = tuple(zip(open_legs, context.open_orders, strict=True)) + tuple(
        zip(reservation_legs, snapshot.reservations, strict=True)
    )
    target_legs = tuple(
        leg for leg, source in target_pairs if source.product_id == intent.product_id
    ) + (candidate_leg,)
    worst_position = worst_position_contracts(context.current_position, target_legs)
    position_check = _dependent_limit(
        context,
        CheckKind.MAX_POSITION,
        worst_position,
        policy.max_position_contracts,
        Unit.CONTRACTS,
    )
    positions = tuple(
        ExposureLeg(
            Side.BUY if item.signed_contracts > 0 else Side.SELL,
            abs(item.signed_contracts),
            item.notional_per_contract,
        )
        for item in snapshot.positions
    )
    total = gross_exposure(positions, (*open_legs, *reservation_legs, candidate_leg))
    exposure_check = _dependent_limit(
        context,
        CheckKind.TOTAL_EXPOSURE,
        total,
        policy.max_total_exposure,
        Unit.REFERENCE_ASSET,
    )
    leverage_facts = (
        tuple((item.product_id, item.effective_leverage) for item in snapshot.positions)
        + tuple(
            (item.product_id, item.configured_leverage)
            for item in context.open_orders
            if not item.reduce_only and item.configured_leverage is not None
        )
        + tuple(
            (item.product_id, item.configured_leverage)
            for item in snapshot.reservations
            if item.risk_effect is RiskEffect.RISK_INCREASE and item.configured_leverage is not None
        )
        + ((intent.product_id, intent.leverage),)
    )
    global_leverage = max(value for _, value in leverage_facts)
    product_leverage = max(
        value for product_id, value in leverage_facts if product_id == intent.product_id
    )
    product_limit = min(policy.max_leverage, snapshot.product.max_leverage)
    if global_leverage > policy.max_leverage:
        leverage_observed = global_leverage
        leverage_limit = policy.max_leverage
    elif product_leverage > product_limit:
        leverage_observed = product_leverage
        leverage_limit = product_limit
    else:
        leverage_observed = global_leverage
        leverage_limit = policy.max_leverage
    leverage_check = _dependent_limit(
        context,
        CheckKind.MAX_LEVERAGE,
        leverage_observed,
        leverage_limit,
        Unit.LEVERAGE,
    )
    if context.candidate_loss is None:
        loss_check = context.check(
            CheckKind.MAX_LOSS_PER_TRADE,
            CheckOutcome.UNKNOWN,
            "MISSING_EVIDENCE",
        )
    else:
        outcome, reason = limit_outcome(context.candidate_loss <= policy.max_loss_per_trade)
        loss_check = context.check(
            CheckKind.MAX_LOSS_PER_TRADE,
            outcome,
            reason,
            context.candidate_loss,
            policy.max_loss_per_trade,
            Unit.REFERENCE_ASSET,
            snapshot.evaluated_at,
        )
    return position_check, exposure_check, leverage_check, loss_check


def _daily_check(context: EvaluationContext) -> tuple[RiskCheck, HaltDirective | None]:
    intent, policy, snapshot = context.intent, context.policy, context.snapshot
    accounting, now = snapshot.accounting, snapshot.evaluated_at
    accounting_age = (
        None if accounting.observed_at is None else age_seconds(now, accounting.observed_at)
    )
    window_proven = accounting.window_start_at == expected_window_start(
        now, policy.daily_reset_second_utc
    )
    proof_complete = accounting.completeness is Completeness.COMPLETE and window_proven
    accounting_ready = (
        proof_complete
        and accounting_age is not None
        and accounting_age <= policy.max_accounting_age_seconds
    )
    actual: Decimal | None = None
    projected: Decimal | None = None
    if proof_complete:
        actual = conservative_daily_loss(accounting)
        unaccounted = tuple(
            item.worst_case_loss
            for item in snapshot.reservations
            if item.reservation_key not in accounting.included_reservation_keys
        )
        if intent.risk_effect is RiskEffect.RISK_INCREASE and context.candidate_loss is None:
            projected = None
        else:
            candidate = (
                (context.candidate_loss,)
                if context.candidate_loss is not None
                and intent.risk_effect is RiskEffect.RISK_INCREASE
                else ()
            )
            projected = sum_amounts((actual, *unaccounted, *candidate))
    directive: HaltDirective | None = None
    if accounting_ready and actual is not None and actual > policy.max_daily_loss:
        evidence = canonical_digest(
            {
                "kind": "halt_directive_v1",
                "cause": HaltCause.DAILY_LOSS,
                "observed": actual,
                "limit": policy.max_daily_loss,
                "context": context.context_sha256,
            }
        )
        directive = HaltDirective(HaltCause.DAILY_LOSS, now, evidence)
    elif not accounting_ready:
        evidence = canonical_digest(
            {
                "kind": "halt_directive_v1",
                "cause": HaltCause.ACCOUNTING_UNKNOWN,
                "context": context.context_sha256,
            }
        )
        directive = HaltDirective(HaltCause.ACCOUNTING_UNKNOWN, now, evidence)
    if intent.risk_effect is RiskEffect.REDUCE_ONLY:
        return (
            context.check(
                CheckKind.MAX_DAILY_LOSS,
                CheckOutcome.NOT_APPLICABLE,
                "NOT_APPLICABLE",
            ),
            directive,
        )
    if not accounting_ready:
        if proof_complete and projected is not None:
            return (
                context.check(
                    CheckKind.MAX_DAILY_LOSS,
                    CheckOutcome.UNKNOWN,
                    "STALE_EVIDENCE",
                    projected,
                    policy.max_daily_loss,
                    Unit.REFERENCE_ASSET,
                    accounting.observed_at,
                ),
                directive,
            )
        reason = (
            "AMBIGUOUS_EVIDENCE"
            if accounting.completeness is Completeness.COMPLETE
            else incomplete_reason(accounting.completeness)
        )
        return (
            context.check(CheckKind.MAX_DAILY_LOSS, CheckOutcome.UNKNOWN, reason),
            directive,
        )
    if projected is None:
        return (
            context.check(
                CheckKind.MAX_DAILY_LOSS,
                CheckOutcome.UNKNOWN,
                "MISSING_EVIDENCE",
            ),
            directive,
        )
    outcome, reason = limit_outcome(projected <= policy.max_daily_loss)
    return (
        context.check(
            CheckKind.MAX_DAILY_LOSS,
            outcome,
            reason,
            projected,
            policy.max_daily_loss,
            Unit.REFERENCE_ASSET,
            accounting.observed_at,
        ),
        directive,
    )


def limit_checks(
    context: EvaluationContext,
) -> tuple[tuple[RiskCheck, ...], HaltDirective | None]:
    """Compute canonical checks one through seven."""
    catalogue, market = _catalogue_checks(context)
    trade_checks = _position_and_trade_checks(context)
    daily, directive = _daily_check(context)
    return (catalogue, market, *trade_checks, daily), directive


__all__ = ["limit_checks"]
