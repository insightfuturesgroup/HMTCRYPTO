"""Event-semantic, prior-state-bound global kill-switch transitions."""

from __future__ import annotations

from typing import Protocol

from hmt_crypto.oms._kill_switch_types import (
    MAX_I63,
    KillAction,
    KillEvent,
    KillMode,
    KillState,
    KillSwitchError,
)
from hmt_crypto.oms._kill_switch_values import (
    KillEventEvidence,
    KillRequest,
    KillTransitionDecision,
    _DecisionState,
    _evidence_state,
    _issue_decision,
    _issue_request,
    _request_state,
    _RequestState,
    record_kill_event,
    rehydrate_kill_request,
)


class KillTransitionRepository(Protocol):
    """Atomically match ID/mode/state/version, insert event, and update state."""

    async def compare_and_swap(self, decision: KillTransitionDecision) -> bool: ...


_COMMON_TRANSITIONS = {
    (KillState.CANCELLING_RISK_INCREASING, KillEvent.RISK_INCREASING_CANCEL_UNRESOLVED): (
        KillState.INCOMPLETE
    ),
    (KillState.FLATTENING, KillEvent.FLATTEN_COMPLETED): KillState.CANCELLING_REMAINDER,
    (KillState.FLATTENING, KillEvent.FLATTEN_UNRESOLVED): KillState.INCOMPLETE,
    (KillState.CANCELLING_REMAINDER, KillEvent.REMAINDER_CANCEL_COMPLETED): (KillState.RECONCILING),
    (KillState.CANCELLING_REMAINDER, KillEvent.REMAINDER_CANCEL_UNRESOLVED): (KillState.INCOMPLETE),
    (KillState.RECONCILING, KillEvent.RECONCILED_FLAT): KillState.FINAL_RECONCILING,
    (KillState.RECONCILING, KillEvent.RECONCILIATION_UNRESOLVED): KillState.INCOMPLETE,
    (KillState.PROTECTION_REESTABLISHING, KillEvent.PROTECTION_SUBMITTED): (
        KillState.PROTECTION_VERIFYING
    ),
    (KillState.PROTECTION_REESTABLISHING, KillEvent.PROTECTION_UNRESOLVED): (KillState.INCOMPLETE),
    (KillState.PROTECTION_VERIFYING, KillEvent.PROTECTION_VERIFIED): (KillState.FINAL_RECONCILING),
    (KillState.PROTECTION_VERIFYING, KillEvent.PROTECTION_UNVERIFIED): KillState.INCOMPLETE,
    (KillState.FINAL_RECONCILING, KillEvent.FINAL_RECONCILED_SAFE): KillState.COMPLETED,
    (KillState.FINAL_RECONCILING, KillEvent.FINAL_RECONCILIATION_UNRESOLVED): (
        KillState.INCOMPLETE
    ),
}


def _transition_target(current: _RequestState, event: KillEvent) -> KillState | None:
    if current.state is KillState.REQUESTED and event is KillEvent.RISK_HALT_PERSISTED:
        return KillState.CANCELLING_RISK_INCREASING
    if (
        current.state is KillState.CANCELLING_RISK_INCREASING
        and event is KillEvent.RISK_INCREASING_CANCEL_COMPLETED
    ):
        return (
            KillState.FLATTENING
            if current.mode is KillMode.CANCEL_AND_FLATTEN
            else KillState.CANCELLING_REMAINDER
        )
    if (
        current.state is KillState.RECONCILING
        and event is KillEvent.RECONCILED_RETAINED
        and current.mode is KillMode.CANCEL_ONLY
    ):
        return KillState.PROTECTION_REESTABLISHING
    return _COMMON_TRANSITIONS.get((current.state, event))


def apply_kill_event(
    current: KillRequest,
    evidence: KillEventEvidence,
) -> KillTransitionDecision:
    """Plan one event-semantic, prior-state-bound repository CAS."""
    retained = _request_state(current)
    observed = _evidence_state(evidence)
    if (
        observed.request_id != retained.request_id
        or observed.expected_transition_count != retained.transition_count
        or retained.transition_count >= MAX_I63 - 1
    ):
        raise KillSwitchError() from None
    target = _transition_target(retained, observed.event)
    if target is None:
        raise KillSwitchError() from None
    next_request = _issue_request(
        _RequestState(
            retained.request_id,
            retained.mode,
            target,
            retained.transition_count + 1,
        )
    )
    return _issue_decision(
        _DecisionState(
            retained.request_id,
            retained.mode,
            retained.state,
            retained.transition_count,
            observed.event_id,
            observed.event,
            next_request,
        )
    )


_ACTIONS = {
    KillState.REQUESTED: KillAction.PERSIST_RISK_HALT,
    KillState.CANCELLING_RISK_INCREASING: KillAction.CANCEL_RISK_INCREASING_ORDERS,
    KillState.FLATTENING: KillAction.FLATTEN_POSITIONS_REDUCE_ONLY,
    KillState.CANCELLING_REMAINDER: KillAction.CANCEL_REMAINING_OPEN_ORDERS,
    KillState.RECONCILING: KillAction.RECONCILE_EXCHANGE_TRUTH,
    KillState.PROTECTION_REESTABLISHING: KillAction.REESTABLISH_PROTECTION,
    KillState.PROTECTION_VERIFYING: KillAction.VERIFY_PROTECTION,
    KillState.FINAL_RECONCILING: KillAction.FINAL_RECONCILIATION,
    KillState.COMPLETED: KillAction.NONE,
    KillState.INCOMPLETE: KillAction.NONE,
}


def next_kill_action(current: KillRequest) -> KillAction:
    """Return the sole idempotent resume action for a committed state."""
    return _ACTIONS[_request_state(current).state]


__all__ = [
    "KillAction",
    "KillEvent",
    "KillEventEvidence",
    "KillMode",
    "KillRequest",
    "KillState",
    "KillSwitchError",
    "KillTransitionDecision",
    "KillTransitionRepository",
    "apply_kill_event",
    "next_kill_action",
    "record_kill_event",
    "rehydrate_kill_request",
]
