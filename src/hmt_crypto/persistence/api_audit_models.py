"""One immutable ORM row and pure storage factory for D13a audit metadata."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Self
from uuid import UUID

from sqlalchemy import Boolean, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from hmt_crypto.persistence._api_audit_allocation_binding import (
    validate_api_audit_allocation_binding,
)
from hmt_crypto.persistence._api_audit_schema import api_audit_table_args
from hmt_crypto.persistence.api_audit_codec import (
    encode_api_audit_evidence,
    encode_api_audit_summary,
    hash_api_audit_closure,
    hash_api_audit_event,
    hash_api_audit_summary,
)
from hmt_crypto.persistence.api_audit_contracts import (
    AccountAuditContext,
    ApiAuditRecordV1,
    AuditEventKind,
    AuditStorageAllocation,
    LocalRejectionSummaryV1,
    SimulatorResponseSummaryV1,
    TransferRejectionSummaryV1,
    VenueResponseSummaryV1,
)
from hmt_crypto.persistence.api_audit_keys import (
    derive_call_close_key,
    derive_event_key,
)
from hmt_crypto.persistence.api_audit_validation import install_api_audit_validation
from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.schema_support import NulFreeString, NulFreeText, StrictBigInteger
from hmt_crypto.persistence.types import UTCDateTime


class ApiAuditEvent(Base):
    """Append-only canonical API-audit metadata; payload bytes belong to D13b."""

    __tablename__ = "api_audit_events"
    __table_args__ = api_audit_table_args()

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    event_kind: Mapped[str] = mapped_column(NulFreeString(32))
    context_kind: Mapped[str] = mapped_column(NulFreeString(16))
    region: Mapped[str] = mapped_column(NulFreeString(16))
    environment: Mapped[str] = mapped_column(NulFreeString(16))
    sequence: Mapped[int] = mapped_column(StrictBigInteger())
    event_key: Mapped[str] = mapped_column(NulFreeString(64))
    audit_schema_version: Mapped[int] = mapped_column(StrictBigInteger())
    audit_recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())
    evidence_payload: Mapped[str] = mapped_column(NulFreeText)
    evidence_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    account_id: Mapped[UUID | None] = mapped_column(Uuid)
    mode: Mapped[str | None] = mapped_column(NulFreeString(16))
    occurred_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    target: Mapped[str | None] = mapped_column(NulFreeString(16))
    root_event_id: Mapped[UUID | None] = mapped_column(Uuid)
    root_event_kind: Mapped[str | None] = mapped_column(NulFreeString(32))
    call_kind: Mapped[str | None] = mapped_column(NulFreeString(32))
    logical_call_id: Mapped[UUID | None] = mapped_column(Uuid)
    correlation_id: Mapped[UUID | None] = mapped_column(Uuid)
    policy_version: Mapped[str | None] = mapped_column(NulFreeString(128))
    traversal_id: Mapped[UUID | None] = mapped_column(Uuid)
    page_number: Mapped[int | None] = mapped_column(StrictBigInteger())
    route_family: Mapped[str | None] = mapped_column(NulFreeString(16))
    route_id: Mapped[str | None] = mapped_column(NulFreeString(128))
    method: Mapped[str | None] = mapped_column(NulFreeString(8))
    path_template: Mapped[str | None] = mapped_column(NulFreeString(256))
    weight: Mapped[int | None] = mapped_column(StrictBigInteger())
    transport_attempt_number: Mapped[int | None] = mapped_column(StrictBigInteger())
    start_event_id: Mapped[UUID | None] = mapped_column(Uuid)
    start_event_kind: Mapped[str | None] = mapped_column(NulFreeString(32))
    command_id: Mapped[UUID | None] = mapped_column(Uuid)
    operation: Mapped[str | None] = mapped_column(NulFreeString(32))
    order_id: Mapped[UUID | None] = mapped_column(Uuid)
    order_attempt_id: Mapped[UUID | None] = mapped_column(Uuid)
    order_attempt_number: Mapped[int | None] = mapped_column(StrictBigInteger())
    attempt_backend: Mapped[str | None] = mapped_column(NulFreeString(16))
    dispatch_event_id: Mapped[UUID | None] = mapped_column(Uuid)
    dispatch_event_sequence: Mapped[int | None] = mapped_column(StrictBigInteger())
    dispatch_event_kind: Mapped[str | None] = mapped_column(NulFreeString(32))
    consumption_id: Mapped[UUID | None] = mapped_column(Uuid)
    dispatch_recorded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    semantic_request_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    command_evidence_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    wire_request_evidence_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    simulator_request_evidence_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    simulator_backend_id: Mapped[str | None] = mapped_column(NulFreeString(128))
    simulator_profile_id: Mapped[str | None] = mapped_column(NulFreeString(128))
    simulator_profile_version: Mapped[str | None] = mapped_column(NulFreeString(128))
    closure_key: Mapped[str | None] = mapped_column(NulFreeString(64))
    closure_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    pre_dispatch_audit_receipt_id: Mapped[UUID | None] = mapped_column(Uuid)
    pre_dispatch_event_kind: Mapped[str | None] = mapped_column(NulFreeString(32))
    last_phase: Mapped[str | None] = mapped_column(NulFreeString(48))
    entry_possible: Mapped[bool | None] = mapped_column(Boolean)
    reconciliation_required: Mapped[bool | None] = mapped_column(Boolean)
    outcome_kind: Mapped[str | None] = mapped_column(NulFreeString(32))
    http_status: Mapped[int | None] = mapped_column(StrictBigInteger())
    exchange_code: Mapped[str | None] = mapped_column(NulFreeString(128))
    exchange_request_id: Mapped[str | None] = mapped_column(NulFreeString(128))
    completed_response_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    completed_response_byte_length: Mapped[int | None] = mapped_column(StrictBigInteger())
    duration_ns: Mapped[int | None] = mapped_column(StrictBigInteger())
    summary_kind: Mapped[str | None] = mapped_column(NulFreeString(32))
    summary_payload: Mapped[str | None] = mapped_column(NulFreeText)
    summary_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    retention_mode: Mapped[str] = mapped_column(NulFreeString(32))
    payload_slot: Mapped[str | None] = mapped_column(NulFreeString(16))
    payload_schema_version: Mapped[int | None] = mapped_column(StrictBigInteger())
    payload_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    payload_byte_length: Mapped[int | None] = mapped_column(StrictBigInteger())
    payload_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    payload_ttl_seconds: Mapped[int | None] = mapped_column(StrictBigInteger())
    retention_policy_version: Mapped[int | None] = mapped_column(StrictBigInteger())
    maintenance_root_id: Mapped[UUID | None] = mapped_column(Uuid)
    maintenance_root_kind: Mapped[str | None] = mapped_column(NulFreeString(32))
    source_event_id: Mapped[UUID | None] = mapped_column(Uuid)
    source_payload_slot: Mapped[str | None] = mapped_column(NulFreeString(16))
    source_payload_schema_version: Mapped[int | None] = mapped_column(StrictBigInteger())
    source_payload_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    source_payload_byte_length: Mapped[int | None] = mapped_column(StrictBigInteger())
    source_payload_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    source_payload_ttl_seconds: Mapped[int | None] = mapped_column(StrictBigInteger())
    source_retention_policy_version: Mapped[int | None] = mapped_column(StrictBigInteger())
    actor_role_oid: Mapped[int | None] = mapped_column(StrictBigInteger())
    actor_role_name: Mapped[str | None] = mapped_column(NulFreeString(128))
    prior_dispatch_event_id: Mapped[UUID | None] = mapped_column(Uuid)
    prior_consumption_id: Mapped[UUID | None] = mapped_column(Uuid)
    prior_bytes_sent_possible: Mapped[bool | None] = mapped_column(Boolean)

    @classmethod
    def from_record(cls, record: ApiAuditRecordV1, allocation: AuditStorageAllocation) -> Self:
        """Flatten one exact record/allocation without accepting hash overrides."""
        try:
            if (
                type(record) is not ApiAuditRecordV1
                or type(allocation) is not AuditStorageAllocation
            ):
                raise PersistenceValueError()
            exact = ApiAuditRecordV1.model_validate(record)
            stored = AuditStorageAllocation.model_validate(allocation)
            if exact.event_kind in (
                AuditEventKind.MAINTENANCE_ROOT,
                AuditEventKind.PAYLOAD_PURGED,
            ):
                raise PersistenceValueError()
            validate_api_audit_allocation_binding(exact, stored)
            values: dict[str, Any] = {
                name: value
                for name, value in exact.model_dump(mode="python", exclude_none=True).items()
                if name not in {"context", "summary"}
            }
            context = exact.context
            values.update(
                context_kind=str.__str__(context.context_kind),
                region=str.__str__(context.region),
                environment=str.__str__(context.environment),
                account_id=context.account_id if type(context) is AccountAuditContext else None,
                mode=str.__str__(context.mode) if type(context) is AccountAuditContext else None,
            )
            for name in ("event_kind", "target", "call_kind", "last_phase", "outcome_kind"):
                value = values.get(name)
                if isinstance(value, str) and type(value) is not str:
                    values[name] = str.__str__(value)
            values.update(
                id=stored.id,
                sequence=stored.sequence,
                event_key=derive_event_key(exact),
                audit_schema_version=1,
                audit_recorded_at=stored.audit_recorded_at,
                evidence_payload=encode_api_audit_evidence(exact, stored).decode("ascii"),
                evidence_sha256=hash_api_audit_event(exact, stored),
                retention_mode="SUMMARY_ONLY",
                root_event_kind=(
                    "CALL_STARTED"
                    if exact.root_event_id is not None
                    or exact.event_kind is AuditEventKind.CALL_STARTED
                    else None
                ),
                start_event_kind="ATTEMPT_STARTED" if exact.start_event_id is not None else None,
                dispatch_event_sequence=1 if exact.dispatch_event_id is not None else None,
                dispatch_event_kind="DISPATCH_STARTED"
                if exact.dispatch_event_id is not None
                else None,
            )
            if exact.event_kind is AuditEventKind.CALL_STARTED:
                values["root_event_id"] = stored.id
            if exact.pre_dispatch_audit_receipt_id is not None:
                values["pre_dispatch_event_kind"] = (
                    "VENUE_PRE_DISPATCH"
                    if values.get("target") == "VENUE"
                    else "SIMULATOR_PRE_DISPATCH"
                )
            descriptor = stored.descriptor
            if descriptor is not None:
                values["retention_mode"] = str.__str__(descriptor.retention_mode)
                for name, value in descriptor.model_dump(exclude_none=True).items():
                    if name != "retention_mode":
                        values[name] = (
                            str.__str__(value)
                            if isinstance(value, str) and type(value) is not str
                            else value
                        )
            if exact.summary is not None:
                summary = exact.summary
                values.update(
                    summary_kind=str.__str__(summary.summary_kind),
                    summary_payload=encode_api_audit_summary(summary).decode("ascii"),
                    summary_sha256=hash_api_audit_summary(exact),
                )
                if type(summary) in (VenueResponseSummaryV1, SimulatorResponseSummaryV1):
                    for name, value in summary.model_dump(exclude_none=True).items():
                        if name not in {"schema_version", "summary_kind"}:
                            values[name] = (
                                str.__str__(value)
                                if isinstance(value, str) and type(value) is not str
                                else value
                            )
                elif type(summary) is TransferRejectionSummaryV1:
                    for name in (
                        "prior_dispatch_event_id",
                        "prior_consumption_id",
                        "prior_bytes_sent_possible",
                    ):
                        values[name] = getattr(summary, name)
                elif type(summary) is LocalRejectionSummaryV1:
                    values["outcome_kind"] = "LOCAL_REJECTED"
            if stored.closure_source is not None:
                terminal = stored.closure_source
                assert terminal.root_event_id is not None
                values["closure_key"] = derive_call_close_key(terminal.root_event_id)
                values["closure_sha256"] = hash_api_audit_closure(terminal)
            row = cls(**values)
            if stored.closure_source is not None:
                row.__dict__["_api_audit_closure_source"] = stored.closure_source
            return row
        except Exception:
            raise PersistenceValueError() from None


install_api_audit_validation(ApiAuditEvent)

__all__ = ["ApiAuditEvent"]
