"""Pure lease-fenced state rules for Live order submission recovery."""

from __future__ import annotations

from base64 import b32encode
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID


class LiveSubmissionTransitionError(Exception):
    """Safe rejection for an invalid or stale submission transition."""


_ALLOWED: dict[str, frozenset[str]] = {
    "PENDING": frozenset({"CLAIMED"}),
    "CLAIMED": frozenset({"PREPARED", "PENDING", "BLOCKED"}),
    "PREPARED": frozenset({"SENDING", "BLOCKED"}),
    "SENDING": frozenset({"ACCEPTED", "REJECTED", "UNCERTAIN"}),
    "UNCERTAIN": frozenset({"ACCEPTED", "REJECTED", "BLOCKED"}),
    "ACCEPTED": frozenset(),
    "REJECTED": frozenset(),
    "BLOCKED": frozenset(),
}


def make_live_client_order_id(account_id: UUID, intent_id: UUID) -> str:
    """Derive one stable venue lookup key without retaining user input."""
    if type(account_id) is not UUID or type(intent_id) is not UUID:
        raise LiveSubmissionTransitionError from None
    digest = sha256(b"hmt-live-submission-v1" + account_id.bytes + intent_id.bytes).digest()
    value = "HML1_" + b32encode(digest).decode("ascii")[:27]
    if len(value) != 32 or not value.isascii():
        raise LiveSubmissionTransitionError from None
    return value


@dataclass(frozen=True, slots=True)
class SubmissionFence:
    owner_id: UUID
    generation: int
    credential_version: int

    def __post_init__(self) -> None:
        if (
            type(self.owner_id) is not UUID
            or self.owner_id.int == 0
            or type(self.generation) is not int
            or self.generation < 1
            or type(self.credential_version) is not int
            or self.credential_version < 1
        ):
            raise LiveSubmissionTransitionError from None


def require_transition(
    *, current: str, target: str, stored_fence: SubmissionFence, actor_fence: SubmissionFence
) -> None:
    """Reject stale workers and every transition outside the reviewed graph."""
    if (
        type(current) is not str
        or type(target) is not str
        or type(stored_fence) is not SubmissionFence
        or type(actor_fence) is not SubmissionFence
        or stored_fence != actor_fence
        or current not in _ALLOWED
        or target not in _ALLOWED[current]
    ):
        raise LiveSubmissionTransitionError from None


__all__ = [
    "LiveSubmissionTransitionError",
    "SubmissionFence",
    "make_live_client_order_id",
    "require_transition",
]
