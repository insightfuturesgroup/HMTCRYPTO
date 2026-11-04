"""Opt-in disposable PostgreSQL evidence. SQLite never satisfies this gate."""

import asyncio
import ipaddress
import os
import re
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from repository_test_support import fence, seed_scope
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema
from test_attempt_event_repository import allocate, append, claimed
from test_command_repository import enqueue

from hmt_crypto.persistence.attempt_event_repository import AttemptEventRepository
from hmt_crypto.persistence.command_repository import CommandRepository
from hmt_crypto.persistence.db import APP_SCHEMA
from hmt_crypto.persistence.execution_evidence import EventEvidence
from hmt_crypto.persistence.leadership_repository import LeadershipRepository
from hmt_crypto.persistence.models import CommandOperation, metadata
from hmt_crypto.persistence.repository_clock import RepositoryClock
from hmt_crypto.persistence.repository_contracts import AttemptEventProposal
from hmt_crypto.persistence.repository_errors import (
    FenceRejectedError,
    LeaseUnavailableError,
    RepositoryConflictError,
)

URL_ENV = "HMT_CRYPTO_REPOSITORY_POSTGRES_URL"
REQUIRED_ENV = "HMT_CRYPTO_REQUIRE_REPOSITORY_POSTGRES"
DISPOSABLE_ENV = "HMT_CRYPTO_REPOSITORY_POSTGRES_DISPOSABLE"


def test_repository_gate_missing_optional_skips(monkeypatch):
    monkeypatch.delenv(URL_ENV, raising=False)
    monkeypatch.delenv(REQUIRED_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception):
        _acceptance_url()


def test_repository_gate_require_fails(monkeypatch):
    monkeypatch.delenv(URL_ENV, raising=False)
    monkeypatch.setenv(REQUIRED_ENV, "true")
    with pytest.raises(pytest.fail.Exception):
        _acceptance_url()


def test_repository_gate_disposable_ack_required(monkeypatch):
    monkeypatch.setenv(URL_ENV, "postgresql+asyncpg://localhost/e1_disposable")
    monkeypatch.delenv(DISPOSABLE_ENV, raising=False)
    with pytest.raises(pytest.fail.Exception):
        _acceptance_url()


@pytest.mark.parametrize(
    "runtime",
    [
        "postgresql+asyncpg://other:other@localhost:5432/e1_disposable",
        "postgresql://localhost./e1_disposable?sslmode=require",
    ],
)
def test_repository_gate_rejects_equivalent_runtime(monkeypatch, tmp_path, runtime):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(URL_ENV, "postgresql+asyncpg://localhost/e1_disposable")
    monkeypatch.setenv(DISPOSABLE_ENV, "true")
    monkeypatch.setenv("DATABASE_URL", runtime)
    monkeypatch.delenv("DATABASE_MIGRATION_URL", raising=False)
    with pytest.raises(pytest.fail.Exception):
        _acceptance_url()


def test_repository_gate_accepts_distinct_disposable_without_connecting(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(URL_ENV, "postgresql+asyncpg://localhost/e1_disposable")
    monkeypatch.setenv(DISPOSABLE_ENV, "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///offline.db")
    monkeypatch.delenv("DATABASE_MIGRATION_URL", raising=False)
    assert _acceptance_url() == make_url("postgresql+asyncpg://localhost/e1_disposable")


def _acceptance_url():
    from test_exact_decimal_postgresql_acceptance import _configured_database_urls, _database_target

    required = os.environ.get(REQUIRED_ENV, "false").lower()
    if required not in ("true", "false"):
        pytest.fail("Repository PostgreSQL require flag must be true or false", pytrace=False)
    raw = os.environ.get(URL_ENV)
    if not raw:
        if required == "true":
            pytest.fail(
                "Disposable repository PostgreSQL acceptance URL is required", pytrace=False
            )
        pytest.skip("Disposable repository PostgreSQL acceptance is not configured")
    if os.environ.get(DISPOSABLE_ENV, "false").lower() != "true":
        pytest.fail("Repository PostgreSQL target is not acknowledged disposable", pytrace=False)
    try:
        url = make_url(raw)
    except Exception:
        pytest.fail("Invalid repository PostgreSQL target", pytrace=False)
    if url.drivername != "postgresql+asyncpg":
        pytest.fail("Repository PostgreSQL acceptance requires asyncpg", pytrace=False)

    def comparable(value):
        if any(
            key.lower() in {"host", "hostaddr", "port", "dbname", "database", "service", "dsn"}
            for key in value.query
        ):
            pytest.fail("Repository PostgreSQL target has ambiguous routing", pytrace=False)
        host, port, database = _database_target(value)
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", host) or not re.fullmatch(
            r"[A-Za-z0-9_-]+", database
        ):
            pytest.fail("Repository PostgreSQL target is not safely comparable", pytrace=False)
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if ":" in host or all(
                re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", label) for label in host.split(".")
            ):
                pytest.fail("Ambiguous numeric PostgreSQL host", pytrace=False)
            canonical_host = "loopback" if host == "localhost" else host
        else:
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
                address = address.ipv4_mapped
            canonical_host = "loopback" if address.is_loopback else address.compressed
        return canonical_host, port, database

    target = comparable(url)
    for runtime, supported in _configured_database_urls():
        if (
            runtime.get_backend_name() in ("postgres", "postgresql")
            and comparable(runtime) == target
        ):
            pytest.fail(
                "Repository PostgreSQL target must differ from runtime and migration targets",
                pytrace=False,
            )
        if not supported:
            pytest.fail("Runtime database target is not safely comparable", pytrace=False)
    return url


class _DisposableRepositoryDatabase:
    """Test-only transaction owner; no runtime Settings or Delta slots are loaded."""

    def __init__(self, url, schema):
        self.engine = create_async_engine(
            url,
            execution_options={"schema_translate_map": {APP_SCHEMA: schema}},
            connect_args={"server_settings": {"timezone": "UTC"}},
            hide_parameters=True,
        )
        self._sessions = async_sessionmaker(
            self.engine, expire_on_commit=False, close_resets_only=False
        )

    def transaction(self):
        return self._sessions.begin()


class _WaitProbe:
    """Observe a real server lock wait; task scheduling alone is not evidence."""

    def __init__(self, db):
        self.db = db
        self.pid = asyncio.get_running_loop().create_future()
        self.statements = []

    @asynccontextmanager
    async def transaction(self):
        async with self.db.transaction() as session:
            connection = (await session.connection()).sync_connection

            def record(conn, cursor, statement, parameters, context, executemany):
                self.statements.append(statement.lower())

            event.listen(connection, "before_cursor_execute", record)
            try:
                self.pid.set_result(
                    (await session.execute(select(func.pg_backend_pid()))).scalar_one()
                )
                yield session
            finally:
                event.remove(connection, "before_cursor_execute", record)

    async def blocked_by(self, holder):
        async with asyncio.timeout(3):
            waiting_pid = await self.pid
            holder_pid = (await holder.execute(select(func.pg_backend_pid()))).scalar_one()
            while (
                holder_pid
                not in (
                    await holder.execute(select(func.pg_blocking_pids(waiting_pid)))
                ).scalar_one()
            ):
                await holder.execute(select(func.pg_sleep(0.005)))


async def _hold_until_expired(holder, expires_at):
    now = (await holder.execute(select(func.clock_timestamp()))).scalar_one()
    remaining = (expires_at - now).total_seconds()
    assert remaining > 0, "Wait must already be observed while the fence is still live"
    await holder.execute(select(func.pg_sleep(remaining + 0.01)))


@pytest_asyncio.fixture
async def postgres_repository_db():
    url = _acceptance_url()
    schema = "e1_acceptance_" + uuid4().hex
    db = _DisposableRepositoryDatabase(url, schema)
    try:
        async with db.engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
            await connection.run_sync(metadata.create_all)
        yield db
    finally:
        async with db.engine.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True))
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_postgres_two_connection_lease_winner(postgres_repository_db):
    db = postgres_repository_db
    scope, lease_id, recovery_id = await seed_scope(db)

    async def acquire_one():
        async with db.transaction() as session:
            return await LeadershipRepository().acquire(
                session,
                scope,
                lease_id=lease_id,
                recovery_incarnation_id=recovery_id,
                owner_id=uuid4(),
                duration=timedelta(minutes=1),
            )

    results = await asyncio.wait_for(
        asyncio.gather(acquire_one(), acquire_one(), return_exceptions=True), 5
    )
    assert sum(isinstance(item, LeaseUnavailableError) for item in results) == 1
    assert sum(not isinstance(item, BaseException) for item in results) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["renew", "release"])
async def test_postgres_expiry_during_lease_lock_wait(postgres_repository_db, operation):
    db = postgres_repository_db
    scope, lease_id, recovery_id = await seed_scope(db)
    repo = LeadershipRepository()
    async with db.transaction() as session:
        held = await repo.acquire(
            session,
            scope,
            lease_id=lease_id,
            recovery_incarnation_id=recovery_id,
            owner_id=uuid4(),
            duration=timedelta(seconds=2),
        )
    probe = _WaitProbe(db)

    async def waiter():
        async with probe.transaction() as session:
            await session.execute(text("SET LOCAL lock_timeout = '3s'"))
            if operation == "renew":
                return await repo.renew(session, scope, fence(held), duration=timedelta(minutes=1))
            return await repo.release(session, scope, fence(held))

    async with db.transaction() as session:
        await repo.lock_scope(
            session, scope, lease_id=lease_id, recovery_incarnation_id=recovery_id
        )
        task = asyncio.create_task(waiter())
        await probe.blocked_by(session)
        await _hold_until_expired(session, held.expires_at)
        assert not task.done()
    with pytest.raises(FenceRejectedError):
        await asyncio.wait_for(task, 5)
    async with db.transaction() as session:
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
async def test_postgres_skip_locked_disjoint_order_and_post_lock_time(postgres_repository_db):
    db = postgres_repository_db
    scope, _, _ = await seed_scope(db)
    async with db.transaction() as session:
        first = await enqueue(session, scope, request="first")
        second = await enqueue(session, scope, request="second")
    async with db.transaction() as first_session:
        before = (await first_session.execute(select(func.clock_timestamp()))).scalar_one()
        a = await CommandRepository().claim_next(
            first_session, scope, owner_id=uuid4(), duration=timedelta(minutes=1), leadership=None
        )
        async with db.transaction() as second_session:
            b = await CommandRepository().claim_next(
                second_session,
                scope,
                owner_id=uuid4(),
                duration=timedelta(minutes=1),
                leadership=None,
            )
        assert a.command.command_id == first.command.command_id
        assert b.command.command_id == second.command.command_id
        assert a.command.updated_at >= before


@pytest.mark.asyncio
async def test_postgres_competing_attempts_have_one_version_winner(postgres_repository_db):
    db = postgres_repository_db
    scope, _, _ = await seed_scope(db)
    original = await claimed(db, scope)
    results = await asyncio.wait_for(
        asyncio.gather(
            allocate(db, scope, original.claim),
            allocate(db, scope, original.claim),
            return_exceptions=True,
        ),
        5,
    )
    assert sum(isinstance(item, FenceRejectedError) for item in results) == 1
    assert sum(not isinstance(item, BaseException) for item in results) == 1
    async with db.transaction() as session:
        row = await session.get(CommandOperation, original.claim.command_id)
        assert row.attempt_count == 1 and row.version == 3


@pytest.mark.asyncio
async def test_postgres_cross_command_event_key_wait_before_sample(postgres_repository_db):
    db = postgres_repository_db
    scope, _, _ = await seed_scope(db)
    initial = await claimed(db, scope)
    first = await allocate(db, scope, initial.claim)
    async with db.transaction() as session:
        await enqueue(session, scope, request="second")
        other = await CommandRepository().claim_next(
            session, scope, owner_id=uuid4(), duration=timedelta(minutes=1), leadership=None
        )
    second = await allocate(db, scope, other.claim)
    proposal = AttemptEventProposal(
        event_key="global-key", event_kind="UNCERTAIN", evidence=EventEvidence(outcome="UNKNOWN")
    )
    repo = AttemptEventRepository()
    async with db.transaction() as session:
        command = await CommandRepository().lock_by_identity(session, scope, first.claim.command_id)
        attempt = await repo.lock_attempt(session, command, first.attempt.attempt_id)
        stream = await repo.lock_event_stream(session, command, attempt, proposal=proposal)
        probe = _WaitProbe(db)
        task = asyncio.create_task(
            append(probe, scope, second.claim.command_id, second.attempt.attempt_id, proposal)
        )
        await probe.blocked_by(session)
        assert "pg_advisory_xact_lock" in probe.statements[-1]
        assert not task.done()
        sampled = await RepositoryClock().sample_after_locks(session, last_lock=stream)
        result = await repo.append_event_at(session, command, attempt, stream, sampled_at=sampled)
        assert result.event.recorded_at == sampled.value
    with pytest.raises(RepositoryConflictError):
        await asyncio.wait_for(task, 5)
    assert not any("clock_timestamp" in stmt or "insert into" in stmt for stmt in probe.statements)


@pytest.mark.asyncio
async def test_postgres_cancellation_while_waiting_releases_transaction(postgres_repository_db):
    db = postgres_repository_db
    scope, _, _ = await seed_scope(db)
    initial = await claimed(db, scope)
    async with db.transaction() as session:
        await CommandRepository().lock_by_identity(session, scope, initial.claim.command_id)
        probe = _WaitProbe(db)
        task = asyncio.create_task(allocate(probe, scope, initial.claim))
        await probe.blocked_by(session)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        fresh_probe = _WaitProbe(db)
        fresh = asyncio.create_task(allocate(fresh_probe, scope, initial.claim))
        await fresh_probe.blocked_by(session)
        release_bound = (await session.execute(select(func.clock_timestamp()))).scalar_one()
    prepared = await asyncio.wait_for(fresh, 5)
    assert prepared.attempt.prepared_at >= release_bound
    assert prepared.command.attempt_count == 1


@pytest.mark.asyncio
async def test_postgres_concurrent_attempt_and_event_64_boundary(postgres_repository_db):
    from hmt_crypto.persistence.repository_errors import RepositoryLimitError

    db = postgres_repository_db
    scope, _, _ = await seed_scope(db)
    prepared = await claimed(db, scope)
    for _ in range(63):
        prepared = await allocate(db, scope, prepared.claim)
    results = await asyncio.wait_for(
        asyncio.gather(
            allocate(db, scope, prepared.claim),
            allocate(db, scope, prepared.claim),
            return_exceptions=True,
        ),
        5,
    )
    winner = next(item for item in results if not isinstance(item, BaseException))
    assert winner.command.attempt_count == 64
    assert any(isinstance(item, (FenceRejectedError, RepositoryLimitError)) for item in results)
    with pytest.raises(RepositoryLimitError):
        await allocate(db, scope, winner.claim)
    for number in range(63):
        proposal = AttemptEventProposal(
            event_key=f"bounded-{number}",
            event_kind="UNCERTAIN",
            evidence=EventEvidence(outcome="UNKNOWN"),
        )
        await append(db, scope, winner.claim.command_id, winner.attempt.attempt_id, proposal)

    async def fresh(key):
        return await append(
            db,
            scope,
            winner.claim.command_id,
            winner.attempt.attempt_id,
            proposal.model_copy(update={"event_key": key}),
        )

    events = await asyncio.wait_for(
        asyncio.gather(fresh("64-a"), fresh("64-b"), return_exceptions=True), 5
    )
    assert sum(isinstance(item, RepositoryLimitError) for item in events) == 1
    assert next(item for item in events if not isinstance(item, BaseException)).event.sequence == 64


@pytest.mark.asyncio
async def test_postgres_reconciliation_wait_precedes_command_lock(postgres_repository_db):
    from risk_reservation_schema_support import reservation_graph
    from schema_test_support import scope as row_scope

    from hmt_crypto.persistence.models import ReconciliationRun, RiskState
    from hmt_crypto.persistence.repository_contracts import AccountScope
    from hmt_crypto.persistence.repository_locks import (
        LockedReconciliationRun,
        LockedRiskState,
        _admit,
        _mint_lock,
    )

    db = postgres_repository_db
    graph = await reservation_graph(db)
    scope = AccountScope(**row_scope(graph[0]))
    run_id, state_id, command_id = graph[2].id, graph[4].id, graph[6].id
    waiting = _WaitProbe(db)

    async def composed():
        async with waiting.transaction() as session:
            await _admit(session)
            await session.execute(text("SET LOCAL lock_timeout = '3s'"))
            with session.no_autoflush:
                state = (
                    await session.execute(
                        select(RiskState).where(RiskState.id == state_id).with_for_update()
                    )
                ).scalar_one()
                risk = _mint_lock(LockedRiskState, session, scope, 20, state.id, state)
                selected = (
                    await session.execute(
                        select(ReconciliationRun)
                        .where(ReconciliationRun.id == state.reconciliation_run_id)
                        .with_for_update()
                    )
                ).scalar_one()
                run = _mint_lock(
                    LockedReconciliationRun, session, scope, 25, selected.id, selected, data=risk
                )
            command = await CommandRepository().lock_by_identity(
                session, scope, command_id, last_lock=run
            )
            return await RepositoryClock().sample_after_locks(session, last_lock=command)

    async with db.transaction() as holder:
        await holder.execute(
            select(ReconciliationRun).where(ReconciliationRun.id == run_id).with_for_update()
        )
        task = asyncio.create_task(composed())
        await waiting.blocked_by(holder)
        assert not task.done()
        async with db.transaction() as probe:
            assert (
                await probe.execute(
                    select(CommandOperation.id)
                    .where(CommandOperation.id == command_id)
                    .with_for_update(nowait=True)
                )
            ).scalar_one() == command_id
        released_after = (await holder.execute(select(func.clock_timestamp()))).scalar_one()
    sampled = await asyncio.wait_for(task, 5)
    assert sampled.value >= released_after


@pytest.mark.asyncio
async def test_postgres_claim_expiry_during_command_lock_wait(postgres_repository_db):
    db = postgres_repository_db
    scope, _, _ = await seed_scope(db)
    async with db.transaction() as session:
        await enqueue(session, scope)
        original = await CommandRepository().claim_next(
            session, scope, owner_id=uuid4(), duration=timedelta(seconds=2), leadership=None
        )
    async with db.transaction() as holder:
        await CommandRepository().lock_by_identity(holder, scope, original.claim.command_id)
        probe = _WaitProbe(db)
        task = asyncio.create_task(allocate(probe, scope, original.claim))
        await probe.blocked_by(holder)
        await _hold_until_expired(holder, original.claim.expires_at)
        assert not task.done()
    with pytest.raises(FenceRejectedError):
        await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
async def test_postgres_concurrent_enqueue_has_one_created_and_one_exact_replay(
    postgres_repository_db,
):
    from hmt_crypto.persistence.repository_contracts import CommandCorrelation

    db = postgres_repository_db
    scope, _, _ = await seed_scope(db)
    correlation = CommandCorrelation(correlation_id=uuid4())

    both_missed = asyncio.Barrier(2)

    async def submit():
        async with db.transaction() as session:
            execute = session.execute
            first_lookup = True

            async def observe_miss(statement, *args, **kwargs):
                nonlocal first_lookup
                result = await execute(statement, *args, **kwargs)
                if first_lookup:
                    first_lookup = False
                    assert statement.is_select
                    copied = result.freeze()
                    assert not copied.data
                    await both_missed.wait()
                    return copied()
                return result

            session.execute = observe_miss
            return await enqueue(session, scope, correlation=correlation)

    a, b = await asyncio.wait_for(asyncio.gather(submit(), submit()), 5)
    assert {a.disposition.value, b.disposition.value} == {"CREATED", "EXISTING"}
    assert a.command == b.command


@pytest.mark.asyncio
async def test_postgres_stale_terminal_claim_cannot_mutate(postgres_repository_db):
    db = postgres_repository_db
    scope, _, _ = await seed_scope(db)
    original = await claimed(db, scope)
    async with db.transaction() as session:
        repo = CommandRepository()
        neutral = await repo.lock_by_identity(session, scope, original.claim.command_id)
        current = repo.refine_current_claim(neutral, original.claim, leadership=None)
        terminal = await repo.lock_unreserved_terminalization(session, current)
        sampled = await RepositoryClock().sample_after_locks(session, last_lock=terminal)
        done = await repo.terminalize_unreserved_at(
            session, terminal, leadership=None, sampled_at=sampled, terminal_status="FAILED"
        )
    async with db.transaction() as session:
        neutral = await CommandRepository().lock_by_identity(
            session, scope, original.claim.command_id
        )
        from hmt_crypto.persistence.repository_errors import RepositoryStateError

        with pytest.raises(RepositoryStateError):
            CommandRepository().refine_current_claim(neutral, original.claim, leadership=None)
        assert neutral._row.version == done.version


@pytest.mark.asyncio
async def test_disposable_setup_does_not_consult_runtime_settings(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "invalid-for-runtime")
    db = _DisposableRepositoryDatabase(
        make_url("postgresql+asyncpg://localhost/offline_only"), "offline_schema"
    )
    await db.engine.dispose()  # No connection opened.


@pytest.mark.parametrize(
    "candidate",
    [
        "postgresql+asyncpg://other/e1_disposable?host=localhost",
        "postgresql+asyncpg://127.0.0.1/e1_disposable",
    ],
)
def test_repository_gate_rejects_ambiguous_or_loopback_equivalent_target(
    monkeypatch, tmp_path, candidate
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(URL_ENV, candidate)
    monkeypatch.setenv(DISPOSABLE_ENV, "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/e1_disposable")
    monkeypatch.delenv("DATABASE_MIGRATION_URL", raising=False)
    with pytest.raises(pytest.fail.Exception):
        _acceptance_url()


@pytest.mark.parametrize(
    "candidate,runtime",
    [
        ("[0:0:0:0:0:0:0:1]", "[::1]"),
        ("[2001:0db8::1]", "[2001:db8:0:0:0:0:0:1]"),
        ("[::ffff:127.0.0.1]", "localhost"),
        ("127.0.0.2", "localhost"),
        ("0177.0.0.1", "localhost"),
    ],
)
def test_equivalent_ip_literal_targets_fail_closed(monkeypatch, tmp_path, candidate, runtime):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(URL_ENV, f"postgresql+asyncpg://{candidate}/same")
    monkeypatch.setenv(DISPOSABLE_ENV, "true")
    monkeypatch.setenv("DATABASE_URL", f"postgresql+asyncpg://{runtime}/same")
    monkeypatch.delenv("DATABASE_MIGRATION_URL", raising=False)
    with pytest.raises(pytest.fail.Exception):
        _acceptance_url()


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_parent", ["account", "credential"])
async def test_postgres_live_fk_wait_precedes_sample_and_expiry(
    postgres_repository_db, blocked_parent
):
    from schema_test_support import credential

    from hmt_crypto.persistence.models import Account, CredentialVersion, OrderAttempt

    db = postgres_repository_db
    scope, lease_id, recovery_id = await seed_scope(db, mode="LIVE")
    owner_id = uuid4()
    leases = LeadershipRepository()
    commands = CommandRepository()
    attempts = AttemptEventRepository()
    async with db.transaction() as s:
        s.add(credential(await s.get(Account, scope.account_id)))
        await s.flush()
        held = await leases.acquire(
            s,
            scope,
            lease_id=lease_id,
            recovery_incarnation_id=recovery_id,
            owner_id=owner_id,
            duration=timedelta(minutes=5),
        )
        await enqueue(s, scope)
    async with db.transaction() as s:
        original = await commands.claim_next(
            s, scope, owner_id=owner_id, duration=timedelta(seconds=2), leadership=fence(held)
        )
    probe = _WaitProbe(db)

    async def prepare():
        async with probe.transaction() as s:
            lease = await leases.lock_scope(
                s, scope, lease_id=lease_id, recovery_incarnation_id=recovery_id
            )
            current_lease = leases.refine_current(lease, fence(held))
            cmd = await commands.lock_by_identity(
                s, scope, original.claim.command_id, last_lock=lease
            )
            order = await attempts.lock_order_parent(s, cmd, order_id=None)
            current = commands.refine_current_claim(cmd, original.claim, leadership=current_lease)
            aset = await attempts.lock_attempt_set(
                s, current, order, last_lock=order, credential_version=1
            )
            sampled = await RepositoryClock().sample_after_locks(s, last_lock=aset)
            return await attempts.allocate_attempt_at(
                s,
                current,
                order,
                aset,
                leadership=current_lease,
                sampled_at=sampled,
                credential_version=1,
            )

    async with db.transaction() as holder:
        if blocked_parent == "account":
            await holder.execute(
                select(Account).where(Account.id == scope.account_id).with_for_update()
            )
        else:
            await holder.execute(
                select(CredentialVersion)
                .filter_by(**scope.model_dump(), credential_version=1)
                .with_for_update()
            )
        task = asyncio.create_task(prepare())
        await probe.blocked_by(holder)
        assert "for key share" in probe.statements[-1]
        assert not any(
            "clock_timestamp" in stmt or "insert into" in stmt for stmt in probe.statements
        )
        await _hold_until_expired(holder, original.claim.expires_at)
    with pytest.raises(FenceRejectedError):
        await asyncio.wait_for(task, 5)
    async with db.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(OrderAttempt)) == 0
        assert (await s.get(CommandOperation, original.claim.command_id)).attempt_count == 0
