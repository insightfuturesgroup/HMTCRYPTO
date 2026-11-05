"""Pure identity and returned-row decoder for the future controlled purge operation."""

from __future__ import annotations

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
        receipt = AuditPayloadPurgeReceipt.model_validate(dict(row))
    except Exception:
        pass
    if receipt is None:
        raise PersistenceValueError() from None
    return receipt


__all__ = [
    "AUDIT_PAYLOAD_PURGE_ARGUMENT_TYPES",
    "AUDIT_PAYLOAD_PURGE_FUNCTION",
    "decode_audit_payload_purge_result",
]
