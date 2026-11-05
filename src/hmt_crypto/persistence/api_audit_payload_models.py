"""One immutable sanitized summary-copy row and its pure source factory."""

from __future__ import annotations

from datetime import datetime
from typing import Self
from uuid import UUID

from sqlalchemy import Uuid
from sqlalchemy.orm import Mapped, mapped_column

from hmt_crypto.persistence._api_audit_payload_schema import (
    api_audit_payload_table_args,
)
from hmt_crypto.persistence.api_audit_contracts import AccountAuditContext
from hmt_crypto.persistence.api_audit_payload_codec import (
    derive_api_audit_payload_copy_key,
    encode_api_audit_payload_copy,
    hash_api_audit_payload_copy,
)
from hmt_crypto.persistence.api_audit_payload_contracts import AuditPayloadSourceV1
from hmt_crypto.persistence.api_audit_payload_validation import (
    install_api_audit_payload_validation,
)
from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.schema_support import NulFreeString, NulFreeText, StrictBigInteger
from hmt_crypto.persistence.types import UTCDateTime


class ApiAuditPayload(Base):
    """Immutable optional export copy of an already-permanent safe D13a summary."""

    __tablename__ = "api_audit_payloads"
    __table_args__ = api_audit_payload_table_args()

    owning_event_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    context_kind: Mapped[str] = mapped_column(NulFreeString(16))
    region: Mapped[str] = mapped_column(NulFreeString(16))
    environment: Mapped[str] = mapped_column(NulFreeString(16))
    account_id: Mapped[UUID | None] = mapped_column(Uuid)
    mode: Mapped[str | None] = mapped_column(NulFreeString(16))
    payload_slot: Mapped[str] = mapped_column(NulFreeString(16), primary_key=True)
    payload_schema_version: Mapped[int] = mapped_column(StrictBigInteger())
    payload_text: Mapped[str] = mapped_column(NulFreeText)
    payload_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    payload_byte_length: Mapped[int] = mapped_column(StrictBigInteger())
    payload_expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    payload_ttl_seconds: Mapped[int] = mapped_column(StrictBigInteger())
    retention_policy_version: Mapped[int] = mapped_column(StrictBigInteger())
    copy_key: Mapped[str] = mapped_column(NulFreeString(64))

    @classmethod
    def from_source(cls, source: AuditPayloadSourceV1) -> Self:
        """Derive every stored value from one exact immutable D13a projection."""
        try:
            if type(source) is not AuditPayloadSourceV1:
                raise PersistenceValueError()
            exact = AuditPayloadSourceV1.model_validate(source)
            descriptor = exact.descriptor
            slot = descriptor.payload_slot
            schema = descriptor.payload_schema_version
            length = descriptor.payload_byte_length
            expiry = descriptor.payload_expires_at
            ttl = descriptor.payload_ttl_seconds
            policy = descriptor.retention_policy_version
            if None in (slot, schema, length, expiry, ttl, policy):
                raise PersistenceValueError()
            payload = encode_api_audit_payload_copy(exact)
            context = exact.context
            row = cls(
                owning_event_id=exact.owning_event_id,
                context_kind=str.__str__(context.context_kind),
                region=str.__str__(context.region),
                environment=str.__str__(context.environment),
                account_id=(context.account_id if type(context) is AccountAuditContext else None),
                mode=(str.__str__(context.mode) if type(context) is AccountAuditContext else None),
                payload_slot=str.__str__(slot),
                payload_schema_version=schema,
                payload_text=payload.decode("ascii"),
                payload_sha256=hash_api_audit_payload_copy(exact),
                payload_byte_length=length,
                payload_expires_at=expiry,
                payload_ttl_seconds=ttl,
                retention_policy_version=policy,
                copy_key=derive_api_audit_payload_copy_key(exact),
            )
            row.__dict__["_api_audit_payload_source"] = exact
            return row
        except Exception:
            raise PersistenceValueError() from None


install_api_audit_payload_validation(ApiAuditPayload)

__all__ = ["ApiAuditPayload"]
