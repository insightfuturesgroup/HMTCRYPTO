"""Named checks and exact descriptor relations for immutable audit payload copies."""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.elements import ColumnElement

from hmt_crypto.persistence.schema_support import (
    bounded,
    mode_check,
    sha_check,
    venue_checks,
)

_PUBLIC = ("context_kind", "region", "environment")
_ACCOUNT = ("context_kind", "account_id", "region", "environment", "mode")
_DESCRIPTOR = (
    "payload_slot",
    "payload_schema_version",
    "payload_sha256",
    "payload_byte_length",
    "payload_expires_at",
    "payload_ttl_seconds",
    "retention_policy_version",
)


class _VisibleAscii(ColumnElement[bool]):
    type = Boolean()
    inherit_cache = True


@compiles(_VisibleAscii, "sqlite")
def _visible_ascii_sqlite(element: _VisibleAscii, compiler: SQLCompiler, **kwargs: Any) -> str:
    del element, compiler, kwargs
    return "payload_text NOT GLOB '*[^ -~]*'"


@compiles(_VisibleAscii, "postgresql")
def _visible_ascii_postgresql(element: _VisibleAscii, compiler: SQLCompiler, **kwargs: Any) -> str:
    del element, compiler, kwargs
    return "payload_text !~ '[^ -~]'"


def _fk(
    name: str,
    child: tuple[str, ...],
    table: str,
    parent: tuple[str, ...],
) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        child,
        [f"hmt_crypto.{table}.{column}" for column in parent],
        name=name,
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    )


def api_audit_payload_table_args() -> tuple[object, ...]:
    """Return fresh portable checks and ordinary PUBLIC/ACCOUNT descriptor FKs."""
    payload_digest = sha_check("payload_sha256")
    payload_digest.name = "payload_sha256"
    return (
        UniqueConstraint("copy_key", name="uq_api_audit_payloads_copy_key"),
        CheckConstraint(
            "(context_kind = 'PUBLIC' AND account_id IS NULL AND mode IS NULL) OR "
            "(context_kind = 'ACCOUNT' AND account_id IS NOT NULL AND mode IS NOT NULL)",
            name="context_shape",
        ),
        CheckConstraint("payload_slot IN ('REQUEST','RESPONSE')", name="payload_slot"),
        CheckConstraint("payload_schema_version = 1", name="payload_schema_version"),
        bounded("payload_text", 2048),
        CheckConstraint(_VisibleAscii(), name="payload_text_ascii"),
        payload_digest,
        CheckConstraint(
            "payload_byte_length BETWEEN 1 AND 2048 AND length(payload_text) = payload_byte_length",
            name="payload_byte_length",
        ),
        CheckConstraint(
            "payload_ttl_seconds BETWEEN 1 AND 2592000",
            name="payload_ttl_seconds",
        ),
        CheckConstraint("retention_policy_version >= 1", name="retention_policy_version"),
        sha_check("copy_key"),
        *venue_checks(),
        mode_check(),
        _fk(
            "fk_api_audit_payloads_account_scope",
            ("account_id", "region", "environment", "mode"),
            "accounts",
            ("id", "region", "environment", "mode"),
        ),
        _fk(
            "fk_api_audit_payloads_descriptor_public",
            ("owning_event_id", *_PUBLIC, *_DESCRIPTOR),
            "api_audit_events",
            ("id", *_PUBLIC, *_DESCRIPTOR),
        ),
        _fk(
            "fk_api_audit_payloads_descriptor_account",
            ("owning_event_id", *_ACCOUNT, *_DESCRIPTOR),
            "api_audit_events",
            ("id", *_ACCOUNT, *_DESCRIPTOR),
        ),
    )


__all__ = ["api_audit_payload_table_args"]
