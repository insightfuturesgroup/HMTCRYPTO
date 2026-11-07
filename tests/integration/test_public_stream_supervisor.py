"""Physical-entry integration for governed public WebSocket generations."""

from __future__ import annotations

import asyncio
import importlib
from datetime import UTC, datetime, timedelta
from types import TracebackType
from uuid import uuid4

import pytest
from public_stream_failure_support import ManualTime, ack, generation, harness, settle
from support.fake_websocket import FakeConnectFactory, FakeWebSocket

import hmt_crypto.exchange.delta.websocket_transport as websocket_transport
import hmt_crypto.market_data.connection_attempts as attempts_module
from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.errors import ExchangeDownError
from hmt_crypto.exchange.delta.rate_limit_policy import RateLimitScope, ScopeKind
from hmt_crypto.exchange.delta.websocket_contracts import WsChannel, WsEndpoint, WsPlane
from hmt_crypto.exchange.delta.websocket_subscriptions import (
    WsSubscription,
)
from hmt_crypto.exchange.delta.websocket_transport import (
    DeltaWebsocketsConnector,
    WebSocketHandshakeCategory,
    WebSocketHandshakeError,
)
from hmt_crypto.market_data.connection_attempts import ConnectionAttemptGovernor
from hmt_crypto.market_data.public_stream import PublicGenerationStatus
from hmt_crypto.market_data.stream_evidence_mailbox import StreamEvidenceMailbox

pytest_plugins = ("tests.integration.support.task210_worker_clock_issuer",)

_DEPLOYMENT = "phase3-public-stream"
_ISSUED = datetime(2026, 11, 6, 10, 0, tzinfo=UTC)


class AttemptTime:
    def __init__(self) -> None:
        self.now = 0

    def monotonic_ns(self) -> int:
        return self.now

    async def sleep_ns(self, delay_ns: int) -> None:
        self.now += delay_ns


class FailedAttemptTime(AttemptTime):
    async def sleep_ns(self, _delay_ns: int) -> None:
        raise BaseException("attempt-dependency-secret")


Sink = StreamEvidenceMailbox


class GuardedConnectFactory(FakeConnectFactory):
    def __init__(self, connection: FakeWebSocket, attempts: ConnectionAttemptGovernor) -> None:
        super().__init__(connection)
        self.attempts = attempts

    def __call__(self, url: str, **options: object):  # type: ignore[no-untyped-def]
        assert self.attempts.consumed_attempt_count == 1
        return super().__call__(url, **options)


class BlockingWebSocket:
    def __init__(self, first_frame: str) -> None:
        self.first_frame: str | None = first_frame
        self.sent: list[str] = []
        self.release = asyncio.Event()
        self.closed = False
        self.transport = self

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if self.first_frame is not None:
            frame = self.first_frame
            self.first_frame = None
            return frame
        await self.release.wait()
        raise EOFError

    async def close(self, code: int = 1000, reason: str = "") -> None:
        del code, reason
        self.closed = True
        self.release.set()

    def abort(self) -> None:
        self.closed = True
        self.release.set()


class BlockingSendWebSocket(BlockingWebSocket):
    def __init__(self) -> None:
        super().__init__("")
        self.send_calls = 0
        self.send_blocked = asyncio.Event()
        self.send_cancellations = 0
        self.active_sends = 0

    async def send(self, message: str) -> None:
        self.send_calls += 1
        if self.send_calls == 1:
            self.sent.append(message)
            return
        self.send_blocked.set()
        self.active_sends += 1
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.send_cancellations += 1
            raise
        finally:
            self.active_sends -= 1


class CancellationAbsorbingWebSocket(BlockingSendWebSocket):
    """A hostile raw send that stops only when its transport is aborted."""

    def __init__(self) -> None:
        super().__init__()
        self.transport = self
        self.abort_calls = 0

    async def send(self, message: str) -> None:
        self.send_calls += 1
        if self.send_calls == 1:
            self.sent.append(message)
            return
        self.send_blocked.set()
        self.active_sends += 1
        try:
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.send_cancellations += 1
        finally:
            self.active_sends -= 1

    def abort(self) -> None:
        self.abort_calls += 1
        self.release.set()


class FatalDependency(BaseException):
    """Hostile dependency failure outside ``Exception``."""


class FailedContext:
    def __init__(self, failure: BaseException) -> None:
        self.failure = failure

    async def __aenter__(self) -> object:
        raise self.failure

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None


class FailedConnector:
    def __init__(self, failure: BaseException) -> None:
        self.failure = failure
        self.opens = 0

    def open(self, _endpoint: object) -> FailedContext:
        self.opens += 1
        return FailedContext(self.failure)


class RaisingConnectFactory:
    def __init__(self, failure: BaseException) -> None:
        self.failure = failure
        self.calls = 0

    def __call__(self, _url: str, **_options: object) -> FailedContext:
        self.calls += 1
        raise self.failure


def _owned_connector(
    monkeypatch: pytest.MonkeyPatch,
    factory: object,
) -> DeltaWebsocketsConnector:
    monkeypatch.setattr(websocket_transport, "_OFFICIAL_CONNECT_FACTORY", factory)
    connection = getattr(factory, "connection", None)
    if connection is not None:
        monkeypatch.setattr(websocket_transport, "_OFFICIAL_CONNECTION_TYPE", type(connection))
    return DeltaWebsocketsConnector()


class HostileConnector:
    def __getattribute__(self, name: str) -> object:
        if name == "open":
            raise FatalDependency("connector-getattr-secret")
        return object.__getattribute__(self, name)

    def __repr__(self) -> str:
        return "<connector-repr-secret>"


class SideEffectConnector:
    def __init__(self) -> None:
        self.pre_admission_side_effects = 0

    @property
    def open(self):  # type: ignore[no-untyped-def]
        self.pre_admission_side_effects += 1
        return lambda _endpoint: FailedContext(AssertionError("descriptor-open"))


class FanoutConnectFactory:
    def __init__(self) -> None:
        self.calls = 0
        self.physical_open_side_effects = 0

    def __call__(self, _url: str, **_options: object) -> FailedContext:
        self.calls += 1
        self.physical_open_side_effects += 2
        return FailedContext(AssertionError("fanout-connect"))


class HostileHandshake(WebSocketHandshakeError):
    @property
    def category(self) -> WebSocketHandshakeCategory:
        raise FatalDependency("handshake-property-secret")


def _production_trace(error: BaseException) -> str:
    rendered: list[str] = []
    seen: set[int] = set()
    pending: list[BaseException | None] = [error]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        rendered.extend((str(current), repr(current), repr(current.args)))
        traceback = current.__traceback__
        while traceback is not None:
            module = str(traceback.tb_frame.f_globals.get("__name__", ""))
            if module.startswith("hmt_crypto"):
                rendered.extend(
                    f"{name}={value!r}" for name, value in traceback.tb_frame.f_locals.items()
                )
            traceback = traceback.tb_next
        pending.extend((current.__cause__, current.__context__))
    return "\n".join(rendered)


def _attempt_owner(
    marker: object,
    time: AttemptTime,
    monkeypatch: pytest.MonkeyPatch,
) -> ConnectionAttemptGovernor:
    support = importlib.import_module("tests.integration.support.task210_worker_clock_issuer")
    proof, currentness = support.issue_single_private_worker(
        marker,
        proof_id=uuid4(),
        deployment_revision=_DEPLOYMENT,
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
    monkeypatch.setattr(attempts_module, "_system_monotonic_ns", time.monotonic_ns)
    monkeypatch.setattr(attempts_module, "_system_sleep_ns", time.sleep_ns)
    return ConnectionAttemptGovernor.for_single_process_egress(
        RateLimitScope(
            region="india",
            environment="testnet",
            kind=ScopeKind.IP,
            external_id="configured-test-egress",
        ),
        ownership_proof=proof,
        ownership_currentness=currentness,
    )


@pytest.mark.asyncio
async def test_physical_entry_consumes_attempt_then_runs_owned_liveness_without_network(
    task210_worker_clock_marker: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(attempts_module, "_PROCESS_OWNER_REF", None)
    attempts_module._GOVERNOR_STATES.clear()
    actor_time = ManualTime()
    actor, registry, _state = harness(actor_time)
    ticker = WsSubscription.for_channel(WsChannel.TICKER, symbols=("XBTUSD_CAT",))
    lease = await registry.acquire(owner_id=uuid4(), entries=(ticker,))
    attempt_time = AttemptTime()
    attempts = _attempt_owner(task210_worker_clock_marker, attempt_time, monkeypatch)
    raw = FakeWebSocket((ack(ticker),))
    connect_factory = GuardedConnectFactory(raw, attempts)
    connector = _owned_connector(monkeypatch, connect_factory)
    sink = Sink()
    endpoint = WsEndpoint(
        plane=WsPlane.PUBLIC,
        url="wss://socket-ind-pub.testnet.deltaex.org",
        region="india",
        environment="testnet",
    )
    method = getattr(actor, "run_physical_generation", None)
    assert callable(method)
    assert not hasattr(actor, "run_generation")
    assert not hasattr(actor, "_run_generation_for_tests")

    result = await method(
        generation(),
        connector=connector,
        endpoint=endpoint,
        attempts=attempts,
        sink=sink,
    )
    await lease.aclose()

    assert result.status is PublicGenerationStatus.RECEIVER_ENDED
    assert attempts.consumed_attempt_count == 1
    assert len(connect_factory.calls) == 1
    assert raw.closed is True
    assert raw.sent[0] == '{"type":"enable_heartbeat"}'
    assert any('"type":"subscribe"' in message for message in raw.sent)
    assert len(sink.receipts) == 1
    assert sink.invalidations


@pytest.mark.asyncio
async def test_attempt_admission_failure_prevents_connector_use_and_sanitizes_dependency(
    task210_worker_clock_marker: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(attempts_module, "_PROCESS_OWNER_REF", None)
    attempts_module._GOVERNOR_STATES.clear()
    actor, _registry, _state = harness(ManualTime())
    attempts = _attempt_owner(task210_worker_clock_marker, FailedAttemptTime(), monkeypatch)
    factory = RaisingConnectFactory(AssertionError("connector-must-not-open"))
    connector = _owned_connector(monkeypatch, factory)
    endpoint = WsEndpoint(
        plane=WsPlane.PUBLIC,
        url="wss://socket-ind-pub.testnet.deltaex.org",
        region="india",
        environment="testnet",
    )

    with pytest.raises(ExchangeDownError) as captured:
        await actor.run_physical_generation(
            generation(),
            connector=connector,
            endpoint=endpoint,
            attempts=attempts,
            sink=Sink(),
        )

    assert factory.calls == 0
    assert captured.value.context["operation"] == "websocket_attempt_admission"
    assert "attempt-dependency-secret" not in _production_trace(captured.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure, canary",
    [
        (FatalDependency("physical-open-secret"), "physical-open-secret"),
        (
            HostileHandshake(
                category=WebSocketHandshakeCategory.NETWORK,
                status_code=None,
            ),
            "handshake-property-secret",
        ),
    ],
)
async def test_physical_open_dependency_failures_are_sanitized(
    task210_worker_clock_marker: object,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
    canary: str,
) -> None:
    monkeypatch.setattr(attempts_module, "_PROCESS_OWNER_REF", None)
    attempts_module._GOVERNOR_STATES.clear()
    actor, _registry, _state = harness(ManualTime())
    attempts = _attempt_owner(task210_worker_clock_marker, AttemptTime(), monkeypatch)
    factory = RaisingConnectFactory(failure)
    connector = _owned_connector(monkeypatch, factory)
    endpoint = WsEndpoint(
        plane=WsPlane.PUBLIC,
        url="wss://socket-ind-pub.testnet.deltaex.org",
        region="india",
        environment="testnet",
    )

    with pytest.raises(ExchangeDownError) as captured:
        await actor.run_physical_generation(
            generation(),
            connector=connector,
            endpoint=endpoint,
            attempts=attempts,
            sink=Sink(),
        )

    assert attempts.consumed_attempt_count == 1
    assert factory.calls == 1
    assert canary not in _production_trace(captured.value)


@pytest.mark.asyncio
async def test_hostile_connector_validation_does_not_retain_or_render_dependency(
    task210_worker_clock_marker: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(attempts_module, "_PROCESS_OWNER_REF", None)
    attempts_module._GOVERNOR_STATES.clear()
    actor, _registry, _state = harness(ManualTime())
    attempts = _attempt_owner(task210_worker_clock_marker, AttemptTime(), monkeypatch)
    endpoint = WsEndpoint(
        plane=WsPlane.PUBLIC,
        url="wss://socket-ind-pub.testnet.deltaex.org",
        region="india",
        environment="testnet",
    )

    with pytest.raises(TypeError) as captured:
        await actor.run_physical_generation(
            generation(),
            connector=HostileConnector(),  # type: ignore[arg-type]
            endpoint=endpoint,
            attempts=attempts,
            sink=Sink(),
        )

    rendered = _production_trace(captured.value)
    assert "connector-getattr-secret" not in rendered
    assert "connector-repr-secret" not in rendered
    assert attempts.consumed_attempt_count == 0


@pytest.mark.asyncio
async def test_connector_descriptor_cannot_run_before_attempt_admission(
    task210_worker_clock_marker: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(attempts_module, "_PROCESS_OWNER_REF", None)
    attempts_module._GOVERNOR_STATES.clear()
    actor, _registry, _state = harness(ManualTime())
    attempts = _attempt_owner(task210_worker_clock_marker, AttemptTime(), monkeypatch)
    connector = SideEffectConnector()
    endpoint = WsEndpoint(
        plane=WsPlane.PUBLIC,
        url="wss://socket-ind-pub.testnet.deltaex.org",
        region="india",
        environment="testnet",
    )

    with pytest.raises(TypeError):
        await actor.run_physical_generation(
            generation(),
            connector=connector,  # type: ignore[arg-type]
            endpoint=endpoint,
            attempts=attempts,
            sink=Sink(),
        )

    assert connector.pre_admission_side_effects == 0
    assert attempts.consumed_attempt_count == 0


@pytest.mark.asyncio
async def test_injected_exact_connector_cannot_fan_out_one_admitted_open(
    task210_worker_clock_marker: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert not hasattr(websocket_transport, "_PHYSICAL_ENTRY_CONNECTORS")
    assert not hasattr(websocket_transport, "_OWNED_CONNECTIONS")
    monkeypatch.setattr(attempts_module, "_PROCESS_OWNER_REF", None)
    attempts_module._GOVERNOR_STATES.clear()
    actor, _registry, _state = harness(ManualTime())
    attempts = _attempt_owner(task210_worker_clock_marker, AttemptTime(), monkeypatch)
    factory = FanoutConnectFactory()
    connector = DeltaWebsocketsConnector(connect_factory=factory)  # type: ignore[arg-type]
    endpoint = WsEndpoint(
        plane=WsPlane.PUBLIC,
        url="wss://socket-ind-pub.testnet.deltaex.org",
        region="india",
        environment="testnet",
    )

    with pytest.raises(TypeError):
        await actor.run_physical_generation(
            generation(),
            connector=connector,
            endpoint=endpoint,
            attempts=attempts,
            sink=Sink(),
        )

    assert attempts.consumed_attempt_count == 0
    assert factory.calls == 0
    assert factory.physical_open_side_effects == 0


@pytest.mark.asyncio
async def test_physical_open_cancellation_is_reissued_without_raw_reason(
    task210_worker_clock_marker: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(attempts_module, "_PROCESS_OWNER_REF", None)
    attempts_module._GOVERNOR_STATES.clear()
    actor, _registry, _state = harness(ManualTime())
    attempts = _attempt_owner(task210_worker_clock_marker, AttemptTime(), monkeypatch)
    endpoint = WsEndpoint(
        plane=WsPlane.PUBLIC,
        url="wss://socket-ind-pub.testnet.deltaex.org",
        region="india",
        environment="testnet",
    )

    with pytest.raises(asyncio.CancelledError) as captured:
        await actor.run_physical_generation(
            generation(),
            connector=_owned_connector(
                monkeypatch,
                RaisingConnectFactory(asyncio.CancelledError("cancel-secret")),
            ),
            endpoint=endpoint,
            attempts=attempts,
            sink=Sink(),
        )

    assert "cancel-secret" not in _production_trace(captured.value)


@pytest.mark.asyncio
async def test_liveness_terminal_wakes_actor_with_receiver_and_poll_still_pending(
    task210_worker_clock_marker: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(attempts_module, "_PROCESS_OWNER_REF", None)
    attempts_module._GOVERNOR_STATES.clear()
    actor_time = ManualTime()
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "time", lambda: actor_time.now / 1_000_000_000)
    actor, registry, _state = harness(actor_time)
    ticker = WsSubscription.for_channel(WsChannel.TICKER, symbols=("XBTUSD_CAT",))
    lease = await registry.acquire(owner_id=uuid4(), entries=(ticker,))
    attempts = _attempt_owner(task210_worker_clock_marker, AttemptTime(), monkeypatch)
    raw = BlockingWebSocket(ack(ticker))
    connector = _owned_connector(monkeypatch, FakeConnectFactory(raw))
    sink = Sink()
    endpoint = WsEndpoint(
        plane=WsPlane.PUBLIC,
        url="wss://socket-ind-pub.testnet.deltaex.org",
        region="india",
        environment="testnet",
    )
    running = asyncio.create_task(
        actor.run_physical_generation(
            generation(),
            connector=connector,
            endpoint=endpoint,
            attempts=attempts,
            sink=sink,
        )
    )
    try:
        for _ in range(100):
            if sink.receipts:
                break
            await asyncio.sleep(0)
        assert sink.receipts
        actor_time.now += 30_000_000_000
        await settle()
        assert '{"type":"ping"}' in raw.sent
        assert not running.done()

        actor_time.now += 5_000_000_000
        await settle()

        assert running.done()
        result = await running
        assert result.status is PublicGenerationStatus.CONTROL_TIMEOUT
    finally:
        raw.release.set()
        if not running.done():
            running.cancel()
        try:
            await running
        except asyncio.CancelledError:
            pass
        await lease.aclose()


@pytest.mark.asyncio
async def test_actor_cancellation_drains_liveness_child_before_return(
    task210_worker_clock_marker: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(attempts_module, "_PROCESS_OWNER_REF", None)
    attempts_module._GOVERNOR_STATES.clear()
    actor_time = ManualTime()
    actor, registry, _state = harness(actor_time)
    ticker = WsSubscription.for_channel(WsChannel.TICKER, symbols=("XBTUSD_CAT",))
    lease = await registry.acquire(owner_id=uuid4(), entries=(ticker,))
    attempts = _attempt_owner(task210_worker_clock_marker, AttemptTime(), monkeypatch)
    raw = BlockingWebSocket(ack(ticker))
    connector = _owned_connector(monkeypatch, FakeConnectFactory(raw))
    sink = Sink()
    endpoint = WsEndpoint(
        plane=WsPlane.PUBLIC,
        url="wss://socket-ind-pub.testnet.deltaex.org",
        region="india",
        environment="testnet",
    )
    running = asyncio.create_task(
        actor.run_physical_generation(
            generation(),
            connector=connector,
            endpoint=endpoint,
            attempts=attempts,
            sink=sink,
        )
    )
    for _ in range(100):
        if sink.receipts:
            break
        await asyncio.sleep(0)
    assert sink.receipts

    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    await settle()

    live_liveness = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and not task.done()
        and task.get_coro().__qualname__ == "run_stream_liveness"
    ]
    assert live_liveness == []
    assert raw.closed is True
    await lease.aclose()


@pytest.mark.asyncio
async def test_terminal_liveness_wakes_a_bounded_control_send(
    task210_worker_clock_marker: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(attempts_module, "_PROCESS_OWNER_REF", None)
    attempts_module._GOVERNOR_STATES.clear()
    actor_time = ManualTime()
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "time", lambda: actor_time.now / 1_000_000_000)
    actor, registry, _state = harness(actor_time)
    ticker = WsSubscription.for_channel(WsChannel.TICKER, symbols=("XBTUSD_CAT",))
    lease = await registry.acquire(owner_id=uuid4(), entries=(ticker,))
    attempts = _attempt_owner(task210_worker_clock_marker, AttemptTime(), monkeypatch)
    raw = BlockingSendWebSocket()
    connector = _owned_connector(monkeypatch, FakeConnectFactory(raw))
    endpoint = WsEndpoint(
        plane=WsPlane.PUBLIC,
        url="wss://socket-ind-pub.testnet.deltaex.org",
        region="india",
        environment="testnet",
    )
    running = asyncio.create_task(
        actor.run_physical_generation(
            generation(),
            connector=connector,
            endpoint=endpoint,
            attempts=attempts,
            sink=Sink(),
        )
    )
    try:
        await asyncio.wait_for(raw.send_blocked.wait(), timeout=0.2)
        actor_time.now += 30_000_000_000
        await settle()

        assert running.done()
        assert (await running).status is PublicGenerationStatus.CONTROL_TIMEOUT
        assert raw.send_cancellations == 1
        assert raw.active_sends == 0
    finally:
        raw.release.set()
        if not running.done():
            running.cancel()
        try:
            await running
        except asyncio.CancelledError:
            pass
        await lease.aclose()


@pytest.mark.asyncio
async def test_terminal_abort_drains_a_cancellation_absorbing_owned_send(
    task210_worker_clock_marker: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(attempts_module, "_PROCESS_OWNER_REF", None)
    attempts_module._GOVERNOR_STATES.clear()
    actor_time = ManualTime()
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "time", lambda: actor_time.now / 1_000_000_000)
    actor, registry, _state = harness(actor_time)
    ticker = WsSubscription.for_channel(WsChannel.TICKER, symbols=("XBTUSD_CAT",))
    lease = await registry.acquire(owner_id=uuid4(), entries=(ticker,))
    attempts = _attempt_owner(task210_worker_clock_marker, AttemptTime(), monkeypatch)
    raw = CancellationAbsorbingWebSocket()
    connector = _owned_connector(monkeypatch, FakeConnectFactory(raw))
    endpoint = WsEndpoint(
        plane=WsPlane.PUBLIC,
        url="wss://socket-ind-pub.testnet.deltaex.org",
        region="india",
        environment="testnet",
    )
    running = asyncio.create_task(
        actor.run_physical_generation(
            generation(),
            connector=connector,
            endpoint=endpoint,
            attempts=attempts,
            sink=Sink(),
        )
    )
    try:
        await asyncio.wait_for(raw.send_blocked.wait(), timeout=0.2)
        actor_time.now += 30_000_000_000
        await settle()

        assert running.done()
        assert (await running).status is PublicGenerationStatus.CONTROL_TIMEOUT
        assert raw.abort_calls == 1
        assert raw.send_cancellations == 1
        assert raw.active_sends == 0
    finally:
        raw.release.set()
        if not running.done():
            running.cancel()
        try:
            await running
        except asyncio.CancelledError:
            pass
        await lease.aclose()


@pytest.mark.asyncio
async def test_exact_connector_entry_requires_and_consumes_attempt_admission(
    task210_worker_clock_marker: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exported physical connector must not bypass the shared governor."""
    monkeypatch.setattr(attempts_module, "_PROCESS_OWNER_REF", None)
    attempts_module._GOVERNOR_STATES.clear()
    attempt_time = AttemptTime()
    attempts = _attempt_owner(task210_worker_clock_marker, attempt_time, monkeypatch)
    factory = FakeConnectFactory(FakeWebSocket())
    connector = _owned_connector(monkeypatch, factory)
    endpoint = WsEndpoint(
        plane=WsPlane.PUBLIC,
        url="wss://socket-ind-pub.testnet.deltaex.org",
        region="india",
        environment="testnet",
    )

    with pytest.raises(TypeError):
        connector.open(endpoint)

    first = connector.open(endpoint, attempts=attempts)
    assert attempts.consumed_attempt_count == 0
    assert factory.calls == []
    async with first:
        assert attempts.consumed_attempt_count == 1
        assert len(factory.calls) == 1

    second = connector.open(endpoint, attempts=attempts)
    assert attempts.consumed_attempt_count == 1
    async with second:
        assert attempts.consumed_attempt_count == 2
        assert len(factory.calls) == 2
