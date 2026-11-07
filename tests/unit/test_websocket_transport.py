"""Injected one-connection WebSocket transport contracts."""

from __future__ import annotations

import asyncio
import copy
import inspect
import pickle
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from importlib import import_module
from types import TracebackType
from typing import Any
from uuid import uuid4

import pytest
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

import hmt_crypto.exchange.delta.websocket_ownership as websocket_ownership
import hmt_crypto.market_data.connection_attempts as attempts_module
from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.errors import ConfigurationError, ExchangeDownError, ExchangeProtocolError
from hmt_crypto.exchange.delta.rate_limit_policy import RateLimitScope, ScopeKind
from hmt_crypto.exchange.delta.websocket_contracts import WsEndpoint, WsPlane
from hmt_crypto.market_data.connection_attempts import ConnectionAttemptGovernor

pytest_plugins = ("tests.integration.support.task210_worker_clock_issuer",)

_ATTEMPTS: ConnectionAttemptGovernor | None = None
_PATCHER: pytest.MonkeyPatch | None = None
_TASK210_MARKER: object | None = None
_ISSUED = datetime(2026, 11, 6, 10, 0, tzinfo=UTC)


class AdmissionTime:
    def __init__(self) -> None:
        self.now = 0

    def monotonic_ns(self) -> int:
        return self.now

    async def sleep_ns(self, delay_ns: int) -> None:
        self.now += delay_ns


@pytest.fixture(autouse=True)
def exact_attempt_admission(
    task210_worker_clock_marker: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global _ATTEMPTS, _PATCHER, _TASK210_MARKER
    admission_time = AdmissionTime()
    monkeypatch.setattr(attempts_module, "_PROCESS_OWNER_REF", None)
    attempts_module._GOVERNOR_STATES.clear()
    monkeypatch.setattr(attempts_module, "_system_monotonic_ns", admission_time.monotonic_ns)
    monkeypatch.setattr(attempts_module, "_system_sleep_ns", admission_time.sleep_ns)
    _ATTEMPTS = None
    _PATCHER = monkeypatch
    _TASK210_MARKER = task210_worker_clock_marker


def attempts() -> ConnectionAttemptGovernor:
    """Issue the exact owner lazily inside the test's running event loop."""
    global _ATTEMPTS
    if _ATTEMPTS is not None:
        return _ATTEMPTS
    assert _TASK210_MARKER is not None
    support = import_module("tests.integration.support.task210_worker_clock_issuer")
    proof, currentness = support.issue_single_private_worker(
        _TASK210_MARKER,
        proof_id=uuid4(),
        deployment_revision="websocket-transport-test",
        external_user_id="12345",
        worker_incarnation_id=uuid4(),
        process_incarnation_id=uuid4(),
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
        ownership_epoch=1,
        issued_at=_ISSUED,
        expires_at=_ISSUED + timedelta(hours=1),
        observed_at=_ISSUED + timedelta(seconds=1),
        process_started_monotonic_ns=0,
    )
    _ATTEMPTS = ConnectionAttemptGovernor.for_single_process_egress(
        RateLimitScope(
            region="india",
            environment="testnet",
            kind=ScopeKind.IP,
            external_id="transport-test-egress",
        ),
        ownership_proof=proof,
        ownership_currentness=currentness,
    )
    return _ATTEMPTS


def transport() -> Any:
    try:
        return import_module("hmt_crypto.exchange.delta.websocket_transport")
    except ModuleNotFoundError:
        pytest.fail("WebSocket transport is missing", pytrace=False)


def endpoint() -> WsEndpoint:
    return WsEndpoint(
        plane=WsPlane.PUBLIC,
        url="wss://socket-ind-pub.testnet.deltaex.org",
        region="india",
        environment="testnet",
    )


def low_level_open(module: Any, connector: Any, selected: WsEndpoint | None = None) -> Any:
    """Enter the real governed boundary with a deterministic tests-only factory."""
    assert _PATCHER is not None
    factory = connector._connect_factory
    _PATCHER.setattr(module, "_OFFICIAL_CONNECT_FACTORY", factory)
    _PATCHER.setattr(module, "_OFFICIAL_CONNECTION_TYPE", type(factory.raw))
    owned = module.DeltaWebsocketsConnector(max_size=connector._max_size)
    return owned.open(selected or endpoint(), attempts=attempts())


class RawConnection:
    def __init__(self, incoming: list[str | bytes] | None = None) -> None:
        self.incoming = list(incoming or ())
        self.sent: list[str] = []
        self.closed = False
        self.active_sends = 0
        self.max_active_sends = 0
        self.send_failure = False
        self.recv_failure = False
        self.close_failure = False
        self.send_gate: asyncio.Event | None = None
        self.send_started = asyncio.Event()
        self.recv_gate: asyncio.Event | None = None
        self.recv_started = asyncio.Event()
        self.close_started = asyncio.Event()
        self.close_gate: asyncio.Event | None = None
        self.transport = self
        self.abort_calls = 0

    async def send(self, message: str) -> None:
        if self.send_failure:
            raise OSError("fixture-secret-send")
        self.send_started.set()
        self.active_sends += 1
        self.max_active_sends = max(self.max_active_sends, self.active_sends)
        try:
            if self.send_gate is not None:
                await self.send_gate.wait()
            else:
                await asyncio.sleep(0)
            self.sent.append(message)
        finally:
            self.active_sends -= 1

    async def recv(self) -> str | bytes:
        if self.recv_failure:
            raise OSError("fixture-secret-recv")
        self.recv_started.set()
        if self.recv_gate is not None:
            await self.recv_gate.wait()
        return self.incoming.pop(0)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_started.set()
        if self.close_gate is not None:
            await self.close_gate.wait()
        if self.close_failure:
            raise OSError("fixture-secret-close")
        self.closed = True

    def abort(self) -> None:
        self.abort_calls += 1


class ConnectContext:
    def __init__(self, factory: ConnectFactory) -> None:
        self.factory = factory

    async def __aenter__(self) -> RawConnection:
        factory = self.factory
        if factory.enter_error is not None:
            raise factory.enter_error
        factory.enters += 1
        factory.owner_active = True
        factory.enter_started.set()
        if factory.enter_gate is not None:
            await factory.enter_gate.wait()
        return factory.raw

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        factory = self.factory
        factory.exit_started.set()
        if factory.exit_gate is not None:
            await factory.exit_gate.wait()
        factory.exits += 1
        factory.owner_active = False
        if factory.exit_failure:
            raise OSError("fixture-secret-manager-exit")


class ConnectFactory:
    def __init__(
        self,
        raw: RawConnection,
        *,
        enter_error: BaseException | None = None,
        enter_gate: asyncio.Event | None = None,
        exit_gate: asyncio.Event | None = None,
    ) -> None:
        self.raw = raw
        self.enter_error = enter_error
        self.enter_gate = enter_gate
        self.exit_gate = exit_gate
        self.exit_failure = False
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.enters = 0
        self.exits = 0
        self.owner_active = False
        self.enter_started = asyncio.Event()
        self.exit_started = asyncio.Event()

    def __call__(self, url: str, **options: object) -> ConnectContext:
        self.calls.append((url, options))
        return ConnectContext(self)


@pytest.mark.asyncio
async def test_sync_open_uses_one_connection_and_exact_bounded_options() -> None:
    module = transport()
    raw = RawConnection(["text", b"binary"])
    factory = ConnectFactory(raw)
    connector = module.DeltaWebsocketsConnector(connect_factory=factory)

    context = low_level_open(module, connector)
    assert not inspect.isawaitable(context)
    async with context as connection:
        with pytest.raises(TypeError, match="cannot be copied"):
            copy.copy(connection)
        with pytest.raises(TypeError, match="cannot be serialized"):
            pickle.dumps(connection)
        receiver = connection.receiver()
        with pytest.raises(TypeError, match="cannot be copied"):
            copy.copy(receiver)
        with pytest.raises(TypeError, match="cannot be serialized"):
            pickle.dumps(receiver)
        assert websocket_ownership._claim_owned_receive_capability(receiver) is True
        assert websocket_ownership._claim_owned_receive_capability(receiver) is False
        assert not hasattr(receiver, "send")
        assert not hasattr(receiver, "close")
        assert await receiver.recv() == "text"
        assert await receiver.recv() == b"binary"
        with pytest.raises(ConfigurationError):
            connection.receiver()

    assert raw.closed is True
    assert factory.exits == 1
    assert factory.calls == [
        (
            "wss://socket-ind-pub.testnet.deltaex.org",
            {
                "compression": None,
                "proxy": None,
                "ping_interval": None,
                "open_timeout": 10,
                "close_timeout": 5,
                "max_size": 4 * 1024 * 1024,
                "max_queue": (4, 1),
                "logger": context._connector.library_logger,
            },
        )
    ]


@pytest.mark.asyncio
async def test_endpoint_is_revalidated_at_context_entry_before_factory_call() -> None:
    module = transport()
    factory = ConnectFactory(RawConnection())
    selected = endpoint()
    context = low_level_open(
        module,
        module.DeltaWebsocketsConnector(connect_factory=factory),
        selected,
    )
    object.__setattr__(selected, "url", "wss://attacker.invalid")

    with pytest.raises(ConfigurationError):
        async with context:
            pytest.fail("invalid endpoint entered")
    assert factory.calls == []


@pytest.mark.asyncio
async def test_private_context_cannot_bypass_connector_or_attempt_admission() -> None:
    module = transport()
    factory = ConnectFactory(RawConnection())
    injected = module.DeltaWebsocketsConnector(connect_factory=factory)

    with pytest.raises(TypeError):
        module._ConnectionContext(injected, endpoint())
    with pytest.raises(TypeError, match="context dependencies"):
        module._ConnectionContext(injected, endpoint(), attempts=attempts())

    assert factory.calls == []


@pytest.mark.asyncio
async def test_parallel_sends_are_serialized_by_one_connection_lock() -> None:
    module = transport()
    raw = RawConnection()
    raw.send_gate = asyncio.Event()
    factory = ConnectFactory(raw)
    async with low_level_open(
        module, module.DeltaWebsocketsConnector(connect_factory=factory)
    ) as connection:
        first = asyncio.create_task(connection.send("one"))
        second = asyncio.create_task(connection.send("two"))
        await asyncio.sleep(0)
        assert raw.max_active_sends == 1
        raw.send_gate.set()
        await asyncio.gather(first, second)
    assert raw.sent == ["one", "two"]


@pytest.mark.asyncio
async def test_close_finishes_physical_cleanup_when_caller_is_cancelled() -> None:
    module = transport()
    raw = RawConnection()
    raw.close_gate = asyncio.Event()
    factory = ConnectFactory(raw)
    connection_context = low_level_open(
        module, module.DeltaWebsocketsConnector(connect_factory=factory)
    )
    connection = await connection_context.__aenter__()
    closing = asyncio.create_task(connection.close())
    await raw.close_started.wait()
    closing.cancel()
    raw.close_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert raw.closed is True
    await connection_context.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_cancelled_direct_close_preserves_cancellation_over_safe_close_failure() -> None:
    module = transport()
    raw = RawConnection()
    raw.close_gate = asyncio.Event()
    raw.close_failure = True
    factory = ConnectFactory(raw)
    context = low_level_open(module, module.DeltaWebsocketsConnector(connect_factory=factory))
    connection = await context.__aenter__()
    closing = asyncio.create_task(connection.close())
    await raw.close_started.wait()

    closing.cancel()
    raw.close_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await closing

    with pytest.raises(ExchangeDownError):
        await context.__aexit__(None, None, None)
    assert factory.exits == 1
    assert factory.owner_active is False


@pytest.mark.asyncio
async def test_cancel_during_manager_enter_waits_for_exactly_one_owner_cleanup() -> None:
    module = transport()
    enter_gate = asyncio.Event()
    exit_gate = asyncio.Event()
    factory = ConnectFactory(
        RawConnection(),
        enter_gate=enter_gate,
        exit_gate=exit_gate,
    )
    context = low_level_open(module, module.DeltaWebsocketsConnector(connect_factory=factory))
    opening = asyncio.create_task(context.__aenter__())
    await factory.enter_started.wait()

    opening.cancel()
    await asyncio.wait_for(factory.exit_started.wait(), timeout=0.5)
    assert not opening.done()
    exit_gate.set()

    with pytest.raises(asyncio.CancelledError):
        await opening
    assert factory.exits == 1
    assert factory.owner_active is False


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("send", "receive"))
async def test_cancel_during_io_exits_manager_once_and_releases_owner(operation: str) -> None:
    module = transport()
    raw = RawConnection(["frame"])
    factory = ConnectFactory(raw)
    if operation == "send":
        raw.send_gate = asyncio.Event()
        started = raw.send_started
    else:
        raw.recv_gate = asyncio.Event()
        started = raw.recv_started

    async def use_connection() -> None:
        async with low_level_open(
            module, module.DeltaWebsocketsConnector(connect_factory=factory)
        ) as connection:
            if operation == "send":
                await connection.send("control")
            else:
                await connection.receiver().recv()

    running = asyncio.create_task(use_connection())
    await started.wait()
    running.cancel()

    with pytest.raises(asyncio.CancelledError):
        await running
    assert raw.closed is True
    assert factory.exits == 1
    assert factory.owner_active is False


@pytest.mark.asyncio
async def test_cancel_during_physical_close_waits_for_manager_owner_cleanup() -> None:
    module = transport()
    raw = RawConnection()
    raw.close_gate = asyncio.Event()
    factory = ConnectFactory(raw)

    async def use_connection() -> None:
        async with low_level_open(module, module.DeltaWebsocketsConnector(connect_factory=factory)):
            pass

    running = asyncio.create_task(use_connection())
    await raw.close_started.wait()
    running.cancel()
    await asyncio.sleep(0)
    assert not running.done()
    raw.close_gate.set()

    with pytest.raises(asyncio.CancelledError):
        await running
    assert raw.closed is True
    assert factory.exits == 1
    assert factory.owner_active is False


@pytest.mark.asyncio
async def test_cancel_during_manager_exit_waits_until_owner_is_released() -> None:
    module = transport()
    exit_gate = asyncio.Event()
    factory = ConnectFactory(RawConnection(), exit_gate=exit_gate)

    async def use_connection() -> None:
        async with low_level_open(module, module.DeltaWebsocketsConnector(connect_factory=factory)):
            pass

    running = asyncio.create_task(use_connection())
    await factory.exit_started.wait()
    running.cancel()
    await asyncio.sleep(0)
    assert not running.done()
    exit_gate.set()

    with pytest.raises(asyncio.CancelledError):
        await running
    assert factory.exits == 1
    assert factory.owner_active is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("physical", "manager"))
async def test_close_failures_are_safely_mapped_after_owner_cleanup(failure: str) -> None:
    module = transport()
    raw = RawConnection()
    factory = ConnectFactory(raw)
    if failure == "physical":
        raw.close_failure = True
    else:
        factory.exit_failure = True

    with pytest.raises(ExchangeDownError) as error:
        async with low_level_open(module, module.DeltaWebsocketsConnector(connect_factory=factory)):
            pass

    assert error.value.context == {"operation": "websocket_close"}
    assert "fixture-secret" not in str(error.value.to_dict())
    assert factory.exits == 1
    assert factory.owner_active is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "category"),
    ((429, "rate_limit"), (403, "client_rejection"), (503, "server_rejection")),
)
async def test_handshake_status_is_typed_and_raw_response_is_redacted(
    status: int, category: str
) -> None:
    module = transport()
    response = Response(
        status,
        "fixture-secret-reason",
        Headers({"x-secret": "fixture-secret-header"}),
        b"fixture-secret-body",
    )
    factory = ConnectFactory(RawConnection(), enter_error=InvalidStatus(response))

    with pytest.raises(module.WebSocketHandshakeError) as error:
        async with low_level_open(module, module.DeltaWebsocketsConnector(connect_factory=factory)):
            pytest.fail("rejected handshake entered")
    assert error.value.status_code == status
    assert error.value.category.value == category
    rendered = str(error.value) + repr(error.value) + str(error.value.to_dict())
    assert "fixture-secret" not in rendered


@pytest.mark.asyncio
async def test_network_handshake_failure_is_typed_without_status() -> None:
    module = transport()
    factory = ConnectFactory(RawConnection(), enter_error=OSError("fixture-secret-network"))
    with pytest.raises(module.WebSocketHandshakeError) as error:
        async with low_level_open(module, module.DeltaWebsocketsConnector(connect_factory=factory)):
            pytest.fail("failed network open entered")
    assert error.value.status_code is None
    assert error.value.category is module.WebSocketHandshakeCategory.NETWORK
    assert "fixture-secret" not in str(error.value.to_dict())


def test_handshake_failure_copy_and_pickle_preserve_only_safe_classification() -> None:
    module = transport()
    original = module.WebSocketHandshakeError(
        category=module.WebSocketHandshakeCategory.RATE_LIMIT,
        status_code=429,
    )

    for restored in (copy.copy(original), pickle.loads(pickle.dumps(original))):
        assert type(restored) is type(original)
        assert restored.category is module.WebSocketHandshakeCategory.RATE_LIMIT
        assert restored.status_code == 429
        assert restored.to_dict() == original.to_dict()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "operation"),
    (("send_failure", "websocket_send"), ("recv_failure", "websocket_receive")),
)
async def test_send_and_receive_failures_map_to_safe_transport_errors(
    failure: str, operation: str
) -> None:
    module = transport()
    raw = RawConnection(["unused"])
    setattr(raw, failure, True)
    factory = ConnectFactory(raw)
    async with low_level_open(
        module, module.DeltaWebsocketsConnector(connect_factory=factory)
    ) as connection:
        with pytest.raises(ExchangeDownError) as error:
            if failure == "send_failure":
                await connection.send("safe-control")
            else:
                await connection.receiver().recv()
        assert error.value.context == {"operation": operation}
        assert "fixture-secret" not in str(error.value.to_dict())


@pytest.mark.asyncio
async def test_invalid_inbound_value_maps_to_protocol_error() -> None:
    module = transport()

    class InvalidRaw(RawConnection):
        async def recv(self) -> Any:
            return {"not": "a websocket frame"}

    async with low_level_open(
        module,
        module.DeltaWebsocketsConnector(connect_factory=ConnectFactory(InvalidRaw())),
    ) as connection:
        with pytest.raises(ExchangeProtocolError):
            await connection.receiver().recv()


@pytest.mark.asyncio
async def test_context_and_close_are_single_use_without_implicit_reconnect() -> None:
    module = transport()
    factory = ConnectFactory(RawConnection())
    context = low_level_open(module, module.DeltaWebsocketsConnector(connect_factory=factory))
    async with context as connection:
        await connection.close()
        await connection.close()
    with pytest.raises(ConfigurationError):
        await context.__aenter__()
    assert len(factory.calls) == 1


@pytest.mark.parametrize("max_size", (True, 0, 1024 * 1024 - 1, 16 * 1024 * 1024 + 1))
def test_connector_frame_limit_is_exactly_one_to_sixteen_mebibytes(max_size: object) -> None:
    with pytest.raises(ConfigurationError):
        transport().DeltaWebsocketsConnector(
            connect_factory=ConnectFactory(RawConnection()),
            max_size=max_size,
        )


def test_owned_transport_abort_invariant_failure_terminates_worker() -> None:
    script = r"""
import hmt_crypto.exchange.delta.websocket_transport as module
import hmt_crypto.exchange.delta.websocket_ownership as ownership

class Raw:
    def __init__(self):
        self.transport = self
    def abort(self):
        raise BaseException("abort-secret")

raw = Raw()
module._OFFICIAL_CONNECTION_TYPE = Raw
connection = module._DeltaWsConnection(
    raw,
    ownership_marker=module._PRODUCTION_CONNECTION_MARKER,
)
ownership._abort_owned_ws_connection(connection)
raise AssertionError("abort invariant returned")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )

    assert completed.returncode == 70
    assert "abort-secret" not in completed.stdout + completed.stderr
