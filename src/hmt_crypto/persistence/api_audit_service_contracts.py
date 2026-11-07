"""Closed input and safe receipt for one atomic API-audit event append."""

from __future__ import annotations

from typing import Never, Self

from pydantic import model_validator

from hmt_crypto.domain._private_model import PrivateBoundaryModel
from hmt_crypto.persistence._api_audit_contexts import (
    AuditContext,
    AuditEventKind,
    AuditRetentionMode,
    AuditUtc,
    NonzeroUuid,
    PayloadSlot,
    PositiveBigInt,
    SchemaVersionOne,
    Sha256Hex,
)
from hmt_crypto.persistence.api_audit_contracts import ApiAuditRecordV1
from hmt_crypto.persistence.api_audit_payload_contracts import AuditRetentionPolicyV1

_SINGLE_EVENT_KINDS = frozenset(
    {
        AuditEventKind.CALL_STARTED,
        AuditEventKind.ATTEMPT_STARTED,
        AuditEventKind.ATTEMPT_FINISHED,
        AuditEventKind.VENUE_PRE_DISPATCH,
        AuditEventKind.SIMULATOR_PRE_DISPATCH,
    }
)
_SUMMARY_KINDS = frozenset(
    {
        AuditEventKind.ATTEMPT_STARTED,
        AuditEventKind.ATTEMPT_FINISHED,
        AuditEventKind.VENUE_PRE_DISPATCH,
        AuditEventKind.SIMULATOR_PRE_DISPATCH,
    }
)
_COPY_SLOTS = {
    AuditEventKind.ATTEMPT_STARTED: PayloadSlot.REQUEST,
    AuditEventKind.ATTEMPT_FINISHED: PayloadSlot.RESPONSE,
    AuditEventKind.VENUE_PRE_DISPATCH: PayloadSlot.REQUEST,
    AuditEventKind.SIMULATOR_PRE_DISPATCH: PayloadSlot.REQUEST,
}


def _invalid(model: PrivateBoundaryModel) -> Never:
    raise type(model)._safe_error(
        "api_audit_service_value", "invalid API audit service value"
    ) from None


class ApiAuditAppendRequest(PrivateBoundaryModel):
    """Exact producer evidence plus immutable retention policy; no storage outputs."""

    record: ApiAuditRecordV1
    retention: AuditRetentionPolicyV1

    @model_validator(mode="before")
    @classmethod
    def require_exact_nested_contracts(cls, values: object) -> object:
        """Reject lookalike mappings and subclasses before nested reconstruction."""
        if type(values) is dict and (
            type(values.get("record")) is not ApiAuditRecordV1
            or type(values.get("retention")) is not AuditRetentionPolicyV1
        ):
            raise cls._safe_root_error() from None
        return values

    @model_validator(mode="after")
    def validate_single_event_slice(self) -> Self:
        """Exclude maintenance and multi-row closure kinds from this repository."""
        kind = self.record.event_kind
        if kind not in _SINGLE_EVENT_KINDS:
            _invalid(self)
        if (
            self.retention.mode is AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT
            and kind not in _COPY_SLOTS
        ):
            _invalid(self)
        return self


class ApiAuditAppendReceipt(PrivateBoundaryModel):
    """Copied committed evidence with no raw summary, ORM state, or authority."""

    context: AuditContext
    event_kind: AuditEventKind
    event_id: NonzeroUuid
    root_event_id: NonzeroUuid
    sequence: PositiveBigInt
    event_key: Sha256Hex
    audit_recorded_at: AuditUtc
    summary_sha256: Sha256Hex | None = None
    retention_mode: AuditRetentionMode
    payload_slot: PayloadSlot | None = None
    payload_schema_version: SchemaVersionOne | None = None
    payload_sha256: Sha256Hex | None = None
    payload_byte_length: PositiveBigInt | None = None
    payload_expires_at: AuditUtc | None = None
    payload_ttl_seconds: PositiveBigInt | None = None
    retention_policy_version: PositiveBigInt | None = None
    payload_copy_key: Sha256Hex | None = None

    @model_validator(mode="after")
    def validate_receipt_shape(self) -> Self:
        """Bind root/sequence, summary presence, and the complete payload bundle."""
        if self.event_kind not in _SINGLE_EVENT_KINDS:
            _invalid(self)
        root = self.event_kind is AuditEventKind.CALL_STARTED
        if root != (self.event_id == self.root_event_id and self.sequence == 1):
            _invalid(self)
        has_summary = self.event_kind in _SUMMARY_KINDS
        if has_summary != (self.summary_sha256 is not None):
            _invalid(self)
        bundle = (
            self.payload_slot,
            self.payload_schema_version,
            self.payload_sha256,
            self.payload_byte_length,
            self.payload_expires_at,
            self.payload_ttl_seconds,
            self.retention_policy_version,
            self.payload_copy_key,
        )
        retained = self.retention_mode is AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT
        if retained != all(value is not None for value in bundle):
            _invalid(self)
        if not retained and any(value is not None for value in bundle):
            _invalid(self)
        if retained and self.payload_slot is not _COPY_SLOTS.get(self.event_kind):
            _invalid(self)
        return self


__all__ = ["ApiAuditAppendReceipt", "ApiAuditAppendRequest"]
