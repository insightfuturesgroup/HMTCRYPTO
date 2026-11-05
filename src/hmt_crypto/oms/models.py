"""Closed, non-authorizing OMS state and decision contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast
from uuid import UUID

_MAX_U128 = 2**128 - 1


class _UuidIntDescriptor(Protocol):
    def __get__(self, instance: UUID, owner: type[UUID]) -> object: ...


_UUID_INT_DESCRIPTOR = cast(_UuidIntDescriptor, vars(UUID)["int"])


class OmsTransitionError(Exception):
    """A requested local OMS transition violates the closed state graph."""

    code = "oms_transition"
    message = "OMS state transition is invalid"

    def __init__(self) -> None:
        super().__init__(f"{self.code}: {self.message}")

    def __repr__(self) -> str:
        return "OmsTransitionError(code='oms_transition')"


class OmsExecutionMode(StrEnum):
    """Execution modes copied into the authority-free OMS domain."""

    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE = "LIVE"


class OmsRegion(StrEnum):
    """Venue account regions copied into durable OMS identity."""

    INDIA = "india"
    GLOBAL = "global"


class OmsEnvironment(StrEnum):
    """Venue environments copied into durable OMS identity."""

    TESTNET = "testnet"
    PROD = "prod"


_OMS_EXECUTION_MODES = tuple(OmsExecutionMode)
_OMS_REGIONS = tuple(OmsRegion)
_OMS_ENVIRONMENTS = tuple(OmsEnvironment)


def _is_canonical(value: object, members: tuple[object, ...]) -> bool:
    return any(value is member for member in members)


def _copy_exact_uuid(value: object) -> UUID:
    """Return a trusted copy after reading only the captured UUID integer slot."""
    if type(value) is not UUID:
        raise OmsTransitionError() from None
    try:
        integer = _UUID_INT_DESCRIPTOR.__get__(value, UUID)
    except Exception:
        raise OmsTransitionError() from None
    if type(integer) is not int or not 0 <= integer <= _MAX_U128:
        raise OmsTransitionError() from None
    return UUID(int=integer)


@dataclass(frozen=True, slots=True)
class OmsAccountScope:
    """Exact durable scope whose account UUID is bound immutably by the coordinator."""

    account_id: UUID
    external_user_id: str
    region: OmsRegion
    environment: OmsEnvironment
    mode: OmsExecutionMode
    credential_version: int

    def __post_init__(self) -> None:
        account_id = _copy_exact_uuid(self.account_id)
        if (
            type(self.external_user_id) is not str
            or not 1 <= len(self.external_user_id) <= 19
            or self.external_user_id[0] == "0"
            or not self.external_user_id.isascii()
            or not self.external_user_id.isdigit()
            or int(self.external_user_id) >= 2**63
            or not _is_canonical(self.region, _OMS_REGIONS)
            or not _is_canonical(self.environment, _OMS_ENVIRONMENTS)
            or not _is_canonical(self.mode, _OMS_EXECUTION_MODES)
            or type(self.credential_version) is not int
            or not 1 <= self.credential_version < 2**63
        ):
            raise OmsTransitionError() from None
        object.__setattr__(self, "account_id", account_id)


class CommandState(StrEnum):
    """Durable logical command states independent of exchange order state."""

    QUEUED = "QUEUED"
    CLAIMED = "CLAIMED"
    SUBMITTING = "SUBMITTING"
    UNCERTAIN = "UNCERTAIN"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @classmethod
    def terminal_states(cls) -> frozenset[CommandState]:
        """Return the closed terminal command-state set."""
        return frozenset((cls.COMPLETED, cls.FAILED, cls.CANCELLED))


class CommandEvent(StrEnum):
    """Classified evidence that may advance one command."""

    CLAIM = "CLAIM"
    RELEASE_UNSTARTED_CLAIM = "RELEASE_UNSTARTED_CLAIM"
    CANCEL_BEFORE_DISPATCH = "CANCEL_BEFORE_DISPATCH"
    BEGIN_SUBMISSION = "BEGIN_SUBMISSION"
    DEFINITE_ACCEPTED = "DEFINITE_ACCEPTED"
    DEFINITE_REJECTED = "DEFINITE_REJECTED"
    DEFINITE_NOT_SENT = "DEFINITE_NOT_SENT"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    RECONCILED_ACCEPTED = "RECONCILED_ACCEPTED"
    RECONCILED_REJECTED = "RECONCILED_REJECTED"
    RECONCILED_NOT_SENT = "RECONCILED_NOT_SENT"


class OrderState(StrEnum):
    """Exchange-truth order projection states."""

    UNKNOWN = "UNKNOWN"
    PREPARED = "PREPARED"
    SUBMITTING = "SUBMITTING"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        """Whether the exchange-truth category cannot change."""
        return any(self is terminal for terminal in _ORDER_STATE_TERMINALS)


_ORDER_STATE_TERMINALS = (
    OrderState.FILLED,
    OrderState.CANCELLED,
    OrderState.REJECTED,
    OrderState.EXPIRED,
)


class OrderOrigin(StrEnum):
    """Provenance of the order projection."""

    LOCAL_INTENT = "LOCAL_INTENT"
    SIMULATOR_INTENT = "SIMULATOR_INTENT"
    ADOPTED = "ADOPTED"


class OrderTransitionSource(StrEnum):
    """Authority category for one order observation."""

    LOCAL = "LOCAL"
    VENUE = "VENUE"
    SIMULATOR = "SIMULATOR"
    RECONCILIATION = "RECONCILIATION"


@dataclass(frozen=True, slots=True)
class CommandTransitionDecision:
    """Pure classified result; it grants no dispatch or persistence authority."""

    previous_state: CommandState
    event: CommandEvent
    next_state: CommandState
    attempt_count: int
    is_terminal: bool
    is_uncertain: bool


@dataclass(frozen=True, slots=True)
class OrderTransitionDecision:
    """Pure order-projection result plus a protection-work obligation."""

    previous_state: OrderState
    next_state: OrderState
    source: OrderTransitionSource
    is_terminal: bool
    requires_protection_reconciliation: bool


__all__ = [
    "CommandEvent",
    "CommandState",
    "CommandTransitionDecision",
    "OmsAccountScope",
    "OmsEnvironment",
    "OmsExecutionMode",
    "OmsRegion",
    "OmsTransitionError",
    "OrderOrigin",
    "OrderState",
    "OrderTransitionDecision",
    "OrderTransitionSource",
]
