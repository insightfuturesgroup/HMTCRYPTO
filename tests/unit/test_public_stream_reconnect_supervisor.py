from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from hmt_crypto.exchange.delta.websocket_transport import (
    WebSocketHandshakeCategory,
    WebSocketHandshakeError,
)
from hmt_crypto.market_data.public_stream_models import (
    PublicConnectionGeneration,
    PublicGenerationResult,
    PublicGenerationStatus,
)
from hmt_crypto.market_data.public_stream_supervisor import (
    PublicRecoveryReceipt,
    PublicRecoveryRequest,
    PublicStreamReconnectSupervisor,
    PublicSupervisorExitReason,
    PublicSupervisorLimits,
)
from hmt_crypto.market_data.reconnect_policy import ReconnectPolicy

SECOND = 1_000_000_000


def generation() -> PublicConnectionGeneration:
    return PublicConnectionGeneration(
        connection_generation=uuid4(),
        region="india",
        environment="testnet",
        control_timeout_ns=SECOND,
        registry_poll_ns=SECOND,
        repair_delay_ns=5 * SECOND,
        finalization_timeout_ns=SECOND,
        max_frame_bytes=1024,
    )


def policy() -> ReconnectPolicy:
    return ReconnectPolicy(
        attempt_window_ns=300 * SECOND,
        max_attempts_per_window=120,
        base_delay_ns=SECOND,
        min_spacing_ns=SECOND,
        max_backoff_ns=60 * SECOND,
        rate_limit_min_delay_ns=300 * SECOND,
        rate_limit_max_delay_ns=600 * SECOND,
    )


class ManualClock:
    def __init__(self) -> None:
        self.now = 0
        self.sleeps: list[int] = []

    def monotonic_ns(self) -> int:
        return self.now

    async def sleep_ns(self, delay_ns: int) -> None:
        self.sleeps.append(delay_ns)
        self.now += delay_ns


class ScriptedRunner:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.generations: list[PublicConnectionGeneration] = []

    async def __call__(self, current: PublicConnectionGeneration) -> PublicGenerationResult:
        self.generations.append(current)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, PublicGenerationStatus)
        return PublicGenerationResult(
            status=outcome,
            connection_generation=current.connection_generation,
            observations_applied=1,
            desired_revision=2,
        )


class RecordingRecovery:
    def __init__(self, *, ready: bool = True, mismatch: bool = False) -> None:
        self.ready = ready
        self.mismatch = mismatch
        self.requests: list[PublicRecoveryRequest] = []

    async def recover(self, request: PublicRecoveryRequest) -> PublicRecoveryReceipt:
        self.requests.append(request)
        generation_id = uuid4() if self.mismatch else request.connection_generation
        return PublicRecoveryReceipt(
            request_id=request.request_id,
            connection_generation=generation_id,
            region=request.region,
            environment=request.environment,
            durable_sequence=len(self.requests),
            evidence_sha256="a" * 64,
            invalidated=True,
            reconciled=self.ready,
            ready=self.ready,
        )


def make_supervisor(
    *,
    runner: Callable[[PublicConnectionGeneration], Awaitable[PublicGenerationResult]],
    recovery: RecordingRecovery,
    clock: ManualClock,
    generations: int = 2,
    jitter: Callable[[], Decimal] = lambda: Decimal("1"),
) -> PublicStreamReconnectSupervisor:
    return PublicStreamReconnectSupervisor._for_tests(
        generation_factory=generation,
        run_generation=runner,
        recovery=recovery,
        monotonic_ns=clock.monotonic_ns,
        sleep_ns=clock.sleep_ns,
        jitter_unit=jitter,
        reconnect_policy=policy(),
        limits=PublicSupervisorLimits(max_generations=generations),
    )


@pytest.mark.asyncio
async def test_startup_and_every_terminal_generation_cross_recovery_barrier_before_retry() -> None:
    clock = ManualClock()
    recovery = RecordingRecovery()
    runner = ScriptedRunner(
        [PublicGenerationStatus.RECEIVER_ENDED, PublicGenerationStatus.PROTOCOL_DRIFT]
    )
    supervisor = make_supervisor(runner=runner, recovery=recovery, clock=clock)

    report = await supervisor.run()

    assert report.reason is PublicSupervisorExitReason.GENERATION_LIMIT
    assert report.generations_started == 2
    assert len(recovery.requests) == 3
    assert recovery.requests[0].cause == "startup"
    assert recovery.requests[0].connection_generation is None
    assert recovery.requests[1].connection_generation == runner.generations[0].connection_generation
    assert recovery.requests[1].cause == "receiver_ended"
    assert recovery.requests[2].connection_generation == runner.generations[1].connection_generation
    assert recovery.requests[2].cause == "protocol_drift"
    assert clock.sleeps == [SECOND]


@pytest.mark.asyncio
@pytest.mark.parametrize(("ready", "mismatch"), [(False, False), (True, True)])
async def test_failed_or_identity_mismatched_recovery_fails_closed_without_reopen(
    ready: bool,
    mismatch: bool,
) -> None:
    clock = ManualClock()
    recovery = RecordingRecovery(ready=ready, mismatch=mismatch)
    runner = ScriptedRunner([PublicGenerationStatus.RECEIVER_ENDED])
    supervisor = make_supervisor(runner=runner, recovery=recovery, clock=clock)

    report = await supervisor.run()

    assert report.reason is PublicSupervisorExitReason.RECOVERY_BLOCKED
    assert report.generations_started == 0
    assert runner.generations == []
    assert clock.sleeps == []


@pytest.mark.asyncio
async def test_terminal_handshake_policy_stops_without_blind_retry() -> None:
    clock = ManualClock()
    recovery = RecordingRecovery()
    runner = ScriptedRunner(
        [
            WebSocketHandshakeError(
                category=WebSocketHandshakeCategory.CLIENT_REJECTION,
                status_code=401,
            )
        ]
    )
    supervisor = make_supervisor(runner=runner, recovery=recovery, clock=clock)

    report = await supervisor.run()

    assert report.reason is PublicSupervisorExitReason.POLICY_STOP
    assert report.generations_started == 1
    assert len(recovery.requests) == 2
    assert recovery.requests[-1].cause == "handshake_client_rejection"
    assert clock.sleeps == []


@pytest.mark.asyncio
async def test_rate_limit_handshake_uses_policy_pause_before_reopen() -> None:
    clock = ManualClock()
    recovery = RecordingRecovery()
    runner = ScriptedRunner(
        [
            WebSocketHandshakeError(
                category=WebSocketHandshakeCategory.RATE_LIMIT,
                status_code=429,
            ),
            PublicGenerationStatus.CONTROL_TIMEOUT,
        ]
    )
    supervisor = make_supervisor(runner=runner, recovery=recovery, clock=clock)

    report = await supervisor.run()

    assert report.reason is PublicSupervisorExitReason.GENERATION_LIMIT
    assert report.generations_started == 2
    assert clock.sleeps == [600 * SECOND]
    assert recovery.requests[1].cause == "handshake_rate_limit"


@pytest.mark.asyncio
async def test_cancellation_propagates_and_never_detaches_an_extra_generation() -> None:
    clock = ManualClock()
    recovery = RecordingRecovery()
    entered = asyncio.Event()

    async def blocked(current: PublicConnectionGeneration) -> PublicGenerationResult:
        del current
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    supervisor = make_supervisor(runner=blocked, recovery=recovery, clock=clock)
    task = asyncio.create_task(supervisor.run())
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(recovery.requests) == 1


@pytest.mark.asyncio
async def test_recovery_timeout_and_concurrent_run_both_fail_closed() -> None:
    clock = ManualClock()
    entered = asyncio.Event()

    class HangingRecovery(RecordingRecovery):
        async def recover(self, request: PublicRecoveryRequest) -> PublicRecoveryReceipt:
            self.requests.append(request)
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    recovery = HangingRecovery()
    supervisor = PublicStreamReconnectSupervisor._for_tests(
        generation_factory=generation,
        run_generation=ScriptedRunner([PublicGenerationStatus.RECEIVER_ENDED]),
        recovery=recovery,
        monotonic_ns=clock.monotonic_ns,
        sleep_ns=clock.sleep_ns,
        jitter_unit=lambda: Decimal("1"),
        reconnect_policy=policy(),
        limits=PublicSupervisorLimits(max_generations=1, recovery_timeout_ns=1_000_000),
    )
    first = asyncio.create_task(supervisor.run())
    await entered.wait()
    with pytest.raises(RuntimeError, match="active owner"):
        await supervisor.run()
    report = await first
    assert report.reason is PublicSupervisorExitReason.RECOVERY_BLOCKED


@pytest.mark.asyncio
async def test_programming_error_is_not_retried_and_terminal_policy_precedes_limit() -> None:
    clock = ManualClock()
    recovery = RecordingRecovery()

    async def broken(_generation: PublicConnectionGeneration) -> PublicGenerationResult:
        raise AssertionError("programming defect")

    broken_report = await make_supervisor(
        runner=broken,
        recovery=recovery,
        clock=clock,
        generations=2,
    ).run()
    assert broken_report.reason is PublicSupervisorExitReason.SUPERVISOR_ERROR
    assert broken_report.generations_started == 1
    assert clock.sleeps == []

    terminal = ScriptedRunner(
        [
            WebSocketHandshakeError(
                category=WebSocketHandshakeCategory.CLIENT_REJECTION,
                status_code=401,
            )
        ]
    )
    terminal_report = await make_supervisor(
        runner=terminal,
        recovery=RecordingRecovery(),
        clock=ManualClock(),
        generations=1,
    ).run()
    assert terminal_report.reason is PublicSupervisorExitReason.POLICY_STOP


def test_values_reject_unsafe_shapes() -> None:
    with pytest.raises((TypeError, ValueError)):
        PublicSupervisorLimits(max_generations=True)  # type: ignore[arg-type]
    with pytest.raises((TypeError, ValueError)):
        PublicRecoveryRequest(
            request_id=uuid4(),
            connection_generation=UUID(int=0),
            cause="bad",
            region="india",
            environment="testnet",
        )
