"""Transaction teardown ownership regressions shared with the schema foundation."""

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import insert, select, text
from test_database_foundation import database as database
from test_database_foundation import probe_table


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_during", ["rollback", "session_close"])
async def test_cancellation_during_teardown_keeps_disposal_blocked_until_session_closed(
    database, monkeypatch, cancel_during
):
    from sqlalchemy.exc import InvalidRequestError
    from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

    from hmt_crypto.persistence.errors import PersistenceError

    table = await probe_table(database)
    rollback_started, rollback_finished = asyncio.Event(), asyncio.Event()
    close_started, close_finished = asyncio.Event(), asyncio.Event()
    allow_rollback, allow_close = asyncio.Event(), asyncio.Event()
    transaction_exit, session_close = AsyncSessionTransaction.__aexit__, AsyncSession.close
    owned_sessions = []

    async def paused_transaction_exit(transaction, *error):
        if transaction.session in owned_sessions:
            rollback_started.set()
            await allow_rollback.wait()
        await transaction_exit(transaction, *error)
        if transaction.session in owned_sessions:
            rollback_finished.set()

    async def paused_session_close(session):
        if session in owned_sessions:
            close_started.set()
            await allow_close.wait()
        await session_close(session)
        if session in owned_sessions:
            close_finished.set()

    monkeypatch.setattr(AsyncSessionTransaction, "__aexit__", paused_transaction_exit)
    monkeypatch.setattr(AsyncSession, "close", paused_session_close)

    async def write_then_fail():
        async with database.transaction() as session:
            owned_sessions.append(session)
            await session.execute(insert(table).values(id=1))
            raise ValueError("safe business failure forces rollback")

    owner = asyncio.create_task(write_then_fail())
    try:
        await asyncio.wait_for(rollback_started.wait(), timeout=5)
        if cancel_during == "session_close":
            allow_rollback.set()
            await asyncio.wait_for(close_started.wait(), timeout=5)
        owner.cancel()
        # FIFO loop barrier: deliver cancellation before attempting disposal,
        # without waiting for a task that must remain pending during cleanup.
        cancellation_delivered = asyncio.Event()
        asyncio.get_running_loop().call_soon(cancellation_delivered.set)
        await cancellation_delivered.wait()
        with pytest.raises(PersistenceError):
            await database.aclose()
        assert not close_finished.is_set()

        allow_rollback.set()
        await asyncio.wait_for(close_started.wait(), timeout=5)
        # A second caller cancellation cannot release ownership either.
        owner.cancel()
        cancellation_delivered.clear()
        asyncio.get_running_loop().call_soon(cancellation_delivered.set)
        await cancellation_delivered.wait()
        with pytest.raises(PersistenceError):
            await database.aclose()
        allow_close.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(owner, timeout=5)
        assert rollback_finished.is_set()
        assert close_finished.is_set()
        with pytest.raises(InvalidRequestError):
            await owned_sessions[0].execute(text("SELECT 1"))
        async with database.transaction() as session:
            assert (await session.execute(select(table))).all() == []
        await database.aclose()
    finally:
        allow_rollback.set()
        allow_close.set()
        await asyncio.gather(owner, return_exceptions=True)
        # Also drain dependency-created cleanup tasks on the expected RED path.
        if owned_sessions and not close_finished.is_set():
            await asyncio.wait_for(close_finished.wait(), timeout=5)


@pytest.mark.asyncio
async def test_successful_transaction_inside_outer_except_commits(database):
    table = await probe_table(database)
    try:
        raise ValueError("already handled outside the database transaction")
    except ValueError:
        async with database.transaction() as session:
            await session.execute(insert(table).values(id=1, amount=Decimal("12.5")))
    async with database.transaction() as session:
        assert (await session.execute(select(table.c.id, table.c.amount))).all() == [
            (1, Decimal("12.5"))
        ]


@pytest.mark.asyncio
async def test_interrupted_entry_closes_session_and_rolls_back_partial_work(database, monkeypatch):
    from sqlalchemy.exc import InvalidRequestError
    from sqlalchemy.ext.asyncio import AsyncSessionTransaction

    table = await probe_table(database)
    original_enter = AsyncSessionTransaction.__aenter__
    sessions = []

    class EntryInterrupted(BaseException):
        pass

    interruption = EntryInterrupted()

    async def interrupted_enter(transaction):
        await original_enter(transaction)
        sessions.append(transaction.session)
        await transaction.session.execute(insert(table).values(id=1))
        raise interruption

    with monkeypatch.context() as patched:
        patched.setattr(AsyncSessionTransaction, "__aenter__", interrupted_enter)
        with pytest.raises(EntryInterrupted) as error:
            async with database.transaction():
                pytest.fail("interrupted entry admitted a transaction body")
        assert error.value is interruption
    with pytest.raises(InvalidRequestError):
        await sessions[0].execute(text("SELECT 1"))
    async with database.transaction() as session:
        assert (await session.execute(select(table))).all() == []
