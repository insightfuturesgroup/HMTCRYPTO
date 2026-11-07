"""Reconciliation collection roots with immutable historical request identity.

FINISHED/COMPLETE records a service claim, not resource coverage, readiness or
permission. PAPER VENUE rows are read-only diagnostic evidence. No collection,
credential probing, current-lease comparison or execution exists here.

ORM history/freeze/version guards do not protect Core/raw SQL. PostgreSQL roles,
triggers, locking and concurrency enforcement remain later reviewed work.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    Uuid,
    event,
    select,
)
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, mapped_column

from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_support import (
    ScopedRow,
    checked,
    guard_model,
    scoped_constraints,
    scoped_fk,
)
from hmt_crypto.persistence.schema_support import SCOPE, NulFreeString, StrictBigInteger, sha_check
from hmt_crypto.persistence.types import UTCDateTime


class ReconciliationRun(ScopedRow, Base):
    """Mutable progress/outcome, immutable provenance, frozen terminal history."""

    __tablename__ = "reconciliation_runs"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        UniqueConstraint(
            "id",
            *SCOPE,
            "requirements_version",
            "requirements_sha256",
            name="uq_reconciliation_runs_risk_decision_parent",
        ),
        checked("trigger", ("STARTUP", "PERIODIC", "MANUAL", "RECOVERY", "RECONNECT")),
        checked("backend", ("VENUE", "SIMULATOR")),
        checked("state", ("PENDING", "COLLECTING", "FINISHED", "FAILED", "CANCELLED")),
        checked("completeness", ("UNKNOWN", "PARTIAL", "COMPLETE")),
        checked(
            "failure_code",
            (
                "REQUEST_FAILED",
                "INVALID_RESPONSE",
                "PAGINATION_INCOMPLETE",
                "LIMIT_REACHED",
                "CANCELLED",
                "UNKNOWN",
            ),
        ),
        *(
            scoped_fk("reconciliation_runs", field, parent)
            for field, parent in (
                ("command_id", "command_operations"),
                ("recovery_incarnation_id", "recovery_incarnations"),
                ("owner_id", "worker_heartbeats"),
                ("lease_id", "leadership_leases"),
            )
        ),
        ForeignKeyConstraint(
            [*SCOPE, "credential_version"],
            [f"hmt_crypto.credential_versions.{c}" for c in (*SCOPE, "credential_version")],
            name="fk_reconciliation_runs_credential_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("requirements_version >= 1", name="requirements_version_positive"),
        CheckConstraint("credential_version >= 1", name="credential_version_positive"),
        sha_check("requirements_sha256"),
        CheckConstraint(
            "(backend = 'VENUE' AND mode IN ('LIVE','PAPER') "
            "AND credential_version IS NOT NULL) OR "
            "(backend = 'SIMULATOR' AND mode IN ('PAPER','BACKTEST') "
            "AND credential_version IS NULL)",
            name="backend_shape",
        ),
        CheckConstraint(
            "(lease_id IS NULL AND leadership_generation IS NULL) OR "
            "(lease_id IS NOT NULL AND leadership_generation IS NOT NULL "
            "AND leadership_generation >= 1 AND owner_id IS NOT NULL)",
            name="fence_shape",
        ),
        CheckConstraint(
            "(state = 'PENDING' AND completeness = 'UNKNOWN' AND started_at IS NULL "
            "AND finished_at IS NULL AND failure_code IS NULL AND version = 1) OR "
            "(state = 'COLLECTING' AND completeness IN ('UNKNOWN','PARTIAL') "
            "AND started_at IS NOT NULL AND finished_at IS NULL AND failure_code IS NULL) OR "
            "(state = 'FINISHED' AND completeness IN ('PARTIAL','COMPLETE') "
            "AND started_at IS NOT NULL AND finished_at IS NOT NULL AND failure_code IS NULL) OR "
            "(state = 'FAILED' AND completeness IN ('UNKNOWN','PARTIAL') "
            "AND finished_at IS NOT NULL AND failure_code IS NOT NULL "
            "AND failure_code <> 'CANCELLED') OR "
            "(state = 'CANCELLED' AND completeness IN ('UNKNOWN','PARTIAL') "
            "AND finished_at IS NOT NULL AND failure_code IS NOT NULL "
            "AND failure_code = 'CANCELLED')",
            name="outcome_shape",
        ),
        CheckConstraint("updated_at >= created_at", name="time_order"),
        *(
            CheckConstraint(
                f"{column} IS NULL OR (created_at <= {column} AND {column} <= updated_at)",
                name=f"{column}_bounds",
            )
            for column in ("started_at", "finished_at")
        ),
        CheckConstraint(
            "started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at",
            name="collection_time_order",
        ),
        *(
            Index(f"ix_reconciliation_runs_{field}", field, *SCOPE)
            for field in (
                "command_id",
                "recovery_incarnation_id",
                "owner_id",
                "lease_id",
            )
        ),
        Index("ix_reconciliation_runs_credential", *SCOPE, "credential_version"),
    )

    trigger: Mapped[str] = mapped_column(NulFreeString(16))
    backend: Mapped[str] = mapped_column(NulFreeString(16))
    command_id: Mapped[UUID | None] = mapped_column(Uuid)
    credential_version: Mapped[int | None] = mapped_column(StrictBigInteger())
    recovery_incarnation_id: Mapped[UUID] = mapped_column(Uuid)
    owner_id: Mapped[UUID | None] = mapped_column(Uuid)
    lease_id: Mapped[UUID | None] = mapped_column(Uuid)
    leadership_generation: Mapped[int | None] = mapped_column(StrictBigInteger())
    requirements_version: Mapped[int] = mapped_column(StrictBigInteger())
    requirements_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    state: Mapped[str] = mapped_column(NulFreeString(16))
    completeness: Mapped[str] = mapped_column(NulFreeString(16))
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    failure_code: Mapped[str | None] = mapped_column(NulFreeString(32))
    version: Mapped[int] = mapped_column(StrictBigInteger())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __mapper_args__ = {"version_id_col": version}


guard_model(
    ReconciliationRun,
    protected=(
        "trigger",
        "backend",
        "command_id",
        "credential_version",
        "recovery_incarnation_id",
        "owner_id",
        "lease_id",
        "leadership_generation",
        "requirements_version",
        "requirements_sha256",
        "created_at",
        "version",
    ),
)


@event.listens_for(ReconciliationRun, "before_insert")
@event.listens_for(ReconciliationRun, "before_update")
def _validate_version(mapper: object, connection: Connection, target: ReconciliationRun) -> None:
    # Validate before SQLAlchemy can normalize a bool/float to generated int 1.
    if type(target.version) is not int or not 1 <= target.version < 2**63:
        raise PersistenceValueError()


@event.listens_for(ReconciliationRun, "before_insert")
def _validate_pending_version(
    mapper: object, connection: Connection, target: ReconciliationRun
) -> None:
    if target.state == "PENDING" and target.version != 1:
        raise PersistenceValueError()


@event.listens_for(ReconciliationRun, "before_update")
def _freeze_terminal(mapper: object, connection: Connection, target: ReconciliationRun) -> None:
    # Attribute history alone loses the prior state when it was expired/unloaded.
    # Read only the persisted state on the mapper's existing connection, without
    # ORM loading or comparing any current lease/credential projection.
    prior = connection.scalar(
        select(ReconciliationRun.state).where(ReconciliationRun.id == target.id)
    )
    if prior in ("FINISHED", "FAILED", "CANCELLED"):
        raise PersistenceValueError()


@event.listens_for(ReconciliationRun, "before_delete")
def _reject_delete(mapper: object, connection: Connection, target: ReconciliationRun) -> None:
    raise PersistenceValueError()
