"""Canonical copies of actor-to-supervisor stream evidence."""

from __future__ import annotations

from uuid import UUID

from hmt_crypto.exchange.delta.websocket_contracts import WsPlane
from hmt_crypto.exchange.delta.websocket_subscriptions import (
    GenerationControlInvalidation,
    GenerationSubscriptionReceipt,
    StreamInvalidationKind,
)


def _sha256_hex(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def canonical_invalidation(
    value: object,
    *,
    plane: WsPlane,
    generation: UUID,
) -> GenerationControlInvalidation | None:
    """Copy one exact, current-generation invalidation or return ``None``."""
    try:
        if type(value) is not GenerationControlInvalidation:
            return None
        if (
            value.plane is not plane
            or type(value.connection_generation) is not UUID
            or value.connection_generation != generation
            or type(value.local_sequence) is not int
            or value.local_sequence <= 0
            or type(value.kind) is not StreamInvalidationKind
            or not _sha256_hex(value.evidence_sha256)
        ):
            return None
        return GenerationControlInvalidation(
            plane=plane,
            connection_generation=generation,
            local_sequence=value.local_sequence,
            kind=value.kind,
            evidence_sha256=value.evidence_sha256,
        )
    except BaseException:
        return None


def canonical_subscription_receipt(
    value: object,
    *,
    plane: WsPlane,
    generation: UUID,
) -> GenerationSubscriptionReceipt | None:
    """Copy one exact, current-generation receipt or return ``None``."""
    try:
        if type(value) is not GenerationSubscriptionReceipt:
            return None
        if (
            value.plane is not plane
            or type(value.connection_generation) is not UUID
            or value.connection_generation != generation
            or type(value.control_revision) is not int
            or value.control_revision <= 0
            or type(value.desired_revision) is not int
            or value.desired_revision < 0
            or type(value.accepted_at_monotonic_ns) is not int
            or value.accepted_at_monotonic_ns < 0
            or not _sha256_hex(value.evidence_sha256)
        ):
            return None
        return GenerationSubscriptionReceipt(
            plane=plane,
            connection_generation=generation,
            control_revision=value.control_revision,
            desired_revision=value.desired_revision,
            evidence_sha256=value.evidence_sha256,
            accepted_at_monotonic_ns=value.accepted_at_monotonic_ns,
        )
    except BaseException:
        return None


__all__ = ["canonical_invalidation", "canonical_subscription_receipt"]
