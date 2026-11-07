"""Authority-free orchestration for the mandatory risk check set."""

from __future__ import annotations

from decimal import Decimal

from hmt_crypto.risk.context import EvaluationContext, build_context
from hmt_crypto.risk.control_checks import control_checks
from hmt_crypto.risk.evidence import canonical_digest
from hmt_crypto.risk.inputs import RiskIntent, RiskPolicy, RiskSnapshot
from hmt_crypto.risk.limit_checks import limit_checks
from hmt_crypto.risk.models import (
    CheckKind,
    CheckOutcome,
    DecisionOutcome,
    ReservationProposal,
    RiskEffect,
    RiskEvaluation,
    RiskInputError,
)
from hmt_crypto.risk.ownership import own_risk_inputs


class RiskManager:
    """Compute evidence only; holds no clock, repository, exchange, or config capability."""

    def evaluate(
        self, policy: RiskPolicy, intent: RiskIntent, snapshot: RiskSnapshot
    ) -> RiskEvaluation:
        """Return a deterministic fail-closed evaluation and capacity proposal."""
        policy, intent, snapshot = own_risk_inputs(policy, intent, snapshot)
        context = build_context(policy, intent, snapshot)
        first_checks, halt_directive = limit_checks(context)
        remaining_checks, price_source = control_checks(context)
        checks = (*first_checks, *remaining_checks)
        if tuple(check.kind for check in checks) != tuple(CheckKind):
            raise RiskInputError() from None
        approved = all(
            check.outcome in {CheckOutcome.PASS, CheckOutcome.NOT_APPLICABLE} for check in checks
        )
        decision_outcome = DecisionOutcome.APPROVED if approved else DecisionOutcome.DENIED
        reservation = self._reservation(context) if approved else None
        check_set_sha = canonical_digest(
            {
                "kind": "risk_check_set_v1",
                "checks": tuple(check.evidence_sha256 for check in checks),
            }
        )
        decision_values = {
            "outcome": decision_outcome,
            "risk_effect": intent.risk_effect,
            "check_set_sha256": check_set_sha,
            "reservation": reservation,
            "halt_directive": halt_directive,
            "price_source": price_source,
            "intent_key_sha256": context.intent_key_sha256,
        }
        decision_sha = canonical_digest({"kind": "risk_decision_v1", "payload": decision_values})
        return RiskEvaluation(
            outcome=decision_outcome,
            risk_effect=intent.risk_effect,
            checks=checks,
            reservation=reservation,
            halt_directive=halt_directive,
            price_source=price_source,
            intent_key_sha256=context.intent_key_sha256,
            check_set_sha256=check_set_sha,
            decision_sha256=decision_sha,
        )

    @staticmethod
    def _reservation(context: EvaluationContext) -> ReservationProposal:
        intent, policy = context.intent, context.policy
        zero = Decimal("0")
        if intent.risk_effect is RiskEffect.RISK_INCREASE:
            if context.candidate_loss is None:
                raise RiskInputError() from None
            exposure = context.candidate_exposure
            loss = context.candidate_loss
            collateral = context.candidate_collateral
        else:
            exposure = loss = collateral = zero
        configured_leverage = (
            intent.leverage if intent.risk_effect is RiskEffect.RISK_INCREASE else None
        )
        plan_values = {
            "product_id": intent.product_id,
            "risk_effect": intent.risk_effect,
            "intent_key_sha256": context.intent_key_sha256,
            "input_context_sha256": context.context_sha256,
            "policy_version": policy.version,
            "policy_sha256": policy.policy_sha256,
            "configured_leverage": configured_leverage,
            "approved_contracts": intent.contracts,
            "approved_side": intent.side,
            "reference_asset_id": policy.reference_asset.asset_id,
            "reference_asset_symbol": policy.reference_asset.symbol,
            "collateral_asset_id": policy.collateral_asset.asset_id,
            "collateral_asset_symbol": policy.collateral_asset.symbol,
            "reserved_incremental_gross_exposure": exposure,
            "reserved_worst_case_loss": loss,
            "reserved_collateral": collateral,
            "reserved_open_order_slots": context.candidate_slots,
            "reserved_pending_rate_units": 1,
            "rate_policy_version": policy.rate_policy_version,
            "rate_policy_sha256": policy.rate_policy_sha256,
            "rate_window_start_at": context.rate_start,
            "rate_window_end_at": context.rate_end,
        }
        plan_sha = canonical_digest({"kind": "risk_reservation_plan_v1", "payload": plan_values})
        return ReservationProposal(
            product_id=intent.product_id,
            risk_effect=intent.risk_effect,
            intent_key_sha256=context.intent_key_sha256,
            input_context_sha256=context.context_sha256,
            policy_version=policy.version,
            policy_sha256=policy.policy_sha256,
            configured_leverage=configured_leverage,
            approved_contracts=intent.contracts,
            approved_side=intent.side,
            reference_asset_id=policy.reference_asset.asset_id,
            reference_asset_symbol=policy.reference_asset.symbol,
            collateral_asset_id=policy.collateral_asset.asset_id,
            collateral_asset_symbol=policy.collateral_asset.symbol,
            reserved_incremental_gross_exposure=exposure,
            reserved_worst_case_loss=loss,
            reserved_collateral=collateral,
            reserved_open_order_slots=context.candidate_slots,
            reserved_pending_rate_units=1,
            rate_policy_version=policy.rate_policy_version,
            rate_policy_sha256=policy.rate_policy_sha256,
            rate_window_start_at=context.rate_start,
            rate_window_end_at=context.rate_end,
            reservation_plan_sha256=plan_sha,
        )


__all__ = ["RiskManager"]
