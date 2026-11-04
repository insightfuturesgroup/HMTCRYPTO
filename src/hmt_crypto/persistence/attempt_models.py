"""Immutable prepared attempts. Missing outcomes always remain unresolved.

Original fences are evidence of preparation, never retroactive network fencing.
Each additional dispatch needs another durable attempt; client lookup cannot
establish non-dispatch. Simulator provenance grants no private capability.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.execution_evidence import OPERATIONS
from hmt_crypto.persistence.execution_support import (
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
from hmt_crypto.persistence.schema_support import SCOPE, NulFreeString, StrictBigInteger
from hmt_crypto.persistence.types import UTCDateTime


class OrderAttempt(ScopedRow, EvidenceRow, RecordedRow, Base):
    """Command-scoped pre-send identity; an order is optional and independent."""

    __tablename__ = "order_attempts"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        *evidence_constraints(),
        scoped_fk(__tablename__, "command_id", "command_operations"),
        scoped_fk(__tablename__, "order_id", "orders"),
        ForeignKeyConstraint(
            [*SCOPE, "credential_version"],
            [f"hmt_crypto.credential_versions.{c}" for c in (*SCOPE, "credential_version")],
            name="fk_order_attempts_credential_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            *SCOPE, "command_id", "attempt_number", name="uq_order_attempts_command_number"
        ),
        UniqueConstraint("id", *SCOPE, "backend", name="uq_order_attempts_id_scope_backend"),
        checked("operation_kind", OPERATIONS),
        checked("backend", ("VENUE", "SIMULATOR")),
        checked("simulator_kind", ("PAPER", "BACKTEST")),
        CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
        CheckConstraint("recorded_at >= prepared_at", name="time_order"),
        pair_check("original_leader_ref", "original_leader_generation"),
        pair_check("original_recovery_ref", "original_recovery_generation"),
        CheckConstraint(
            "(original_claim_owner IS NULL AND original_claim_generation IS NULL "
            "AND original_claim_expires_at IS NULL) OR (original_claim_owner IS NOT NULL "
            "AND original_claim_generation IS NOT NULL AND original_claim_generation >= 1 "
            "AND original_claim_expires_at IS NOT NULL "
            "AND original_claim_expires_at > prepared_at)",
            name="original_claim",
        ),
        CheckConstraint(
            "(backend = 'VENUE' AND mode = 'LIVE' AND credential_version IS NOT NULL "
            "AND simulator_kind IS NULL AND original_claim_owner IS NOT NULL "
            "AND original_leader_ref IS NOT NULL) OR "
            "(backend = 'SIMULATOR' AND mode IN ('PAPER','BACKTEST') AND simulator_kind = mode "
            "AND simulator_kind IS NOT NULL AND credential_version IS NULL "
            "AND original_claim_owner IS NULL AND original_claim_generation IS NULL "
            "AND original_claim_expires_at IS NULL AND original_leader_ref IS NULL "
            "AND original_leader_generation IS NULL AND original_recovery_ref IS NULL "
            "AND original_recovery_generation IS NULL)",
            name="backend_provenance",
        ),
        Index("ix_order_attempts_command", "command_id", *SCOPE),
        Index("ix_order_attempts_order", "order_id", *SCOPE),
        Index("ix_order_attempts_credential", *SCOPE, "credential_version"),
    )

    command_id: Mapped[UUID] = mapped_column(Uuid)
    order_id: Mapped[UUID | None] = mapped_column(Uuid)
    attempt_number: Mapped[int] = mapped_column(StrictBigInteger())
    operation_kind: Mapped[str] = mapped_column(NulFreeString(32))
    backend: Mapped[str] = mapped_column(NulFreeString(16))
    simulator_kind: Mapped[str | None] = mapped_column(NulFreeString(16))
    credential_version: Mapped[int | None] = mapped_column(StrictBigInteger())
    correlation_id: Mapped[UUID] = mapped_column(Uuid)
    prepared_at: Mapped[datetime] = mapped_column(UTCDateTime())
    original_claim_owner: Mapped[UUID | None] = mapped_column(Uuid)
    original_claim_generation: Mapped[int | None] = mapped_column(StrictBigInteger())
    original_claim_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    original_leader_ref: Mapped[UUID | None] = mapped_column(Uuid)
    original_leader_generation: Mapped[int | None] = mapped_column(StrictBigInteger())
    original_recovery_ref: Mapped[UUID | None] = mapped_column(Uuid)
    original_recovery_generation: Mapped[int | None] = mapped_column(StrictBigInteger())


guard_model(OrderAttempt, evidence_kind="intent", immutable=True)
