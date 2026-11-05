"""Pure durable halt proof and freshness check."""

from hmt_crypto.risk.context import EvaluationContext, age_seconds, incomplete_reason
from hmt_crypto.risk.models import (
    CheckKind,
    CheckOutcome,
    Completeness,
    RiskCheck,
    RiskEffect,
    Unit,
)


def halt_check(context: EvaluationContext) -> RiskCheck:
    """Fail closed unless a fresh authoritative halt-state read is proven."""
    intent, policy, snapshot = context.intent, context.policy, context.snapshot
    halt = snapshot.halt
    if halt.completeness is not Completeness.COMPLETE:
        return context.check(
            CheckKind.KILL_HALT,
            CheckOutcome.UNKNOWN,
            incomplete_reason(halt.completeness),
        )
    if halt.read_at is None:
        return context.check(
            CheckKind.KILL_HALT,
            CheckOutcome.UNKNOWN,
            "MISSING_EVIDENCE",
        )
    age = age_seconds(snapshot.evaluated_at, halt.read_at)
    if age > policy.max_halt_age_seconds:
        return context.check(
            CheckKind.KILL_HALT,
            CheckOutcome.UNKNOWN,
            "STALE_EVIDENCE",
            age,
            policy.max_halt_age_seconds,
            Unit.SECONDS,
            halt.read_at,
        )
    if halt.active_causes and intent.risk_effect is RiskEffect.RISK_INCREASE:
        return context.check(
            CheckKind.KILL_HALT,
            CheckOutcome.FAIL,
            "HALT_ACTIVE",
            age,
            policy.max_halt_age_seconds,
            Unit.SECONDS,
            halt.read_at,
        )
    return context.check(
        CheckKind.KILL_HALT,
        CheckOutcome.PASS,
        "SATISFIED",
        age,
        policy.max_halt_age_seconds,
        Unit.SECONDS,
        halt.read_at,
    )


__all__ = ["halt_check"]
