"""Narrow neutral capabilities for Delta WebSocket supervision."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from enum import StrEnum
from typing import Protocol

from hmt_crypto.exchange.delta.websocket_contracts import WsEndpoint
from hmt_crypto.exchange.delta.websocket_subscriptions import (
    GenerationControlInvalidation,
    GenerationSubscriptionReceipt,
)


class InboundKind(StrEnum):
    """Bounded frame classifications observed by the supervisor."""

    TEXT_FRAME = "text_frame"
    BINARY_FRAME = "binary_frame"
    HEARTBEAT = "heartbeat"
    PONG = "pong"
    DATA = "data"


class WsReceivePort(Protocol):
    """Receive-only actor capability; it cannot send or close."""

    async def recv(self) -> str | bytes:
        """Receive exactly one physical frame."""
        ...


class WsConnection(Protocol):
    """Supervisor-private physical connection capability."""

    def receiver(self) -> WsReceivePort:
        """Issue the sole receive capability exactly once."""
        ...

    async def send(self, message: str) -> None:
        """Serialize one text control frame."""
        ...

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Close the physical connection idempotently."""
        ...


class WsConnector(Protocol):
    """Governed connector interface for one admitted physical open."""

    def open(
        self,
        endpoint: WsEndpoint,
        *,
        attempts: object,
    ) -> AbstractAsyncContextManager[WsConnection]:
        """Bind admission to one unopened async context."""
        ...


class StreamControlPort(Protocol):
    """Neutral actor-to-supervisor control dependency."""

    async def send(self, message: str) -> None:
        """Send through the supervisor's sole serialized writer."""
        ...

    def observe_inbound(self, kind: InboundKind) -> None:
        """Record bounded liveness classification without raw content."""
        ...

    async def invalidate(self, receipt: GenerationControlInvalidation) -> None:
        """Invalidate one exact connection generation before repair."""
        ...

    async def subscriptions_accepted(self, receipt: GenerationSubscriptionReceipt) -> None:
        """Record one complete desired-set acknowledgement."""
        ...


__all__ = [
    "InboundKind",
    "StreamControlPort",
    "WsConnection",
    "WsConnector",
    "WsReceivePort",
]
