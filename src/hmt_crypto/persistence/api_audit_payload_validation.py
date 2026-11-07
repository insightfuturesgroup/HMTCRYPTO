"""Complete-source admission and append-only ORM boundaries for D13b copies."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Never, TypeVar
from uuid import UUID

from sqlalchemy import event, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import attributes, object_session

from hmt_crypto._private_enums import exact_enum_member_for_literal
from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.persistence.api_audit_contracts import (
    AccountAuditContext,
    AuditContextKind,
    AuditEventKind,
    AuditPayloadDescriptor,
    AuditRetentionMode,
    AuditSummaryKind,
    PayloadSlot,
    PublicAuditContext,
)
from hmt_crypto.persistence.api_audit_payload_contracts import AuditPayloadSourceV1
from hmt_crypto.persistence.errors import PersistenceValueError

E = TypeVar("E", bound=StrEnum)
_PARENT_COLUMNS = (
    "id",
    "context_kind",
    "region",
    "environment",
    "account_id",
    "mode",
    "event_kind",
    "entry_possible",
    "summary_kind",
    "summary_payload",
    "summary_sha256",
    "audit_recorded_at",
    "retention_mode",
    "payload_slot",
    "payload_schema_version",
    "payload_sha256",
    "payload_byte_length",
    "payload_expires_at",
    "payload_ttl_seconds",
    "retention_policy_version",
)


def _fail() -> Never:
    raise PersistenceValueError() from None


def _member(enum_type: type[E], value: object) -> E:
    member = exact_enum_member_for_literal(enum_type, value)
    if member is None:
        _fail()
    return member


def _uuid(value: object) -> UUID:
    if type(value) is not UUID:
        _fail()
    return value


def _integer(value: object) -> int:
    if type(value) is not int:
        _fail()
    return value


def _text(value: object) -> str:
    if type(value) is not str:
        _fail()
    return value


def _datetime(value: object) -> datetime:
    if type(value) is not datetime:
        _fail()
    return value


def _source_from_parent(row: dict[str, object]) -> AuditPayloadSourceV1:
    if set(row) != set(_PARENT_COLUMNS):
        _fail()
    region = _member(DeltaRegion, row["region"])
    environment = _member(DeltaEnvironment, row["environment"])
    context_kind = _member(AuditContextKind, row["context_kind"])
    context: PublicAuditContext | AccountAuditContext
    if context_kind is AuditContextKind.PUBLIC:
        if row["account_id"] is not None or row["mode"] is not None:
            _fail()
        context = PublicAuditContext(
            context_kind=context_kind,
            region=region,
            environment=environment,
        )
    elif context_kind is AuditContextKind.ACCOUNT:
        context = AccountAuditContext(
            context_kind=context_kind,
            account_id=_uuid(row["account_id"]),
            region=region,
            environment=environment,
            mode=_member(ExecutionMode, row["mode"]),
        )
    else:
        _fail()
    descriptor = AuditPayloadDescriptor(
        retention_mode=_member(AuditRetentionMode, row["retention_mode"]),
        payload_slot=_member(PayloadSlot, row["payload_slot"]),
        payload_schema_version=_integer(row["payload_schema_version"]),
        payload_sha256=_text(row["payload_sha256"]),
        payload_byte_length=_integer(row["payload_byte_length"]),
        payload_expires_at=_datetime(row["payload_expires_at"]),
        payload_ttl_seconds=_integer(row["payload_ttl_seconds"]),
        retention_policy_version=_integer(row["retention_policy_version"]),
    )
    owning_event_id = _uuid(row["id"])
    event_kind = _member(AuditEventKind, row["event_kind"])
    summary_kind = _member(AuditSummaryKind, row["summary_kind"])
    summary_payload = _text(row["summary_payload"])
    summary_sha256 = _text(row["summary_sha256"])
    audit_recorded_at = _datetime(row["audit_recorded_at"])
    entry_possible = row["entry_possible"]
    if event_kind is AuditEventKind.SIMULATOR_OUTCOME:
        if entry_possible is not True:
            _fail()
        return AuditPayloadSourceV1(
            owning_event_id=owning_event_id,
            context=context,
            event_kind=event_kind,
            entry_possible=True,
            summary_kind=summary_kind,
            summary_payload=summary_payload,
            summary_sha256=summary_sha256,
            audit_recorded_at=audit_recorded_at,
            descriptor=descriptor,
        )
    if event_kind is AuditEventKind.VENUE_ENTERED:
        if entry_possible is not True:
            _fail()
    elif entry_possible is not None:
        _fail()
    return AuditPayloadSourceV1(
        owning_event_id=owning_event_id,
        context=context,
        event_kind=event_kind,
        summary_kind=summary_kind,
        summary_payload=summary_payload,
        summary_sha256=summary_sha256,
        audit_recorded_at=audit_recorded_at,
        descriptor=descriptor,
    )


def _parent_source(target: Any, connection: Any) -> AuditPayloadSourceV1:
    from hmt_crypto.persistence.api_audit_models import ApiAuditEvent

    table = ApiAuditEvent.__table__
    columns = tuple(table.c[name] for name in _PARENT_COLUMNS)
    parent = (
        connection.execute(select(*columns).where(table.c.id == target.owning_event_id))
        .mappings()
        .one()
    )
    return _source_from_parent(dict(parent))


def _attached_source(target: Any) -> AuditPayloadSourceV1 | None:
    attached = target.__dict__.get("_api_audit_payload_source")
    if attached is None:
        return None
    if type(attached) is not AuditPayloadSourceV1:
        _fail()
    try:
        return AuditPayloadSourceV1(**attached.model_dump(mode="python", exclude_none=True))
    except Exception:
        _fail()


def validate_api_audit_payload_row(target: Any, connection: Any) -> None:
    """Reconstruct and compare the exact parent source and every stored copy field."""
    try:
        state = sa_inspect(target)
        columns = tuple(column.key for column in state.mapper.column_attrs)
        values = object.__getattribute__(target, "__dict__")
        if state.unloaded or any(name not in values for name in columns):
            _fail()
        source = _parent_source(target, connection)
        attached = _attached_source(target)
        if attached is not None and attached != source:
            _fail()
        from hmt_crypto.persistence.api_audit_payload_models import ApiAuditPayload

        expected = ApiAuditPayload.from_source(source)
        for name in columns:
            if getattr(target, name) != getattr(expected, name):
                _fail()
    except PersistenceValueError:
        raise
    except Exception:
        _fail()


def install_api_audit_payload_validation(model: type[Any]) -> None:
    """Install exact insert/load/refresh checks and deny ORM mutation."""
    if vars(model).get("_api_audit_payload_validation_installed") is True:
        return

    def complete(target: Any) -> None:
        state = sa_inspect(target)
        unloaded = tuple(state.unloaded)
        if not unloaded:
            return
        session = object_session(target)
        if session is None or state.identity is None or len(state.identity) != 2:
            _fail()
        table = state.mapper.local_table
        row = (
            session.connection()
            .execute(
                select(*table.c).where(
                    table.c.owning_event_id == state.identity[0],
                    table.c.payload_slot == state.identity[1],
                )
            )
            .mappings()
            .one()
        )
        for name in unloaded:
            if name not in row:
                _fail()
            attributes.set_committed_value(target, name, row[name])

    def connection_for(target: Any) -> Any:
        session = object_session(target)
        if session is None:
            _fail()
        return session.connection()

    def before_insert(mapper: object, connection: Any, target: Any) -> None:
        del mapper
        validate_api_audit_payload_row(target, connection)

    def loaded(target: Any, context: object) -> None:
        del context
        complete(target)
        validate_api_audit_payload_row(target, connection_for(target))

    def refreshed(target: Any, context: object, attrs: object) -> None:
        del context, attrs
        complete(target)
        validate_api_audit_payload_row(target, connection_for(target))

    def reject(mapper: object, connection: object, target: Any) -> None:
        del mapper, connection, target
        _fail()

    event.listen(model, "before_insert", before_insert)
    event.listen(model, "load", loaded, restore_load_context=True)
    event.listen(model, "refresh", refreshed, restore_load_context=True)
    event.listen(model, "before_update", reject)
    event.listen(model, "before_delete", reject)
    model._api_audit_payload_validation_installed = True


__all__: list[str] = []
