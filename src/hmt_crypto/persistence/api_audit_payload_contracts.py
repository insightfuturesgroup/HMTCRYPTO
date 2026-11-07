"""Strict sanitized-copy policy, immutable source, and purge receipt contracts."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Literal, Never, Self

from pydantic import BeforeValidator, model_validator

from hmt_crypto.domain._private_model import PrivateBoundaryModel
from hmt_crypto.persistence._api_audit_contexts import (
    AuditContext,
    AuditEventKind,
    AuditRetentionMode,
    AuditSummaryKind,
    AuditUtc,
    NonzeroUuid,
    PayloadSlot,
    PositiveBigInt,
    SchemaVersionOne,
    Sha256Hex,
)
from hmt_crypto.persistence.api_audit_contracts import (
    AccountAuditContext,
    AuditPayloadDescriptor,
    PublicAuditContext,
)

_MAX_TTL_SECONDS = 2_592_000
_ELIGIBLE_SHAPES = {
    AuditEventKind.ATTEMPT_STARTED: (PayloadSlot.REQUEST, AuditSummaryKind.VENUE_REQUEST),
    AuditEventKind.ATTEMPT_FINISHED: (PayloadSlot.RESPONSE, AuditSummaryKind.VENUE_RESPONSE),
    AuditEventKind.VENUE_PRE_DISPATCH: (
        PayloadSlot.REQUEST,
        AuditSummaryKind.VENUE_REQUEST,
    ),
    AuditEventKind.VENUE_ENTERED: (PayloadSlot.RESPONSE, AuditSummaryKind.VENUE_RESPONSE),
    AuditEventKind.SIMULATOR_PRE_DISPATCH: (
        PayloadSlot.REQUEST,
        AuditSummaryKind.SIMULATOR_REQUEST,
    ),
    AuditEventKind.SIMULATOR_OUTCOME: (
        PayloadSlot.RESPONSE,
        AuditSummaryKind.SIMULATOR_RESPONSE,
    ),
}


def _invalid(model: PrivateBoundaryModel) -> Never:
    raise type(model)._safe_error(
        "api_audit_payload_value", "invalid audit payload value"
    ) from None


def _ttl(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_TTL_SECONDS:
        raise ValueError("value must be an exact bounded retention TTL")
    return value


def _summary_payload(value: object) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value.encode("utf-8")) <= 2048
        or not value.isascii()
        or "\x00" in value
    ):
        raise ValueError("value must be bounded canonical ASCII summary text")
    return value


RetentionTtlSeconds = Annotated[int, BeforeValidator(_ttl)]
CanonicalSummaryText = Annotated[str, BeforeValidator(_summary_payload)]


class AuditRetentionPolicyV1(PrivateBoundaryModel):
    """Closed safe-default policy; retained copies require explicit bounded identity."""

    mode: AuditRetentionMode
    ttl_seconds: RetentionTtlSeconds | None = None
    retention_policy_version: PositiveBigInt | None = None

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        """Require absent values for SUMMARY_ONLY and a complete retained pair."""
        fields = object.__getattribute__(self, "__pydantic_fields_set__")
        state = object.__getattribute__(self, "__dict__")
        if any(state.get(name) is None for name in fields):
            _invalid(self)
        pair = (self.ttl_seconds, self.retention_policy_version)
        if self.mode is AuditRetentionMode.SUMMARY_ONLY:
            if any(value is not None for value in pair):
                _invalid(self)
        elif any(value is None for value in pair):
            _invalid(self)
        return self


class AuditPayloadSourceV1(PrivateBoundaryModel):
    """Exact immutable D13a projection from which one safe export copy is derived."""

    owning_event_id: NonzeroUuid
    context: AuditContext
    event_kind: AuditEventKind
    entry_possible: Literal[True] | None = None
    summary_kind: AuditSummaryKind
    summary_payload: CanonicalSummaryText
    summary_sha256: Sha256Hex
    audit_recorded_at: AuditUtc
    descriptor: AuditPayloadDescriptor

    @model_validator(mode="after")
    def validate_source_shape(self) -> Self:
        """Bind eligible event, context, fixed slot, entry flag, and historical expiry."""
        fields = object.__getattribute__(self, "__pydantic_fields_set__")
        state = object.__getattribute__(self, "__dict__")
        if any(state.get(name) is None for name in fields):
            _invalid(self)
        shape = _ELIGIBLE_SHAPES.get(self.event_kind)
        descriptor = self.descriptor
        if (
            shape is None
            or type(descriptor) is not AuditPayloadDescriptor
            or descriptor.retention_mode is not AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT
            or descriptor.payload_slot is not shape[0]
            or self.summary_kind is not shape[1]
        ):
            _invalid(self)
        simulator = self.event_kind in (
            AuditEventKind.SIMULATOR_PRE_DISPATCH,
            AuditEventKind.SIMULATOR_OUTCOME,
        )
        venue_dispatch = self.event_kind in (
            AuditEventKind.VENUE_PRE_DISPATCH,
            AuditEventKind.VENUE_ENTERED,
        )
        if simulator:
            if type(self.context) is not AccountAuditContext or str.__str__(
                self.context.mode
            ) not in ("PAPER", "BACKTEST"):
                _invalid(self)
        elif venue_dispatch and (
            type(self.context) is not AccountAuditContext
            or str.__str__(self.context.mode) != "LIVE"
        ):
            _invalid(self)
        elif not venue_dispatch and type(self.context) not in (
            PublicAuditContext,
            AccountAuditContext,
        ):
            _invalid(self)
        if self.event_kind is AuditEventKind.SIMULATOR_OUTCOME:
            if self.entry_possible is not True:
                _invalid(self)
        elif self.entry_possible is not None:
            _invalid(self)
        ttl = descriptor.payload_ttl_seconds
        expiry = descriptor.payload_expires_at
        if ttl is None or expiry is None:
            _invalid(self)
        try:
            if self.audit_recorded_at + timedelta(seconds=ttl) != expiry:
                _invalid(self)
        except Exception:
            _invalid(self)
        return self


class AuditPayloadPurgeReceipt(PrivateBoundaryModel):
    """Safe immutable evidence returned only after the future controlled operation."""

    maintenance_root_id: NonzeroUuid
    purge_event_id: NonzeroUuid
    owning_event_id: NonzeroUuid
    context: AuditContext
    payload_slot: PayloadSlot
    payload_schema_version: SchemaVersionOne
    payload_sha256: Sha256Hex
    payload_byte_length: PositiveBigInt
    payload_expires_at: AuditUtc
    payload_ttl_seconds: RetentionTtlSeconds
    retention_policy_version: PositiveBigInt
    actor_role_oid: PositiveBigInt
    actor_role_name: Literal["hmt_audit_retention"]
    purged_at: AuditUtc
    audit_recorded_at: AuditUtc

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        """Require distinct identities and one post-lock database time sample."""
        if (
            len({self.maintenance_root_id, self.purge_event_id, self.owning_event_id}) != 3
            or self.purged_at != self.audit_recorded_at
            or self.purged_at < self.payload_expires_at
            or self.payload_byte_length > 2048
        ):
            _invalid(self)
        return self


__all__ = [
    "AuditPayloadPurgeReceipt",
    "AuditPayloadSourceV1",
    "AuditRetentionPolicyV1",
]
