"""Event-driven supervisor timer for the stream liveness owner."""

from __future__ import annotations

import asyncio
import copy
import inspect
import pickle
from uuid import UUID

import pytest

import hmt_crypto.market_data.public_stream_control as control_module
import hmt_crypto.market_data.stream_control_owner as owner_module
from hmt_crypto.exchange.delta.websocket_codec import encode_enable_heartbeat, encode_ping
from hmt_crypto.exchange.delta.websocket_contracts import WsPlane
from hmt_crypto.exchange.delta.websocket_transport import InboundKind
from hmt_crypto.market_data.public_stream_models import PublicConnectionGeneration
from hmt_crypto.market_data.stream_control_owner import (
    StreamControlOwner,
    StreamControlStateError,
)
from hmt_crypto.market_data.stream_liveness_runner import run_stream_liveness

PublicControlSession = control_module._PublicControlSession

SECOND = 1_000_000_000
GENERATION = UUID("10000000-0000-0000-0000-000000000001")


class ManualRuntime:
    def __init__(self) -> None:
        self.now = 0
        self.waiters: list[tuple[int, asyncio.Future[None]]] = []
        self.delays: list[int] = []

    def monotonic_ns(self) -> int:
        return self.now

    async def sleep_ns(self, delay_ns: int) -> None:
        self.delays.append(delay_ns)
        future = asyncio.get_running_loop().create_future()
        self.waiters.append((self.now + delay_ns, future))
        try:
            await future
        finally:
            self.waiters = [item for item in self.waiters if item[1] is not future]

    def advance(self, delay_ns: int) -> None:
        self.now += delay_ns
        for target, future in tuple(self.waiters):
            if target <= self.now and not future.done():
                future.set_result(None)


class ImmediateRuntime(ManualRuntime):
    async def sleep_ns(self, delay_ns: int) -> None:
        self.now += delay_ns
        await asyncio.sleep(0)


def bind_loop_clock(monkeypatch: pytest.MonkeyPatch, runtime: ManualRuntime) -> None:
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "time", lambda: runtime.now / SECOND)


class Connection:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)


class Sink:
    async def invalidate(self, _receipt: object) -> None:
        return None

    async def subscriptions_accepted(self, _receipt: object) -> None:
        return None


class Receiver:
    async def recv(self) -> str:
        return ""


class Refresh:
    async def request_catalogue_refresh(self, _request: object) -> None:
        return None


def control(runtime: ManualRuntime, connection: Connection) -> StreamControlOwner:
    return StreamControlOwner(
        _token=owner_module._OWNER_TOKEN,
        connection=connection,  # type: ignore[arg-type]
        sink=Sink(),  # type: ignore[arg-type]
        monotonic_ns=runtime.monotonic_ns,
        plane=WsPlane.PUBLIC,
        connection_generation=GENERATION,
    )


async def settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_observation_wakes_timer_and_ping_pong_deadline_closes_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ManualRuntime()
    connection = Connection()
    owner = control(runtime, connection)
    bind_loop_clock(monkeypatch, runtime)
    await owner.start()
    monitor = asyncio.create_task(run_stream_liveness(owner))
    await settle()
    runtime.advance(10 * SECOND)
    owner.observe_inbound(InboundKind.HEARTBEAT)
    await settle()

    runtime.advance(20 * SECOND)
    await settle()
    await monitor
    await owner.send_reserved_ping()
    assert connection.sent == [encode_enable_heartbeat(), encode_ping()]

    monitor = asyncio.create_task(run_stream_liveness(owner))
    await settle()
    runtime.advance(5 * SECOND)
    with pytest.raises(StreamControlStateError) as captured:
        await monitor
    assert captured.value.context["state"] == "close_pong_timeout"


@pytest.mark.asyncio
async def test_timely_pong_moves_back_to_heartbeat_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ManualRuntime()
    connection = Connection()
    owner = control(runtime, connection)
    bind_loop_clock(monkeypatch, runtime)
    await owner.start()
    monitor = asyncio.create_task(run_stream_liveness(owner))
    await settle()
    runtime.advance(30 * SECOND)
    await settle()
    await monitor
    await owner.send_reserved_ping()
    monitor = asyncio.create_task(run_stream_liveness(owner))
    await settle()
    runtime.advance(4 * SECOND)
    owner.observe_inbound(InboundKind.PONG)
    owner.observe_inbound(InboundKind.HEARTBEAT)
    await settle()
    assert not monitor.done()

    monitor.cancel()
    with pytest.raises(asyncio.CancelledError):
        await monitor


@pytest.mark.asyncio
async def test_cancellation_has_no_detached_timer_or_observation_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ManualRuntime()
    connection = Connection()
    owner = control(runtime, connection)
    bind_loop_clock(monkeypatch, runtime)
    await owner.start()
    monitor = asyncio.create_task(run_stream_liveness(owner))
    await settle()
    monitor.cancel()
    with pytest.raises(asyncio.CancelledError):
        await monitor
    await settle()
    live_children = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and not task.done()
    ]
    assert live_children == []


@pytest.mark.asyncio
async def test_loop_timer_wake_without_owner_clock_progress_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ManualRuntime()
    connection = Connection()
    owner = control(runtime, connection)
    loop_now = [0]
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "time", lambda: loop_now[0] / SECOND)
    await owner.start()
    monitor = asyncio.create_task(run_stream_liveness(owner))
    await settle()
    loop_now[0] = 30 * SECOND
    await settle()

    with pytest.raises(StreamControlStateError) as captured:
        await monitor
    assert captured.value.context["state"] == "timer_deadline_not_reached"


@pytest.mark.asyncio
async def test_runner_exposes_no_untrusted_sleep_dependency_or_cleanup_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "sleep_ns" not in inspect.signature(run_stream_liveness).parameters
    runtime = ManualRuntime()
    owner = control(runtime, Connection())
    bind_loop_clock(monkeypatch, runtime)
    await owner.start()
    monitor = asyncio.create_task(run_stream_liveness(owner))
    await settle()
    monitor.cancel()
    with pytest.raises(asyncio.CancelledError):
        await monitor
    live_runner_children = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and not task.done()
        and task.get_coro().__qualname__ == "_sleep_without_retaining_failure"
    ]
    assert live_runner_children == []


@pytest.mark.asyncio
async def test_invalid_dependencies_fail_before_creating_tasks() -> None:
    runtime = ManualRuntime()
    connection = Connection()
    owner = control(runtime, connection)
    await owner.start()
    with pytest.raises(TypeError):
        await run_stream_liveness(object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_ping_due_runner_returns_before_any_transport_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ManualRuntime()
    connection = Connection()
    owner = control(runtime, connection)
    bind_loop_clock(monkeypatch, runtime)
    await owner.start()
    monitor = asyncio.create_task(run_stream_liveness(owner))
    try:
        await settle()
        runtime.advance(30 * SECOND)
        await settle()

        assert monitor.done()
        assert connection.sent == [encode_enable_heartbeat()]
    finally:
        if not monitor.done():
            monitor.cancel()
        try:
            await monitor
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_generic_cancellation_resistant_awaitable_is_rejected_without_a_child() -> None:
    assert not hasattr(control_module, "PublicControlSession")
    runtime = ManualRuntime()
    owner = control(runtime, Connection())
    session = PublicControlSession(
        generation=PublicConnectionGeneration(
            connection_generation=GENERATION,
            region="india",
            environment="testnet",
            control_timeout_ns=SECOND,
            registry_poll_ns=SECOND,
            repair_delay_ns=5 * SECOND,
            finalization_timeout_ns=SECOND,
            max_frame_bytes=4096,
        ),
        receiver=Receiver(),  # type: ignore[arg-type]
        control=owner,
        catalogue_refresh=Refresh(),  # type: ignore[arg-type]
        monotonic_ns=runtime.monotonic_ns,
        sleep_ns=runtime.sleep_ns,
        production_owned=False,
    )
    completed = asyncio.create_task(asyncio.sleep(0))
    await completed
    session.bind_liveness_task(completed)
    started = False

    async def stubborn() -> None:
        nonlocal started
        started = True
        while True:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                continue

    with pytest.raises(TypeError, match="module-owned"):
        await session.bounded(stubborn(), deadline=SECOND)
    await settle()

    assert started is False
    live_stubborn = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and not task.done()
        and task.get_coro().__qualname__.endswith("stubborn")
    ]
    assert live_stubborn == []


@pytest.mark.asyncio
async def test_bounded_operation_handles_are_session_bound_single_use_and_not_forgeable() -> None:
    runtime = ImmediateRuntime()

    def new_session() -> PublicControlSession:
        return PublicControlSession(
            generation=PublicConnectionGeneration(
                connection_generation=GENERATION,
                region="india",
                environment="testnet",
                control_timeout_ns=SECOND,
                registry_poll_ns=SECOND,
                repair_delay_ns=5 * SECOND,
                finalization_timeout_ns=SECOND,
                max_frame_bytes=4096,
            ),
            receiver=Receiver(),  # type: ignore[arg-type]
            control=control(runtime, Connection()),
            catalogue_refresh=Refresh(),  # type: ignore[arg-type]
            monotonic_ns=runtime.monotonic_ns,
            sleep_ns=runtime.sleep_ns,
            production_owned=False,
        )

    first = new_session()
    second = new_session()
    forged = control_module._BoundedOperation(control_module._OPERATION_TOKEN)
    with pytest.raises(TypeError, match="module-owned"):
        await first.bounded(forged, deadline=SECOND)

    owned = control_module._issue_operation(
        first,
        control_module._OperationKind.SLEEP,
        1,
    )
    with pytest.raises(TypeError, match="cannot be copied"):
        copy.copy(owned)
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(owned)
    await first.bounded(owned, deadline=SECOND)
    with pytest.raises(TypeError, match="module-owned"):
        await first.bounded(owned, deadline=SECOND)

    cross_session = control_module._issue_operation(
        first,
        control_module._OperationKind.SLEEP,
        1,
    )
    with pytest.raises(TypeError, match="module-owned"):
        await second.bounded(cross_session, deadline=SECOND)
    with pytest.raises(TypeError, match="module-owned"):
        await first.bounded(cross_session, deadline=SECOND)
