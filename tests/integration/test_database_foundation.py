"""Local database boundary tests; no external database or business tables."""

import asyncio
import importlib
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import Column, ForeignKey, Integer, MetaData, Table, insert, select, text

from hmt_crypto.config import Settings
from hmt_crypto.errors import ConfigurationError


@pytest_asyncio.fixture
async def database(tmp_path):
    from hmt_crypto.persistence.db import Database

    database = Database(Settings(_env_file=None, DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path}/db"))
    yield database
    await database.aclose()


async def probe_table(database):
    from hmt_crypto.persistence.db import APP_SCHEMA, Base
    from hmt_crypto.persistence.types import ExactDecimal, UTCDateTime

    metadata = MetaData(schema=APP_SCHEMA, naming_convention=Base.metadata.naming_convention)
    table = Table(
        "boundary_probe",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("amount", ExactDecimal(precision=38, scale=18)),
        Column("observed_at", UTCDateTime()),
    )
    async with database.engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    return table


@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST"])
def test_import_settings_and_engine_construction_create_no_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode
):
    monkeypatch.chdir(tmp_path)
    module = importlib.import_module("hmt_crypto.persistence.db")
    assert module.Base.metadata.tables == {}
    assert list(tmp_path.iterdir()) == []
    settings = Settings(_env_file=None, EXECUTION_MODE=mode)
    database = module.Database(settings)
    assert list(tmp_path.iterdir()) == []
    asyncio.run(database.aclose())
    assert list(tmp_path.iterdir()) == []


def test_base_emits_private_schema_and_stable_constraint_names():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    from hmt_crypto.persistence.db import Base

    metadata = MetaData(
        schema=Base.metadata.schema, naming_convention=Base.metadata.naming_convention
    )
    table = Table("schema_probe", metadata, Column("id", Integer, primary_key=True))
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "CREATE TABLE hmt_crypto.schema_probe" in ddl
    assert "CONSTRAINT pk_schema_probe PRIMARY KEY" in ddl


@pytest.mark.parametrize(
    "mode,single_process", [("LIVE", True), ("PAPER", False), ("BACKTEST", False)]
)
def test_sqlite_restrictions_are_checked_before_file_creation(tmp_path, mode, single_process):
    from hmt_crypto.persistence.db import Database

    settings = Settings(
        _env_file=None,
        EXECUTION_MODE=mode,
        DATABASE_SINGLE_PROCESS=single_process,
        DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path}/forbidden.db",
        DELTA_LIVE_TRADING_ENABLED=True,
        DELTA_LIVE_CONFIRMATION="I_UNDERSTAND_THIS_CAN_PLACE_REAL_ORDERS",
        DELTA_INDIA_TESTNET_API_KEY="fixture-key",
        DELTA_INDIA_TESTNET_API_SECRET="fixture-secret",
    )
    with pytest.raises(ConfigurationError):
        Database(settings)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST", "LIVE"])
async def test_postgres_configuration_is_lazy_and_does_not_use_migration_url(mode):
    from hmt_crypto.persistence.db import Database

    database = Database(
        Settings(
            _env_file=None,
            EXECUTION_MODE=mode,
            DATABASE_SINGLE_PROCESS=False,
            DATABASE_URL="postgresql+asyncpg://runtime:runtime-private@invalid.example/db",
            DATABASE_MIGRATION_URL="postgresql+asyncpg://owner:owner-private@direct.invalid/db",
            DELTA_LIVE_TRADING_ENABLED=True,
            DELTA_LIVE_CONFIRMATION="I_UNDERSTAND_THIS_CAN_PLACE_REAL_ORDERS",
            DELTA_INDIA_TESTNET_API_KEY="fixture-key",
            DELTA_INDIA_TESTNET_API_SECRET="fixture-secret",
        )
    )
    assert database.engine.url.host == "invalid.example"
    assert database.engine.sync_engine.hide_parameters is True
    assert "runtime-private" not in repr(database)
    assert "owner-private" not in repr(database)
    await database.aclose()


@pytest.mark.asyncio
async def test_transaction_commits_exact_decimal_and_utc_as_canonical_text(database):
    table = await probe_table(database)
    amount = Decimal("12345678901234567890.123456789012345678")
    observed = datetime(2026, 11, 4, 12, 30, 0, 123456, tzinfo=timezone(timedelta(hours=5.5)))
    async with database.transaction() as session:
        await session.execute(insert(table).values(id=1, amount=amount, observed_at=observed))
    async with database.transaction() as session:
        row = (await session.execute(select(table))).one()
        assert row.amount == amount
        assert row.observed_at == datetime(2026, 11, 4, 7, 0, 0, 123456, tzinfo=UTC)
        assert row.observed_at.tzinfo is UTC
        raw = (
            await session.execute(
                text(
                    "SELECT amount, observed_at, typeof(amount), typeof(observed_at) "
                    "FROM boundary_probe"
                )
            )
        ).one()
        assert tuple(raw) == (
            "12345678901234567890.123456789012345678",
            "2026-11-04T07:00:00.123456+00:00",
            "text",
            "text",
        )


@pytest.mark.asyncio
async def test_body_failure_rolls_back_and_preserves_the_original_safe_error(database):
    table = await probe_table(database)

    class BusinessFailure(Exception):
        pass

    with pytest.raises(BusinessFailure):
        async with database.transaction() as session:
            await session.execute(insert(table).values(id=1, amount=Decimal("1")))
            raise BusinessFailure
    async with database.transaction() as session:
        assert (await session.execute(select(table))).all() == []


@pytest.mark.asyncio
async def test_constraint_failure_is_safe_rolls_back_and_keeps_database_usable(database, caplog):
    from hmt_crypto.persistence.errors import PersistenceError

    table = await probe_table(database)
    async with database.transaction() as session:
        await session.execute(insert(table).values(id=1))
    with pytest.raises(PersistenceError) as error:
        async with database.transaction() as session:
            await session.execute(insert(table).values(id=2))
            await session.execute(
                text("INSERT INTO boundary_probe (id, amount) VALUES (1, :v)"),
                {"v": "private-parameter"},
            )
    assert "private-parameter" not in str(error.value) + repr(error.value) + caplog.text
    async with database.transaction() as session:
        assert (await session.execute(select(table.c.id))).scalars().all() == [1]


@pytest.mark.asyncio
async def test_foreign_keys_apply_on_every_connection(database):
    from hmt_crypto.persistence.db import APP_SCHEMA
    from hmt_crypto.persistence.errors import PersistenceError

    metadata = MetaData(schema=APP_SCHEMA)
    Table("parent_probe", metadata, Column("id", Integer, primary_key=True))
    child = Table(
        "child_probe",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("parent_id", ForeignKey(f"{APP_SCHEMA}.parent_probe.id")),
    )
    async with database.engine.begin() as connection:
        await connection.run_sync(metadata.create_all)

    async def write_invalid(index):
        with pytest.raises(PersistenceError):
            async with database.transaction() as session:
                assert (await session.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
                await session.execute(insert(child).values(id=index, parent_id=999))

    await asyncio.gather(*(write_invalid(index) for index in range(3)))


@pytest.mark.asyncio
async def test_concurrent_sessions_cannot_observe_uncommitted_writes(database):
    table = await probe_table(database)
    inserted, observed = asyncio.Event(), asyncio.Event()
    sessions = []

    async def writer():
        async with database.transaction() as session:
            sessions.append(session)
            await session.execute(insert(table).values(id=1))
            inserted.set()
            await observed.wait()

    async def reader():
        await inserted.wait()
        try:
            async with database.transaction() as session:
                sessions.append(session)
                assert (await session.execute(select(table))).all() == []
        finally:
            observed.set()

    await asyncio.wait_for(asyncio.gather(writer(), reader()), timeout=5)
    assert sessions[0] is not sessions[1]
    async with database.transaction() as session:
        assert (await session.execute(select(table.c.id))).scalar_one() == 1


@pytest.mark.asyncio
async def test_cancelled_transaction_rolls_back_and_releases_session(database):
    table = await probe_table(database)
    inserted = asyncio.Event()

    async def write_until_cancelled():
        async with database.transaction() as session:
            await session.execute(insert(table).values(id=1))
            inserted.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(write_until_cancelled())
    await asyncio.wait_for(inserted.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with database.transaction() as session:
        assert (await session.execute(select(table))).all() == []


@pytest.mark.asyncio
async def test_close_is_idempotent_and_prevents_reopening(database):
    from hmt_crypto.persistence.errors import PersistenceError

    async with database.transaction() as session:
        assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
        with pytest.raises(PersistenceError):
            await database.aclose()
    await database.aclose()
    await database.aclose()
    with pytest.raises(PersistenceError):
        async with database.transaction():
            pytest.fail("closed database admitted work")


@pytest.mark.asyncio
async def test_invalid_bound_value_is_rejected_before_insertion(database):
    from hmt_crypto.persistence.errors import PersistenceError

    table = await probe_table(database)
    with pytest.raises(PersistenceError):
        async with database.transaction() as session:
            await session.execute(insert(table).values(id=1, amount=Decimal("NaN")))
    async with database.transaction() as session:
        assert (await session.execute(select(table))).all() == []


@pytest.mark.asyncio
async def test_session_cannot_be_reused_after_transaction_exit(database):
    from sqlalchemy.exc import InvalidRequestError

    async with database.transaction() as session:
        await session.execute(text("SELECT 1"))
    with pytest.raises(InvalidRequestError):
        await session.execute(text("SELECT 1"))


@pytest.mark.asyncio
async def test_unavailable_sqlite_path_has_safe_error_and_cleanup(tmp_path):
    from hmt_crypto.persistence.db import Database
    from hmt_crypto.persistence.errors import PersistenceError

    database = Database(
        Settings(
            _env_file=None,
            DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path}/private-location/missing/db",
        )
    )
    try:
        with pytest.raises(PersistenceError) as error:
            async with database.transaction() as session:
                await session.execute(text("SELECT 1"))
        assert "private-location" not in str(error.value) + repr(error.value)
    finally:
        await database.aclose()


@pytest.mark.asyncio
async def test_statement_cache_cannot_reuse_a_different_decimal_precision_contract(database):
    from sqlalchemy import literal

    from hmt_crypto.persistence.errors import PersistenceValueError
    from hmt_crypto.persistence.types import ExactDecimal

    value = Decimal("0.0001")
    async with database.transaction() as session:
        assert (
            await session.execute(select(literal(value, type_=ExactDecimal(precision=8, scale=4))))
        ).scalar_one() == value
    with pytest.raises(PersistenceValueError):
        async with database.transaction() as session:
            await session.execute(select(literal(value, type_=ExactDecimal(precision=6, scale=3))))


@pytest.mark.asyncio
async def test_failed_disposal_stays_closed_to_work_and_allows_safe_cleanup_retry(
    database, monkeypatch
):
    from sqlalchemy.ext.asyncio import AsyncEngine

    from hmt_crypto.persistence.errors import PersistenceError

    async with database.transaction() as session:
        await session.execute(text("SELECT 1"))
    dispose = AsyncEngine.dispose
    fail_once = True

    async def interrupted_dispose(engine, close=True):
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise RuntimeError("private-disposal-diagnostic")
        await dispose(engine, close=close)

    monkeypatch.setattr(AsyncEngine, "dispose", interrupted_dispose)
    with pytest.raises(PersistenceError) as error:
        await database.aclose()
    assert "private-disposal-diagnostic" not in str(error.value) + repr(error.value)
    with pytest.raises(PersistenceError):
        async with database.transaction():
            pytest.fail("cleanup failure reopened the database to work")
    await database.aclose()
    await database.aclose()


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
