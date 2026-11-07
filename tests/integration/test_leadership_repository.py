"""Lease lifecycle algorithms under caller-owned real SQLite transactions."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from repository_test_support import fence, seed_scope
from schema_test_support import schema_db as schema_db
from sqlalchemy import event, select, update

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.leadership_repository import LeadershipRepository
from hmt_crypto.persistence.models import LeadershipLease
from hmt_crypto.persistence.repository_errors import (
    FenceRejectedError,
    LeaseUnavailableError,
    RepositoryStateError,
)


@pytest.mark.asyncio
async def test_acquire_renew_release_and_stale_fence(schema_db):
    scope, lease_id, recovery_id = await seed_scope(schema_db)
    repo = LeadershipRepository()
    async with schema_db.transaction() as session:
        first = await repo.acquire(
            session,
            scope,
            lease_id=lease_id,
            recovery_incarnation_id=recovery_id,
            owner_id=uuid4(),
            duration=timedelta(minutes=5),
        )
        assert (first.state, first.generation, first.version) == ("HELD", 1, 2)
    async with schema_db.transaction() as session:
        with pytest.raises(LeaseUnavailableError):
            await repo.acquire(
                session,
                scope,
                lease_id=lease_id,
                recovery_incarnation_id=recovery_id,
                owner_id=first.owner_id,
                duration=timedelta(minutes=1),
            )
    async with schema_db.transaction() as session:
        renewed = await repo.renew(session, scope, fence(first), duration=timedelta(minutes=5))
        assert (renewed.generation, renewed.version) == (1, 3)
    async with schema_db.transaction() as session:
        with pytest.raises(FenceRejectedError):
            await repo.release(session, scope, fence(first))
    async with schema_db.transaction() as session:
        released = await repo.release(session, scope, fence(renewed))
        assert (released.state, released.generation, released.version) == ("UNOWNED", 1, 4)
        assert released.owner_id is released.heartbeat_at is released.expires_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "duration", [True, 1, "1", timedelta(0), timedelta(seconds=-1), timedelta.max]
)
async def test_duration_rejection_no_mutation(schema_db, duration):
    scope, lease_id, recovery_id = await seed_scope(schema_db)
    async with schema_db.transaction() as session:
        with pytest.raises(PersistenceValueError):
            await LeadershipRepository().acquire(
                session,
                scope,
                lease_id=lease_id,
                recovery_incarnation_id=recovery_id,
                owner_id=uuid4(),
                duration=duration,
            )
        assert not session.dirty


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_caller_rollback_after_flush(schema_db, failure):
    scope, lease_id, recovery_id = await seed_scope(schema_db)
    with pytest.raises(failure):
        async with schema_db.transaction() as session:
            await LeadershipRepository().acquire(
                session,
                scope,
                lease_id=lease_id,
                recovery_incarnation_id=recovery_id,
                owner_id=uuid4(),
                duration=timedelta(minutes=1),
            )
            raise failure()
    async with schema_db.transaction() as session:
        row = await session.get(LeadershipLease, lease_id)
        assert row.state == "UNOWNED" and row.version == 1


@pytest.mark.asyncio
async def test_neutral_lock_refinement_no_sql_and_expired_takeover(schema_db):
    scope, lease_id, recovery_id = await seed_scope(schema_db)
    repo = LeadershipRepository()
    async with schema_db.transaction() as session:
        held = await repo.acquire(
            session,
            scope,
            lease_id=lease_id,
            recovery_incarnation_id=recovery_id,
            owner_id=uuid4(),
            duration=timedelta(minutes=5),
        )
    async with schema_db.transaction() as session:
        neutral = await repo.lock_scope(
            session, scope, lease_id=lease_id, recovery_incarnation_id=recovery_id
        )
        statements = []
        event.listen(
            schema_db.engine.sync_engine,
            "before_cursor_execute",
            lambda *args: statements.append(args[2]),
        )
        current = repo.refine_current(neutral, fence(held))
        assert current.rank == 10 and not statements
    expired = held.created_at + timedelta(seconds=1)
    async with schema_db.transaction() as session:
        await session.execute(
            update(LeadershipLease)
            .where(LeadershipLease.id == lease_id)
            .values(heartbeat_at=held.created_at, expires_at=expired)
        )
    async with schema_db.transaction() as session:
        neutral = await repo.lock_scope(
            session, scope, lease_id=lease_id, recovery_incarnation_id=recovery_id
        )
        assert neutral.rank == 10
        with pytest.raises(FenceRejectedError):
            await repo.release(session, scope, fence(held))
    async with schema_db.transaction() as session:
        taken = await repo.acquire(
            session,
            scope,
            lease_id=lease_id,
            recovery_incarnation_id=recovery_id,
            owner_id=uuid4(),
            duration=timedelta(minutes=1),
        )
        assert taken.generation == 2


@pytest.mark.asyncio
async def test_dirty_and_deleted_reject_before_query(schema_db):
    scope, lease_id, recovery_id = await seed_scope(schema_db)
    async with schema_db.transaction() as session:
        row = (await session.execute(select(LeadershipLease))).scalar_one()
        row.updated_at += timedelta(seconds=1)
        with pytest.raises(RepositoryStateError):
            await LeadershipRepository().lock_scope(
                session, scope, lease_id=lease_id, recovery_incarnation_id=recovery_id
            )
        await session.refresh(row)
        await session.delete(row)
        with pytest.raises(RepositoryStateError):
            await LeadershipRepository().lock_scope(
                session, scope, lease_id=lease_id, recovery_incarnation_id=recovery_id
            )
        session.expunge(row)


@pytest.mark.asyncio
async def test_renew_exhausted_version_rejects_before_mutation(schema_db):
    from hmt_crypto.persistence.repository_errors import RepositoryInvariantError

    scope, lease_id, recovery_id = await seed_scope(schema_db)
    repo = LeadershipRepository()
    async with schema_db.transaction() as session:
        held = await repo.acquire(
            session,
            scope,
            lease_id=lease_id,
            recovery_incarnation_id=recovery_id,
            owner_id=uuid4(),
            duration=timedelta(minutes=5),
        )
    async with schema_db.transaction() as session:
        await session.execute(update(LeadershipLease).values(version=2**63 - 1))
    async with schema_db.transaction() as session:
        expected = fence(held).model_copy(update={"version": 2**63 - 1})
        with pytest.raises(RepositoryInvariantError):
            await repo.renew(session, scope, expected, duration=timedelta(minutes=1))
        assert not session.dirty


@pytest.mark.asyncio
async def test_lease_equality_at_expiry_fails_without_mutation(schema_db):
    from hmt_crypto.persistence.leadership_repository import _lease_at
    from hmt_crypto.persistence.repository_locks import _sample, consume_database_time

    scope, lease_id, recovery_id = await seed_scope(schema_db)
    repo = LeadershipRepository()
    async with schema_db.transaction() as session:
        held = await repo.acquire(
            session,
            scope,
            lease_id=lease_id,
            recovery_incarnation_id=recovery_id,
            owner_id=uuid4(),
            duration=timedelta(minutes=1),
        )
    async with schema_db.transaction() as session:
        neutral = await repo.lock_scope(
            session, scope, lease_id=lease_id, recovery_incarnation_id=recovery_id
        )
        current = repo.refine_current(neutral, fence(held))
        sampled = _sample(session, neutral, held.expires_at, False)
        with pytest.raises(FenceRejectedError):
            _lease_at(current, consume_database_time(session, sampled, neutral))
        assert not session.dirty


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["renew", "release"])
async def test_stale_mutable_fence_is_checked_after_database_sample(schema_db, operation):
    scope, lease_id, recovery_id = await seed_scope(schema_db)
    repo = LeadershipRepository()
    async with schema_db.transaction() as session:
        held = await repo.acquire(
            session,
            scope,
            lease_id=lease_id,
            recovery_incarnation_id=recovery_id,
            owner_id=uuid4(),
            duration=timedelta(minutes=5),
        )
    async with schema_db.transaction() as session:
        seen = []
        event.listen(
            schema_db.engine.sync_engine,
            "before_cursor_execute",
            lambda *args: seen.append(args[2]),
        )
        expected = fence(held).model_copy(update={"version": 1})
        with pytest.raises(FenceRejectedError):
            if operation == "renew":
                await repo.renew(session, scope, expected, duration=timedelta(minutes=1))
            else:
                await repo.release(session, scope, expected)
        assert len([stmt for stmt in seen if "strftime" in stmt]) == 1
        assert not session.dirty


@pytest.mark.asyncio
async def test_same_clock_tick_renewal_advances_version_once(schema_db, monkeypatch):
    from hmt_crypto.persistence.repository_clock import RepositoryClock
    from hmt_crypto.persistence.repository_locks import _sample

    scope, lease_id, recovery_id = await seed_scope(schema_db)
    duration = timedelta(minutes=5)
    repo = LeadershipRepository()
    async with schema_db.transaction() as session:
        held = await repo.acquire(
            session,
            scope,
            lease_id=lease_id,
            recovery_incarnation_id=recovery_id,
            owner_id=uuid4(),
            duration=duration,
        )

    async def same_tick(self, session, *, last_lock):
        return _sample(session, last_lock, held.heartbeat_at, False)

    monkeypatch.setattr(RepositoryClock, "sample_after_locks", same_tick)
    async with schema_db.transaction() as session:
        renewed = await repo.renew(session, scope, fence(held), duration=duration)
        assert renewed.heartbeat_at == held.heartbeat_at
        assert renewed.expires_at == held.expires_at
        assert renewed.version == held.version + 1
    async with schema_db.transaction() as session:
        with pytest.raises(FenceRejectedError):
            await repo.release(session, scope, fence(held))
