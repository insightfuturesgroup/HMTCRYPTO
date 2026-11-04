"""Append-only ORM evidence; DB append-only/sequence enforcement is deferred."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.execution_evidence import ATTEMPT_EVENT_KINDS, TRANSITION_KINDS
from hmt_crypto.persistence.execution_support import (
    STATES,
    EvidenceRow,
    RecordedRow,
    ScopedRow,
    checked,
    evidence_constraints,
    guard_model,
    pair_check,
    scoped_constraints,
    scoped_fk,
)
from hmt_crypto.persistence.schema_support import SCOPE, NulFreeString, StrictBigInteger, bounded
from hmt_crypto.persistence.types import UTCDateTime


class OrderAttemptEvent(ScopedRow, EvidenceRow, RecordedRow, Base):
    """Dispatch/outcome evidence, with current ingester distinct from original fence."""

    __tablename__ = "order_attempt_events"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        *evidence_constraints(),
        ForeignKeyConstraint(
            ["attempt_id", *SCOPE, "backend"],
            [f"hmt_crypto.order_attempts.{c}" for c in ("id", *SCOPE, "backend")],
            name="fk_order_attempt_events_attempt_scope_backend",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(*SCOPE, "attempt_id", "sequence", name="uq_order_attempt_events_sequence"),
        UniqueConstraint(*SCOPE, "event_key", name="uq_order_attempt_events_identity"),
        checked("backend", ("VENUE", "SIMULATOR")),
        bounded("event_key"),
        checked("event_kind", ATTEMPT_EVENT_KINDS),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("observed_at IS NULL OR observed_at <= recorded_at", name="time_order"),
        CheckConstraint(
            "(event_kind NOT IN ('RESPONSE_ACCEPTED','RESPONSE_REJECTED') OR backend = 'VENUE') "
            "AND (event_kind NOT IN ('SIMULATED_ACCEPTED','SIMULATED_REJECTED') "
            "OR backend = 'SIMULATOR')",
            name="response_backend",
        ),
        pair_check("ingester_id", "ingester_generation"),
        pair_check("ingester_leader_ref", "ingester_leader_generation"),
        pair_check("ingester_recovery_ref", "ingester_recovery_generation"),
        CheckConstraint(
            "event_kind <> 'RECONCILED' OR "
            "(ingester_id IS NOT NULL AND reconciliation_ref IS NOT NULL)",
            name="resolution_evidence",
        ),
        Index("ix_order_attempt_events_attempt", "attempt_id", *SCOPE, "backend"),
    )

    attempt_id: Mapped[UUID] = mapped_column(Uuid)
    backend: Mapped[str] = mapped_column(NulFreeString(16))
    sequence: Mapped[int] = mapped_column(StrictBigInteger())
    event_key: Mapped[str] = mapped_column(NulFreeString(128))
    event_kind: Mapped[str] = mapped_column(NulFreeString(32))
    observed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ingester_id: Mapped[UUID | None] = mapped_column(Uuid)
    ingester_generation: Mapped[int | None] = mapped_column(StrictBigInteger())
    reconciliation_ref: Mapped[UUID | None] = mapped_column(Uuid)
    ingester_leader_ref: Mapped[UUID | None] = mapped_column(Uuid)
    ingester_leader_generation: Mapped[int | None] = mapped_column(StrictBigInteger())
    ingester_recovery_ref: Mapped[UUID | None] = mapped_column(Uuid)
    ingester_recovery_generation: Mapped[int | None] = mapped_column(StrictBigInteger())


class OrderTransition(ScopedRow, EvidenceRow, RecordedRow, Base):
    """Order observation stream; same-state events and first unknown prior state are valid."""

    __tablename__ = "order_transitions"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        *evidence_constraints(),
        scoped_fk(__tablename__, "order_id", "orders"),
        ForeignKeyConstraint(
            ["attempt_id", *SCOPE, "attempt_backend"],
            [f"hmt_crypto.order_attempts.{c}" for c in ("id", *SCOPE, "backend")],
            name="fk_order_transitions_attempt_scope_backend",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        scoped_fk(__tablename__, "command_id", "command_operations"),
        UniqueConstraint(*SCOPE, "order_id", "sequence", name="uq_order_transitions_sequence"),
        UniqueConstraint(*SCOPE, "event_key", name="uq_order_transitions_identity"),
        checked("event_kind", TRANSITION_KINDS),
        checked("from_state", STATES),
        checked("to_state", STATES),
        checked("source", ("LOCAL", "VENUE", "SIMULATOR", "RECONCILIATION")),
        checked("attempt_backend", ("VENUE", "SIMULATOR")),
        CheckConstraint(
            "(attempt_id IS NULL AND attempt_backend IS NULL) OR "
            "(attempt_id IS NOT NULL AND attempt_backend IS NOT NULL)",
            name="attempt_backend_pair",
        ),
        CheckConstraint(
            "event_kind <> 'RESPONSE' OR (attempt_id IS NOT NULL AND "
            "((source = 'VENUE' AND mode = 'LIVE' AND attempt_backend = 'VENUE') OR "
            "(source = 'SIMULATOR' AND mode IN ('PAPER','BACKTEST') "
            "AND attempt_backend = 'SIMULATOR')))",
            name="response_provenance",
        ),
        bounded("event_key"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("observed_at IS NULL OR observed_at <= recorded_at", name="time_order"),
        pair_check("leader_ref", "leader_generation"),
        pair_check("recovery_ref", "recovery_generation"),
        Index("ix_order_transitions_order", "order_id", *SCOPE),
        Index("ix_order_transitions_attempt", "attempt_id", *SCOPE, "attempt_backend"),
        Index("ix_order_transitions_command", "command_id", *SCOPE),
    )

    order_id: Mapped[UUID] = mapped_column(Uuid)
    sequence: Mapped[int] = mapped_column(StrictBigInteger())
    event_key: Mapped[str] = mapped_column(NulFreeString(128))
    event_kind: Mapped[str] = mapped_column(NulFreeString(32))
    from_state: Mapped[str | None] = mapped_column(NulFreeString(32))
    to_state: Mapped[str] = mapped_column(NulFreeString(32))
    source: Mapped[str] = mapped_column(NulFreeString(16))
    attempt_id: Mapped[UUID | None] = mapped_column(Uuid)
    attempt_backend: Mapped[str | None] = mapped_column(NulFreeString(16))
    command_id: Mapped[UUID | None] = mapped_column(Uuid)
    observed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    leader_ref: Mapped[UUID | None] = mapped_column(Uuid)
    leader_generation: Mapped[int | None] = mapped_column(StrictBigInteger())
    recovery_ref: Mapped[UUID | None] = mapped_column(Uuid)
    recovery_generation: Mapped[int | None] = mapped_column(StrictBigInteger())


guard_model(OrderAttemptEvent, evidence_kind="event", operation_field="event_kind", immutable=True)
guard_model(
    OrderTransition, evidence_kind="transition", operation_field="event_kind", immutable=True
)
