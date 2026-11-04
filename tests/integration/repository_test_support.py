"""Offline fixtures: SQLite supplies single-process behavior only."""

from datetime import timedelta
from uuid import uuid4

from schema_test_support import NOW, account, scope

from hmt_crypto.persistence.models import LeadershipLease, RecoveryIncarnation
from hmt_crypto.persistence.repository_contracts import AccountScope, LeadershipFence


async def seed_scope(database, *, mode="PAPER"):
    owner = account(
        mode=mode, created_at=NOW - timedelta(days=365), updated_at=NOW - timedelta(days=365)
    )
    values = scope(owner)
    recovery = RecoveryIncarnation(
        id=uuid4(), **values, reason="INITIALIZATION", recorded_at=owner.created_at
    )
    lease = LeadershipLease(
        id=uuid4(),
        **values,
        recovery_incarnation_id=recovery.id,
        state="UNOWNED",
        owner_id=None,
        generation=0,
        heartbeat_at=None,
        expires_at=None,
        version=1,
        created_at=owner.created_at,
        updated_at=owner.created_at,
    )
    async with database.transaction() as session:
        session.add(owner)
        await session.flush()
        session.add(recovery)
        await session.flush()
        session.add(lease)
    return AccountScope(**values), lease.id, recovery.id


def fence(snapshot):
    return LeadershipFence(**{key: getattr(snapshot, key) for key in LeadershipFence.model_fields})
