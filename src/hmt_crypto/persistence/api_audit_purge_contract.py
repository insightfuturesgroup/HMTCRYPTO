"""Pure identity and returned-row decoder for the future controlled purge operation."""

from __future__ import annotations

from uuid import UUID

from hmt_crypto._private_enums import exact_enum_member_for_literal
from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.persistence._api_audit_contexts import AuditContextKind, PayloadSlot
from hmt_crypto.persistence.api_audit_contracts import AccountAuditContext, PublicAuditContext
from hmt_crypto.persistence.api_audit_payload_contracts import AuditPayloadPurgeReceipt
from hmt_crypto.persistence.errors import PersistenceValueError

AUDIT_PAYLOAD_PURGE_FUNCTION = "hmt_crypto.purge_expired_audit_payload"
AUDIT_PAYLOAD_PURGE_ARGUMENT_TYPES = ("UUID", "TEXT")

_RESULT_COLUMNS = (
    "maintenance_root_id",
    "purge_event_id",
    "owning_event_id",
    "context",
    "payload_slot",
    "payload_schema_version",
    "payload_sha256",
    "payload_byte_length",
    "payload_expires_at",
    "payload_ttl_seconds",
    "retention_policy_version",
    "actor_role_oid",
    "actor_role_name",
    "purged_at",
    "audit_recorded_at",
)


def decode_audit_payload_purge_result(row: object) -> AuditPayloadPurgeReceipt:
    """Decode one exact ordered safe result without retaining driver diagnostics."""
    if type(row) is not dict or tuple(row) != _RESULT_COLUMNS:
        raise PersistenceValueError() from None
    receipt: AuditPayloadPurgeReceipt | None = None
    try:
        candidate = dict(row)
        raw_context = candidate["context"]
        if type(raw_context) in {PublicAuditContext, AccountAuditContext}:
            context = raw_context
        elif type(raw_context) is not dict:
            raise ValueError
        else:
            kind = exact_enum_member_for_literal(AuditContextKind, raw_context.get("context_kind"))
            region = exact_enum_member_for_literal(DeltaRegion, raw_context.get("region"))
            environment = exact_enum_member_for_literal(
                DeltaEnvironment, raw_context.get("environment")
            )
            if kind is None or region is None or environment is None:
                raise ValueError
            context = _database_context(raw_context, kind, region, environment)
        candidate["context"] = context
        for identifier in ("maintenance_root_id", "purge_event_id", "owning_event_id"):
            raw_identifier = candidate[identifier]
            if not isinstance(raw_identifier, UUID):
                raise ValueError
            candidate[identifier] = UUID(str(raw_identifier))
        raw_slot = candidate["payload_slot"]
        candidate["payload_slot"] = (
            raw_slot
            if type(raw_slot) is PayloadSlot
            else exact_enum_member_for_literal(PayloadSlot, raw_slot)
        )
        receipt = AuditPayloadPurgeReceipt.model_validate(candidate)
    except Exception:
        pass
    if receipt is None:
        raise PersistenceValueError() from None
    return receipt


def _database_context(
    raw_context: dict[object, object],
    kind: AuditContextKind,
    region: DeltaRegion,
    environment: DeltaEnvironment,
) -> PublicAuditContext | AccountAuditContext:
    if kind is AuditContextKind.PUBLIC and set(raw_context) == {
        "context_kind",
        "region",
        "environment",
    }:
        return PublicAuditContext(
            context_kind=kind,
            region=region,
            environment=environment,
        )
    if kind is AuditContextKind.ACCOUNT and set(raw_context) == {
        "context_kind",
        "account_id",
        "region",
        "environment",
        "mode",
    }:
        raw_account_id = raw_context["account_id"]
        if type(raw_account_id) is not str:
            raise ValueError
        return AccountAuditContext(
            context_kind=kind,
            account_id=UUID(raw_account_id),
            region=region,
            environment=environment,
            mode=exact_enum_member_for_literal(ExecutionMode, raw_context.get("mode")),
        )
    raise ValueError


__all__ = [
    "AUDIT_PAYLOAD_PURGE_ARGUMENT_TYPES",
    "AUDIT_PAYLOAD_PURGE_FUNCTION",
    "decode_audit_payload_purge_result",
]
