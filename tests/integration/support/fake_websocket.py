"""Tests-only deterministic WebSocket connection and connector support."""

from __future__ import annotations

from collections import deque
from contextlib import AbstractAsyncContextManager
from types import TracebackType


class FakeWebSocket:
    """Minimal deterministic physical connection for later actor integration tests."""

    def __init__(self, incoming: tuple[str | bytes, ...] = ()) -> None:
        self.incoming = deque(incoming)
        self.sent: list[str] = []
        self.closed = False
        self.transport = self

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        return self.incoming.popleft()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True

    def abort(self) -> None:
        self.closed = True


class FakeOpenContext(AbstractAsyncContextManager[FakeWebSocket]):
    """Single-use async context mirroring the external connector shape."""

    def __init__(self, connection: FakeWebSocket) -> None:
        self.connection = connection
        self.entered = False

    async def __aenter__(self) -> FakeWebSocket:
        if self.entered:
            raise RuntimeError("fake connection context reused")
        self.entered = True
        return self.connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.connection.close()


class FakeConnectFactory:
    """Callable factory recording bounded transport options without a socket."""

    def __init__(self, connection: FakeWebSocket) -> None:
        self.connection = connection
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __call__(self, url: str, **options: object) -> FakeOpenContext:
        self.calls.append((url, dict(options)))
        return FakeOpenContext(self.connection)
