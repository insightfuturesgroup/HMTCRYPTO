"""Bounded owner loop for public WebSocket generations and REST recovery barriers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from hmt_crypto.errors import ExchangeDownError
from hmt_crypto.exchange.delta.websocket_contracts import WsEndpoint, WsPlane
from hmt_crypto.exchange.delta.websocket_transport import (
    DeltaWebsocketsConnector,
    WebSocketHandshakeError,
    _is_owned_physical_connector,
)
from hmt_crypto.market_data.connection_attempts import ConnectionAttemptGovernor
from hmt_crypto.market_data.public_stream import PublicStreamActor
from hmt_crypto.market_data.public_stream_models import (
    PublicConnectionGeneration,
    PublicGenerationResult,
)
from hmt_crypto.market_data.reconnect_policy import (
    DEFAULT_RECONNECT_POLICY,
    ReconnectAction,
    ReconnectCause,
    ReconnectPolicy,
    cause_from_generation,
    cause_from_handshake,
    plan_reconnect,
    record_connection_attempt,
)
from hmt_crypto.market_data.stream_evidence_mailbox import StreamEvidenceMailbox

GenerationFactory = Callable[[], PublicConnectionGeneration]
GenerationRunner = Callable[[PublicConnectionGeneration], Awaitable[PublicGenerationResult]]
MonotonicNs = Callable[[], int]
SleepNs = Callable[[int], Awaitable[None]]
JitterUnit = Callable[[], Decimal]

_CAUSES = frozenset({"startup", *(cause.value for cause in ReconnectCause)})
_PHYSICAL_FACTORY_TOKEN = object()
_TEST_FACTORY_TOKEN = object()


@dataclass(frozen=True, slots=True, kw_only=True)
class PublicSupervisorLimits:
    """Hard process-lifetime bound for one supervisor invocation."""

    max_generations: int = 10_000
    recovery_timeout_ns: int = 30_000_000_000

    def __post_init__(self) -> None:
        if type(self.max_generations) is not int or not 1 <= self.max_generations <= 10_000:
            raise ValueError("public supervisor generation bound is invalid")
        if (
            type(self.recovery_timeout_ns) is not int
            or not 1_000_000 <= self.recovery_timeout_ns <= 60_000_000_000
        ):
            raise ValueError("public supervisor recovery timeout is invalid")


DEFAULT_SUPERVISOR_LIMITS = PublicSupervisorLimits()


@dataclass(frozen=True, slots=True, kw_only=True)
class PublicRecoveryRequest:
    """Exact request for durable invalidation plus authoritative REST recovery."""

    request_id: UUID
    connection_generation: UUID | None
    cause: str
    region: str
    environment: str

    def __post_init__(self) -> None:
        if type(self.request_id) is not UUID or self.request_id.int == 0:
            raise TypeError("public recovery request identity is invalid")
        if self.connection_generation is not None and (
            type(self.connection_generation) is not UUID or self.connection_generation.int == 0
        ):
            raise TypeError("public recovery generation identity is invalid")
        if type(self.cause) is not str or self.cause not in _CAUSES:
            raise ValueError("public recovery cause is invalid")
        if self.region != "india" or self.environment not in {"testnet", "prod"}:
            raise ValueError("public recovery scope is invalid")
        if self.cause == "startup" and self.connection_generation is not None:
            raise ValueError("startup recovery cannot cite a connection generation")
        if self.cause != "startup" and self.connection_generation is None:
            raise ValueError("terminal recovery requires a connection generation")


@dataclass(frozen=True, slots=True, kw_only=True)
class PublicRecoveryReceipt:
    """Durable barrier acknowledgement; all three safety claims must be true."""

    request_id: UUID
    connection_generation: UUID | None
    region: str
    environment: str
    durable_sequence: int
    evidence_sha256: str
    invalidated: bool
    reconciled: bool
    ready: bool

    def __post_init__(self) -> None:
        if type(self.request_id) is not UUID or self.request_id.int == 0:
            raise TypeError("public recovery receipt identity is invalid")
        if self.connection_generation is not None and (
            type(self.connection_generation) is not UUID or self.connection_generation.int == 0
        ):
            raise TypeError("public recovery receipt generation is invalid")
        if type(self.durable_sequence) is not int or self.durable_sequence <= 0:
            raise ValueError("public recovery durable sequence is invalid")
        if self.region != "india" or self.environment not in {"testnet", "prod"}:
            raise ValueError("public recovery receipt scope is invalid")
        if (
            type(self.evidence_sha256) is not str
            or len(self.evidence_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.evidence_sha256)
        ):
            raise ValueError("public recovery evidence is invalid")
        flags = (self.invalidated, self.reconciled, self.ready)
        if any(type(value) is not bool for value in flags):
            raise TypeError("public recovery receipt flags are invalid")


class PublicRecoveryPort(Protocol):
    """Persist invalidation and complete a bounded authoritative recovery read."""

    async def recover(self, request: PublicRecoveryRequest) -> PublicRecoveryReceipt: ...


class PublicSupervisorExitReason(StrEnum):
    """Sanitized terminal classification for one supervisor invocation."""

    POLICY_STOP = "policy_stop"
    RECOVERY_BLOCKED = "recovery_blocked"
    GENERATION_LIMIT = "generation_limit"
    SUPERVISOR_ERROR = "supervisor_error"


@dataclass(frozen=True, slots=True, kw_only=True)
class PublicSupervisorReport:
    """Bounded terminal evidence with no execution or readiness authority."""

    reason: PublicSupervisorExitReason
    generations_started: int
    recovery_sequence: int
    last_generation: UUID | None
    last_cause: str | None

    def __post_init__(self) -> None:
        if type(self.reason) is not PublicSupervisorExitReason:
            raise TypeError("public supervisor exit reason is invalid")
        if type(self.generations_started) is not int or self.generations_started < 0:
            raise ValueError("public supervisor generation count is invalid")
        if type(self.recovery_sequence) is not int or self.recovery_sequence < 0:
            raise ValueError("public supervisor recovery sequence is invalid")
        if self.last_generation is not None and type(self.last_generation) is not UUID:
            raise TypeError("public supervisor generation identity is invalid")
        if self.last_cause is not None and (
            type(self.last_cause) is not str or self.last_cause not in _CAUSES
        ):
            raise ValueError("public supervisor cause is invalid")


class PublicStreamReconnectSupervisor:
    """Own repeated generations; never reconnect around an unproven recovery barrier."""

    def __init__(
        self,
        *,
        generation_factory: GenerationFactory,
        run_generation: GenerationRunner,
        recovery: PublicRecoveryPort,
        monotonic_ns: MonotonicNs,
        sleep_ns: SleepNs,
        jitter_unit: JitterUnit,
        reconnect_policy: ReconnectPolicy = DEFAULT_RECONNECT_POLICY,
        limits: PublicSupervisorLimits = DEFAULT_SUPERVISOR_LIMITS,
        region: str,
        environment: str,
        _factory_token: object,
    ) -> None:
        if not all(
            callable(value)
            for value in (generation_factory, run_generation, monotonic_ns, sleep_ns, jitter_unit)
        ):
            raise TypeError("public supervisor dependencies are invalid")
        if type(reconnect_policy) is not ReconnectPolicy:
            raise TypeError("public supervisor reconnect policy is invalid")
        if type(limits) is not PublicSupervisorLimits or not callable(
            getattr(recovery, "recover", None)
        ):
            raise TypeError("public supervisor recovery dependency is invalid")
        if (
            _factory_token not in {_PHYSICAL_FACTORY_TOKEN, _TEST_FACTORY_TOKEN}
            or region != "india"
            or environment not in {"testnet", "prod"}
        ):
            raise TypeError("public supervisor requires an owned factory")
        self._generation_factory = generation_factory
        self._run_generation = run_generation
        self._recovery = recovery
        self._monotonic_ns = monotonic_ns
        self._sleep_ns = sleep_ns
        self._jitter_unit = jitter_unit
        self._policy = reconnect_policy
        self._limits = limits
        self._region = region
        self._environment = environment
        self._recovery_sequence = 0
        self._running = False

    @classmethod
    def for_physical_actor(
        cls,
        *,
        actor: PublicStreamActor,
        connector: DeltaWebsocketsConnector,
        endpoint: WsEndpoint,
        attempts: ConnectionAttemptGovernor,
        sink: StreamEvidenceMailbox,
        generation_factory: GenerationFactory,
        recovery: PublicRecoveryPort,
        monotonic_ns: MonotonicNs,
        sleep_ns: SleepNs,
        jitter_unit: JitterUnit,
        reconnect_policy: ReconnectPolicy = DEFAULT_RECONNECT_POLICY,
        limits: PublicSupervisorLimits = DEFAULT_SUPERVISOR_LIMITS,
    ) -> PublicStreamReconnectSupervisor:
        """Bind the loop to the reviewed actor, endpoint and attempt governor."""
        if (
            type(actor) is not PublicStreamActor
            or type(endpoint) is not WsEndpoint
            or endpoint.plane is not WsPlane.PUBLIC
            or type(attempts) is not ConnectionAttemptGovernor
            or type(sink) is not StreamEvidenceMailbox
            or not _is_owned_physical_connector(connector)
            or endpoint.region != attempts.region
            or endpoint.environment != attempts.environment
        ):
            raise TypeError("public physical supervisor dependencies are invalid")

        async def run_generation(
            generation: PublicConnectionGeneration,
        ) -> PublicGenerationResult:
            return await actor.run_physical_generation(
                generation,
                connector=connector,
                endpoint=endpoint,
                attempts=attempts,
                sink=sink,
            )

        return cls(
            generation_factory=generation_factory,
            run_generation=run_generation,
            recovery=recovery,
            monotonic_ns=monotonic_ns,
            sleep_ns=sleep_ns,
            jitter_unit=jitter_unit,
            reconnect_policy=reconnect_policy,
            limits=limits,
            region=endpoint.region,
            environment=endpoint.environment,
            _factory_token=_PHYSICAL_FACTORY_TOKEN,
        )

    @classmethod
    def _for_tests(
        cls,
        *,
        generation_factory: GenerationFactory,
        run_generation: GenerationRunner,
        recovery: PublicRecoveryPort,
        monotonic_ns: MonotonicNs,
        sleep_ns: SleepNs,
        jitter_unit: JitterUnit,
        reconnect_policy: ReconnectPolicy = DEFAULT_RECONNECT_POLICY,
        limits: PublicSupervisorLimits = DEFAULT_SUPERVISOR_LIMITS,
    ) -> PublicStreamReconnectSupervisor:
        return cls(
            generation_factory=generation_factory,
            run_generation=run_generation,
            recovery=recovery,
            monotonic_ns=monotonic_ns,
            sleep_ns=sleep_ns,
            jitter_unit=jitter_unit,
            reconnect_policy=reconnect_policy,
            limits=limits,
            region="india",
            environment="testnet",
            _factory_token=_TEST_FACTORY_TOKEN,
        )

    def _now(self, previous: int | None = None) -> int:
        value = self._monotonic_ns()
        if type(value) is not int or value < 0 or (previous is not None and value < previous):
            raise RuntimeError("public supervisor monotonic clock is invalid")
        return value

    async def _cross_recovery(self, generation: UUID | None, cause: str) -> bool:
        request = PublicRecoveryRequest(
            request_id=uuid4(),
            connection_generation=generation,
            cause=cause,
            region=self._region,
            environment=self._environment,
        )
        try:
            async with asyncio.timeout(self._limits.recovery_timeout_ns / 1_000_000_000):
                receipt = await self._recovery.recover(request)
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
        if (
            type(receipt) is not PublicRecoveryReceipt
            or receipt.request_id != request.request_id
            or receipt.connection_generation != generation
            or receipt.region != self._region
            or receipt.environment != self._environment
            or receipt.durable_sequence <= self._recovery_sequence
            or not receipt.invalidated
            or not receipt.reconciled
            or not receipt.ready
        ):
            return False
        self._recovery_sequence = receipt.durable_sequence
        return True

    def _report(
        self,
        reason: PublicSupervisorExitReason,
        *,
        started: int,
        generation: UUID | None,
        cause: str | None,
    ) -> PublicSupervisorReport:
        return PublicSupervisorReport(
            reason=reason,
            generations_started=started,
            recovery_sequence=self._recovery_sequence,
            last_generation=generation,
            last_cause=cause,
        )

    async def run(self) -> PublicSupervisorReport:
        """Run until terminal policy, failed recovery, or the invocation bound."""
        if self._running:
            raise RuntimeError("public supervisor already has an active owner")
        self._running = True
        try:
            return await self._run_owned()
        finally:
            self._running = False

    async def _run_owned(self) -> PublicSupervisorReport:
        """Execute one exclusively owned reconnect loop."""
        started = 0
        last_generation: UUID | None = None
        last_cause: str | None = None
        attempted_at_ns: tuple[int, ...] = ()
        consecutive_failures = 0
        previous_now: int | None = None
        seen_generations: set[UUID] = set()

        if not await self._cross_recovery(None, "startup"):
            return self._report(
                PublicSupervisorExitReason.RECOVERY_BLOCKED,
                started=0,
                generation=None,
                cause="startup",
            )

        while started < self._limits.max_generations:
            try:
                generation = self._generation_factory()
                if (
                    type(generation) is not PublicConnectionGeneration
                    or generation.connection_generation in seen_generations
                    or generation.region != self._region
                    or generation.environment != self._environment
                ):
                    raise RuntimeError("public supervisor generation factory is invalid")
                seen_generations.add(generation.connection_generation)
                last_generation = generation.connection_generation
                now_ns = self._now(previous_now)
                previous_now = now_ns
                attempted_at_ns = record_connection_attempt(
                    now_ns=now_ns,
                    attempted_at_ns=attempted_at_ns,
                    policy=self._policy,
                )
                started += 1
                try:
                    result = await self._run_generation(generation)
                    if (
                        type(result) is not PublicGenerationResult
                        or result.connection_generation != generation.connection_generation
                    ):
                        raise RuntimeError("public generation result is invalid")
                    cause = cause_from_generation(result.status)
                except asyncio.CancelledError:
                    raise
                except WebSocketHandshakeError as error:
                    cause = cause_from_handshake(error.category)
                except ExchangeDownError:
                    cause = ReconnectCause.HANDSHAKE_NETWORK
                except Exception:
                    last_cause = ReconnectCause.PROTOCOL_DRIFT.value
                    if not await self._cross_recovery(last_generation, last_cause):
                        return self._report(
                            PublicSupervisorExitReason.RECOVERY_BLOCKED,
                            started=started,
                            generation=last_generation,
                            cause=last_cause,
                        )
                    return self._report(
                        PublicSupervisorExitReason.SUPERVISOR_ERROR,
                        started=started,
                        generation=last_generation,
                        cause=last_cause,
                    )
                last_cause = cause.value
                consecutive_failures += 1

                if not await self._cross_recovery(last_generation, last_cause):
                    return self._report(
                        PublicSupervisorExitReason.RECOVERY_BLOCKED,
                        started=started,
                        generation=last_generation,
                        cause=last_cause,
                    )
                now_ns = self._now(previous_now)
                previous_now = now_ns
                plan = plan_reconnect(
                    cause=cause,
                    consecutive_failures=consecutive_failures,
                    jitter_unit=self._jitter_unit(),
                    now_ns=now_ns,
                    attempted_at_ns=attempted_at_ns,
                    policy=self._policy,
                )
                if plan.action is ReconnectAction.STOP:
                    return self._report(
                        PublicSupervisorExitReason.POLICY_STOP,
                        started=started,
                        generation=last_generation,
                        cause=last_cause,
                    )
                if started >= self._limits.max_generations:
                    return self._report(
                        PublicSupervisorExitReason.GENERATION_LIMIT,
                        started=started,
                        generation=last_generation,
                        cause=last_cause,
                    )
                assert plan.delay_ns is not None
                wake_at = now_ns + plan.delay_ns
                await self._sleep_ns(plan.delay_ns)
                after_sleep = self._now(previous_now)
                previous_now = after_sleep
                if after_sleep < wake_at:
                    raise RuntimeError("public supervisor sleep completed early")
            except asyncio.CancelledError:
                raise
            except Exception:
                return self._report(
                    PublicSupervisorExitReason.SUPERVISOR_ERROR,
                    started=started,
                    generation=last_generation,
                    cause=last_cause,
                )

        return self._report(
            PublicSupervisorExitReason.GENERATION_LIMIT,
            started=started,
            generation=last_generation,
            cause=last_cause,
        )


__all__ = [
    "PublicRecoveryPort",
    "PublicRecoveryReceipt",
    "PublicRecoveryRequest",
    "PublicStreamReconnectSupervisor",
    "PublicSupervisorExitReason",
    "PublicSupervisorLimits",
    "PublicSupervisorReport",
]
