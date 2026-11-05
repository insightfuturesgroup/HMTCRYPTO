"""Injected single-connection adapter for reviewed Delta WebSocket endpoints."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from contextlib import AbstractAsyncContextManager
from enum import StrEnum
from types import TracebackType
from typing import Protocol, TypeVar, cast

from websockets.asyncio.client import connect as websockets_connect
from websockets.exceptions import InvalidStatus

from hmt_crypto.errors import (
    ConfigurationError,
    ExchangeDownError,
    ExchangeProtocolError,
)
from hmt_crypto.exchange.delta.websocket_contracts import (
    WsEndpoint,
    validate_ws_endpoint_for_transport,
)
from hmt_crypto.exchange.delta.websocket_subscriptions import (
    GenerationControlInvalidation,
    GenerationSubscriptionReceipt,
)

_MIN_FRAME_BYTES = 1024 * 1024
_MAX_FRAME_BYTES = 16 * 1024 * 1024
_CleanupResult = TypeVar("_CleanupResult")


class _CancellationState:
    __slots__ = ("pending",)

    def __init__(self) -> None:
        self.pending = False


async def _finish_cleanup(
    awaitable: Awaitable[_CleanupResult],
    cancellation: _CancellationState,
) -> _CleanupResult:
    task = asyncio.ensure_future(awaitable)
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancellation.pending = True
            if task.done():
                return task.result()


class InboundKind(StrEnum):
    """Bounded frame classifications observed by the later supervisor."""

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
    """Injected connector interface with synchronous context construction."""

    def open(self, endpoint: WsEndpoint) -> AbstractAsyncContextManager[WsConnection]:
        """Return one async context without opening eagerly."""
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


class _RawConnection(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


class _ConnectFactory(Protocol):
    def __call__(
        self, url: str, **options: object
    ) -> AbstractAsyncContextManager[_RawConnection]: ...


class WebSocketHandshakeCategory(StrEnum):
    """Safe reconnect classification for a failed physical open."""

    RATE_LIMIT = "rate_limit"
    CLIENT_REJECTION = "client_rejection"
    SERVER_REJECTION = "server_rejection"
    NETWORK = "network"
    PROTOCOL = "protocol"


class WebSocketHandshakeError(ExchangeDownError):
    """A redacted handshake failure retaining only status and category."""

    _code = "websocket_handshake_rejected"
    _message = "WebSocket handshake failed"

    def __init__(
        self,
        *,
        category: WebSocketHandshakeCategory,
        status_code: int | None,
    ) -> None:
        if type(category) is not WebSocketHandshakeCategory or (
            status_code is not None
            and (type(status_code) is not int or not 100 <= status_code <= 599)
        ):
            raise ValueError("invalid WebSocket handshake classification")
        self._category = category
        self._status_code = status_code
        context: dict[str, object] = {
            "operation": "websocket_open",
            "state": category.value,
        }
        if status_code is not None:
            context["http_status"] = status_code
        super().__init__(context=context)

    @property
    def category(self) -> WebSocketHandshakeCategory:
        """Return the bounded reconnect category."""
        return self._category

    @property
    def status_code(self) -> int | None:
        """Return the bounded HTTP status when the handshake produced one."""
        return self._status_code

    def __reduce__(self) -> tuple[object, tuple[object, ...]]:
        """Reconstruct copies and pickles from bounded classification only."""
        return _reconstruct_handshake_error, (self.category, self.status_code)


def _reconstruct_handshake_error(
    category: WebSocketHandshakeCategory,
    status_code: int | None,
) -> WebSocketHandshakeError:
    return WebSocketHandshakeError(category=category, status_code=status_code)


# Source-manifest F02: excess connection attempts are reported as HTTP 429.
def _open_error(error: BaseException) -> WebSocketHandshakeError:
    status: int | None = None
    if isinstance(error, InvalidStatus):
        candidate = error.response.status_code
        if type(candidate) is int and 100 <= candidate <= 599:
            status = candidate
    if status == 429:
        category = WebSocketHandshakeCategory.RATE_LIMIT
    elif status is not None and 400 <= status <= 499:
        category = WebSocketHandshakeCategory.CLIENT_REJECTION
    elif status is not None and 500 <= status <= 599:
        category = WebSocketHandshakeCategory.SERVER_REJECTION
    elif isinstance(error, (OSError, TimeoutError)):
        category = WebSocketHandshakeCategory.NETWORK
    else:
        category = WebSocketHandshakeCategory.PROTOCOL
    return WebSocketHandshakeError(category=category, status_code=status)


def _transport_error(operation: str) -> ExchangeDownError:
    return ExchangeDownError(context={"operation": operation})


class _ReceiveCapability:
    __slots__ = ("_connection",)

    def __init__(self, connection: _DeltaWsConnection) -> None:
        self._connection = connection

    async def recv(self) -> str | bytes:
        return await self._connection._recv()


class _DeltaWsConnection:
    __slots__ = ("_close_task", "_raw", "_receiver_issued", "_send_lock")

    def __init__(self, raw: _RawConnection) -> None:
        self._raw = raw
        self._send_lock = asyncio.Lock()
        self._receiver_issued = False
        self._close_task: asyncio.Task[None] | None = None

    def receiver(self) -> WsReceivePort:
        if self._receiver_issued:
            raise ConfigurationError(context={"operation": "websocket_receiver_claim"})
        self._receiver_issued = True
        return _ReceiveCapability(self)

    def _closing(self) -> bool:
        return self._close_task is not None

    async def send(self, message: str) -> None:
        if type(message) is not str or self._closing():
            raise ExchangeProtocolError(context={"operation": "websocket_send"})
        try:
            async with self._send_lock:
                if self._closing():
                    raise ExchangeProtocolError(context={"operation": "websocket_send"})
                await self._raw.send(message)
        except asyncio.CancelledError:
            raise
        except ExchangeProtocolError:
            raise
        except Exception:
            raise _transport_error("websocket_send") from None

    async def _recv(self) -> str | bytes:
        if self._closing():
            raise ExchangeProtocolError(context={"operation": "websocket_receive"})
        try:
            frame = await self._raw.recv()
        except asyncio.CancelledError:
            raise
        except Exception:
            raise _transport_error("websocket_receive") from None
        if type(frame) not in {str, bytes}:
            raise ExchangeProtocolError(context={"operation": "websocket_receive"})
        return frame

    async def _close_once(self, code: int, reason: str) -> None:
        try:
            async with self._send_lock:
                await self._raw.close(code=code, reason=reason)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise _transport_error("websocket_close") from None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if (
            type(code) is not int
            or not 1000 <= code <= 4999
            or type(reason) is not str
            or len(reason.encode("utf-8")) > 123
        ):
            raise ExchangeProtocolError(context={"operation": "websocket_close"})
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_once(code, reason))
        task = self._close_task
        cancellation = _CancellationState()
        failure: BaseException | None = None
        try:
            await _finish_cleanup(task, cancellation)
        except asyncio.CancelledError:
            cancellation.pending = True
        except BaseException as error:
            failure = error
        if cancellation.pending:
            raise asyncio.CancelledError
        if failure is not None:
            raise failure


class _ConnectionContext(AbstractAsyncContextManager[WsConnection]):
    __slots__ = ("_connector", "_connection", "_endpoint", "_entered", "_manager")

    def __init__(self, connector: DeltaWebsocketsConnector, endpoint: WsEndpoint) -> None:
        self._connector = connector
        self._endpoint = endpoint
        self._entered = False
        self._manager: AbstractAsyncContextManager[_RawConnection] | None = None
        self._connection: _DeltaWsConnection | None = None

    async def __aenter__(self) -> WsConnection:
        if self._entered:
            raise ConfigurationError(context={"operation": "websocket_open"})
        self._entered = True
        selected = validate_ws_endpoint_for_transport(self._endpoint)
        try:
            manager = self._connector._connect_factory(
                selected.url,
                compression=None,
                proxy=None,
                ping_interval=None,
                open_timeout=10,
                close_timeout=5,
                max_size=self._connector._max_size,
                max_queue=(4, 1),
                logger=self._connector.library_logger,
            )
            self._manager = manager
            raw = await manager.__aenter__()
        except asyncio.CancelledError as error:
            cleanup_manager = self._manager
            if cleanup_manager is not None:
                cancellation = _CancellationState()
                cancellation.pending = True
                try:
                    await _finish_cleanup(
                        cleanup_manager.__aexit__(type(error), error, error.__traceback__),
                        cancellation,
                    )
                except BaseException:
                    pass
                self._manager = None
            raise
        except Exception as error:
            mapped = _open_error(error)
            del error
            raise mapped from None
        self._connection = _DeltaWsConnection(raw)
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        if self._connection is None or self._manager is None:
            return None
        cancellation = _CancellationState()
        close_error: ExchangeDownError | None = None
        try:
            await _finish_cleanup(self._connection.close(), cancellation)
        except asyncio.CancelledError:
            cancellation.pending = True
        except ExchangeDownError as error:
            close_error = error
        manager_error: ExchangeDownError | None = None
        suppressed: bool | None = None
        try:
            suppressed = await _finish_cleanup(
                self._manager.__aexit__(exc_type, exc, traceback),
                cancellation,
            )
        except asyncio.CancelledError:
            cancellation.pending = True
        except Exception:
            manager_error = _transport_error("websocket_close")
        finally:
            self._manager = None
            self._connection = None
        if cancellation.pending or isinstance(exc, asyncio.CancelledError):
            raise asyncio.CancelledError
        if manager_error is not None:
            if exc is None:
                raise manager_error
            return None
        if close_error is not None and exc is None:
            raise close_error
        return suppressed


class DeltaWebsocketsConnector:
    """One-shot injected websockets 17.x adapter with no reconnect iterator."""

    def __init__(
        self,
        *,
        max_size: int = 4 * 1024 * 1024,
        connect_factory: _ConnectFactory | None = None,
    ) -> None:
        if type(max_size) is not int or not _MIN_FRAME_BYTES <= max_size <= _MAX_FRAME_BYTES:
            raise ConfigurationError(context={"operation": "configure_websocket_transport"})
        self._max_size = max_size
        self._connect_factory = (
            cast(_ConnectFactory, websockets_connect)
            if connect_factory is None
            else connect_factory
        )
        self._library_logger = logging.Logger(
            "hmt_crypto.websocket.library", level=logging.CRITICAL
        )
        self._library_logger.disabled = True

    @property
    def library_logger(self) -> logging.Logger:
        """Return the dedicated disabled logger passed to the library."""
        return self._library_logger

    def open(self, endpoint: WsEndpoint) -> AbstractAsyncContextManager[WsConnection]:
        """Return one single-use async context; opening occurs on entry."""
        return _ConnectionContext(self, endpoint)


__all__ = [
    "DeltaWebsocketsConnector",
    "InboundKind",
    "StreamControlPort",
    "WebSocketHandshakeCategory",
    "WebSocketHandshakeError",
    "WsConnection",
    "WsConnector",
    "WsReceivePort",
]
