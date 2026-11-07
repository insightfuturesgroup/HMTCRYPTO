"""Isolated durable records for offline PAPER/BACKTEST execution.

Existing venue reconciliation tables intentionally cannot represent a denied
intent without fabricating an order. These two tables instead hold a small
canonical event journal and its atomic mutable checkpoint.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, Uuid, event
from sqlalchemy.engine import Connection, Dialect
from sqlalchemy.orm import Mapped, mapped_column

from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_support import (
    ScopedRow,
    checked,
    guard_model,
    scoped_constraints,
)
from hmt_crypto.persistence.schema_support import (
    SCOPE,
    NulFreeString,
    NulFreeText,
    StrictBigInteger,
    bounded,
    sha_check,
)
from hmt_crypto.persistence.types import UTCDateTime

MAX_PAPER_EVENT_PAYLOAD_BYTES = 65_536
PAPER_EVENT_KINDS = (
    "INTENT",
    "SIGNAL",
    "RISK_DENIED",
    "RISK_APPROVED",
    "RESERVATION",
    "COMMAND",
    "ATTEMPT",
    "SIMULATION",
    "FILL",
    "POSITION",
    "PNL",
    "FUNDING",
    "RISK_HALT",
    "ORDER_CANCELLED",
    "FLATTEN_FILL",
    "RECONCILIATION",
    "PROTECTION",
    "KILL_ACTION",
    "RUN_INCOMPLETE",
    "RUN_COMPLETE",
)
PAPER_HEAD_STATUSES = (
    "INTENT_RECORDED",
    "DENIED",
    "PREPARED",
    "SIMULATED",
    "COMPLETE",
    "INCOMPLETE",
)


def _canonical_json_value(value: object, *, depth: int = 0) -> Any:
    if depth > 24:
        raise PersistenceValueError()
    if value is None or type(value) in (bool, int, str):
        if type(value) is int and not -(2**63) <= value < 2**63:
            raise PersistenceValueError()
        if type(value) is str and "\x00" in value:
            raise PersistenceValueError()
        return value
    if type(value) is list and len(value) <= 4096:
        return [_canonical_json_value(item, depth=depth + 1) for item in value]
    if type(value) is dict and len(value) <= 1024:
        if any(type(key) is not str or not key or len(key) > 128 for key in value):
            raise PersistenceValueError()
        return {key: _canonical_json_value(item, depth=depth + 1) for key, item in value.items()}
    raise PersistenceValueError()


def encode_paper_event_payload(value: object) -> tuple[str, str]:
    """Encode one bounded JSON value and return its canonical SHA-256."""

    if type(value) is not dict:
        raise PersistenceValueError()
    encoded = json.dumps(
        _canonical_json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    if len(encoded.encode("ascii")) > MAX_PAPER_EVENT_PAYLOAD_BYTES:
        raise PersistenceValueError()
    return encoded, hashlib.sha256(encoded.encode("ascii")).hexdigest()


class _CanonicalPaperPayload(NulFreeText):
    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> str:
        payload = super().process_bind_param(value, dialect)
        if payload is None or len(payload.encode("utf-8")) > MAX_PAPER_EVENT_PAYLOAD_BYTES:
            raise PersistenceValueError()
        try:
            canonical, _ = encode_paper_event_payload(json.loads(payload))
        except Exception:
            raise PersistenceValueError() from None
        if canonical != payload:
            raise PersistenceValueError()
        return payload

    def process_result_value(self, value: object, dialect: Dialect) -> str:
        return self.process_bind_param(value, dialect)


class PaperExecutionHead(ScopedRow, Base):
    """Atomic request checkpoint; it contains no exchange dispatch capability."""

    __tablename__ = "paper_execution_heads"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        UniqueConstraint(*SCOPE, "request_id", name="uq_paper_heads_scope_request"),
        UniqueConstraint("id", *SCOPE, "request_id", name="uq_paper_heads_event_parent"),
        CheckConstraint("mode IN ('BACKTEST','PAPER')", name="paper_mode_only"),
        checked("status", PAPER_HEAD_STATUSES),
        bounded("request_id"),
        sha_check("intent_sha256"),
        CheckConstraint(
            "version >= 1 AND last_sequence >= 1 AND claim_generation >= 0",
            name="positive_counters",
        ),
        CheckConstraint("updated_at >= created_at", name="time_order"),
        CheckConstraint(
            "(claim_owner IS NULL AND claim_expires_at IS NULL) OR "
            "(claim_owner IS NOT NULL AND claim_generation >= 1 "
            "AND claim_expires_at IS NOT NULL AND claim_expires_at > updated_at)",
            name="claim_consistency",
        ),
        CheckConstraint(
            "(status IN ('DENIED','COMPLETE','INCOMPLETE') AND completed_at IS NOT NULL "
            "AND completed_at >= created_at AND completed_at <= updated_at "
            "AND claim_owner IS NULL) OR "
            "(status NOT IN ('DENIED','COMPLETE','INCOMPLETE') AND completed_at IS NULL)",
            name="terminal_consistency",
        ),
        Index("ix_paper_heads_claim", *SCOPE, "status", "claim_expires_at"),
    )

    request_id: Mapped[str] = mapped_column(NulFreeString(128))
    intent_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    status: Mapped[str] = mapped_column(NulFreeString(32))
    last_sequence: Mapped[int] = mapped_column(StrictBigInteger())
    claim_owner: Mapped[UUID | None] = mapped_column(Uuid)
    claim_generation: Mapped[int] = mapped_column(StrictBigInteger())
    claim_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    version: Mapped[int] = mapped_column(StrictBigInteger())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class PaperExecutionEvent(ScopedRow, Base):
    """Append-only canonical paper evidence and deterministic projections."""

    __tablename__ = "paper_execution_events"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        ForeignKeyConstraint(
            ["head_id", *SCOPE, "request_id"],
            [f"hmt_crypto.paper_execution_heads.{field}" for field in ("id", *SCOPE, "request_id")],
            name="fk_paper_events_head_scope_request",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint("head_id", *SCOPE, "sequence", name="uq_paper_events_head_sequence"),
        UniqueConstraint(*SCOPE, "scope_sequence", name="uq_paper_events_scope_sequence"),
        UniqueConstraint(*SCOPE, "event_key", name="uq_paper_events_scope_key"),
        CheckConstraint("mode IN ('BACKTEST','PAPER')", name="paper_mode_only"),
        checked("event_kind", PAPER_EVENT_KINDS),
        bounded("request_id"),
        sha_check("event_key"),
        sha_check("payload_sha256"),
        CheckConstraint("sequence >= 1 AND scope_sequence >= 1", name="sequence_positive"),
        CheckConstraint("payload_schema_version = 1", name="payload_schema_version"),
        CheckConstraint("occurred_at <= recorded_at", name="time_order"),
        CheckConstraint(
            f"length(payload) BETWEEN 2 AND {MAX_PAPER_EVENT_PAYLOAD_BYTES}",
            name="payload_size",
        ),
        Index("ix_paper_events_replay", "head_id", *SCOPE, "sequence"),
        Index("ix_paper_events_scope_replay", *SCOPE, "scope_sequence"),
    )

    head_id: Mapped[UUID] = mapped_column(Uuid)
    request_id: Mapped[str] = mapped_column(NulFreeString(128))
    sequence: Mapped[int] = mapped_column(StrictBigInteger())
    scope_sequence: Mapped[int] = mapped_column(StrictBigInteger())
    event_key: Mapped[str] = mapped_column(NulFreeString(64))
    event_kind: Mapped[str] = mapped_column(NulFreeString(32))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())
    payload_schema_version: Mapped[int] = mapped_column(StrictBigInteger())
    payload: Mapped[str] = mapped_column(_CanonicalPaperPayload())
    payload_sha256: Mapped[str] = mapped_column(NulFreeString(64))


@event.listens_for(PaperExecutionEvent, "before_insert")
@event.listens_for(PaperExecutionEvent, "before_update")
def _validate_payload(mapper: object, connection: Connection, target: PaperExecutionEvent) -> None:
    try:
        payload, digest = encode_paper_event_payload(json.loads(target.payload))
    except Exception:
        raise PersistenceValueError() from None
    if payload != target.payload or digest != target.payload_sha256:
        raise PersistenceValueError()


guard_model(PaperExecutionHead, protected=("request_id", "intent_sha256", "created_at"))
guard_model(PaperExecutionEvent, immutable=True)


__all__ = [
    "PAPER_EVENT_KINDS",
    "PAPER_HEAD_STATUSES",
    "PaperExecutionEvent",
    "PaperExecutionHead",
    "encode_paper_event_payload",
]
