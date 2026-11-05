"""Sealed Task 2.10 single-private-worker ownership proof supplier."""

from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import NoReturn, Self, SupportsIndex
from uuid import UUID
from zoneinfo import ZoneInfo

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.domain.private_primitives import (
    ExactUuid,
    ExternalUserText,
    StrictPositivePid,
)
from hmt_crypto.errors import ConfigurationError

_MAX_I63 = 2**63 - 1
_UUID_INT_SLOT = vars(UUID)["int"]
_MIN_TIME = datetime(2000, 1, 1, tzinfo=UTC)
_MAX_TIME = datetime(2100, 1, 1, tzinfo=UTC)
_TEST_FIXTURE_MARKER = object()


def _error() -> ConfigurationError:
    return ConfigurationError(context={"operation": "task210_worker_ownership"})


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
    if type(raw) is not int or not 1 <= raw <= 2**128 - 1:
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


def _exact_revision(value: object) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 128
        or not value.isascii()
        or any(not 0x21 <= ord(character) <= 0x7E for character in value)
    ):
        _fail()
    return value


def _exact_external_user(value: object) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 19
        or value[0] == "0"
        or not value.isascii()
        or not value.isdigit()
    ):
        _fail()
    try:
        parsed = int(value)
    except Exception:
        _fail()
    if not 1 <= parsed <= _MAX_I63:
        _fail()
    return value


def _loop_identity() -> int | None:
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        return None


@dataclass(frozen=True, slots=True)
class _WorkerRecord:
    proof_id: UUID
    deployment_revision: str
    external_user_id: str
    worker_incarnation_id: UUID
    process_incarnation_id: UUID
    process_id: int
    region: DeltaRegion
    environment: DeltaEnvironment
    ownership_epoch: int
    issued_at: datetime
    expires_at: datetime


class _WorkerState:
    __slots__ = (
        "active",
        "currentness",
        "loop_id",
        "observed_at",
        "process_started_monotonic_ns",
        "proof",
        "record",
        "thread_id",
    )

    def __init__(
        self,
        *,
        proof: Task210SinglePrivateWorkerProof,
        currentness: Task210PrivateWorkerCurrentnessPort,
        record: _WorkerRecord,
        observed_at: datetime,
        process_started_monotonic_ns: int,
    ) -> None:
        self.proof = proof
        self.currentness = currentness
        self.record = record
        self.observed_at = observed_at
        self.process_started_monotonic_ns = process_started_monotonic_ns
        self.thread_id = threading.get_ident()
        self.loop_id = _loop_identity()
        self.active = True


_PROOF_STATES: dict[int, _WorkerState] = {}
_PROOF_OBJECTS: dict[int, Task210SinglePrivateWorkerProof] = {}
_PROOF_RECORDS: dict[int, _WorkerRecord] = {}
_PROOF_PREDECESSORS: dict[int, Task210SinglePrivateWorkerProof | None] = {}
_CURRENTNESS_STATES: dict[int, _WorkerState] = {}
_CLASSES_CLOSED = False


class _OpaqueWorkerAuthority:
    __slots__ = ()

    def __new__(cls, *_args: object, **_kwargs: object) -> Self:
        raise TypeError("Task 2.10 worker authority cannot be constructed")

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if _CLASSES_CLOSED:
            raise TypeError("Task 2.10 worker authority cannot be subclassed")

    def __setattr__(self, _name: str, _value: object) -> NoReturn:
        raise AttributeError("Task 2.10 worker authority is immutable")

    def __delattr__(self, _name: str) -> NoReturn:
        raise AttributeError("Task 2.10 worker authority is immutable")

    def __copy__(self) -> NoReturn:
        raise TypeError("Task 2.10 worker authority cannot be copied")

    def __deepcopy__(self, _memo: dict[int, object]) -> NoReturn:
        raise TypeError("Task 2.10 worker authority cannot be copied")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError("Task 2.10 worker authority cannot be serialized")

    def __reduce__(self) -> NoReturn:
        raise TypeError("Task 2.10 worker authority cannot be serialized")

    def __getstate__(self) -> NoReturn:
        raise TypeError("Task 2.10 worker authority cannot be serialized")

    def __hash__(self) -> NoReturn:
        raise TypeError("Task 2.10 worker authority is equality-free")

    def __eq__(self, _other: object) -> NoReturn:
        raise TypeError("Task 2.10 worker authority is equality-free")


def _proof_state(value: object) -> _WorkerState:
    if type(value) is not Task210SinglePrivateWorkerProof:
        _fail()
    state = _PROOF_STATES.get(id(value))
    if state is None or _PROOF_OBJECTS.get(id(value)) is not value:
        _fail()
    return state


def _proof_record(value: object) -> _WorkerRecord:
    _proof_state(value)
    record = _PROOF_RECORDS.get(id(value))
    if type(record) is not _WorkerRecord:
        _fail()
    return record


def _currentness_state(value: object) -> _WorkerState:
    if type(value) is not Task210PrivateWorkerCurrentnessPort:
        _fail()
    state = _CURRENTNESS_STATES.get(id(value))
    if state is None or state.currentness is not value:
        _fail()
    return state


def _require_context(state: _WorkerState) -> None:
    record = state.record
    if (
        not state.active
        or os.getpid() != record.process_id
        or threading.get_ident() != state.thread_id
        or _loop_identity() != state.loop_id
        or state.observed_at >= record.expires_at
    ):
        _fail()


class Task210SinglePrivateWorkerProof(_OpaqueWorkerAuthority):
    """Sealed evidence that one exact Task 2.10 worker owns private work."""

    __slots__ = ()

    @property
    def proof_id(self) -> UUID:
        """Return the proof UUID as a defensive copy."""
        return _exact_uuid(_proof_record(self).proof_id)

    @property
    def deployment_revision(self) -> str:
        """Return the bound deployment revision."""
        return _proof_record(self).deployment_revision

    @property
    def external_user_id(self) -> str:
        """Return the bound external-user identifier."""
        return _proof_record(self).external_user_id

    @property
    def worker_incarnation_id(self) -> UUID:
        """Return the worker-incarnation UUID as a defensive copy."""
        return _exact_uuid(_proof_record(self).worker_incarnation_id)

    @property
    def process_incarnation_id(self) -> UUID:
        """Return the process-incarnation UUID as a defensive copy."""
        return _exact_uuid(_proof_record(self).process_incarnation_id)

    @property
    def process_id(self) -> int:
        """Return the bound operating-system process ID."""
        return _proof_record(self).process_id

    @property
    def region(self) -> DeltaRegion:
        """Return the exact bound Delta region."""
        return _proof_record(self).region

    @property
    def environment(self) -> DeltaEnvironment:
        """Return the exact bound Delta environment."""
        return _proof_record(self).environment

    @property
    def ownership_epoch(self) -> int:
        """Return the exact ownership epoch."""
        return _proof_record(self).ownership_epoch

    @property
    def issued_at(self) -> datetime:
        """Return the exact UTC issuance time."""
        return _proof_record(self).issued_at

    @property
    def expires_at(self) -> datetime:
        """Return the exact UTC expiry time."""
        return _proof_record(self).expires_at

    def __repr__(self) -> str:
        return "Task210SinglePrivateWorkerProof(<redacted>)"

    __str__ = __repr__


class Task210PrivateWorkerCurrentnessPort(_OpaqueWorkerAuthority):
    """Exact synchronous owner that revalidates one worker proof identity."""

    __slots__ = ()

    def revalidate_single_private_worker_nonblocking(
        self,
        proof: Task210SinglePrivateWorkerProof,
        *,
        expected_external_user_id: ExternalUserText,
        expected_region: DeltaRegion,
        expected_environment: DeltaEnvironment,
        expected_process_id: StrictPositivePid,
        expected_process_incarnation_id: ExactUuid,
    ) -> Task210SinglePrivateWorkerProof:
        """Revalidate and return the same exact current proof without I/O."""
        state = _currentness_state(self)
        if proof is not state.proof or _proof_state(proof) is not state:
            _fail()
        if type(expected_external_user_id) is not str:
            _fail()
        if expected_region is not DeltaRegion.INDIA:
            _fail()
        if (
            expected_environment is not DeltaEnvironment.TESTNET
            and expected_environment is not DeltaEnvironment.PROD
        ):
            _fail()
        process_id = _exact_int(expected_process_id, minimum=1, maximum=2**31 - 1)
        incarnation = _exact_uuid(expected_process_incarnation_id)
        record = state.record
        if (
            not str.__eq__(expected_external_user_id, record.external_user_id)
            or expected_environment is not record.environment
            or process_id != record.process_id
            or incarnation != record.process_incarnation_id
        ):
            _fail()
        _require_context(state)
        return proof

    def revalidate_worker_replacement_pair_nonblocking(
        self,
        current: Task210SinglePrivateWorkerProof,
        replacement: Task210SinglePrivateWorkerProof,
        *,
        expected_external_user_id: ExternalUserText,
        expected_region: DeltaRegion,
        expected_environment: DeltaEnvironment,
        expected_process_id: StrictPositivePid,
        expected_process_incarnation_id: ExactUuid,
    ) -> tuple[Task210SinglePrivateWorkerProof, Task210SinglePrivateWorkerProof]:
        """Revalidate one exact immutable predecessor/replacement lineage pair."""
        state = _currentness_state(self)
        if (
            replacement is not state.proof
            or _proof_state(current) is not state
            or _proof_state(replacement) is not state
            or _PROOF_PREDECESSORS.get(id(replacement)) is not current
        ):
            _fail()
        current_record = _proof_record(current)
        replacement_record = _proof_record(replacement)
        if type(expected_external_user_id) is not str:
            _fail()
        process_id = _exact_int(expected_process_id, minimum=1, maximum=2**31 - 1)
        incarnation = _exact_uuid(expected_process_incarnation_id)
        expected_owner = (
            expected_external_user_id,
            expected_region,
            expected_environment,
            process_id,
            incarnation,
        )
        current_owner = (
            current_record.external_user_id,
            current_record.region,
            current_record.environment,
            current_record.process_id,
            current_record.process_incarnation_id,
        )
        replacement_owner = (
            replacement_record.external_user_id,
            replacement_record.region,
            replacement_record.environment,
            replacement_record.process_id,
            replacement_record.process_incarnation_id,
        )
        if (
            expected_region is not DeltaRegion.INDIA
            or expected_environment not in (DeltaEnvironment.TESTNET, DeltaEnvironment.PROD)
            or current_owner != expected_owner
            or replacement_owner != expected_owner
            or current_record.deployment_revision != replacement_record.deployment_revision
            or current_record.worker_incarnation_id != replacement_record.worker_incarnation_id
            or replacement_record.ownership_epoch <= current_record.ownership_epoch
            or replacement_record.expires_at <= current_record.expires_at
            or state.observed_at >= current_record.expires_at
            or state.observed_at >= replacement_record.expires_at
        ):
            _fail()
        _require_context(state)
        return current, replacement

    def __repr__(self) -> str:
        return "Task210PrivateWorkerCurrentnessPort(<redacted>)"

    __str__ = __repr__


_CLASSES_CLOSED = True


def _make_record(
    *,
    proof_id: object,
    deployment_revision: object,
    external_user_id: object,
    worker_incarnation_id: object,
    process_incarnation_id: object,
    region: object,
    environment: object,
    ownership_epoch: object,
    issued_at: object,
    expires_at: object,
) -> _WorkerRecord:
    if region is not DeltaRegion.INDIA:
        _fail()
    if environment is not DeltaEnvironment.TESTNET and environment is not DeltaEnvironment.PROD:
        _fail()
    issued = _exact_utc(issued_at)
    expires = _exact_utc(expires_at)
    if issued >= expires:
        _fail()
    return _WorkerRecord(
        proof_id=_exact_uuid(proof_id),
        deployment_revision=_exact_revision(deployment_revision),
        external_user_id=_exact_external_user(external_user_id),
        worker_incarnation_id=_exact_uuid(worker_incarnation_id),
        process_incarnation_id=_exact_uuid(process_incarnation_id),
        process_id=os.getpid(),
        region=region,
        environment=environment,
        ownership_epoch=_exact_int(ownership_epoch, minimum=1),
        issued_at=issued,
        expires_at=expires,
    )


def _issue_single_private_worker_for_tests(
    marker: object,
    *,
    proof_id: object,
    deployment_revision: object,
    external_user_id: object,
    worker_incarnation_id: object,
    process_incarnation_id: object,
    region: object,
    environment: object,
    ownership_epoch: object,
    issued_at: object,
    expires_at: object,
    observed_at: object,
    process_started_monotonic_ns: object,
) -> tuple[Task210SinglePrivateWorkerProof, Task210PrivateWorkerCurrentnessPort]:
    if marker is not _TEST_FIXTURE_MARKER:
        _fail()
    record = _make_record(
        proof_id=proof_id,
        deployment_revision=deployment_revision,
        external_user_id=external_user_id,
        worker_incarnation_id=worker_incarnation_id,
        process_incarnation_id=process_incarnation_id,
        region=region,
        environment=environment,
        ownership_epoch=ownership_epoch,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    observed = _exact_utc(observed_at)
    started = _exact_int(process_started_monotonic_ns, minimum=0)
    if observed < record.issued_at or observed >= record.expires_at:
        _fail()
    proof = object.__new__(Task210SinglePrivateWorkerProof)
    currentness = object.__new__(Task210PrivateWorkerCurrentnessPort)
    state = _WorkerState(
        proof=proof,
        currentness=currentness,
        record=record,
        observed_at=observed,
        process_started_monotonic_ns=started,
    )
    _PROOF_STATES[id(proof)] = state
    _PROOF_OBJECTS[id(proof)] = proof
    _PROOF_RECORDS[id(proof)] = record
    _PROOF_PREDECESSORS[id(proof)] = None
    _CURRENTNESS_STATES[id(currentness)] = state
    return proof, currentness


def _task210_fixture_marker_for_tests() -> object:
    return _TEST_FIXTURE_MARKER


def _advance_worker_observed_at_for_tests(
    marker: object,
    currentness: object,
    observed_at: object,
) -> None:
    if marker is not _TEST_FIXTURE_MARKER:
        _fail()
    state = _currentness_state(currentness)
    _require_context(state)
    observed = _exact_utc(observed_at)
    if observed < state.observed_at:
        _fail()
    state.observed_at = observed


def _invalidate_worker_ownership_for_tests(marker: object, currentness: object) -> None:
    if marker is not _TEST_FIXTURE_MARKER:
        _fail()
    state = _currentness_state(currentness)
    _require_context(state)
    state.active = False


def _replace_single_private_worker_for_tests(
    marker: object,
    currentness: object,
    *,
    current: object,
    proof_id: object,
    ownership_epoch: object,
    issued_at: object,
    expires_at: object,
    observed_at: object,
) -> Task210SinglePrivateWorkerProof:
    if marker is not _TEST_FIXTURE_MARKER:
        _fail()
    state = _currentness_state(currentness)
    if current is not state.proof or _proof_state(current) is not state:
        _fail()
    _require_context(state)
    prior = state.record
    record = _make_record(
        proof_id=proof_id,
        deployment_revision=prior.deployment_revision,
        external_user_id=prior.external_user_id,
        worker_incarnation_id=prior.worker_incarnation_id,
        process_incarnation_id=prior.process_incarnation_id,
        region=prior.region,
        environment=prior.environment,
        ownership_epoch=ownership_epoch,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    observed = _exact_utc(observed_at)
    if (
        record.proof_id == prior.proof_id
        or record.ownership_epoch <= prior.ownership_epoch
        or record.expires_at <= prior.expires_at
        or record.issued_at < prior.issued_at
        or record.issued_at < state.observed_at
        or observed < state.observed_at
        or observed < record.issued_at
        or observed >= record.expires_at
    ):
        _fail()
    replacement = object.__new__(Task210SinglePrivateWorkerProof)
    state.proof = replacement
    state.record = record
    state.observed_at = observed
    _PROOF_STATES[id(replacement)] = state
    _PROOF_OBJECTS[id(replacement)] = replacement
    _PROOF_RECORDS[id(replacement)] = record
    _PROOF_PREDECESSORS[id(replacement)] = current
    return replacement


def _authenticated_limiter_cold_start_allows_nonblocking(
    currentness: Task210PrivateWorkerCurrentnessPort,
    *,
    observed_monotonic_ns: int,
) -> bool:
    state = _currentness_state(currentness)
    _require_context(state)
    observed = _exact_int(observed_monotonic_ns, minimum=0)
    if observed < state.process_started_monotonic_ns:
        _fail()
    return observed - state.process_started_monotonic_ns >= 300_000_000_000
