"""Scoped recovery identity and coordination evidence, never leadership authority.

No acquisition/renewal/restore protocol exists here. A later repository must lock
the lease, obtain current database wall time *after* the lock wait, evaluate
expiry and retain the lock through commit. Transaction-start time is insufficient.
After a restore, a privileged procedure must generate a fresh incarnation UUID,
move coordination, invalidate readiness/claims as appropriate and reconcile.
Restored counters and table existence provide none of those guarantees.

Generation is structural evidence only, not monotonic fencing. ORM immutability
and optimistic versions do not enforce Core/raw SQL writes; PostgreSQL grants,
triggers, locking, concurrency and restore acceptance remain deferred.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, Uuid, event
from sqlalchemy.orm import Mapped, mapped_column

from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_support import (
    RecordedRow,
    ScopedRow,
    checked,
    guard_model,
    scoped_constraints,
    scoped_fk,
)
from hmt_crypto.persistence.schema_support import SCOPE, NulFreeString, StrictBigInteger, bounded
from hmt_crypto.persistence.types import UTCDateTime


class RecoveryIncarnation(ScopedRow, RecordedRow, Base):
    """Immutable scoped retry identity; reason is classified provenance only."""

    __tablename__ = "recovery_incarnations"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        checked("reason", ("INITIALIZATION", "RESTORE", "RECOVERY")),
    )

    reason: Mapped[str] = mapped_column(NulFreeString(16))


class LeadershipLease(ScopedRow, Base):
    """One mutable coordination projection per scope, including expired HELD rows."""

    __tablename__ = "leadership_leases"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        UniqueConstraint(*SCOPE, name="uq_leadership_leases_scope"),
        scoped_fk(__tablename__, "recovery_incarnation_id", "recovery_incarnations"),
        checked("state", ("UNOWNED", "HELD")),
        CheckConstraint("generation >= 0", name="generation_nonnegative"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("updated_at >= created_at", name="time_order"),
        CheckConstraint(
            "(state = 'UNOWNED' AND owner_id IS NULL AND heartbeat_at IS NULL "
            "AND expires_at IS NULL) OR "
            "(state = 'HELD' AND owner_id IS NOT NULL AND heartbeat_at IS NOT NULL "
            "AND expires_at IS NOT NULL AND generation >= 1 "
            "AND created_at <= heartbeat_at AND heartbeat_at <= updated_at "
            "AND expires_at > heartbeat_at)",
            name="ownership_shape",
        ),
        Index("ix_leadership_leases_incarnation", "recovery_incarnation_id", *SCOPE),
    )

    recovery_incarnation_id: Mapped[UUID] = mapped_column(Uuid)
    state: Mapped[str] = mapped_column(NulFreeString(16))
    owner_id: Mapped[UUID | None] = mapped_column(Uuid)
    generation: Mapped[int] = mapped_column(StrictBigInteger())
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    version: Mapped[int] = mapped_column(StrictBigInteger())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __mapper_args__ = {"version_id_col": version}


class WorkerHeartbeat(ScopedRow, Base):
    """Worker-instance health, independent of optional historical leadership evidence.

    Health grants no authority and cannot renew a lease. Observed generation and
    stable identities need not match a lease's current projection after takeover.
    """

    __tablename__ = "worker_heartbeats"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        checked("health_state", ("UNKNOWN", "HEALTHY", "DEGRADED", "STOPPED")),
        scoped_fk(__tablename__, "observed_recovery_incarnation_id", "recovery_incarnations"),
        scoped_fk(__tablename__, "observed_lease_id", "leadership_leases"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("last_seen_at >= started_at", name="time_order"),
        CheckConstraint(
            "(observed_recovery_incarnation_id IS NULL AND observed_lease_id IS NULL "
            "AND observed_leadership_generation IS NULL) OR "
            "(observed_recovery_incarnation_id IS NOT NULL AND observed_lease_id IS NOT NULL "
            "AND observed_leadership_generation IS NOT NULL "
            "AND observed_leadership_generation >= 1)",
            name="observation_shape",
        ),
        Index("ix_worker_heartbeats_incarnation", "observed_recovery_incarnation_id", *SCOPE),
        Index("ix_worker_heartbeats_lease", "observed_lease_id", *SCOPE),
    )

    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    health_state: Mapped[str] = mapped_column(NulFreeString(16))
    observed_recovery_incarnation_id: Mapped[UUID | None] = mapped_column(Uuid)
    observed_lease_id: Mapped[UUID | None] = mapped_column(Uuid)
    observed_leadership_generation: Mapped[int | None] = mapped_column(StrictBigInteger())
    version: Mapped[int] = mapped_column(StrictBigInteger())

    __mapper_args__ = {"version_id_col": version}


class SchedulerState(ScopedRow, Base):
    """Mutable local job projection, without acquisition or execution semantics.

    job_key identifies a local registry entry. It carries no schedule, command,
    import path or executable payload. Unfenced and expired claims are valid
    evidence; later repositories must decide authority, freshness and overlap.
    """

    __tablename__ = "scheduler_state"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        UniqueConstraint(*SCOPE, "job_key", name="uq_scheduler_state_scope_job"),
        bounded("job_key"),
        checked("state", ("IDLE", "CLAIMED")),
        checked("last_failure_code", ("FAILED", "CANCELLED", "UNKNOWN")),
        scoped_fk(__tablename__, "claim_owner_id", "worker_heartbeats"),
        scoped_fk(__tablename__, "claim_recovery_incarnation_id", "recovery_incarnations"),
        scoped_fk(__tablename__, "claim_lease_id", "leadership_leases"),
        CheckConstraint("claim_generation >= 0", name="claim_generation_nonnegative"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("updated_at >= created_at", name="time_order"),
        CheckConstraint(
            "(state = 'IDLE' AND claim_owner_id IS NULL AND claimed_at IS NULL "
            "AND claim_expires_at IS NULL AND claim_recovery_incarnation_id IS NULL "
            "AND claim_lease_id IS NULL AND claim_leadership_generation IS NULL) OR "
            "(state = 'CLAIMED' AND claim_owner_id IS NOT NULL AND claimed_at IS NOT NULL "
            "AND claim_expires_at IS NOT NULL AND claim_generation >= 1 "
            "AND created_at <= claimed_at AND claimed_at <= updated_at "
            "AND claim_expires_at > claimed_at)",
            name="claim_shape",
        ),
        CheckConstraint(
            "(claim_recovery_incarnation_id IS NULL AND claim_lease_id IS NULL "
            "AND claim_leadership_generation IS NULL) OR "
            "(claim_recovery_incarnation_id IS NOT NULL AND claim_lease_id IS NOT NULL "
            "AND claim_leadership_generation IS NOT NULL AND claim_leadership_generation >= 1)",
            name="claim_fence_shape",
        ),
        CheckConstraint(
            "(last_failure_at IS NULL AND last_failure_code IS NULL) OR "
            "(last_failure_at IS NOT NULL AND last_failure_code IS NOT NULL)",
            name="failure_shape",
        ),
        *(
            CheckConstraint(
                f"{column} IS NULL OR (created_at <= {column} AND {column} <= updated_at)",
                name=f"{column}_bounds",
            )
            for column in ("last_started_at", "last_success_at", "last_failure_at")
        ),
        Index("ix_scheduler_state_eligibility", *SCOPE, "state", "next_eligible_at"),
        Index("ix_scheduler_state_owner", "claim_owner_id", *SCOPE),
        Index("ix_scheduler_state_incarnation", "claim_recovery_incarnation_id", *SCOPE),
        Index("ix_scheduler_state_lease", "claim_lease_id", *SCOPE),
    )

    job_key: Mapped[str] = mapped_column(NulFreeString(128))
    state: Mapped[str] = mapped_column(NulFreeString(16))
    claim_owner_id: Mapped[UUID | None] = mapped_column(Uuid)
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    claim_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    claim_generation: Mapped[int] = mapped_column(StrictBigInteger())
    claim_recovery_incarnation_id: Mapped[UUID | None] = mapped_column(Uuid)
    claim_lease_id: Mapped[UUID | None] = mapped_column(Uuid)
    claim_leadership_generation: Mapped[int | None] = mapped_column(StrictBigInteger())
    last_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_failure_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_failure_code: Mapped[str | None] = mapped_column(NulFreeString(16))
    next_eligible_at: Mapped[datetime] = mapped_column(UTCDateTime())
    version: Mapped[int] = mapped_column(StrictBigInteger())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __mapper_args__ = {"version_id_col": version}


guard_model(RecoveryIncarnation, immutable=True)
guard_model(LeadershipLease, protected=("created_at", "version"))
guard_model(WorkerHeartbeat, protected=("started_at", "version"))
guard_model(SchedulerState, protected=("job_key", "created_at", "version"))


@event.listens_for(LeadershipLease, "before_insert")
@event.listens_for(LeadershipLease, "before_update")
@event.listens_for(WorkerHeartbeat, "before_insert")
@event.listens_for(WorkerHeartbeat, "before_update")
@event.listens_for(SchedulerState, "before_insert")
@event.listens_for(SchedulerState, "before_update")
def _validate_version(
    mapper: object, connection: object, target: LeadershipLease | WorkerHeartbeat | SchedulerState
) -> None:
    # The ORM generator runs before type binding, so validate supplied values
    # here too. Numerically equal bool/float assignments can evade history's
    # has_changes(), but must never become valid generated integer versions.
    if type(target.version) is not int or not 1 <= target.version < 2**63:
        raise PersistenceValueError()
