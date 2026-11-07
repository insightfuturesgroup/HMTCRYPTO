"""Shared record/allocation binding for canonical API-audit storage outputs."""

from __future__ import annotations

from typing import Never, cast

from hmt_crypto.domain._private_model import PrivateBoundaryModel
from hmt_crypto.persistence._api_audit_contexts import (
    AuditCallKind,
    AuditEventKind,
    AuditRetentionMode,
    NonzeroUuid,
    PayloadSlot,
    PositiveBigInt,
    Token128,
)
from hmt_crypto.persistence._api_audit_records import (
    ApiAuditRecordV1,
    AuditStorageAllocation,
)
from hmt_crypto.persistence._api_audit_summaries import (
    SimulatorResponseSummaryV1,
    VenueResponseSummaryV1,
)
from hmt_crypto.persistence.api_audit_keys import (
    _context_array,
    derive_call_close_key,
    derive_event_key,
)
from hmt_crypto.persistence.errors import PersistenceValueError

_MAINTENANCE_KINDS = (
    AuditEventKind.MAINTENANCE_ROOT,
    AuditEventKind.PAYLOAD_PURGED,
)
_TERMINAL_KINDS = (
    AuditEventKind.LOCAL_REJECTED,
    AuditEventKind.VENUE_NO_ENTRY,
    AuditEventKind.VENUE_ENTERED,
    AuditEventKind.VENUE_TRANSFER_REJECTED,
    AuditEventKind.SIMULATOR_OUTCOME,
)
_COPY_SLOTS = {
    AuditEventKind.ATTEMPT_STARTED: PayloadSlot.REQUEST,
    AuditEventKind.ATTEMPT_FINISHED: PayloadSlot.RESPONSE,
    AuditEventKind.VENUE_PRE_DISPATCH: PayloadSlot.REQUEST,
    AuditEventKind.SIMULATOR_PRE_DISPATCH: PayloadSlot.REQUEST,
    AuditEventKind.VENUE_ENTERED: PayloadSlot.RESPONSE,
    AuditEventKind.SIMULATOR_OUTCOME: PayloadSlot.RESPONSE,
}


class _AuditMaintenanceStorageProjection(PrivateBoundaryModel):
    """Database-owned maintenance root and actor values absent from public allocation."""

    maintenance_root_id: NonzeroUuid
    actor_role_oid: PositiveBigInt
    actor_role_name: Token128


def _fail() -> Never:
    raise PersistenceValueError() from None


def _exact_api_audit_allocation(allocation: object) -> AuditStorageAllocation:
    if type(allocation) is not AuditStorageAllocation:
        _fail()
    try:
        return AuditStorageAllocation.model_validate(allocation)
    except Exception:
        _fail()


def _descriptor_dict(allocation: AuditStorageAllocation) -> dict[str, object] | None:
    descriptor = allocation.descriptor
    if descriptor is None:
        return None
    values = descriptor.model_dump(mode="python", exclude_none=True, exclude_unset=True)
    values.pop("retention_mode")
    return cast(dict[str, object], values)


def _api_audit_storage(
    record: ApiAuditRecordV1,
    allocation: AuditStorageAllocation,
    maintenance: _AuditMaintenanceStorageProjection | None = None,
) -> dict[str, object]:
    """Flatten validated storage outputs without admitting independent overrides."""
    from hmt_crypto.persistence.api_audit_codec import (
        hash_api_audit_closure,
        hash_api_audit_summary,
    )

    descriptor = allocation.descriptor
    retention = AuditRetentionMode.SUMMARY_ONLY if descriptor is None else descriptor.retention_mode
    storage: dict[str, object] = {
        "id": allocation.id,
        "sequence": allocation.sequence,
        "audit_recorded_at": allocation.audit_recorded_at,
        "event_key": derive_event_key(record),
        "retention_mode": retention,
    }
    if record.summary is not None:
        storage["summary_sha256"] = hash_api_audit_summary(record)
    descriptor_values = _descriptor_dict(allocation)
    if descriptor_values is not None:
        storage["payload_descriptor"] = descriptor_values
    if record.event_kind is AuditEventKind.CALL_STARTED:
        storage["root_event_id"] = allocation.id
    elif record.event_kind in _MAINTENANCE_KINDS:
        if maintenance is None:
            _fail()
        storage.update(
            maintenance_root_id=maintenance.maintenance_root_id,
            actor_role_oid=maintenance.actor_role_oid,
            actor_role_name=maintenance.actor_role_name,
        )
    if allocation.closure_source is not None:
        terminal = allocation.closure_source
        if terminal.root_event_id is None:
            _fail()
        storage["closure_key"] = derive_call_close_key(terminal.root_event_id)
        storage["closure_sha256"] = hash_api_audit_closure(terminal)
    return storage


def _validate_descriptor(record: ApiAuditRecordV1, allocation: AuditStorageAllocation) -> None:
    descriptor = allocation.descriptor
    if descriptor is None:
        return
    if descriptor.retention_mode is not AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT:
        _fail()
    expected_slot = _COPY_SLOTS.get(record.event_kind)
    if (
        expected_slot is None
        or record.summary is None
        or descriptor.payload_slot is not expected_slot
    ):
        _fail()
    from hmt_crypto.persistence.api_audit_codec import (
        encode_api_audit_summary,
        hash_api_audit_payload_copy,
    )

    summary_bytes = encode_api_audit_summary(record.summary)
    if (
        descriptor.payload_sha256 != hash_api_audit_payload_copy(record.summary)
        or descriptor.payload_byte_length != len(summary_bytes)
        or (
            record.event_kind is AuditEventKind.SIMULATOR_OUTCOME
            and record.entry_possible is not True
        )
    ):
        _fail()


def _validate_closure(record: ApiAuditRecordV1, allocation: AuditStorageAllocation) -> None:
    source = allocation.closure_source
    if record.event_kind in _TERMINAL_KINDS:
        if source is None:
            _fail()
        from hmt_crypto.persistence.api_audit_codec import encode_api_audit_closure

        if encode_api_audit_closure(source) != encode_api_audit_closure(record):
            _fail()
        return
    if record.event_kind is not AuditEventKind.CALL_FINISHED:
        if source is not None:
            _fail()
        return
    if source is None:
        if record.call_kind is not AuditCallKind.READ:
            _fail()
        return
    if source.event_kind not in _TERMINAL_KINDS or (
        source.root_event_id,
        source.target,
        source.logical_call_id,
        source.correlation_id,
        _context_array(source),
    ) != (
        record.root_event_id,
        record.target,
        record.logical_call_id,
        record.correlation_id,
        _context_array(record),
    ):
        _fail()
    summary = source.summary
    expected_outcome: object
    if type(summary) is VenueResponseSummaryV1:
        expected_outcome = summary.outcome_kind
    elif type(summary) is SimulatorResponseSummaryV1:
        expected_outcome = summary.outcome_kind
    else:
        expected_outcome = "LOCAL_REJECTED"
    if record.outcome_kind != expected_outcome:
        _fail()


def validate_api_audit_allocation_binding(
    record: ApiAuditRecordV1,
    allocation: AuditStorageAllocation,
    maintenance: _AuditMaintenanceStorageProjection | None = None,
) -> None:
    """Require one allocation to produce a valid envelope for its exact record."""
    if type(record) is not ApiAuditRecordV1 or type(allocation) is not AuditStorageAllocation:
        _fail()
    if maintenance is not None and type(maintenance) is not _AuditMaintenanceStorageProjection:
        _fail()
    if record.event_kind in _MAINTENANCE_KINDS:
        if (
            maintenance is None
            or allocation.descriptor is not None
            or allocation.closure_source is not None
        ):
            _fail()
        expected_sequence = 1 if record.event_kind is AuditEventKind.MAINTENANCE_ROOT else 2
        if allocation.sequence != expected_sequence:
            _fail()
        if record.event_kind is AuditEventKind.MAINTENANCE_ROOT:
            if maintenance.maintenance_root_id != allocation.id:
                _fail()
        elif maintenance.maintenance_root_id == allocation.id:
            _fail()
        return
    if maintenance is not None:
        _fail()
    if record.event_kind is AuditEventKind.CALL_STARTED and allocation.sequence != 1:
        _fail()
    if record.occurred_at is not None and record.occurred_at > allocation.audit_recorded_at:
        _fail()
    if (
        record.dispatch_recorded_at is not None
        and record.dispatch_recorded_at > allocation.audit_recorded_at
    ):
        _fail()
    _validate_descriptor(record, allocation)
    _validate_closure(record, allocation)


__all__: list[str] = []
