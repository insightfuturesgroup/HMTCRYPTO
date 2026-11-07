"""Bounded non-blocking handoff for sanitized stream-control evidence."""

from __future__ import annotations

from collections import deque

from hmt_crypto.errors import ExchangeDownError
from hmt_crypto.exchange.delta.websocket_subscriptions import (
    GenerationControlInvalidation,
    GenerationSubscriptionReceipt,
)


class StreamEvidenceMailbox:
    """Retain exact evidence until its durable consumer drains it."""

    __slots__ = ("_invalidations", "_max_pending", "_receipts")

    def __init__(self, *, max_pending: int = 4096) -> None:
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError("stream evidence bound is invalid")
        self._max_pending = max_pending
        self._invalidations: deque[GenerationControlInvalidation] = deque()
        self._receipts: deque[GenerationSubscriptionReceipt] = deque()

    def _has_capacity(self) -> bool:
        return len(self._invalidations) + len(self._receipts) < self._max_pending

    async def invalidate(self, receipt: GenerationControlInvalidation) -> None:
        """Enqueue one exact sanitized invalidation without external I/O."""
        if type(receipt) is not GenerationControlInvalidation or not self._has_capacity():
            del receipt
            raise ExchangeDownError(context={"operation": "websocket_evidence_mailbox"})
        self._invalidations.append(receipt)

    async def subscriptions_accepted(self, receipt: GenerationSubscriptionReceipt) -> None:
        """Enqueue one exact sanitized receipt without external I/O."""
        if type(receipt) is not GenerationSubscriptionReceipt or not self._has_capacity():
            del receipt
            raise ExchangeDownError(context={"operation": "websocket_evidence_mailbox"})
        self._receipts.append(receipt)

    @property
    def invalidations(self) -> tuple[GenerationControlInvalidation, ...]:
        """Return an immutable snapshot of pending invalidations."""
        return tuple(self._invalidations)

    @property
    def receipts(self) -> tuple[GenerationSubscriptionReceipt, ...]:
        """Return an immutable snapshot of pending subscription receipts."""
        return tuple(self._receipts)


__all__ = ["StreamEvidenceMailbox"]
