"""Mutable durable queue receipts. Queue completion says nothing about order state."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, Uuid, event, text
from sqlalchemy.orm import Mapped, mapped_column

from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_evidence import COMMAND_OPERATIONS
from hmt_crypto.persistence.execution_support import (
    EvidenceRow,
    ScopedRow,
    checked,
    evidence_constraints,
    guard_model,
    pair_check,
    scoped_constraints,
)
from hmt_crypto.persistence.schema_support import SCOPE, NulFreeString, StrictBigInteger, bounded
from hmt_crypto.persistence.types import UTCDateTime


class CommandOperation(ScopedRow, EvidenceRow, Base):
    """A request identity and immutable intent, never an execution authorization."""

    __tablename__ = "command_operations"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        *evidence_constraints(),
        checked("operation_kind", COMMAND_OPERATIONS),
        UniqueConstraint(
            "id",
            *SCOPE,
            "operation_kind",
            "evidence_sha256",
            name="uq_command_operations_risk_decision_parent",
        ),
        bounded("request_id"),
        checked("status", ("QUEUED", "CLAIMED", "COMPLETED", "FAILED", "CANCELLED")),
        CheckConstraint(
            "version >= 1 AND attempt_count >= 0 AND claim_generation >= 0", name="counters"
        ),
        CheckConstraint(
            "updated_at >= created_at AND available_at >= created_at", name="time_order"
        ),
        CheckConstraint(
            "(status IN ('COMPLETED','FAILED','CANCELLED') AND completed_at IS NOT NULL "
            "AND completed_at >= created_at AND completed_at <= updated_at) OR "
            "(status IN ('QUEUED','CLAIMED') AND completed_at IS NULL)",
            name="terminal_time",
        ),
        CheckConstraint(
            "(status = 'CLAIMED' AND claim_owner IS NOT NULL AND claim_expires_at IS NOT NULL "
            "AND claim_generation >= 1 AND claim_expires_at > updated_at) OR "
            "(status <> 'CLAIMED' AND claim_owner IS NULL AND claim_expires_at IS NULL)",
            name="claim_consistency",
        ),
        pair_check("leader_ref", "leader_generation"),
        pair_check("recovery_ref", "recovery_generation"),
        UniqueConstraint(*SCOPE, "request_id", name="uq_command_operations_scope_request"),
        Index(
            "uq_command_operations_one_active_global_kill",
            *SCOPE,
            unique=True,
            postgresql_where=text(
                "operation_kind = 'GLOBAL_KILL' AND status IN ('QUEUED','CLAIMED')"
            ),
            sqlite_where=text("operation_kind = 'GLOBAL_KILL' AND status IN ('QUEUED','CLAIMED')"),
        ),
        Index("ix_command_operations_queue", *SCOPE, "status", "available_at", "created_at"),
    )

    request_id: Mapped[str] = mapped_column(NulFreeString(128))
    operation_kind: Mapped[str] = mapped_column(NulFreeString(32))
    correlation_id: Mapped[UUID] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(NulFreeString(16))
    available_at: Mapped[datetime] = mapped_column(UTCDateTime())
    claim_owner: Mapped[UUID | None] = mapped_column(Uuid)
    claim_generation: Mapped[int] = mapped_column(StrictBigInteger())
    claim_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    attempt_count: Mapped[int] = mapped_column(StrictBigInteger())
    leader_ref: Mapped[UUID | None] = mapped_column(Uuid)
    leader_generation: Mapped[int | None] = mapped_column(StrictBigInteger())
    recovery_ref: Mapped[UUID | None] = mapped_column(Uuid)
    recovery_generation: Mapped[int | None] = mapped_column(StrictBigInteger())
    version: Mapped[int] = mapped_column(StrictBigInteger())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __mapper_args__ = {"version_id_col": version}


guard_model(
    CommandOperation,
    evidence_kind="intent",
    protected=(
        "version",
        "request_id",
        "operation_kind",
        "correlation_id",
        "created_at",
        "evidence_schema_version",
        "evidence_payload",
        "evidence_sha256",
    ),
)


@event.listens_for(CommandOperation, "before_insert")
@event.listens_for(CommandOperation, "before_update")
def _validate_version(mapper: object, connection: object, target: CommandOperation) -> None:
    if type(target.version) is not int or not 1 <= target.version < 2**63:
        raise PersistenceValueError()
