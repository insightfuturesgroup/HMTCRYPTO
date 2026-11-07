"""Invariant-specific, flush-only repository for one API-audit event."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Never
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from hmt_crypto.persistence._api_audit_contexts import (
    AuditContext,
    AuditEventKind,
    AuditRetentionMode,
    PayloadSlot,
)
from hmt_crypto.persistence.api_audit_codec import (
    _decode_api_audit_evidence,
    encode_api_audit_summary,
    hash_api_audit_payload_copy,
)
from hmt_crypto.persistence.api_audit_contracts import (
    ApiAuditRecordV1,
    AuditPayloadDescriptor,
    AuditStorageAllocation,
)
from hmt_crypto.persistence.api_audit_keys import derive_event_key
from hmt_crypto.persistence.api_audit_models import ApiAuditEvent
from hmt_crypto.persistence.api_audit_payload_contracts import AuditPayloadSourceV1
from hmt_crypto.persistence.api_audit_payload_models import ApiAuditPayload
from hmt_crypto.persistence.api_audit_service_contracts import ApiAuditAppendRequest
from hmt_crypto.persistence.api_audit_service_errors import (
    AuditCommitUncertain,
    AuditConflict,
    AuditEvidenceRejected,
    AuditInvariantViolation,
    AuditUnavailable,
)
from hmt_crypto.persistence.errors import PersistenceValueError

_COPY_SLOTS = {
    AuditEventKind.ATTEMPT_STARTED: PayloadSlot.REQUEST,
    AuditEventKind.ATTEMPT_FINISHED: PayloadSlot.RESPONSE,
    AuditEventKind.VENUE_PRE_DISPATCH: PayloadSlot.REQUEST,
    AuditEventKind.SIMULATOR_PRE_DISPATCH: PayloadSlot.REQUEST,
}
_TERMINAL_EVENT_KINDS = (
    "LOCAL_REJECTED",
    "VENUE_NO_ENTRY",
    "VENUE_ENTERED",
    "VENUE_TRANSFER_REJECTED",
    "SIMULATOR_OUTCOME",
)
_STREAM_END_KINDS = (*_TERMINAL_EVENT_KINDS, "CALL_FINISHED")


@dataclass(frozen=True, slots=True)
class _ProvisionalAppend:
    """Private scalar copy retained until the service acknowledges transaction exit."""

    context: AuditContext
    event_kind: AuditEventKind
    event_id: UUID
    root_event_id: UUID
    sequence: int
    event_key: str
    audit_recorded_at: datetime
    summary_sha256: str | None
    retention_mode: AuditRetentionMode
    payload_slot: PayloadSlot | None
    payload_schema_version: int | None
    payload_sha256: str | None
    payload_byte_length: int | None
    payload_expires_at: datetime | None
    payload_ttl_seconds: int | None
    retention_policy_version: int | None
    payload_copy_key: str | None


def _safe(error: Exception) -> Never:
    """Raise a prepared safe error outside the originating exception handler."""
    raise error from None


def _context_source(row: ApiAuditEvent, record: ApiAuditRecordV1) -> AuditPayloadSourceV1:
    descriptor = _descriptor_from_row(row)
    summary = record.summary
    if descriptor is None or summary is None:
        raise AuditInvariantViolation()
    try:
        if row.summary_payload is None or row.summary_sha256 is None:
            raise ValueError
        source = AuditPayloadSourceV1(
            owning_event_id=row.id,
            context=record.context,
            event_kind=record.event_kind,
            summary_kind=summary.summary_kind,
            summary_payload=row.summary_payload,
            summary_sha256=row.summary_sha256,
            audit_recorded_at=row.audit_recorded_at,
            descriptor=descriptor,
        )
    except Exception:
        error = AuditInvariantViolation()
    else:
        return source
    _safe(error)


def _descriptor_from_row(row: ApiAuditEvent) -> AuditPayloadDescriptor | None:
    if row.retention_mode == "SUMMARY_ONLY":
        fields = (
            row.payload_slot,
            row.payload_schema_version,
            row.payload_sha256,
            row.payload_byte_length,
            row.payload_expires_at,
            row.payload_ttl_seconds,
            row.retention_policy_version,
        )
        if any(value is not None for value in fields):
            raise AuditInvariantViolation()
        return None
    try:
        slot = row.payload_slot
        if type(slot) is not str:
            raise ValueError
        descriptor = AuditPayloadDescriptor(
            retention_mode=AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT,
            payload_slot=PayloadSlot(slot),
            payload_schema_version=row.payload_schema_version,
            payload_sha256=row.payload_sha256,
            payload_byte_length=row.payload_byte_length,
            payload_expires_at=row.payload_expires_at,
            payload_ttl_seconds=row.payload_ttl_seconds,
            retention_policy_version=row.retention_policy_version,
        )
    except Exception:
        error = AuditInvariantViolation()
    else:
        return descriptor
    _safe(error)


def _retention_matches(
    request: ApiAuditAppendRequest, descriptor: AuditPayloadDescriptor | None
) -> bool:
    policy = request.retention
    if policy.mode is AuditRetentionMode.SUMMARY_ONLY:
        return descriptor is None
    return bool(
        descriptor is not None
        and descriptor.retention_mode is policy.mode
        and descriptor.payload_ttl_seconds == policy.ttl_seconds
        and descriptor.retention_policy_version == policy.retention_policy_version
    )


def _provisional(
    row: ApiAuditEvent,
    record: ApiAuditRecordV1,
    payload: ApiAuditPayload | None,
) -> _ProvisionalAppend:
    try:
        root = row.root_event_id
        if type(root) is not UUID:
            raise ValueError
        mode = AuditRetentionMode(row.retention_mode)
        slot = None if row.payload_slot is None else PayloadSlot(row.payload_slot)
        provisional = _ProvisionalAppend(
            context=record.context,
            event_kind=record.event_kind,
            event_id=row.id,
            root_event_id=root,
            sequence=row.sequence,
            event_key=row.event_key,
            audit_recorded_at=row.audit_recorded_at,
            summary_sha256=row.summary_sha256,
            retention_mode=mode,
            payload_slot=slot,
            payload_schema_version=row.payload_schema_version,
            payload_sha256=row.payload_sha256,
            payload_byte_length=row.payload_byte_length,
            payload_expires_at=row.payload_expires_at,
            payload_ttl_seconds=row.payload_ttl_seconds,
            retention_policy_version=row.retention_policy_version,
            payload_copy_key=None if payload is None else payload.copy_key,
        )
    except Exception:
        error = AuditInvariantViolation()
    else:
        return provisional
    _safe(error)


async def _database_utc(session: AsyncSession) -> datetime:
    """Sample one UTC storage instant from the active database transaction."""
    try:
        dialect = session.get_bind().dialect.name
        value: object
        if dialect == "sqlite":
            raw = await session.scalar(select(func.strftime("%Y-%m-%dT%H:%M:%f+00:00", "now")))
            if type(raw) is not str:
                raise ValueError
            value = datetime.fromisoformat(raw)
        else:
            value = await session.scalar(select(func.clock_timestamp()))
        if type(value) is not datetime or value.utcoffset() != timedelta(0):
            raise ValueError
        return value.astimezone(UTC)
    except SQLAlchemyError:
        error: Exception = AuditUnavailable()
    except Exception:
        error = AuditInvariantViolation()
    _safe(error)


def _new_descriptor(
    request: ApiAuditAppendRequest, recorded_at: datetime
) -> AuditPayloadDescriptor | None:
    policy = request.retention
    if policy.mode is AuditRetentionMode.SUMMARY_ONLY:
        return None
    summary = request.record.summary
    slot = _COPY_SLOTS.get(request.record.event_kind)
    try:
        if summary is None or slot is None:
            raise ValueError
        payload = encode_api_audit_summary(summary)
        assert policy.ttl_seconds is not None
        assert policy.retention_policy_version is not None
        descriptor = AuditPayloadDescriptor(
            retention_mode=policy.mode,
            payload_slot=slot,
            payload_schema_version=1,
            payload_sha256=hash_api_audit_payload_copy(summary),
            payload_byte_length=len(payload),
            payload_expires_at=recorded_at + timedelta(seconds=policy.ttl_seconds),
            payload_ttl_seconds=policy.ttl_seconds,
            retention_policy_version=policy.retention_policy_version,
        )
    except Exception:
        error = AuditEvidenceRejected()
    else:
        return descriptor
    _safe(error)


async def _payloads_for(session: AsyncSession, event_id: UUID) -> tuple[ApiAuditPayload, ...]:
    try:
        return tuple(
            (
                await session.scalars(
                    select(ApiAuditPayload)
                    .where(ApiAuditPayload.owning_event_id == event_id)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
    except PersistenceValueError:
        error: Exception = AuditInvariantViolation()
    except SQLAlchemyError:
        error = AuditUnavailable()
    _safe(error)


async def _existing(
    session: AsyncSession, event_key: str
) -> tuple[ApiAuditEvent, ApiAuditRecordV1, AuditStorageAllocation] | None:
    try:
        row = (
            await session.scalars(
                select(ApiAuditEvent)
                .where(ApiAuditEvent.event_key == event_key)
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if row is None:
            return None
        if type(row.evidence_payload) is not str:
            raise PersistenceValueError()
        record, allocation, _ = _decode_api_audit_evidence(
            row.evidence_payload.encode("ascii"), row.evidence_sha256
        )
        return row, record, allocation
    except PersistenceValueError:
        error: Exception = AuditInvariantViolation()
    except SQLAlchemyError:
        error = AuditUnavailable()
    _safe(error)


async def _replay(
    session: AsyncSession,
    request: ApiAuditAppendRequest,
    existing: tuple[ApiAuditEvent, ApiAuditRecordV1, AuditStorageAllocation],
) -> _ProvisionalAppend:
    row, record, allocation = existing
    descriptor = allocation.descriptor
    if record != request.record or not _retention_matches(request, descriptor):
        raise AuditConflict()
    payloads = await _payloads_for(session, row.id)
    if descriptor is None:
        if payloads:
            raise AuditInvariantViolation()
        return _provisional(row, record, None)
    if len(payloads) != 1:
        raise AuditInvariantViolation()
    payload = payloads[0]
    source = _context_source(row, record)
    try:
        expected = ApiAuditPayload.from_source(source)
        if any(
            getattr(payload, column.name) != getattr(expected, column.name)
            for column in ApiAuditPayload.__table__.columns
        ):
            raise ValueError
    except Exception:
        error = AuditInvariantViolation()
    else:
        return _provisional(row, record, payload)
    _safe(error)


async def _sequence(session: AsyncSession, record: ApiAuditRecordV1) -> int:
    if record.event_kind is AuditEventKind.CALL_STARTED:
        return 1
    root_id = record.root_event_id
    if root_id is None:
        raise AuditEvidenceRejected()
    try:
        root = (
            await session.scalars(
                select(ApiAuditEvent)
                .where(ApiAuditEvent.id == root_id)
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if root is None or root.event_kind != "CALL_STARTED":
            raise AuditEvidenceRejected()
        maximum = await session.scalar(
            select(func.max(ApiAuditEvent.sequence)).where(ApiAuditEvent.root_event_id == root_id)
        )
        if type(maximum) is not int or maximum >= 2**63 - 1:
            raise AuditInvariantViolation()
        return maximum + 1
    except (AuditConflict, AuditEvidenceRejected, AuditInvariantViolation):
        raise
    except PersistenceValueError:
        error: Exception = AuditInvariantViolation()
    except SQLAlchemyError:
        error = AuditUnavailable()
    _safe(error)


async def _stream_is_closed(session: AsyncSession, root_id: UUID) -> bool:
    """Validate every pre-existing terminal/finish row and report stream closure."""
    try:
        rows = tuple(
            (
                await session.scalars(
                    select(ApiAuditEvent)
                    .where(
                        ApiAuditEvent.root_event_id == root_id,
                        ApiAuditEvent.event_kind.in_(_STREAM_END_KINDS),
                    )
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
    except PersistenceValueError:
        error: Exception = AuditInvariantViolation()
    except SQLAlchemyError:
        error = AuditUnavailable()
    else:
        terminals = tuple(row for row in rows if row.event_kind in _TERMINAL_EVENT_KINDS)
        finishes = tuple(row for row in rows if row.event_kind == "CALL_FINISHED")
        if len(terminals) > 1 or len(finishes) > 1:
            raise AuditInvariantViolation()
        if not terminals:
            if not finishes:
                return False
            finish = finishes[0]
            if (
                finish.call_kind != "READ"
                or finish.closure_key is not None
                or finish.closure_sha256 is not None
            ):
                raise AuditInvariantViolation()
            return True
        if not finishes:
            raise AuditInvariantViolation()
        terminal, finish = terminals[0], finishes[0]
        if (
            terminal.closure_key is None
            or terminal.closure_sha256 is None
            or finish.closure_key != terminal.closure_key
            or finish.closure_sha256 != terminal.closure_sha256
            or finish.sequence != terminal.sequence + 1
            or finish.audit_recorded_at != terminal.audit_recorded_at
        ):
            raise AuditInvariantViolation()
        return True
    _safe(error)


async def _fresh(session: AsyncSession, request: ApiAuditAppendRequest) -> _ProvisionalAppend:
    record = request.record
    if record.root_event_id is not None and await _stream_is_closed(session, record.root_event_id):
        raise AuditConflict()
    sequence = await _sequence(session, record)
    recorded_at = await _database_utc(session)
    descriptor = _new_descriptor(request, recorded_at)
    try:
        allocation = AuditStorageAllocation(
            id=uuid4(),
            sequence=sequence,
            audit_recorded_at=recorded_at,
            descriptor=descriptor,
            closure_source=None,
        )
        row = ApiAuditEvent.from_record(record, allocation)
        session.add(row)
        await session.flush()
        payload = None
        if descriptor is not None:
            source = _context_source(row, record)
            payload = ApiAuditPayload.from_source(source)
            session.add(payload)
            await session.flush()
        return _provisional(row, record, payload)
    except (AuditEvidenceRejected, AuditInvariantViolation):
        raise
    except (ValidationError, PersistenceValueError, IntegrityError):
        error: Exception = AuditEvidenceRejected()
    except SQLAlchemyError:
        error = AuditCommitUncertain()
    _safe(error)


class ApiAuditRepository:
    """Append or exactly replay one event using a caller-owned transaction."""

    __slots__ = ()

    async def _append_or_replay(
        self, session: AsyncSession, request: ApiAuditAppendRequest
    ) -> _ProvisionalAppend:
        """Flush new rows only; return private copied evidence without final authority."""
        key = derive_event_key(request.record)
        existing = await _existing(session, key)
        if existing is not None:
            return await _replay(session, request, existing)
        return await _fresh(session, request)


__all__ = ["ApiAuditRepository"]
