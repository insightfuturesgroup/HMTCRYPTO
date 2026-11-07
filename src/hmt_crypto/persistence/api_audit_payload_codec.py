"""Canonical byte-identical sanitized summary copies for D13b payload rows."""

from __future__ import annotations

import hashlib
from typing import Never

from hmt_crypto.persistence.api_audit_codec import (
    decode_api_audit_summary,
    encode_api_audit_summary,
)
from hmt_crypto.persistence.api_audit_contracts import (
    SimulatorRequestSummaryV1,
    SimulatorResponseSummaryV1,
    VenueRequestSummaryV1,
    VenueResponseSummaryV1,
)
from hmt_crypto.persistence.api_audit_keys import derive_payload_copy_key
from hmt_crypto.persistence.api_audit_payload_contracts import AuditPayloadSourceV1
from hmt_crypto.persistence.errors import PersistenceValueError

_COPY_SUMMARY_TYPES = (
    VenueRequestSummaryV1,
    VenueResponseSummaryV1,
    SimulatorRequestSummaryV1,
    SimulatorResponseSummaryV1,
)


def _invalid() -> Never:
    raise PersistenceValueError() from None


def _exact_source(source: object) -> AuditPayloadSourceV1:
    if type(source) is not AuditPayloadSourceV1:
        _invalid()
    exact: AuditPayloadSourceV1 | None = None
    try:
        exact = AuditPayloadSourceV1.model_validate(source)
    except Exception:
        pass
    if exact is None:
        _invalid()
    return exact


def encode_api_audit_payload_copy(source: AuditPayloadSourceV1) -> bytes:
    """Return the exact stored D13a canonical summary bytes for one eligible source."""
    exact = _exact_source(source)
    payload = exact.summary_payload.encode("ascii")
    summary: object | None = None
    try:
        summary = decode_api_audit_summary(payload)
    except Exception:
        pass
    if type(summary) not in _COPY_SUMMARY_TYPES:
        _invalid()
    canonical = encode_api_audit_summary(summary)
    descriptor = exact.descriptor
    digest = hashlib.sha256(canonical).hexdigest()
    if (
        canonical != payload
        or summary.summary_kind is not exact.summary_kind  # type: ignore[union-attr]
        or descriptor.payload_schema_version != 1
        or descriptor.payload_sha256 != digest
        or descriptor.payload_byte_length != len(canonical)
    ):
        _invalid()
    return canonical


def hash_api_audit_payload_copy(source: AuditPayloadSourceV1) -> str:
    """Hash only the byte-identical canonical sanitized summary copy."""
    return hashlib.sha256(encode_api_audit_payload_copy(source)).hexdigest()


def derive_api_audit_payload_copy_key(source: AuditPayloadSourceV1) -> str:
    """Derive PAYLOAD_COPY/v1 from the exact source event and its fixed slot."""
    exact = _exact_source(source)
    slot = exact.descriptor.payload_slot
    if slot is None:
        _invalid()
    return derive_payload_copy_key(exact.owning_event_id, slot)


__all__ = [
    "derive_api_audit_payload_copy_key",
    "encode_api_audit_payload_copy",
    "hash_api_audit_payload_copy",
]
