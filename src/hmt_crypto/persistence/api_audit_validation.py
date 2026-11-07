"""Complete-row ORM admission and unconditional immutability for API audit rows."""

from __future__ import annotations

from typing import Any, Never

from sqlalchemy import event, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import attributes, object_session

from hmt_crypto.persistence._api_audit_parent_validation import (
    validate_api_audit_parent_semantics,
)
from hmt_crypto.persistence.api_audit_codec import (
    _decode_api_audit_evidence,
)
from hmt_crypto.persistence.api_audit_contracts import (
    AccountAuditContext,
    ApiAuditRecordV1,
    AuditEventKind,
    AuditStorageAllocation,
)
from hmt_crypto.persistence.api_audit_keys import _decoded_uuid, derive_event_key
from hmt_crypto.persistence.errors import PersistenceValueError


def _fail() -> Never:
    raise PersistenceValueError() from None


def _plain(value: object) -> object:
    return str.__str__(value) if isinstance(value, str) and type(value) is not str else value


def _maintenance_expected(
    target: Any,
    record: ApiAuditRecordV1,
    allocation: AuditStorageAllocation,
    raw: dict[str, object],
    columns: tuple[str, ...],
) -> dict[str, object]:
    storage = raw["storage"]
    if type(storage) is not dict:
        _fail()
    expected = dict.fromkeys(columns)
    context = record.context
    expected.update(
        id=allocation.id,
        event_kind=str.__str__(record.event_kind),
        context_kind=str.__str__(context.context_kind),
        region=str.__str__(context.region),
        environment=str.__str__(context.environment),
        account_id=context.account_id if type(context) is AccountAuditContext else None,
        mode=str.__str__(context.mode) if type(context) is AccountAuditContext else None,
        sequence=allocation.sequence,
        event_key=derive_event_key(record),
        audit_schema_version=1,
        audit_recorded_at=allocation.audit_recorded_at,
        evidence_payload=target.evidence_payload,
        evidence_sha256=target.evidence_sha256,
        retention_mode="SUMMARY_ONLY",
        maintenance_root_id=_decoded_uuid(storage["maintenance_root_id"]),
        maintenance_root_kind="MAINTENANCE_ROOT",
        actor_role_oid=storage["actor_role_oid"],
        actor_role_name=storage["actor_role_name"],
    )
    for name, value in record.model_dump(mode="python", exclude_none=True).items():
        if name not in {"context", "summary", "event_kind"}:
            expected[name] = _plain(value)
    return expected


def _expected_row(
    target: Any,
    record: ApiAuditRecordV1,
    allocation: AuditStorageAllocation,
    raw: dict[str, object],
    columns: tuple[str, ...],
) -> object:
    if record.event_kind in (AuditEventKind.MAINTENANCE_ROOT, AuditEventKind.PAYLOAD_PURGED):
        return _maintenance_expected(target, record, allocation, raw, columns)
    storage = raw["storage"]
    if type(storage) is not dict:
        _fail()
    if record.event_kind is AuditEventKind.CALL_FINISHED and "closure_key" in storage:
        source = target.__dict__.get("_api_audit_closure_source")
        if type(source) is not ApiAuditRecordV1:
            _fail()
        allocation = AuditStorageAllocation(
            id=allocation.id,
            sequence=allocation.sequence,
            audit_recorded_at=allocation.audit_recorded_at,
            descriptor=allocation.descriptor,
            closure_source=source,
        )
    from hmt_crypto.persistence.api_audit_models import ApiAuditEvent

    return ApiAuditEvent.from_record(record, allocation)


def validate_api_audit_row(target: Any) -> None:
    """Validate every persisted column without trusting partial cached state."""
    try:
        state = sa_inspect(target)
        columns = tuple(column.key for column in state.mapper.column_attrs)
        values = object.__getattribute__(target, "__dict__")
        is_new = state.transient or state.pending
        if not is_new and (state.unloaded or any(name not in values for name in columns)):
            _fail()
        if is_new:
            for name in columns:
                getattr(target, name)
        payload = target.evidence_payload
        if type(payload) is not str or not payload.isascii():
            _fail()
        record, allocation, raw = _decode_api_audit_evidence(
            payload.encode("ascii"), target.evidence_sha256
        )
        expected = _expected_row(target, record, allocation, raw, columns)
        for name in columns:
            expected_value = expected[name] if type(expected) is dict else getattr(expected, name)
            if getattr(target, name) != expected_value:
                _fail()
    except PersistenceValueError:
        raise
    except Exception:
        _fail()


def install_api_audit_validation(model: type[Any]) -> None:
    """Install insert/load/refresh checks and unconditional ORM mutation denial."""
    if vars(model).get("_api_audit_validation_installed") is True:
        return

    def before_insert(mapper: object, connection: object, target: Any) -> None:
        del mapper
        validate_api_audit_row(target)
        validate_api_audit_parent_semantics(target, connection)

    def complete(target: Any) -> None:
        state = sa_inspect(target)
        unloaded = tuple(state.unloaded)
        if not unloaded:
            return
        session = object_session(target)
        if session is None:
            _fail()
        table = state.mapper.local_table
        identifier = object.__getattribute__(target, "__dict__").get("id")
        if identifier is None:
            _fail()
        row = (
            session.connection()
            .execute(select(*table.c).where(table.c.id == identifier))
            .mappings()
            .one()
        )
        for name in unloaded:
            if name not in row:
                _fail()
            attributes.set_committed_value(target, name, row[name])

    def complete_closure_source(target: Any) -> None:
        if target.event_kind != "CALL_FINISHED" or target.closure_key is None:
            return
        if type(target.__dict__.get("_api_audit_closure_source")) is ApiAuditRecordV1:
            return
        session = object_session(target)
        if session is None:
            _fail()
        table = sa_inspect(target).mapper.local_table
        terminal_kinds = (
            "LOCAL_REJECTED",
            "VENUE_NO_ENTRY",
            "VENUE_ENTERED",
            "VENUE_TRANSFER_REJECTED",
            "SIMULATOR_OUTCOME",
        )
        source = (
            session.connection()
            .execute(
                select(table.c.evidence_payload, table.c.evidence_sha256).where(
                    table.c.closure_key == target.closure_key,
                    table.c.event_kind.in_(terminal_kinds),
                )
            )
            .mappings()
            .one()
        )
        record, allocation, raw = _decode_api_audit_evidence(
            source["evidence_payload"].encode("ascii"), source["evidence_sha256"]
        )
        storage = raw["storage"]
        if type(storage) is not dict or (
            allocation.audit_recorded_at != target.audit_recorded_at
            or allocation.sequence + 1 != target.sequence
            or storage.get("closure_sha256") != target.closure_sha256
        ):
            _fail()
        target.__dict__["_api_audit_closure_source"] = record

    def loaded(target: Any, context: object) -> None:
        del context
        complete(target)
        complete_closure_source(target)
        validate_api_audit_row(target)
        validate_api_audit_parent_semantics(target)

    def refreshed(target: Any, context: object, attrs: object) -> None:
        del context, attrs
        complete(target)
        target.__dict__.pop("_api_audit_closure_source", None)
        complete_closure_source(target)
        validate_api_audit_row(target)
        validate_api_audit_parent_semantics(target)

    def reject(mapper: object, connection: object, target: Any) -> None:
        del mapper, connection, target
        _fail()

    event.listen(model, "before_insert", before_insert)
    event.listen(model, "load", loaded, restore_load_context=True)
    event.listen(model, "refresh", refreshed, restore_load_context=True)
    event.listen(model, "before_update", reject)
    event.listen(model, "before_delete", reject)
    model._api_audit_validation_installed = True


__all__ = ["install_api_audit_validation", "validate_api_audit_row"]
