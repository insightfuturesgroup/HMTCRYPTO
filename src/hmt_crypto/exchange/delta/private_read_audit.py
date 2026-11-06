"""Bounded redacted durable-audit authority for Delta private reads."""

from __future__ import annotations

import asyncio
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Annotated, NoReturn, Self, SupportsIndex
from uuid import UUID

from pydantic import BeforeValidator, model_validator

from hmt_crypto.domain.account import AccountRef
from hmt_crypto.domain.private_primitives import (
    ExactAwareUtcDatetime,
    ExactStrEnum,
    ExactUuid,
    ExternalUserText,
    PrivateBoundaryModel,
    Sha256Hex,
    StrictPositiveInt,
)
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId

_TEST_MARKER = object()
_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z", re.ASCII)
_MAX_TEST_AUDIT_EVENTS = 10_000


def _optional_status(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 100 <= value <= 599:
        raise ValueError("invalid private-read audit status")
    return value


def _optional_error_code(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str or _ERROR_CODE.fullmatch(value) is None:
        raise ValueError("invalid private-read audit error code")
    return value


class PrivateReadAuditPhase(ExactStrEnum):
    """Durable private-read attempt phases."""

    PRE_DISPATCH = "PRE_DISPATCH"
    TERMINAL = "TERMINAL"


class PrivateReadAuditOutcome(ExactStrEnum):
    """Redacted terminal classification without response or credential material."""

    DISPATCH_PENDING = "DISPATCH_PENDING"
    SUCCESS = "SUCCESS"
    RETRY = "RETRY"
    FAILURE = "FAILURE"


class PrivateReadAuditEvent(PrivateBoundaryModel):
    """One bounded, secret-free private-read audit event."""

    event_id: ExactUuid
    correlation_id: ExactUuid
    account_id: ExactUuid
    external_user_id: ExternalUserText
    route_id: PrivateRouteId
    request_evidence_sha256: Sha256Hex
    attempt: StrictPositiveInt
    phase: PrivateReadAuditPhase
    outcome: PrivateReadAuditOutcome
    occurred_at: ExactAwareUtcDatetime
    http_status: Annotated[int | None, BeforeValidator(_optional_status)] = None
    error_code: Annotated[str | None, BeforeValidator(_optional_error_code)] = None

    @model_validator(mode="after")
    def require_phase_shape(self) -> PrivateReadAuditEvent:
        if self.phase is PrivateReadAuditPhase.PRE_DISPATCH:
            if (
                self.outcome is not PrivateReadAuditOutcome.DISPATCH_PENDING
                or self.http_status is not None
                or self.error_code is not None
            ):
                raise type(self)._safe_error("private_read_audit", "invalid audit event")
        elif self.outcome is PrivateReadAuditOutcome.DISPATCH_PENDING:
            raise type(self)._safe_error("private_read_audit", "invalid audit event")
        return self


class PrivateReadAuditReceipt(PrivateBoundaryModel):
    """Exact durable append acknowledgement."""

    event_id: ExactUuid
    durable_sequence: StrictPositiveInt


def _error() -> ConfigurationError:
    return ConfigurationError(context={"operation": "private_read_audit"})


def _fail() -> NoReturn:
    raise _error() from None


def _loop_identity() -> int | None:
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        return None


@dataclass(slots=True)
class _SinkState:
    sink: PrivateReadAuditSink
    account: AccountRef
    process_id: int
    thread_id: int
    loop_id: int | None
    records: list[PrivateReadAuditEvent] = field(default_factory=list)
    pending: dict[tuple[UUID, int], PrivateReadAuditEvent] = field(default_factory=dict)


_STATES: dict[int, _SinkState] = {}
_CLASSES_CLOSED = False


class _OpaqueSink:
    __slots__ = ()

    def __new__(cls, *_args: object, **_kwargs: object) -> Self:
        raise TypeError("private-read audit sink cannot be constructed")

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if _CLASSES_CLOSED:
            raise TypeError("private-read audit sink cannot be subclassed")

    def __setattr__(self, _name: str, _value: object) -> NoReturn:
        raise AttributeError("private-read audit sink is immutable")

    def __delattr__(self, _name: str) -> NoReturn:
        raise AttributeError("private-read audit sink is immutable")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError("private-read audit sink cannot be serialized")

    def __reduce__(self) -> NoReturn:
        raise TypeError("private-read audit sink cannot be serialized")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>)"

    __str__ = __repr__


def _state(value: object) -> _SinkState:
    if type(value) is not PrivateReadAuditSink:
        _fail()
    state = _STATES.get(id(value))
    if state is None or state.sink is not value:
        _fail()
    if (
        os.getpid() != state.process_id
        or threading.get_ident() != state.thread_id
        or _loop_identity() != state.loop_id
    ):
        _fail()
    return state


class PrivateReadAuditSink(_OpaqueSink):
    """Exact sink whose append must durably acknowledge one redacted event."""

    __slots__ = ()

    def attest_account_nonblocking(self, account: AccountRef) -> None:
        """Attest the exact account/lifecycle association without appending."""
        state = _state(self)
        if type(account) is not AccountRef or account.model_dump() != state.account.model_dump():
            _fail()

    async def append_durable(self, event: PrivateReadAuditEvent) -> PrivateReadAuditReceipt:
        """Append one exact event and return its durable identity."""
        state = _state(self)
        if type(event) is not PrivateReadAuditEvent:
            _fail()
        try:
            owned = PrivateReadAuditEvent.model_validate(event.model_dump(), strict=True)
        except Exception:
            _fail()
        key = (owned.correlation_id, owned.attempt)
        if len(state.records) >= _MAX_TEST_AUDIT_EVENTS:
            _fail()
        if owned.phase is PrivateReadAuditPhase.PRE_DISPATCH:
            if key in state.pending:
                _fail()
            state.pending[key] = owned
        else:
            pending = state.pending.get(key)
            if (
                pending is None
                or pending.account_id != owned.account_id
                or pending.external_user_id != owned.external_user_id
                or pending.route_id is not owned.route_id
                or pending.request_evidence_sha256 != owned.request_evidence_sha256
                or owned.occurred_at < pending.occurred_at
            ):
                _fail()
            del state.pending[key]
        state.records.append(owned)
        return PrivateReadAuditReceipt(
            event_id=owned.event_id,
            durable_sequence=len(state.records),
        )


_CLASSES_CLOSED = True


def _issue_private_read_audit_sink_for_tests(
    marker: object,
    *,
    account: object,
) -> PrivateReadAuditSink:
    if marker is not _TEST_MARKER or type(account) is not AccountRef:
        _fail()
    sink = object.__new__(PrivateReadAuditSink)
    _STATES[id(sink)] = _SinkState(
        sink=sink,
        account=AccountRef.model_validate(account).model_copy(),
        process_id=os.getpid(),
        thread_id=threading.get_ident(),
        loop_id=_loop_identity(),
    )
    return sink


def _private_read_audit_records_for_tests(
    marker: object,
    sink: object,
) -> tuple[PrivateReadAuditEvent, ...]:
    if marker is not _TEST_MARKER:
        _fail()
    return tuple(
        PrivateReadAuditEvent.model_validate(event.model_dump(), strict=True)
        for event in _state(sink).records
    )


def _private_read_audit_marker_for_tests() -> object:
    return _TEST_MARKER


__all__ = [
    "PrivateReadAuditEvent",
    "PrivateReadAuditOutcome",
    "PrivateReadAuditPhase",
    "PrivateReadAuditReceipt",
    "PrivateReadAuditSink",
]
