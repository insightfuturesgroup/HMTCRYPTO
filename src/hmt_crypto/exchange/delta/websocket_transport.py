"""Injected single-connection adapter for reviewed Delta WebSocket endpoints."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import NoReturn, Protocol, SupportsIndex, TypeVar, cast

from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as websockets_connect

from hmt_crypto.errors import (
    ConfigurationError,
    ExchangeDownError,
    ExchangeProtocolError,
)
from hmt_crypto.exchange.delta.websocket_contracts import (
    WsEndpoint,
    validate_ws_endpoint_for_transport,
)
from hmt_crypto.exchange.delta.websocket_errors import (
    WebSocketAttemptAdmissionError,
    WebSocketHandshakeCategory,
    WebSocketHandshakeError,
    classify_open_error,
)
from hmt_crypto.exchange.delta.websocket_ports import (
    InboundKind,
    StreamControlPort,
    WsConnection,
    WsConnector,
    WsReceivePort,
)

_MIN_FRAME_BYTES = 1024 * 1024
_MAX_FRAME_BYTES = 16 * 1024 * 1024
_CleanupResult = TypeVar("_CleanupResult")
_PRODUCTION_CONNECTOR_MARKER = object()
_PRODUCTION_CONNECTION_MARKER = object()
_RECEIVER_CAPABILITY_MARKER = object()


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


class _AbortTransport(Protocol):
    def abort(self) -> None: ...


class _RawConnection(Protocol):
    transport: _AbortTransport

    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


class _ConnectFactory(Protocol):
    def __call__(
        self, url: str, **options: object
    ) -> AbstractAsyncContextManager[_RawConnection]: ...


_OFFICIAL_CONNECT_FACTORY = cast(_ConnectFactory, websockets_connect)
_OFFICIAL_CONNECTION_TYPE: type[object] = ClientConnection


def _transport_error(operation: str) -> ExchangeDownError:
    return ExchangeDownError(context={"operation": operation})


class _ReceiveCapability:
    __slots__ = ("_connection", "_issuance_marker", "_session_claimed")

    def __init__(self, connection: _DeltaWsConnection) -> None:
        self._connection = connection
        self._issuance_marker = _RECEIVER_CAPABILITY_MARKER
        self._session_claimed = False

    def __copy__(self) -> NoReturn:
        raise TypeError("WebSocket receivers cannot be copied")

    def __deepcopy__(self, _memo: dict[int, object]) -> NoReturn:
        raise TypeError("WebSocket receivers cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("WebSocket receivers cannot be serialized")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError("WebSocket receivers cannot be serialized")

    async def recv(self) -> str | bytes:
        return await self._connection._recv()


class _DeltaWsConnection:
    __slots__ = (
        "_close_task",
        "_owner_issued",
        "_ownership_marker",
        "_raw",
        "_issued_receiver",
        "_receiver_issued",
        "_send_lock",
    )

    def __init__(self, raw: _RawConnection, *, ownership_marker: object | None) -> None:
        self._raw = raw
        self._ownership_marker = ownership_marker
        self._owner_issued = False
        self._issued_receiver: _ReceiveCapability | None = None
        self._send_lock = asyncio.Lock()
        self._receiver_issued = False
        self._close_task: asyncio.Task[None] | None = None

    def __copy__(self) -> NoReturn:
        raise TypeError("WebSocket connections cannot be copied")

    def __deepcopy__(self, _memo: dict[int, object]) -> NoReturn:
        raise TypeError("WebSocket connections cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("WebSocket connections cannot be serialized")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError("WebSocket connections cannot be serialized")

    def receiver(self) -> WsReceivePort:
        if self._receiver_issued:
            raise ConfigurationError(context={"operation": "websocket_receiver_claim"})
        self._receiver_issued = True
        receiver = _ReceiveCapability(self)
        self._issued_receiver = receiver
        return receiver

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
    __slots__ = (
        "_attempts",
        "_connector",
        "_connection",
        "_endpoint",
        "_entered",
        "_manager",
    )

    def __init__(
        self,
        connector: DeltaWebsocketsConnector,
        endpoint: WsEndpoint,
        *,
        attempts: object,
    ) -> None:
        from hmt_crypto.market_data.connection_attempts import ConnectionAttemptGovernor

        if not (
            _is_owned_physical_connector(connector)
            and type(endpoint) is WsEndpoint
            and type(attempts) is ConnectionAttemptGovernor
            and endpoint.region == attempts.region
            and endpoint.environment == attempts.environment
        ):
            del connector, endpoint, attempts
            raise TypeError("physical WebSocket context dependencies are invalid") from None
        self._connector = connector
        self._endpoint = endpoint
        self._attempts = attempts
        self._entered = False
        self._manager: AbstractAsyncContextManager[_RawConnection] | None = None
        self._connection: _DeltaWsConnection | None = None

    async def __aenter__(self) -> WsConnection:
        if self._entered:
            raise ConfigurationError(context={"operation": "websocket_open"})
        self._entered = True
        selected = validate_ws_endpoint_for_transport(self._endpoint)
        attempts = self._attempts
        del self._attempts
        from hmt_crypto.market_data.connection_attempts import ConnectionAttemptGovernor

        if not (
            type(attempts) is ConnectionAttemptGovernor
            and selected.region == attempts.region
            and selected.environment == attempts.environment
        ):
            del attempts
            raise WebSocketAttemptAdmissionError from None
        try:
            await attempts.consume_physical_open_attempt()
        except asyncio.CancelledError:
            raise
        except BaseException:
            raise WebSocketAttemptAdmissionError from None
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
            if _is_owned_physical_connector(self._connector) and (
                type(raw) is not _OFFICIAL_CONNECTION_TYPE
            ):
                raise TypeError("unexpected physical WebSocket connection")
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
        except BaseException as error:
            cleanup_manager = self._manager
            if cleanup_manager is not None:
                cancellation = _CancellationState()
                try:
                    await _finish_cleanup(
                        cleanup_manager.__aexit__(type(error), error, error.__traceback__),
                        cancellation,
                    )
                except BaseException:
                    pass
                self._manager = None
                if cancellation.pending:
                    del error
                    raise asyncio.CancelledError from None
            mapped = classify_open_error(error)
            del error
            raise mapped from None
        marker = (
            _PRODUCTION_CONNECTION_MARKER if _is_owned_physical_connector(self._connector) else None
        )
        self._connection = _DeltaWsConnection(raw, ownership_marker=marker)
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

    __slots__ = ("_connect_factory", "_library_logger", "_max_size", "_ownership_marker")

    def __init__(
        self,
        *,
        max_size: int = 4 * 1024 * 1024,
        connect_factory: _ConnectFactory | None = None,
    ) -> None:
        if type(max_size) is not int or not _MIN_FRAME_BYTES <= max_size <= _MAX_FRAME_BYTES:
            raise ConfigurationError(context={"operation": "configure_websocket_transport"})
        self._max_size = max_size
        self._ownership_marker = _PRODUCTION_CONNECTOR_MARKER if connect_factory is None else None
        self._connect_factory = (
            _OFFICIAL_CONNECT_FACTORY if connect_factory is None else connect_factory
        )
        self._library_logger = logging.Logger(
            "hmt_crypto.websocket.library", level=logging.CRITICAL
        )
        self._library_logger.disabled = True

    @property
    def library_logger(self) -> logging.Logger:
        """Return the dedicated disabled logger passed to the library."""
        return self._library_logger

    def open(
        self,
        endpoint: WsEndpoint,
        *,
        attempts: object,
    ) -> AbstractAsyncContextManager[WsConnection]:
        """Bind exact admission for consumption immediately before the factory."""
        from hmt_crypto.market_data.connection_attempts import ConnectionAttemptGovernor

        valid = (
            _is_owned_physical_connector(self)
            and type(endpoint) is WsEndpoint
            and type(attempts) is ConnectionAttemptGovernor
            and endpoint.region == attempts.region
            and endpoint.environment == attempts.environment
        )
        if not valid:
            del endpoint, attempts
            raise TypeError("physical WebSocket admission dependencies are invalid") from None
        return _ConnectionContext(self, endpoint, attempts=attempts)


def _is_owned_physical_connector(value: object) -> bool:
    """Return whether composition created the exact default connector."""
    try:
        return (
            type(value) is DeltaWebsocketsConnector
            and value._ownership_marker is _PRODUCTION_CONNECTOR_MARKER
            and value._connect_factory is _OFFICIAL_CONNECT_FACTORY
        )
    except BaseException:
        return False


__all__ = [
    "DeltaWebsocketsConnector",
    "InboundKind",
    "StreamControlPort",
    "WebSocketHandshakeCategory",
    "WebSocketHandshakeError",
    "WebSocketAttemptAdmissionError",
    "WsConnection",
    "WsConnector",
    "WsReceivePort",
]
