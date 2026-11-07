"""Closed kill-switch vocabulary and reachable workflow versions."""

from __future__ import annotations

from enum import StrEnum

MAX_I63 = 2**63 - 1
FLATTEN_CONFIRMATION = "FLATTEN_ALL_POSITIONS"


class KillSwitchError(Exception):
    """A kill request, evidence item, or transition violated the workflow."""

    code = "kill_switch_transition"
    message = "Kill-switch transition is invalid"

    def __init__(self) -> None:
        super().__init__(f"{self.code}: {self.message}")

    def __repr__(self) -> str:
        return "KillSwitchError(code='kill_switch_transition')"


class KillMode(StrEnum):
    """Operator-selected scope fixed for the lifetime of one request."""

    CANCEL_ONLY = "CANCEL_ONLY"
    CANCEL_AND_FLATTEN = "CANCEL_AND_FLATTEN"


class KillState(StrEnum):
    """Durable workflow states; terminal states never imply hidden retries."""

    REQUESTED = "REQUESTED"
    CANCELLING_RISK_INCREASING = "CANCELLING_RISK_INCREASING"
    FLATTENING = "FLATTENING"
    CANCELLING_REMAINDER = "CANCELLING_REMAINDER"
    RECONCILING = "RECONCILING"
    PROTECTION_REESTABLISHING = "PROTECTION_REESTABLISHING"
    PROTECTION_VERIFYING = "PROTECTION_VERIFYING"
    FINAL_RECONCILING = "FINAL_RECONCILING"
    COMPLETED = "COMPLETED"
    INCOMPLETE = "INCOMPLETE"


class KillEvent(StrEnum):
    """Persisted, classified evidence that may advance the workflow."""

    RISK_HALT_PERSISTED = "RISK_HALT_PERSISTED"
    RISK_INCREASING_CANCEL_COMPLETED = "RISK_INCREASING_CANCEL_COMPLETED"
    RISK_INCREASING_CANCEL_UNRESOLVED = "RISK_INCREASING_CANCEL_UNRESOLVED"
    FLATTEN_COMPLETED = "FLATTEN_COMPLETED"
    FLATTEN_UNRESOLVED = "FLATTEN_UNRESOLVED"
    REMAINDER_CANCEL_COMPLETED = "REMAINDER_CANCEL_COMPLETED"
    REMAINDER_CANCEL_UNRESOLVED = "REMAINDER_CANCEL_UNRESOLVED"
    RECONCILED_FLAT = "RECONCILED_FLAT"
    RECONCILED_RETAINED = "RECONCILED_RETAINED"
    RECONCILIATION_UNRESOLVED = "RECONCILIATION_UNRESOLVED"
    PROTECTION_SUBMITTED = "PROTECTION_SUBMITTED"
    PROTECTION_UNRESOLVED = "PROTECTION_UNRESOLVED"
    PROTECTION_VERIFIED = "PROTECTION_VERIFIED"
    PROTECTION_UNVERIFIED = "PROTECTION_UNVERIFIED"
    FINAL_RECONCILED_SAFE = "FINAL_RECONCILED_SAFE"
    FINAL_RECONCILIATION_UNRESOLVED = "FINAL_RECONCILIATION_UNRESOLVED"


class KillAction(StrEnum):
    """Exactly one idempotent side effect required by a persisted state."""

    PERSIST_RISK_HALT = "PERSIST_RISK_HALT"
    CANCEL_RISK_INCREASING_ORDERS = "CANCEL_RISK_INCREASING_ORDERS"
    FLATTEN_POSITIONS_REDUCE_ONLY = "FLATTEN_POSITIONS_REDUCE_ONLY"
    CANCEL_REMAINING_OPEN_ORDERS = "CANCEL_REMAINING_OPEN_ORDERS"
    RECONCILE_EXCHANGE_TRUTH = "RECONCILE_EXCHANGE_TRUTH"
    REESTABLISH_PROTECTION = "REESTABLISH_PROTECTION"
    VERIFY_PROTECTION = "VERIFY_PROTECTION"
    FINAL_RECONCILIATION = "FINAL_RECONCILIATION"
    NONE = "NONE"


MODES = tuple(KillMode)
STATES = tuple(KillState)
EVENTS = tuple(KillEvent)
TERMINAL_STATES = frozenset((KillState.COMPLETED, KillState.INCOMPLETE))

REACHABLE: dict[KillMode, dict[KillState, frozenset[int]]] = {
    KillMode.CANCEL_ONLY: {
        KillState.REQUESTED: frozenset((0,)),
        KillState.CANCELLING_RISK_INCREASING: frozenset((1,)),
        KillState.CANCELLING_REMAINDER: frozenset((2,)),
        KillState.RECONCILING: frozenset((3,)),
        KillState.PROTECTION_REESTABLISHING: frozenset((4,)),
        KillState.PROTECTION_VERIFYING: frozenset((5,)),
        KillState.FINAL_RECONCILING: frozenset((4, 6)),
        KillState.COMPLETED: frozenset((5, 7)),
        KillState.INCOMPLETE: frozenset((2, 3, 4, 5, 6, 7)),
    },
    KillMode.CANCEL_AND_FLATTEN: {
        KillState.REQUESTED: frozenset((0,)),
        KillState.CANCELLING_RISK_INCREASING: frozenset((1,)),
        KillState.FLATTENING: frozenset((2,)),
        KillState.CANCELLING_REMAINDER: frozenset((3,)),
        KillState.RECONCILING: frozenset((4,)),
        KillState.FINAL_RECONCILING: frozenset((5,)),
        KillState.COMPLETED: frozenset((6,)),
        KillState.INCOMPLETE: frozenset((2, 3, 4, 5, 6)),
    },
}
