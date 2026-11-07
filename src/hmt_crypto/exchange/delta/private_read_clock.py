"""Sealed refreshable trusted-instant authority for private REST reads."""

from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass
from typing import NoReturn, Self, SupportsIndex
from uuid import UUID

from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.private_dispatch import TrustedDispatchInstant
from hmt_crypto.worker import clock as trusted_clock

_TEST_MARKER = object()
_MAX_SIGNING_OBSERVATION_WIDTH_NS = 4_000_000_000


def _error() -> ConfigurationError:
    return ConfigurationError(context={"operation": "private_read_trusted_instant"})


def _fail() -> NoReturn:
    raise _error() from None


def _loop_identity() -> int | None:
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        return None


@dataclass(slots=True)
class _IssuerState:
    issuer: PrivateReadTrustedInstantIssuer
    clock: trusted_clock.Task210TrustedDispatchClock
    currentness: trusted_clock.Task210TrustedClockCurrentnessPort
    observations: tuple[TrustedDispatchInstant, ...]
    process_id: int
    thread_id: int
    loop_id: int | None
    next_index: int = 0
    last_monotonic_ns: int = -1
    last_utc_epoch_seconds: int = -1
    last_utc_upper_bound_ns: int = -1


_STATES: dict[int, _IssuerState] = {}
_CLASSES_CLOSED = False


class _OpaqueIssuer:
    __slots__ = ()

    def __new__(cls, *_args: object, **_kwargs: object) -> Self:
        raise TypeError("private-read trusted-instant authority cannot be constructed")

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if _CLASSES_CLOSED:
            raise TypeError("private-read trusted-instant authority cannot be subclassed")

    def __setattr__(self, _name: str, _value: object) -> NoReturn:
        raise AttributeError("private-read trusted-instant authority is immutable")

    def __delattr__(self, _name: str) -> NoReturn:
        raise AttributeError("private-read trusted-instant authority is immutable")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError("private-read trusted-instant authority cannot be serialized")

    def __reduce__(self) -> NoReturn:
        raise TypeError("private-read trusted-instant authority cannot be serialized")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>)"

    __str__ = __repr__


def _state(value: object) -> _IssuerState:
    if type(value) is not PrivateReadTrustedInstantIssuer:
        _fail()
    state = _STATES.get(id(value))
    if state is None or state.issuer is not value:
        _fail()
    if (
        os.getpid() != state.process_id
        or threading.get_ident() != state.thread_id
        or _loop_identity() != state.loop_id
    ):
        _fail()
    return state


class PrivateReadTrustedInstantIssuer(_OpaqueIssuer):
    """Issue a fresh process-bound trusted observation for every requested phase."""

    __slots__ = ()

    def attest_association_nonblocking(
        self,
        *,
        clock: trusted_clock.Task210TrustedDispatchClock,
        clock_currentness: trusted_clock.Task210TrustedClockCurrentnessPort,
        expected_process_id: int,
        expected_process_incarnation_id: UUID,
    ) -> None:
        """Attest issuer/clock identity without consuming an observation."""
        state = _state(self)
        if clock is not state.clock or clock_currentness is not state.currentness:
            _fail()
        returned = clock_currentness.revalidate_dispatch_clock_nonblocking(
            clock,
            expected_process_id=expected_process_id,
            expected_process_incarnation_id=expected_process_incarnation_id,
        )
        if returned is not clock:
            _fail()

    def issue_fresh_nonblocking(
        self,
        *,
        clock: trusted_clock.Task210TrustedDispatchClock,
        clock_currentness: trusted_clock.Task210TrustedClockCurrentnessPort,
        expected_process_id: int,
        expected_process_incarnation_id: UUID,
    ) -> TrustedDispatchInstant:
        """Return the next exact fresh observation or fail closed before reuse."""
        state = _state(self)
        if clock is not state.clock or clock_currentness is not state.currentness:
            _fail()
        returned = clock_currentness.revalidate_dispatch_clock_nonblocking(
            clock,
            expected_process_id=expected_process_id,
            expected_process_incarnation_id=expected_process_incarnation_id,
        )
        if returned is not clock or state.next_index >= len(state.observations):
            _fail()
        observation = state.observations[state.next_index]
        if (
            observation.process_id != expected_process_id
            or observation.process_incarnation_id != expected_process_incarnation_id
            or observation.clock_epoch != clock.clock_epoch
            or observation.correlation_epoch != clock.correlation_epoch
            or observation.monotonic_ns <= state.last_monotonic_ns
            or observation.utc_epoch_seconds < state.last_utc_epoch_seconds
            or observation.utc_upper_bound_ns < state.last_utc_upper_bound_ns
            or observation.utc_upper_bound_ns - observation.utc_epoch_seconds * 1_000_000_000
            > _MAX_SIGNING_OBSERVATION_WIDTH_NS
        ):
            _fail()
        state.next_index += 1
        state.last_monotonic_ns = observation.monotonic_ns
        state.last_utc_epoch_seconds = observation.utc_epoch_seconds
        state.last_utc_upper_bound_ns = observation.utc_upper_bound_ns
        return TrustedDispatchInstant.model_validate(observation.model_dump(), strict=True)


_CLASSES_CLOSED = True


def _issue_private_read_trusted_instant_issuer_for_tests(
    marker: object,
    *,
    clock: object,
    clock_currentness: object,
    observations: object,
) -> PrivateReadTrustedInstantIssuer:
    if (
        marker is not _TEST_MARKER
        or type(clock) is not trusted_clock.Task210TrustedDispatchClock
        or type(clock_currentness) is not trusted_clock.Task210TrustedClockCurrentnessPort
        or type(observations) is not tuple
        or not observations
    ):
        _fail()
    owned: list[TrustedDispatchInstant] = []
    for value in observations:
        if type(value) is not TrustedDispatchInstant:
            _fail()
        try:
            exact = TrustedDispatchInstant.model_validate(value.model_dump(), strict=True)
        except Exception:
            _fail()
        owned.append(exact)
    baseline = clock.observe_nonblocking(
        expected_process_incarnation_id=clock.process_incarnation_id,
        expected_clock_epoch=clock.clock_epoch,
    )
    issuer = object.__new__(PrivateReadTrustedInstantIssuer)
    state = _IssuerState(
        issuer=issuer,
        clock=clock,
        currentness=clock_currentness,
        observations=tuple(owned),
        process_id=os.getpid(),
        thread_id=threading.get_ident(),
        loop_id=_loop_identity(),
        last_monotonic_ns=baseline.monotonic_ns,
        last_utc_epoch_seconds=baseline.utc_epoch_seconds,
        last_utc_upper_bound_ns=baseline.utc_upper_bound_ns,
    )
    _STATES[id(issuer)] = state
    return issuer


def _private_read_trusted_instant_marker_for_tests() -> object:
    return _TEST_MARKER


__all__ = ["PrivateReadTrustedInstantIssuer"]
