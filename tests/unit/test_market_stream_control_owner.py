"""Supervisor-owned serialization of WebSocket control and liveness."""

from __future__ import annotations

import asyncio
import copy
import pickle
from uuid import UUID

import pytest

import hmt_crypto.market_data.stream_control_owner as owner_module
from hmt_crypto.errors import ExchangeDownError
from hmt_crypto.exchange.delta.websocket_codec import encode_enable_heartbeat, encode_ping
from hmt_crypto.exchange.delta.websocket_contracts import WsPlane
from hmt_crypto.exchange.delta.websocket_subscriptions import (
    GenerationControlInvalidation,
    GenerationSubscriptionReceipt,
    StreamInvalidationKind,
)
from hmt_crypto.exchange.delta.websocket_transport import InboundKind
from hmt_crypto.market_data.stream_control_owner import (
    StreamControlOwner,
    StreamControlStateError,
)
from hmt_crypto.market_data.stream_liveness import StreamLivenessAction

SECOND = 1_000_000_000
GENERATION = UUID("10000000-0000-0000-0000-000000000001")


class ManualTime:
    def __init__(self) -> None:
        self.now = 0

    def monotonic_ns(self) -> int:
        return self.now


class Connection:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.send_gate: asyncio.Event | None = None
        self.send_started = asyncio.Event()
        self.fail = False
        self.unsafe_fail = False
        self.active = 0
        self.max_active = 0

    async def send(self, message: str) -> None:
        self.send_started.set()
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.send_gate is not None:
                await self.send_gate.wait()
            if self.fail:
                raise ExchangeDownError(context={"operation": "fixture_send"})
            if self.unsafe_fail:
                raise RuntimeError("fixture-secret-transport")
            self.sent.append(message)
        finally:
            self.active -= 1


class Sink:
    def __init__(self) -> None:
        self.invalidations: list[GenerationControlInvalidation] = []
        self.receipts: list[GenerationSubscriptionReceipt] = []
        self.fail = False

    async def invalidate(self, receipt: GenerationControlInvalidation) -> None:
        if self.fail:
            raise RuntimeError("fixture-secret-sink")
        self.invalidations.append(receipt)

    async def subscriptions_accepted(self, receipt: GenerationSubscriptionReceipt) -> None:
        if self.fail:
            raise RuntimeError("fixture-secret-sink")
        self.receipts.append(receipt)


class SyncFailConnection:
    def send(self, _message: str) -> object:
        raise RuntimeError("fixture-secret-sync-send")


class SyncFailSink:
    def invalidate(self, _receipt: GenerationControlInvalidation) -> object:
        raise RuntimeError("fixture-secret-sync-sink")

    def subscriptions_accepted(self, _receipt: GenerationSubscriptionReceipt) -> object:
        raise RuntimeError("fixture-secret-sync-sink")


class FatalDependency(BaseException):
    """A dependency failure that ordinary ``Exception`` handlers miss."""


class FatalAfterStartConnection(Connection):
    async def send(self, message: str) -> None:
        if self.sent:
            raise FatalDependency("fixture-fatal-send-secret")
        await super().send(message)


class HostileConstructorDependency:
    def __getattribute__(self, name: str) -> object:
        if name in {"send", "invalidate", "subscriptions_accepted"}:
            raise FatalDependency("fixture-constructor-getattr-secret")
        return object.__getattribute__(self, name)

    def __repr__(self) -> str:
        return "<fixture-constructor-repr-secret>"


class HostileEvidence:
    def __repr__(self) -> str:
        return "<fixture-evidence-repr-secret>"


def owner(
    time: ManualTime,
    connection: Connection | None = None,
    sink: Sink | None = None,
    *,
    max_pending_observations: int = 4096,
) -> StreamControlOwner:
    return StreamControlOwner(
        _token=owner_module._OWNER_TOKEN,
        connection=connection or Connection(),
        sink=sink or Sink(),
        monotonic_ns=time.monotonic_ns,
        plane=WsPlane.PUBLIC,
        connection_generation=GENERATION,
        max_pending_observations=max_pending_observations,
    )


def receipt(
    *, control_revision: int = 1, desired_revision: int = 1
) -> GenerationSubscriptionReceipt:
    return GenerationSubscriptionReceipt(
        plane=WsPlane.PUBLIC,
        connection_generation=GENERATION,
        control_revision=control_revision,
        desired_revision=desired_revision,
        evidence_sha256="a" * 64,
        accepted_at_monotonic_ns=0,
    )


def invalidation(*, local_sequence: int = 1) -> GenerationControlInvalidation:
    return GenerationControlInvalidation(
        plane=WsPlane.PUBLIC,
        connection_generation=GENERATION,
        local_sequence=local_sequence,
        kind=StreamInvalidationKind.PROTOCOL_DRIFT,
        evidence_sha256="a" * 64,
    )


def traceback_rendering(error: BaseException) -> str:
    rendered: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        rendered.extend((str(current), repr(current)))
        traceback = current.__traceback__
        while traceback is not None:
            if "/src/hmt_crypto/" in traceback.tb_frame.f_code.co_filename:
                for name, value in traceback.tb_frame.f_locals.items():
                    rendered.append(name)
                    try:
                        rendered.append(repr(value))
                    except Exception:
                        rendered.append("<unprintable>")
            traceback = traceback.tb_next
        current = current.__cause__ or current.__context__
    return "\n".join(rendered)


@pytest.mark.asyncio
async def test_start_sends_heartbeat_before_initializing_liveness_once() -> None:
    time = ManualTime()
    connection = Connection()
    control = owner(time, connection)

    await control.start()

    assert connection.sent == [encode_enable_heartbeat()]
    assert (await control.snapshot()).started_at_ns == 0
    with pytest.raises(StreamControlStateError):
        await control.start()


@pytest.mark.asyncio
async def test_inbound_uses_observation_time_not_later_tick_time() -> None:
    time = ManualTime()
    control = owner(time)
    await control.start()
    time.now = 30 * SECOND
    control.observe_inbound(InboundKind.HEARTBEAT)
    time.now = 34 * SECOND

    decision = await control.tick()

    assert decision.action is StreamLivenessAction.SEND_PING
    state = await control.snapshot()
    assert state.last_heartbeat_at_ns == 30 * SECOND
    assert state.pending_ping_at_ns == 34 * SECOND


@pytest.mark.asyncio
async def test_late_observation_cannot_resurrect_expired_connection() -> None:
    time = ManualTime()
    control = owner(time)
    await control.start()
    time.now = 36 * SECOND
    control.observe_inbound(InboundKind.HEARTBEAT)

    with pytest.raises(StreamControlStateError) as captured:
        await control.tick()
    assert captured.value.context["state"] == "close_heartbeat_timeout"
    with pytest.raises(StreamControlStateError):
        await control.snapshot()


@pytest.mark.asyncio
async def test_ping_reservation_precedes_send_and_fast_pong_is_queued_safely() -> None:
    time = ManualTime()
    connection = Connection()
    connection.send_gate = asyncio.Event()
    control = owner(time, connection)
    connection.send_gate.set()
    await control.start()
    connection.send_gate.clear()
    connection.send_started.clear()
    time.now = 30 * SECOND

    decision = await control.tick()
    assert decision.action is StreamLivenessAction.SEND_PING
    ping = asyncio.create_task(control.send_reserved_ping())
    await connection.send_started.wait()
    time.now = 31 * SECOND
    control.observe_inbound(InboundKind.PONG)
    connection.send_gate.set()
    await ping
    assert control.pending_observation_count == 1

    settled = await control.tick()
    assert settled.action is StreamLivenessAction.NONE
    state = await control.snapshot()
    assert state.pending_ping_at_ns is None
    assert state.last_pong_at_ns == 31 * SECOND
    assert connection.sent[-1] == encode_ping()


@pytest.mark.asyncio
async def test_actor_control_send_and_ping_are_physically_serialized() -> None:
    time = ManualTime()
    connection = Connection()
    connection.send_gate = asyncio.Event()
    connection.send_gate.set()
    control = owner(time, connection)
    await control.start()
    connection.send_gate.clear()
    connection.send_started.clear()
    time.now = 30 * SECOND
    assert (await control.tick()).action is StreamLivenessAction.SEND_PING

    actor_send = asyncio.create_task(control.send('{"type":"subscribe"}'))
    await connection.send_started.wait()
    ping = asyncio.create_task(control.send_reserved_ping())
    await asyncio.sleep(0)
    assert connection.max_active == 1
    connection.send_gate.set()
    await asyncio.gather(actor_send, ping)
    assert connection.max_active == 1
    assert connection.sent[-2:] == ['{"type":"subscribe"}', encode_ping()]


@pytest.mark.asyncio
async def test_ping_send_failure_breaks_owner_before_error_escapes() -> None:
    time = ManualTime()
    connection = Connection()
    control = owner(time, connection)
    await control.start()
    time.now = 30 * SECOND
    connection.fail = True
    await control.tick()

    with pytest.raises(ExchangeDownError):
        await control.send_reserved_ping()
    with pytest.raises(StreamControlStateError) as captured:
        await control.send("later")
    assert captured.value.context["state"] == "broken"


@pytest.mark.asyncio
async def test_unknown_transport_failure_is_sanitized_without_exception_context() -> None:
    time = ManualTime()
    connection = Connection()
    control = owner(time, connection)
    await control.start()
    connection.unsafe_fail = True

    secret = "control-message-secret-canary"
    with pytest.raises(ExchangeDownError) as captured:
        await control.send(secret)
    rendered = traceback_rendering(captured.value)
    assert secret not in rendered
    assert "fixture-secret" not in rendered


@pytest.mark.asyncio
async def test_non_cancellation_base_exception_is_sanitized_and_breaks_owner() -> None:
    time = ManualTime()
    connection = FatalAfterStartConnection()
    control = owner(time, connection)
    await control.start()
    raw_message = "raw-control-secret"

    with pytest.raises(ExchangeDownError) as captured:
        await control.send(raw_message)

    rendered = traceback_rendering(captured.value)
    assert raw_message not in rendered
    assert "fixture-fatal-send-secret" not in rendered
    with pytest.raises(StreamControlStateError) as broken:
        await control.snapshot()
    assert broken.value.context["state"] == "broken"


@pytest.mark.asyncio
async def test_synchronous_transport_failure_is_sanitized_too() -> None:
    time = ManualTime()
    control = StreamControlOwner(
        _token=owner_module._OWNER_TOKEN,
        connection=SyncFailConnection(),  # type: ignore[arg-type]
        sink=Sink(),
        monotonic_ns=time.monotonic_ns,
        plane=WsPlane.PUBLIC,
        connection_generation=GENERATION,
    )
    with pytest.raises(ExchangeDownError) as captured:
        await control.start()
    assert "fixture-secret" not in repr(captured.value.__context__)


@pytest.mark.asyncio
async def test_cancelled_ping_send_breaks_owner_and_never_commits_late_pong() -> None:
    time = ManualTime()
    connection = Connection()
    connection.send_gate = asyncio.Event()
    connection.send_gate.set()
    control = owner(time, connection)
    await control.start()
    connection.send_gate.clear()
    connection.send_started.clear()
    time.now = 30 * SECOND
    await control.tick()
    task = asyncio.create_task(control.send_reserved_ping())
    await connection.send_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    time.now = 31 * SECOND
    control.observe_inbound(InboundKind.PONG)
    with pytest.raises(StreamControlStateError) as captured:
        await control.tick()
    assert captured.value.context["state"] == "broken"


@pytest.mark.asyncio
async def test_pending_observation_bound_fails_closed_without_unbounded_growth() -> None:
    time = ManualTime()
    control = owner(time, max_pending_observations=2)
    await control.start()
    control.observe_inbound(InboundKind.DATA)
    control.observe_inbound(InboundKind.DATA)
    control.observe_inbound(InboundKind.DATA)

    with pytest.raises(StreamControlStateError) as captured:
        await control.tick()
    assert captured.value.context["state"] == "observation_overflow"
    assert control.pending_observation_count <= 2


@pytest.mark.asyncio
async def test_observation_wait_is_edge_safe_and_tick_consumes_notification() -> None:
    time = ManualTime()
    control = owner(time)
    await control.start()
    control.observe_inbound(InboundKind.DATA)

    await asyncio.wait_for(control.wait_for_observation(), timeout=0.1)
    await control.tick()
    waiter = asyncio.create_task(control.wait_for_observation())
    await asyncio.sleep(0)
    assert not waiter.done()
    time.now = 1
    control.observe_inbound(InboundKind.DATA)
    await asyncio.wait_for(waiter, timeout=0.1)


@pytest.mark.asyncio
async def test_receipts_and_invalidations_are_forwarded_and_generation_fenced() -> None:
    time = ManualTime()
    sink = Sink()
    control = owner(time, sink=sink)
    await control.start()
    await control.send("subscribe-1")

    accepted = receipt()
    await control.subscriptions_accepted(accepted)
    assert await asyncio.wait_for(control.wait_for_subscription_receipt(), timeout=0.1) == accepted
    replay = asyncio.create_task(control.wait_for_subscription_receipt())
    await asyncio.sleep(0)
    assert not replay.done()
    replay.cancel()
    with pytest.raises(asyncio.CancelledError):
        await replay
    assert control.accepted_receipt == accepted
    assert sink.receipts == [accepted]

    fence = invalidation()
    await control.invalidate(fence)
    assert control.accepted_receipt is None
    assert sink.invalidations == [fence]
    waiting = asyncio.create_task(control.wait_for_subscription_receipt())
    await asyncio.sleep(0)
    assert not waiting.done()
    await control.send("subscribe-2")
    await control.subscriptions_accepted(receipt(control_revision=2, desired_revision=2))
    assert await asyncio.wait_for(waiting, timeout=0.1) == control.accepted_receipt


@pytest.mark.asyncio
async def test_subscription_receipt_is_stamped_by_owner_clock_generation() -> None:
    time = ManualTime()
    time.now = 10
    sink = Sink()
    control = owner(time, sink=sink)
    await control.start()
    await control.send("subscription-control")
    foreign_future = GenerationSubscriptionReceipt(
        plane=WsPlane.PUBLIC,
        connection_generation=GENERATION,
        control_revision=1,
        desired_revision=1,
        evidence_sha256="a" * 64,
        accepted_at_monotonic_ns=11,
    )

    await control.subscriptions_accepted(foreign_future)

    accepted = control.accepted_receipt
    assert accepted is not None
    assert accepted.accepted_at_monotonic_ns == 10
    assert sink.receipts == [accepted]


@pytest.mark.asyncio
async def test_subscription_receipt_revision_must_equal_successful_actor_sends() -> None:
    control = owner(ManualTime())
    await control.start()

    with pytest.raises(StreamControlStateError) as captured:
        await control.subscriptions_accepted(receipt(control_revision=99))

    assert captured.value.context["state"] == "subscription_receipt_not_current"


@pytest.mark.asyncio
async def test_replayed_or_regressing_generation_evidence_is_rejected() -> None:
    time = ManualTime()
    sink = Sink()
    control = owner(time, sink=sink)
    await control.start()
    await control.send("control-1")
    await control.send("control-2")
    await control.subscriptions_accepted(receipt(control_revision=2, desired_revision=3))
    with pytest.raises(StreamControlStateError):
        await control.subscriptions_accepted(receipt(control_revision=2, desired_revision=3))
    await control.invalidate(invalidation(local_sequence=1))
    with pytest.raises(StreamControlStateError):
        await control.invalidate(invalidation(local_sequence=1))
    await control.send("control-3")
    with pytest.raises(StreamControlStateError):
        await control.subscriptions_accepted(receipt(control_revision=3, desired_revision=2))
    assert len(sink.receipts) == 1
    assert len(sink.invalidations) == 1


@pytest.mark.asyncio
async def test_invalidation_sequence_cannot_jump_ahead() -> None:
    control = owner(ManualTime())
    await control.start()

    with pytest.raises(StreamControlStateError) as captured:
        await control.invalidate(invalidation(local_sequence=2))

    assert captured.value.context["state"] == "invalidation_not_current"


@pytest.mark.asyncio
async def test_sink_failure_is_sanitized_and_breaks_generation() -> None:
    time = ManualTime()
    sink = Sink()
    control = owner(time, sink=sink)
    await control.start()
    await control.send("subscription-control")
    sink.fail = True

    with pytest.raises(ExchangeDownError) as captured:
        await control.subscriptions_accepted(receipt())
    rendered = str(captured.value) + repr(captured.value.__context__)
    assert "fixture-secret" not in rendered
    with pytest.raises(StreamControlStateError):
        await control.tick()


@pytest.mark.asyncio
async def test_synchronous_sink_failure_is_sanitized_too() -> None:
    time = ManualTime()
    control = StreamControlOwner(
        _token=owner_module._OWNER_TOKEN,
        connection=Connection(),
        sink=SyncFailSink(),  # type: ignore[arg-type]
        monotonic_ns=time.monotonic_ns,
        plane=WsPlane.PUBLIC,
        connection_generation=GENERATION,
    )
    await control.start()
    await control.send("subscription-control")
    with pytest.raises(ExchangeDownError) as captured:
        await control.subscriptions_accepted(receipt())
    assert "fixture-secret" not in repr(captured.value.__context__)


@pytest.mark.asyncio
async def test_wrong_generation_or_plane_evidence_fails_closed() -> None:
    time = ManualTime()
    control = owner(time)
    await control.start()
    private = GenerationSubscriptionReceipt(
        plane=WsPlane.PRIVATE,
        connection_generation=GENERATION,
        control_revision=1,
        desired_revision=1,
        evidence_sha256="a" * 64,
        accepted_at_monotonic_ns=1,
    )
    with pytest.raises(StreamControlStateError):
        await control.subscriptions_accepted(private)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["invalidate", "subscriptions_accepted"])
async def test_invalid_evidence_is_rejected_without_retaining_hostile_repr(
    operation: str,
) -> None:
    control = owner(ManualTime())
    await control.start()
    method = getattr(control, operation)

    with pytest.raises(StreamControlStateError) as captured:
        await method(HostileEvidence())

    assert "fixture-evidence-repr-secret" not in traceback_rendering(captured.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["invalidate", "subscriptions_accepted"])
async def test_reflectively_mutated_exact_evidence_is_sanitized(
    operation: str,
) -> None:
    control = owner(ManualTime())
    await control.start()
    evidence: GenerationControlInvalidation | GenerationSubscriptionReceipt
    if operation == "invalidate":
        evidence = invalidation()
        object.__setattr__(evidence, "plane", "mutated-exact-evidence-secret")
    else:
        await control.send("subscription-control")
        evidence = receipt()
        object.__setattr__(evidence, "control_revision", "mutated-exact-evidence-secret")

    with pytest.raises(StreamControlStateError) as captured:
        await getattr(control, operation)(evidence)

    assert "mutated-exact-evidence-secret" not in traceback_rendering(captured.value)


@pytest.mark.asyncio
async def test_invalid_observation_or_clock_breaks_owner_without_raw_context() -> None:
    time = ManualTime()
    control = owner(time)
    await control.start()
    control.observe_inbound("secret-kind")  # type: ignore[arg-type]
    with pytest.raises(StreamControlStateError) as captured:
        await control.tick()
    assert "secret-kind" not in str(captured.value)


@pytest.mark.asyncio
async def test_raising_clock_is_sanitized_without_exception_context() -> None:
    def raising_clock() -> int:
        raise RuntimeError("fixture-secret-clock")

    control = StreamControlOwner(
        _token=owner_module._OWNER_TOKEN,
        connection=Connection(),
        sink=Sink(),
        monotonic_ns=raising_clock,
        plane=WsPlane.PUBLIC,
        connection_generation=GENERATION,
    )
    with pytest.raises(StreamControlStateError) as captured:
        await control.start()
    assert "fixture-secret" not in repr(captured.value.__context__)


@pytest.mark.asyncio
async def test_observation_before_start_fails_closed_without_retaining_it() -> None:
    time = ManualTime()
    control = owner(time)
    control.observe_inbound(InboundKind.DATA)
    with pytest.raises(StreamControlStateError) as captured:
        await control.start()
    assert captured.value.context["state"] == "already_started"
    assert control.pending_observation_count == 0


def test_constructor_rejects_invalid_dependencies_and_bounds() -> None:
    time = ManualTime()
    with pytest.raises(TypeError):
        StreamControlOwner(  # type: ignore[call-arg]
            connection=Connection(),
            sink=Sink(),
            monotonic_ns=time.monotonic_ns,
            plane=WsPlane.PUBLIC,
            connection_generation=GENERATION,
        )
    with pytest.raises(TypeError):
        StreamControlOwner(
            _token=owner_module._OWNER_TOKEN,
            connection=object(),
            sink=Sink(),
            monotonic_ns=time.monotonic_ns,
            plane=WsPlane.PUBLIC,
            connection_generation=GENERATION,
        )
    with pytest.raises(ValueError):
        owner(time, max_pending_observations=0)
    with pytest.raises(ValueError):
        owner(time, max_pending_observations=4097)

    issued = owner(time)
    with pytest.raises(TypeError, match="cannot be copied"):
        copy.copy(issued)
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(issued)


def test_constructor_contains_hostile_dependency_attribute_and_repr_failures() -> None:
    hostile = HostileConstructorDependency()
    with pytest.raises(TypeError) as captured:
        StreamControlOwner(
            _token=owner_module._OWNER_TOKEN,
            connection=hostile,  # type: ignore[arg-type]
            sink=Sink(),
            monotonic_ns=lambda: 0,
            plane=WsPlane.PUBLIC,
            connection_generation=GENERATION,
        )

    rendered = traceback_rendering(captured.value)
    assert "fixture-constructor-getattr-secret" not in rendered
    assert "fixture-constructor-repr-secret" not in rendered
