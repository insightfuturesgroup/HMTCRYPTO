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
from hmt_crypto.persistence.schema_support import SCOPE, NulFreeString, StrictBigInteger
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


guard_model(RecoveryIncarnation, immutable=True)
guard_model(LeadershipLease, protected=("created_at", "version"))


@event.listens_for(LeadershipLease, "before_insert")
@event.listens_for(LeadershipLease, "before_update")
def _validate_version(mapper: object, connection: object, target: LeadershipLease) -> None:
    # The ORM generator runs before type binding, so validate supplied values
    # here too. Numerically equal bool/float assignments can evade history's
    # has_changes(), but must never become valid generated integer versions.
    if type(target.version) is not int or not 1 <= target.version < 2**63:
        raise PersistenceValueError()
