"""Real SQLite token ownership tests; no concurrency authority is claimed."""

from copy import copy
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from hmt_crypto.persistence.repository_clock import RepositoryClock
from hmt_crypto.persistence.repository_contracts import AccountScope
from hmt_crypto.persistence.repository_errors import RepositoryStateError
from hmt_crypto.persistence.repository_locks import (
    LockedCommand,
    LockedDatabaseTime,
    _admit,
    _mint_lock,
    consume_database_time,
)


@pytest.mark.asyncio
async def test_exact_final_identity_single_use_and_transaction_binding():
    engine = create_async_engine("sqlite+aiosqlite://")
    scope = AccountScope(account_id=uuid4(), region="india", environment="testnet", mode="PAPER")
    async with AsyncSession(engine) as session, session.begin():
        statements = []
        event.listen(
            engine.sync_engine, "before_cursor_execute", lambda *args: statements.append(args[2])
        )
        await _admit(session)
        lock = _mint_lock(LockedCommand, session, scope, 30, uuid4(), None)
        sampled = await RepositoryClock().sample_after_locks(session, last_lock=lock)
        assert len(statements) == 1
        assert not sampled.authoritative
        with pytest.raises(RepositoryStateError):
            consume_database_time(session, copy(sampled), lock)
        with pytest.raises(RepositoryStateError):
            consume_database_time(session, sampled, copy(lock))
        mutation = consume_database_time(session, sampled, lock)
        assert mutation.value.tzinfo is not None
        with pytest.raises(RepositoryStateError):
            consume_database_time(session, sampled, lock)
        old = await RepositoryClock().sample_after_locks(session, last_lock=lock)
        with pytest.raises(RepositoryStateError):
            _mint_lock(LockedCommand, session, scope, 30, uuid4(), None)
        RepositoryClock().discard_sample(session, last_lock=lock, sampled_at=old)
        with pytest.raises(RepositoryStateError):
            consume_database_time(session, old, lock)
    async with AsyncSession(engine) as other, other.begin():
        with pytest.raises(RepositoryStateError):
            await RepositoryClock().sample_after_locks(other, last_lock=lock)
    await engine.dispose()


@pytest.mark.parametrize("cls", [LockedCommand, LockedDatabaseTime])
def test_constructor_is_inaccessible(cls):
    with pytest.raises((TypeError, RepositoryStateError)):
        cls()


@pytest.mark.asyncio
async def test_clock_rejects_pending_unit_before_sql():
    from hmt_crypto.persistence.models import Account

    engine = create_async_engine("sqlite+aiosqlite://")
    scope = AccountScope(account_id=uuid4(), region="india", environment="testnet", mode="PAPER")
    async with AsyncSession(engine) as session, session.begin():
        await _admit(session)
        lock = _mint_lock(LockedCommand, session, scope, 30, uuid4(), None)
        session.add(Account())
        with pytest.raises(RepositoryStateError):
            await RepositoryClock().sample_after_locks(session, last_lock=lock)
        session.expunge_all()
        await session.execute(text("SELECT 1"))
    await engine.dispose()


@pytest.mark.asyncio
async def test_older_same_lock_sample_and_backward_rank_are_rejected_before_sql():
    engine = create_async_engine("sqlite+aiosqlite://")
    scope = AccountScope(account_id=uuid4(), region="india", environment="testnet", mode="PAPER")
    async with AsyncSession(engine) as session, session.begin():
        await _admit(session)
        lock = _mint_lock(LockedCommand, session, scope, 30, uuid4(), None)
        old = await RepositoryClock().sample_after_locks(session, last_lock=lock)
        with pytest.raises(RepositoryStateError):
            await RepositoryClock().sample_after_locks(session, last_lock=lock)
        RepositoryClock().discard_sample(session, last_lock=lock, sampled_at=old)
        with pytest.raises(RepositoryStateError):
            consume_database_time(session, old, lock)
        _mint_lock(LockedCommand, session, scope, 70, uuid4(), None)
        statements = []
        event.listen(
            engine.sync_engine, "before_cursor_execute", lambda *args: statements.append(args[2])
        )
        with pytest.raises(RepositoryStateError):
            await RepositoryClock().sample_after_locks(session, last_lock=lock)
        assert not statements
        with pytest.raises(RepositoryStateError):
            _mint_lock(LockedCommand, session, scope, 30, uuid4(), None)
    await engine.dispose()


def test_postgresql_advisory_key_uses_database_json_text_and_stable_seed():
    from sqlalchemy.dialects import postgresql

    from hmt_crypto.persistence.attempt_event_repository import _advisory_statement

    scope = AccountScope(account_id=uuid4(), region="india", environment="testnet", mode="PAPER")
    sql = str(_advisory_statement(scope, "key").compile(dialect=postgresql.dialect()))
    assert "pg_advisory_xact_lock" in sql and "hashtextextended" in sql
    assert "jsonb_build_array" in sql and "AS TEXT" in sql
    assert sql.count("AS TEXT") == 2
