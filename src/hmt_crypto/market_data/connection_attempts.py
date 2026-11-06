"""Leader-bound WebSocket physical-open attempt admission."""

from __future__ import annotations

import asyncio
import threading
import time
import weakref
from dataclasses import dataclass
from enum import StrEnum
from typing import Self, cast
from uuid import UUID

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.exchange.delta.rate_limit_policy import RateLimitScope, ScopeKind
from hmt_crypto.market_data.reconnect_policy import (
    DEFAULT_RECONNECT_POLICY,
    ReconnectPolicy,
)
from hmt_crypto.worker.leadership import (
    Task210PrivateWorkerCurrentnessPort,
    Task210SinglePrivateWorkerProof,
)

_MAX_EGRESS_ID_LENGTH = 128
_FACTORY_TOKEN = object()
_NANOSECONDS_PER_SECOND = 1_000_000_000


class _SleepOutcome(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class _FactoryFailure(StrEnum):
    EGRESS = "egress"
    POLICY = "policy"
    CLOCK = "clock"
    OWNERSHIP = "ownership"
    SCOPE = "scope"
    MISMATCH = "mismatch"


class _AttemptFailure(StrEnum):
    CANCELLED = "cancelled"
    CLOCK = "clock"
    REGRESSED = "regressed"
    OWNERSHIP = "ownership"
    TIMER = "timer"
    EARLY = "early"


@dataclass(frozen=True, slots=True)
class _OwnerBinding:
    deployment_revision: str
    external_user_id: str
    process_id: int
    process_incarnation_id: UUID
    region: DeltaRegion
    environment: DeltaEnvironment


class _GovernorState:
    __slots__ = (
        "attempted_at_ns",
        "binding",
        "cold_start_ready_at_ns",
        "currentness",
        "egress_identity",
        "last_observed_ns",
        "lock",
        "policy",
        "proof",
        "sequence",
    )

    def __init__(
        self,
        *,
        binding: _OwnerBinding,
        egress_identity: str,
        proof: Task210SinglePrivateWorkerProof,
        currentness: Task210PrivateWorkerCurrentnessPort,
        policy: ReconnectPolicy,
        started_at_ns: int,
    ) -> None:
        self.binding = binding
        self.egress_identity = egress_identity
        self.proof = proof
        self.currentness = currentness
        self.policy = policy
        self.lock = asyncio.Lock()
        self.attempted_at_ns: tuple[int, ...] = ()
        self.sequence = 0
        self.last_observed_ns: int | None = started_at_ns
        self.cold_start_ready_at_ns = started_at_ns + policy.attempt_window_ns + 1


_PROCESS_LOCK = threading.RLock()
_PROCESS_OWNER_REF: weakref.ReferenceType[ConnectionAttemptGovernor] | None = None
_GOVERNOR_STATES: weakref.WeakKeyDictionary[ConnectionAttemptGovernor, _GovernorState] = (
    weakref.WeakKeyDictionary()
)


def _exact_identifier(value: object, *, maximum: int) -> str | None:
    if (
        type(value) is not str
        or not 1 <= len(value) <= maximum
        or not value.isascii()
        or value.strip() != value
        or any(not 0x21 <= ord(character) <= 0x7E for character in value)
    ):
        return None
    return value


def _owner_binding(
    proof: object,
    currentness: object,
) -> _OwnerBinding | None:
    try:
        if (
            type(proof) is not Task210SinglePrivateWorkerProof
            or type(currentness) is not Task210PrivateWorkerCurrentnessPort
        ):
            return None
        binding = _OwnerBinding(
            deployment_revision=proof.deployment_revision,
            external_user_id=proof.external_user_id,
            process_id=proof.process_id,
            process_incarnation_id=proof.process_incarnation_id,
            region=proof.region,
            environment=proof.environment,
        )
        returned = currentness.revalidate_single_private_worker_nonblocking(
            proof,
            expected_external_user_id=binding.external_user_id,
            expected_region=binding.region,
            expected_environment=binding.environment,
            expected_process_id=binding.process_id,
            expected_process_incarnation_id=binding.process_incarnation_id,
        )
        if returned is not proof:
            return None
        return binding
    except BaseException:
        return None


def _owner_is_current(state: _GovernorState) -> bool:
    current = _owner_binding(state.proof, state.currentness)
    return current == state.binding


def _system_monotonic_ns() -> int:
    return time.monotonic_ns()


async def _system_sleep_ns(delay_ns: int) -> None:
    await asyncio.sleep(delay_ns / _NANOSECONDS_PER_SECOND)


def _sample_clock() -> int | None:
    try:
        return _system_monotonic_ns()
    except BaseException:
        return None


async def _sleep(delay_ns: int) -> _SleepOutcome:
    try:
        await _system_sleep_ns(delay_ns)
    except asyncio.CancelledError:
        return _SleepOutcome.CANCELLED
    except BaseException:
        return _SleepOutcome.FAILED
    return _SleepOutcome.COMPLETED


def _state(owner: ConnectionAttemptGovernor) -> _GovernorState | None:
    with _PROCESS_LOCK:
        active = _PROCESS_OWNER_REF() if _PROCESS_OWNER_REF is not None else None
        if active is not owner:
            return None
        return _GOVERNOR_STATES.get(owner)


def _now(state: _GovernorState) -> int | _AttemptFailure:
    value = _sample_clock()
    if type(value) is not int or value < 0:
        return _AttemptFailure.CLOCK
    if state.last_observed_ns is not None and value < state.last_observed_ns:
        return _AttemptFailure.REGRESSED
    state.last_observed_ns = value
    return value


def _active(state: _GovernorState, now_ns: int) -> tuple[int, ...]:
    cutoff = now_ns - state.policy.attempt_window_ns
    return tuple(value for value in state.attempted_at_ns if value >= cutoff)


def _required_delay(state: _GovernorState, active: tuple[int, ...], now_ns: int) -> int:
    cold_delay = max(0, state.cold_start_ready_at_ns - now_ns)
    spacing_delay = 0
    if active:
        spacing_delay = max(0, active[-1] + state.policy.min_spacing_ns - now_ns)
    budget_delay = 0
    if len(active) >= state.policy.max_attempts_per_window:
        expiry_index = len(active) - state.policy.max_attempts_per_window
        budget_delay = max(
            0,
            active[expiry_index] + state.policy.attempt_window_ns + 1 - now_ns,
        )
    return max(cold_delay, spacing_delay, budget_delay)


async def _consume(owner: ConnectionAttemptGovernor) -> None | _AttemptFailure:
    try:
        state = _state(owner)
        if state is None:
            return _AttemptFailure.OWNERSHIP
        while True:
            async with state.lock:
                if not _owner_is_current(state):
                    return _AttemptFailure.OWNERSHIP
                before = _now(state)
                if isinstance(before, _AttemptFailure):
                    return before
                active = _active(state, before)
                state.attempted_at_ns = active
                delay_ns = _required_delay(state, active, before)
                if delay_ns == 0:
                    state.sequence += 1
                    state.attempted_at_ns = (*active, before)
                    return None
                target_ns = before + delay_ns
            outcome = await _sleep(delay_ns)
            if outcome is _SleepOutcome.CANCELLED:
                return _AttemptFailure.CANCELLED
            if outcome is not _SleepOutcome.COMPLETED:
                return _AttemptFailure.TIMER
            async with state.lock:
                if not _owner_is_current(state):
                    return _AttemptFailure.OWNERSHIP
                after = _now(state)
                if isinstance(after, _AttemptFailure):
                    return after
                if after < target_ns:
                    return _AttemptFailure.EARLY
    except asyncio.CancelledError:
        return _AttemptFailure.CANCELLED
    except BaseException:
        return _AttemptFailure.OWNERSHIP


async def _snapshot(
    owner: ConnectionAttemptGovernor,
) -> tuple[int, ...] | _AttemptFailure:
    try:
        state = _state(owner)
        if state is None:
            return _AttemptFailure.OWNERSHIP
        async with state.lock:
            if not _owner_is_current(state):
                return _AttemptFailure.OWNERSHIP
            now_ns = _now(state)
            if isinstance(now_ns, _AttemptFailure):
                return now_ns
            state.attempted_at_ns = _active(state, now_ns)
            return tuple(state.attempted_at_ns)
    except asyncio.CancelledError:
        return _AttemptFailure.CANCELLED
    except BaseException:
        return _AttemptFailure.OWNERSHIP


class ConnectionAttemptGovernor:
    """Sole leader-bound physical-open budget for this operating-system process."""

    __slots__ = ("__weakref__",)

    def __init__(self, *, _token: object, state: _GovernorState) -> None:
        if _token is not _FACTORY_TOKEN or type(state) is not _GovernorState:
            raise TypeError("connection-attempt governors require the owned factory")
        with _PROCESS_LOCK:
            if self in _GOVERNOR_STATES:
                raise TypeError("connection-attempt governors require the owned factory")
            _GOVERNOR_STATES[self] = state

    @classmethod
    def for_single_process_egress(
        cls,
        scope: object,
        *,
        ownership_proof: object,
        ownership_currentness: object,
        policy: object = DEFAULT_RECONNECT_POLICY,
    ) -> Self:
        """Return the sole exact leader/process/egress attempt owner."""
        outcome = cls._try_factory(
            scope=scope,
            ownership_proof=ownership_proof,
            ownership_currentness=ownership_currentness,
            policy=policy,
        )
        del (
            scope,
            ownership_proof,
            ownership_currentness,
            policy,
        )
        if outcome is _FactoryFailure.EGRESS:
            raise ValueError("egress scope is invalid") from None
        if outcome is _FactoryFailure.POLICY:
            raise TypeError("connection-attempt policy must be ReconnectPolicy") from None
        if outcome is _FactoryFailure.CLOCK:
            raise RuntimeError("connection-attempt monotonic clock is unavailable") from None
        if outcome is _FactoryFailure.OWNERSHIP:
            raise RuntimeError("connection-attempt ownership is unavailable") from None
        if outcome is _FactoryFailure.SCOPE:
            raise RuntimeError("connection-attempt process scope mismatch") from None
        if outcome is _FactoryFailure.MISMATCH:
            raise RuntimeError("egress attempt owner dependency mismatch") from None
        return outcome

    @classmethod
    def _try_factory(
        cls,
        **values: object,
    ) -> Self | _FactoryFailure:
        try:
            if cls is not ConnectionAttemptGovernor:
                return _FactoryFailure.OWNERSHIP
            scope = values.get("scope")
            if type(scope) is not RateLimitScope or scope.kind is not ScopeKind.IP:
                return _FactoryFailure.EGRESS
            egress = _exact_identifier(scope.external_id, maximum=_MAX_EGRESS_ID_LENGTH)
            if egress is None:
                return _FactoryFailure.EGRESS
            policy = values.get("policy")
            if type(policy) is not ReconnectPolicy:
                return _FactoryFailure.POLICY
            proof = values.get("ownership_proof")
            currentness = values.get("ownership_currentness")
            binding = _owner_binding(proof, currentness)
            if binding is None:
                return _FactoryFailure.OWNERSHIP
            if (
                scope.region != binding.region.value
                or scope.environment != binding.environment.value
            ):
                return _FactoryFailure.EGRESS
            sampled = _sample_clock()
            if type(sampled) is not int or sampled < 0:
                return _FactoryFailure.CLOCK
            global _PROCESS_OWNER_REF
            with _PROCESS_LOCK:
                existing = _PROCESS_OWNER_REF() if _PROCESS_OWNER_REF is not None else None
                if existing is not None:
                    state = _GOVERNOR_STATES.get(existing)
                    if state is None or not _owner_is_current(state):
                        return _FactoryFailure.OWNERSHIP
                    if (
                        state.binding != binding
                        or state.egress_identity != egress
                        or state.proof is not proof
                        or state.currentness is not currentness
                    ):
                        return _FactoryFailure.SCOPE
                    if state.policy != policy:
                        return _FactoryFailure.MISMATCH
                    return cast(Self, existing)
                exact_proof = cast(Task210SinglePrivateWorkerProof, proof)
                exact_currentness = cast(Task210PrivateWorkerCurrentnessPort, currentness)
                state = _GovernorState(
                    binding=binding,
                    egress_identity=egress,
                    proof=exact_proof,
                    currentness=exact_currentness,
                    policy=policy,
                    started_at_ns=sampled,
                )
                created = cls(_token=_FACTORY_TOKEN, state=state)
                _PROCESS_OWNER_REF = weakref.ref(created)
                return created
        except BaseException:
            return _FactoryFailure.OWNERSHIP

    async def consume_physical_open_attempt(self) -> None:
        """Revalidate leadership and irreversibly consume one physical open."""
        outcome = await _consume(self)
        if outcome is None:
            return
        if outcome is _AttemptFailure.CANCELLED:
            raise asyncio.CancelledError from None
        if outcome is _AttemptFailure.CLOCK:
            raise RuntimeError("connection-attempt monotonic clock is unavailable") from None
        if outcome is _AttemptFailure.REGRESSED:
            raise RuntimeError("connection-attempt monotonic clock regressed") from None
        if outcome is _AttemptFailure.OWNERSHIP:
            raise RuntimeError("connection-attempt ownership is unavailable") from None
        if outcome is _AttemptFailure.TIMER:
            raise RuntimeError("connection-attempt timer is unavailable") from None
        raise RuntimeError("connection-attempt timer deadline was not reached") from None

    async def snapshot(self) -> tuple[int, ...]:
        """Return independently owned active history after owner revalidation."""
        outcome = await _snapshot(self)
        if isinstance(outcome, tuple):
            return outcome
        if outcome is _AttemptFailure.CANCELLED:
            raise asyncio.CancelledError from None
        if outcome is _AttemptFailure.CLOCK:
            raise RuntimeError("connection-attempt monotonic clock is unavailable") from None
        if outcome is _AttemptFailure.REGRESSED:
            raise RuntimeError("connection-attempt monotonic clock regressed") from None
        raise RuntimeError("connection-attempt ownership is unavailable") from None

    @property
    def consumed_attempt_count(self) -> int:
        """Return the process-lifetime count for this exact owned scope."""
        state = _state(self)
        return 0 if state is None else state.sequence

    @property
    def region(self) -> str:
        """Return the region bound by the exact worker owner."""
        state = _state(self)
        return "" if state is None else state.binding.region.value

    @property
    def environment(self) -> str:
        """Return the environment bound by the exact worker owner."""
        state = _state(self)
        return "" if state is None else state.binding.environment.value

    @property
    def egress_identity(self) -> str:
        """Return the exact configured egress identity."""
        state = _state(self)
        return "" if state is None else state.egress_identity


__all__ = ["ConnectionAttemptGovernor"]
