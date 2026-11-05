"""Sealed Task 2.10 prewarmed trusted-dispatch clock supplier."""

from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import NoReturn, Self, SupportsIndex
from uuid import UUID
from zoneinfo import ZoneInfo

from hmt_crypto.domain.private_primitives import ExactUuid, StrictEpoch, StrictPositivePid
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.private_dispatch import (
    TrustedDispatchDeadline as _DispatchDeadline,
)
from hmt_crypto.exchange.private_dispatch import (
    TrustedDispatchInstant as _DispatchInstant,
)
from hmt_crypto.exchange.private_dispatch import (
    TrustedUtcExpiry as _UtcExpiry,
)

_MAX_I63 = 2**63 - 1
_MAX_PID = 2**31 - 1
_MAX_UUID_INT = 2**128 - 1
_UUID_INT_SLOT = vars(UUID)["int"]
_MIN_TIME = datetime(2000, 1, 1, tzinfo=UTC)
_MAX_TIME = datetime(2100, 1, 1, tzinfo=UTC)
_TEST_FIXTURE_MARKER = object()


def _error() -> ConfigurationError:
    return ConfigurationError(context={"operation": "task210_trusted_clock"})


def _fail() -> NoReturn:
    raise _error() from None


def _exact_int(value: object, *, minimum: int, maximum: int = _MAX_I63) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        _fail()
    return value


def _exact_uuid(value: object) -> UUID:
    if type(value) is not UUID:
        _fail()
    try:
        raw = _UUID_INT_SLOT.__get__(value, UUID)
    except Exception:
        _fail()
    if type(raw) is not int or not 1 <= raw <= _MAX_UUID_INT:
        _fail()
    return UUID(int=raw)


def _exact_utc(value: object) -> datetime:
    if type(value) is not datetime or type(value.tzinfo) not in (timezone, ZoneInfo):
        _fail()
    try:
        if value.utcoffset() != timedelta(0):
            _fail()
        result = value.astimezone(UTC)
    except ConfigurationError:
        raise
    except Exception:
        _fail()
    if not _MIN_TIME <= result <= _MAX_TIME:
        _fail()
    return result


def _utc_ns(value: object) -> int:
    exact = _exact_utc(value)
    delta = exact - datetime(1970, 1, 1, tzinfo=UTC)
    result = (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000
    return _exact_int(result, minimum=0)


def _validated_expiry_ns(value: object) -> int:
    if type(value) is not _UtcExpiry:
        _fail()
    try:
        validated = _UtcExpiry.model_validate(value, strict=True)
        if type(validated) is _UtcExpiry and validated is not value:
            return _utc_ns(validated.expires_at)
    except Exception:
        pass
    _fail()


def _loop_identity() -> int | None:
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        return None


@dataclass(frozen=True, slots=True)
class _ClockRecord:
    clock_owner_id: UUID
    process_id: int
    process_incarnation_id: UUID
    clock_epoch: int
    correlation_epoch: int
    sample_monotonic_ns: int
    observed_monotonic_ns: int
    utc_epoch_seconds: int
    utc_upper_bound_ns: int
    nonblocking_execution_guard_ns: int


class _ClockState:
    __slots__ = (
        "active",
        "clock",
        "currentness",
        "invalidation_correlation_id",
        "loop_id",
        "record",
        "thread_id",
    )

    def __init__(
        self,
        *,
        clock: Task210TrustedDispatchClock,
        currentness: Task210TrustedClockCurrentnessPort,
        record: _ClockRecord,
    ) -> None:
        self.clock = clock
        self.currentness = currentness
        self.record = record
        self.thread_id = threading.get_ident()
        self.loop_id = _loop_identity()
        self.active = True
        self.invalidation_correlation_id: UUID | None = None


_CLOCK_STATES: dict[int, _ClockState] = {}
_CURRENTNESS_STATES: dict[int, _ClockState] = {}
_CLASSES_CLOSED = False


class _OpaqueClockAuthority:
    __slots__ = ()

    def __new__(cls, *_args: object, **_kwargs: object) -> Self:
        raise TypeError("Task 2.10 clock authority cannot be constructed")

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if _CLASSES_CLOSED:
            raise TypeError("Task 2.10 clock authority cannot be subclassed")

    def __setattr__(self, _name: str, _value: object) -> NoReturn:
        raise AttributeError("Task 2.10 clock authority is immutable")

    def __delattr__(self, _name: str) -> NoReturn:
        raise AttributeError("Task 2.10 clock authority is immutable")

    def __copy__(self) -> NoReturn:
        raise TypeError("Task 2.10 clock authority cannot be copied")

    def __deepcopy__(self, _memo: dict[int, object]) -> NoReturn:
        raise TypeError("Task 2.10 clock authority cannot be copied")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError("Task 2.10 clock authority cannot be serialized")

    def __reduce__(self) -> NoReturn:
        raise TypeError("Task 2.10 clock authority cannot be serialized")

    def __getstate__(self) -> NoReturn:
        raise TypeError("Task 2.10 clock authority cannot be serialized")

    def __hash__(self) -> NoReturn:
        raise TypeError("Task 2.10 clock authority is equality-free")

    def __eq__(self, _other: object) -> NoReturn:
        raise TypeError("Task 2.10 clock authority is equality-free")


def _clock_state(value: object) -> _ClockState:
    if type(value) is not Task210TrustedDispatchClock:
        _fail()
    state = _CLOCK_STATES.get(id(value))
    if state is None or state.clock is not value:
        _fail()
    return state


def _currentness_state(value: object) -> _ClockState:
    if type(value) is not Task210TrustedClockCurrentnessPort:
        _fail()
    state = _CURRENTNESS_STATES.get(id(value))
    if state is None or state.currentness is not value:
        _fail()
    return state


def _require_context(state: _ClockState) -> None:
    if (
        not state.active
        or os.getpid() != state.record.process_id
        or threading.get_ident() != state.thread_id
        or _loop_identity() != state.loop_id
    ):
        _fail()


def _require_expected_clock(
    state: _ClockState,
    *,
    expected_process_incarnation_id: object,
    expected_clock_epoch: object,
) -> None:
    incarnation = _exact_uuid(expected_process_incarnation_id)
    epoch = _exact_int(expected_clock_epoch, minimum=1)
    if incarnation != state.record.process_incarnation_id or epoch != state.record.clock_epoch:
        _fail()
    _require_context(state)


class Task210TrustedDispatchClock(_OpaqueClockAuthority):
    """Exact nonblocking owner of one prewarmed UTC/monotonic publication."""

    __slots__ = ()

    @property
    def clock_owner_id(self) -> UUID:
        """Return the owner UUID as a defensive copy."""
        return _exact_uuid(_clock_state(self).record.clock_owner_id)

    @property
    def process_id(self) -> int:
        """Return the bound operating-system process ID."""
        return _clock_state(self).record.process_id

    @property
    def process_incarnation_id(self) -> UUID:
        """Return the process-incarnation UUID as a defensive copy."""
        return _exact_uuid(_clock_state(self).record.process_incarnation_id)

    @property
    def clock_epoch(self) -> int:
        """Return the exact clock epoch."""
        return _clock_state(self).record.clock_epoch

    @property
    def correlation_epoch(self) -> int:
        """Return the exact UTC/monotonic correlation epoch."""
        return _clock_state(self).record.correlation_epoch

    def derive_deadline_nonblocking(
        self,
        *,
        utc_expiries: tuple[_UtcExpiry, ...],
        monotonic_deadlines_ns: tuple[int, ...],
        expected_process_incarnation_id: ExactUuid,
        expected_clock_epoch: StrictEpoch,
    ) -> _DispatchDeadline:
        """Derive one conservative future deadline without I/O or callbacks."""
        state = _clock_state(self)
        _require_expected_clock(
            state,
            expected_process_incarnation_id=expected_process_incarnation_id,
            expected_clock_epoch=expected_clock_epoch,
        )
        if type(utc_expiries) is not tuple or type(monotonic_deadlines_ns) is not tuple:
            _fail()
        if not utc_expiries and not monotonic_deadlines_ns:
            _fail()
        record = state.record
        candidates: list[int] = []
        for expiry in utc_expiries:
            expiry_ns = _validated_expiry_ns(expiry)
            remaining_ns = expiry_ns - record.utc_upper_bound_ns
            if remaining_ns <= record.nonblocking_execution_guard_ns:
                _fail()
            candidate = (
                record.sample_monotonic_ns + remaining_ns - record.nonblocking_execution_guard_ns
            )
            candidates.append(_exact_int(candidate, minimum=0))
        for deadline in monotonic_deadlines_ns:
            candidates.append(_exact_int(deadline, minimum=0))
        result = min(candidates)
        if result <= record.observed_monotonic_ns:
            _fail()
        return _DispatchDeadline(
            process_id=record.process_id,
            process_incarnation_id=record.process_incarnation_id,
            clock_epoch=record.clock_epoch,
            monotonic_ns=result,
            correlation_epoch=record.correlation_epoch,
        )

    def observe_nonblocking(
        self,
        *,
        expected_process_incarnation_id: ExactUuid,
        expected_clock_epoch: StrictEpoch,
    ) -> _DispatchInstant:
        """Return one copied observation of the prewarmed correlation."""
        state = _clock_state(self)
        _require_expected_clock(
            state,
            expected_process_incarnation_id=expected_process_incarnation_id,
            expected_clock_epoch=expected_clock_epoch,
        )
        record = state.record
        return _DispatchInstant(
            process_id=record.process_id,
            process_incarnation_id=record.process_incarnation_id,
            clock_epoch=record.clock_epoch,
            correlation_epoch=record.correlation_epoch,
            monotonic_ns=record.observed_monotonic_ns,
            utc_epoch_seconds=record.utc_epoch_seconds,
            utc_upper_bound_ns=record.utc_upper_bound_ns,
        )

    def __repr__(self) -> str:
        return "Task210TrustedDispatchClock(<redacted>)"

    __str__ = __repr__


class Task210TrustedClockCurrentnessPort(_OpaqueClockAuthority):
    """Exact synchronous owner that revalidates one trusted clock identity."""

    __slots__ = ()

    def revalidate_dispatch_clock_nonblocking(
        self,
        clock: Task210TrustedDispatchClock,
        *,
        expected_process_id: StrictPositivePid,
        expected_process_incarnation_id: ExactUuid,
    ) -> Task210TrustedDispatchClock:
        """Revalidate and return the same exact current clock without I/O."""
        state = _currentness_state(self)
        if clock is not state.clock or _clock_state(clock) is not state:
            _fail()
        process_id = _exact_int(expected_process_id, minimum=1, maximum=_MAX_PID)
        incarnation = _exact_uuid(expected_process_incarnation_id)
        if (
            process_id != state.record.process_id
            or incarnation != state.record.process_incarnation_id
        ):
            _fail()
        _require_context(state)
        return clock

    def invalidate_signature_expired_nonblocking(
        self,
        clock: Task210TrustedDispatchClock,
        *,
        correlation_id: ExactUuid,
    ) -> None:
        """Publish terminal signature-expiry invalidation for this clock."""
        state = _currentness_state(self)
        if clock is not state.clock or _clock_state(clock) is not state:
            _fail()
        correlation = _exact_uuid(correlation_id)
        _require_context(state)
        state.active = False
        state.invalidation_correlation_id = correlation

    def __repr__(self) -> str:
        return "Task210TrustedClockCurrentnessPort(<redacted>)"

    __str__ = __repr__


_CLASSES_CLOSED = True


def _issue_trusted_dispatch_clock_for_tests(
    marker: object,
    *,
    clock_owner_id: object,
    process_incarnation_id: object,
    clock_epoch: object,
    correlation_epoch: object,
    sample_monotonic_ns: object,
    observed_monotonic_ns: object,
    utc_epoch_seconds: object,
    utc_upper_bound_ns: object,
    nonblocking_execution_guard_ns: object,
) -> tuple[Task210TrustedDispatchClock, Task210TrustedClockCurrentnessPort]:
    if marker is not _TEST_FIXTURE_MARKER:
        _fail()
    sample = _exact_int(sample_monotonic_ns, minimum=0)
    observed = _exact_int(observed_monotonic_ns, minimum=0)
    utc_seconds = _exact_int(utc_epoch_seconds, minimum=0)
    utc_upper = _exact_int(utc_upper_bound_ns, minimum=0)
    if observed < sample or utc_upper < utc_seconds * 1_000_000_000:
        _fail()
    record = _ClockRecord(
        clock_owner_id=_exact_uuid(clock_owner_id),
        process_id=os.getpid(),
        process_incarnation_id=_exact_uuid(process_incarnation_id),
        clock_epoch=_exact_int(clock_epoch, minimum=1),
        correlation_epoch=_exact_int(correlation_epoch, minimum=1),
        sample_monotonic_ns=sample,
        observed_monotonic_ns=observed,
        utc_epoch_seconds=utc_seconds,
        utc_upper_bound_ns=utc_upper,
        nonblocking_execution_guard_ns=_exact_int(
            nonblocking_execution_guard_ns,
            minimum=0,
        ),
    )
    clock = object.__new__(Task210TrustedDispatchClock)
    currentness = object.__new__(Task210TrustedClockCurrentnessPort)
    state = _ClockState(clock=clock, currentness=currentness, record=record)
    _CLOCK_STATES[id(clock)] = state
    _CURRENTNESS_STATES[id(currentness)] = state
    return clock, currentness


def _task210_fixture_marker_for_tests() -> object:
    return _TEST_FIXTURE_MARKER
