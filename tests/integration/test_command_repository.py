"""Immediate queue and pre-attempt terminalization; no dispatch composition."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from repository_test_support import seed_scope
from schema_test_support import schema_db as schema_db
from sqlalchemy import event, select, update

from hmt_crypto.persistence.command_repository import CommandRepository
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.models import CommandOperation
from hmt_crypto.persistence.repository_clock import RepositoryClock
from hmt_crypto.persistence.repository_contracts import CommandCorrelation
from hmt_crypto.persistence.repository_errors import (
    FenceRejectedError,
    RepositoryConflictError,
    RepositoryStateError,
)


async def enqueue(session, scope, *, request="request", correlation=None):
    return await CommandRepository().enqueue_or_replay(
        session,
        scope,
        request_id=request,
        operation_kind="SET_MARGIN_MODE",
        correlation=correlation or CommandCorrelation(correlation_id=uuid4()),
        evidence={"margin_mode": "isolated"},
    )


@pytest.mark.asyncio
async def test_enqueue_exact_replay_conflict_and_one_cte_time(schema_db, monkeypatch):
    scope, _, _ = await seed_scope(schema_db)
    correlation = CommandCorrelation(correlation_id=uuid4())
    statements = []
    event.listen(
        schema_db.engine.sync_engine,
        "before_cursor_execute",
        lambda *args: statements.append(args[2]),
    )
    async with schema_db.transaction() as session:
        first = await enqueue(session, scope, correlation=correlation)
        assert first.disposition.value == "CREATED"
        command = first.command
        assert command.created_at == command.available_at == command.updated_at
        assert command.version == 1 and command.attempt_count == 0
    inserts = [s for s in statements if "INSERT" in s]
    assert len(inserts) == 1 and inserts[0].count("strftime") == 1 and "WITH" in inserts[0]

    def forbidden():
        pytest.fail("Replay allocated UUID")

    monkeypatch.setattr("hmt_crypto.persistence.command_repository.uuid4", forbidden)
    async with schema_db.transaction() as session:
        replay = await enqueue(session, scope, correlation=correlation)
        assert replay.disposition.value == "EXISTING" and replay.command == command
        with pytest.raises(RepositoryConflictError):
            await enqueue(session, scope)


@pytest.mark.asyncio
async def test_claim_and_unreserved_terminal_auto_version(schema_db):
    scope, _, _ = await seed_scope(schema_db)
    repo = CommandRepository()
    async with schema_db.transaction() as session:
        first = await enqueue(session, scope)
    async with schema_db.transaction() as session:
        claimed = await repo.claim_next(
            session, scope, owner_id=uuid4(), duration=timedelta(minutes=2), leadership=None
        )
        assert claimed.command.command_id == first.command.command_id
        assert claimed.command.version == 2 and claimed.claim.generation == 1
    async with schema_db.transaction() as session:
        assert (
            await repo.claim_next(
                session, scope, owner_id=uuid4(), duration=timedelta(minutes=1), leadership=None
            )
            is None
        )
    async with schema_db.transaction() as session:
        command = await repo.lock_by_identity(session, scope, first.command.command_id)
        current = repo.refine_current_claim(command, claimed.claim, leadership=None)
        terminal = await repo.lock_unreserved_terminalization(session, current)
        sampled = await RepositoryClock().sample_after_locks(session, last_lock=terminal)
        result = await repo.terminalize_unreserved_at(
            session, terminal, leadership=None, sampled_at=sampled, terminal_status="CANCELLED"
        )
        assert result.status == "CANCELLED" and result.version == 3
        assert result.completed_at == result.updated_at
        assert result.claim_owner is result.claim_expires_at is None
        with pytest.raises(RepositoryStateError):
            await repo.terminalize_unreserved_at(
                session, terminal, leadership=None, sampled_at=sampled, terminal_status="CANCELLED"
            )
    async with schema_db.transaction() as session:
        neutral = await repo.lock_by_identity(session, scope, result.command_id)
        with pytest.raises(RepositoryStateError):
            repo.refine_current_claim(neutral, claimed.claim, leadership=None)


@pytest.mark.asyncio
async def test_future_queued_and_claimed_not_selected(schema_db):
    scope, _, _ = await seed_scope(schema_db)
    async with schema_db.transaction() as session:
        first = await enqueue(session, scope)
    async with schema_db.transaction() as session:
        await session.execute(
            update(CommandOperation)
            .where(CommandOperation.id == first.command.command_id)
            .values(available_at=first.command.created_at + timedelta(days=5))
        )
    async with schema_db.transaction() as session:
        assert (
            await CommandRepository().claim_next(
                session, scope, owner_id=uuid4(), duration=timedelta(minutes=1), leadership=None
            )
            is None
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_enqueue_rollback_on_caller_failure(schema_db, failure):
    scope, _, _ = await seed_scope(schema_db)
    with pytest.raises(failure):
        async with schema_db.transaction() as session:
            await enqueue(session, scope)
            raise failure()
    async with schema_db.transaction() as session:
        assert (await session.execute(select(CommandOperation))).scalars().all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [True, 1.0, 2])
async def test_command_version_cannot_be_assigned(schema_db, bad):
    scope, _, _ = await seed_scope(schema_db)
    async with schema_db.transaction() as session:
        first = await enqueue(session, scope)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            row = await session.get(CommandOperation, first.command.command_id)
            row.version = bad
            row.updated_at += timedelta(seconds=1)
            await session.flush()


@pytest.mark.asyncio
async def test_stale_claim_and_invalid_terminal_status(schema_db):
    scope, _, _ = await seed_scope(schema_db)
    repo = CommandRepository()
    async with schema_db.transaction() as session:
        await enqueue(session, scope)
        claimed = await repo.claim_next(
            session, scope, owner_id=uuid4(), duration=timedelta(minutes=2), leadership=None
        )
    async with schema_db.transaction() as session:
        neutral = await repo.lock_by_identity(session, scope, claimed.claim.command_id)
        with pytest.raises(FenceRejectedError):
            repo.refine_current_claim(
                neutral, claimed.claim.model_copy(update={"version": 1}), leadership=None
            )
    async with schema_db.transaction() as session:
        neutral = await repo.lock_by_identity(session, scope, claimed.claim.command_id)
        current = repo.refine_current_claim(neutral, claimed.claim, leadership=None)
        terminal = await repo.lock_unreserved_terminalization(session, current)
        sampled = await RepositoryClock().sample_after_locks(session, last_lock=terminal)
        with pytest.raises(PersistenceValueError):
            await repo.terminalize_unreserved_at(
                session, terminal, leadership=None, sampled_at=sampled, terminal_status="COMPLETED"
            )
        assert not session.dirty


@pytest.mark.asyncio
async def test_backward_lock_rejected_before_select_and_reads_disable_autoflush(schema_db):
    from hmt_crypto.persistence.repository_locks import LockedEventStream, _mint_lock

    scope, _, _ = await seed_scope(schema_db)
    async with schema_db.transaction() as session:
        first = await enqueue(session, scope)
    async with schema_db.transaction() as session:
        observed = []

        def inspect_reads(*args):
            if "SELECT" in args[2]:
                observed.append(session.autoflush)

        event.listen(schema_db.engine.sync_engine, "before_cursor_execute", inspect_reads)
        await CommandRepository().lock_by_identity(session, scope, first.command.command_id)
        assert observed and not any(observed) and session.autoflush
        _mint_lock(LockedEventStream, session, scope, 70, uuid4(), None)
        observed.clear()
        with pytest.raises(RepositoryStateError):
            await CommandRepository().lock_by_identity(session, scope, first.command.command_id)
        assert not observed


@pytest.mark.asyncio
async def test_live_owner_and_fence_shape(schema_db):
    from repository_test_support import fence

    from hmt_crypto.persistence.leadership_repository import LeadershipRepository

    scope, lease_id, recovery_id = await seed_scope(schema_db, mode="LIVE")
    owner = uuid4()
    async with schema_db.transaction() as session:
        held = await LeadershipRepository().acquire(
            session,
            scope,
            lease_id=lease_id,
            recovery_incarnation_id=recovery_id,
            owner_id=owner,
            duration=timedelta(minutes=5),
        )
        await enqueue(session, scope)
    async with schema_db.transaction() as session:
        with pytest.raises(FenceRejectedError):
            await CommandRepository().claim_next(
                session,
                scope,
                owner_id=uuid4(),
                duration=timedelta(minutes=1),
                leadership=fence(held),
            )
        result = await CommandRepository().claim_next(
            session, scope, owner_id=owner, duration=timedelta(minutes=1), leadership=fence(held)
        )
        assert result.claim.leader_ref == lease_id
        assert result.claim.recovery_ref == recovery_id
        assert result.claim.leader_generation == result.claim.recovery_generation == 1


@pytest.mark.asyncio
async def test_reconciliation_rank_25_must_match_locked_risk_selection(schema_db):
    from types import SimpleNamespace

    from hmt_crypto.persistence.repository_locks import (
        LockedReconciliationRun,
        LockedRiskState,
        _mint_lock,
    )

    scope, _, _ = await seed_scope(schema_db)
    async with schema_db.transaction() as session:
        first = await enqueue(session, scope)
    async with schema_db.transaction() as session:
        from hmt_crypto.persistence.repository_locks import _admit

        await _admit(session)
        run_id = uuid4()
        risk = _mint_lock(
            LockedRiskState,
            session,
            scope,
            20,
            uuid4(),
            SimpleNamespace(reconciliation_run_id=run_id, reconciliation_run_version=3),
        )
        with pytest.raises(RepositoryStateError):
            await CommandRepository().lock_by_identity(
                session, scope, first.command.command_id, last_lock=risk
            )
        run = _mint_lock(
            LockedReconciliationRun,
            session,
            scope,
            25,
            run_id,
            SimpleNamespace(id=run_id, version=2),
            data=risk,
        )
        with pytest.raises(RepositoryStateError):
            await CommandRepository().lock_by_identity(
                session, scope, first.command.command_id, last_lock=run
            )
        good = _mint_lock(
            LockedReconciliationRun,
            session,
            scope,
            25,
            run_id,
            SimpleNamespace(id=run_id, version=3),
            data=risk,
        )
        assert (
            await CommandRepository().lock_by_identity(
                session, scope, first.command.command_id, last_lock=good
            )
        ).rank == 30


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry",
    [
        "clock",
        "leadership.lock_scope",
        "leadership.acquire",
        "leadership.renew",
        "leadership.release",
        "command.enqueue_or_replay",
        "command.claim_next",
        "command.lock_by_identity",
        "command.lock_unreserved_terminalization",
        "command.terminalize_unreserved_at",
        "attempt.lock_order_parent",
        "attempt.lock_attempt_set",
        "attempt.allocate_attempt_at",
        "attempt.lock_attempt",
        "attempt.lock_event_stream",
        "attempt.append_event_at",
    ],
)
async def test_every_session_entry_rejects_pending_work_before_sql(schema_db, entry):
    from hmt_crypto.persistence.attempt_event_repository import AttemptEventRepository
    from hmt_crypto.persistence.leadership_repository import LeadershipRepository
    from hmt_crypto.persistence.models import Account

    scope, lease_id, recovery_id = await seed_scope(schema_db)
    async with schema_db.transaction() as session:
        session.add(Account())
        statements = []
        event.listen(
            schema_db.engine.sync_engine,
            "before_cursor_execute",
            lambda *args: statements.append(args[2]),
        )
        calls = {
            "clock": lambda: RepositoryClock().sample_after_locks(session, last_lock=None),
            "leadership.lock_scope": lambda: LeadershipRepository().lock_scope(
                session, scope, lease_id=lease_id, recovery_incarnation_id=recovery_id
            ),
            "leadership.acquire": lambda: LeadershipRepository().acquire(
                session,
                scope,
                lease_id=lease_id,
                recovery_incarnation_id=recovery_id,
                owner_id=uuid4(),
                duration=timedelta(seconds=1),
            ),
            "leadership.renew": lambda: LeadershipRepository().renew(
                session, scope, None, duration=timedelta(seconds=1)
            ),
            "leadership.release": lambda: LeadershipRepository().release(session, scope, None),
            "command.enqueue_or_replay": lambda: enqueue(session, scope),
            "command.claim_next": lambda: CommandRepository().claim_next(
                session, scope, owner_id=uuid4(), duration=timedelta(seconds=1), leadership=None
            ),
            "command.lock_by_identity": lambda: CommandRepository().lock_by_identity(
                session, scope, uuid4()
            ),
            "command.lock_unreserved_terminalization": lambda: (
                CommandRepository().lock_unreserved_terminalization(session, None)
            ),
            "command.terminalize_unreserved_at": lambda: (
                CommandRepository().terminalize_unreserved_at(
                    session, None, leadership=None, sampled_at=None, terminal_status="FAILED"
                )
            ),
            "attempt.lock_order_parent": lambda: AttemptEventRepository().lock_order_parent(
                session, None, order_id=None
            ),
            "attempt.lock_attempt_set": lambda: AttemptEventRepository().lock_attempt_set(
                session, None, None, last_lock=None
            ),
            "attempt.allocate_attempt_at": lambda: AttemptEventRepository().allocate_attempt_at(
                session, None, None, None, leadership=None, sampled_at=None, credential_version=None
            ),
            "attempt.lock_attempt": lambda: AttemptEventRepository().lock_attempt(
                session, None, uuid4()
            ),
            "attempt.lock_event_stream": lambda: AttemptEventRepository().lock_event_stream(
                session, None, None, proposal=None
            ),
            "attempt.append_event_at": lambda: AttemptEventRepository().append_event_at(
                session, None, None, None, sampled_at=None
            ),
        }
        with pytest.raises(RepositoryStateError):
            await calls[entry]()
        assert not statements and session.autoflush
        session.expunge_all()


@pytest.mark.asyncio
async def test_claim_equality_at_expiry_fails_without_mutation(schema_db):
    from hmt_crypto.persistence.repository_locks import _sample

    scope, _, _ = await seed_scope(schema_db)
    repo = CommandRepository()
    async with schema_db.transaction() as session:
        await enqueue(session, scope)
        claimed = await repo.claim_next(
            session, scope, owner_id=uuid4(), duration=timedelta(minutes=1), leadership=None
        )
    async with schema_db.transaction() as session:
        neutral = await repo.lock_by_identity(session, scope, claimed.claim.command_id)
        current = repo.refine_current_claim(neutral, claimed.claim, leadership=None)
        terminal = await repo.lock_unreserved_terminalization(session, current)
        # Controlled private token factory tests only the exact comparison boundary.
        # It is not evidence of a real database clock or PostgreSQL wait.
        sampled = _sample(session, terminal, claimed.claim.expires_at, False)
        with pytest.raises(FenceRejectedError):
            await repo.terminalize_unreserved_at(
                session, terminal, leadership=None, sampled_at=sampled, terminal_status="FAILED"
            )
        assert not session.dirty


@pytest.mark.asyncio
async def test_postgresql_queue_sql_compiles_skip_locked_and_exact_order(schema_db):
    from sqlalchemy.dialects import postgresql

    scope, _, _ = await seed_scope(schema_db)
    async with schema_db.transaction() as session:
        await enqueue(session, scope)
    compiled = []
    async with schema_db.transaction() as session:

        def capture(state):
            if state.is_select:
                compiled.append(str(state.statement.compile(dialect=postgresql.dialect())))

        event.listen(session.sync_session, "do_orm_execute", capture)
        await CommandRepository().claim_next(
            session, scope, owner_id=uuid4(), duration=timedelta(minutes=1), leadership=None
        )
    queue = next(sql for sql in compiled if "SKIP LOCKED" in sql)
    assert "FOR UPDATE SKIP LOCKED" in queue
    assert (
        "ORDER BY hmt_crypto.command_operations.available_at, "
        "hmt_crypto.command_operations.created_at, hmt_crypto.command_operations.id" in queue
    )


@pytest.mark.asyncio
async def test_terminal_exhausted_version_rejects_before_mutation(schema_db):
    from hmt_crypto.persistence.repository_errors import RepositoryInvariantError

    scope, _, _ = await seed_scope(schema_db)
    repo = CommandRepository()
    async with schema_db.transaction() as session:
        await enqueue(session, scope)
        claimed = await repo.claim_next(
            session, scope, owner_id=uuid4(), duration=timedelta(minutes=1), leadership=None
        )
    async with schema_db.transaction() as session:
        await session.execute(update(CommandOperation).values(version=2**63 - 1))
    async with schema_db.transaction() as session:
        neutral = await repo.lock_by_identity(session, scope, claimed.claim.command_id)
        current = repo.refine_current_claim(
            neutral, claimed.claim.model_copy(update={"version": 2**63 - 1}), leadership=None
        )
        terminal = await repo.lock_unreserved_terminalization(session, current)
        sampled = await RepositoryClock().sample_after_locks(session, last_lock=terminal)
        with pytest.raises(RepositoryInvariantError):
            await repo.terminalize_unreserved_at(
                session, terminal, leadership=None, sampled_at=sampled, terminal_status="FAILED"
            )
        assert not session.dirty


@pytest.mark.asyncio
@pytest.mark.parametrize("divergent", [False, True])
async def test_enqueue_insert_boundary_winner_replay_or_conflict(schema_db, divergent):
    from schema_test_support import NOW
    from sqlalchemy import insert

    from hmt_crypto.persistence.execution_evidence import encode_evidence

    scope, _, _ = await seed_scope(schema_db)
    correlation = CommandCorrelation(correlation_id=uuid4())
    winner_id = uuid4()
    payload, digest = encode_evidence("intent", "SET_MARGIN_MODE", 1, {"margin_mode": "isolated"})
    values = dict(
        id=winner_id,
        **scope.model_dump(),
        request_id="race",
        operation_kind="SET_MARGIN_MODE",
        correlation_id=uuid4() if divergent else correlation.correlation_id,
        status="QUEUED",
        available_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        version=1,
        attempt_count=0,
        claim_generation=0,
        evidence_schema_version=1,
        evidence_payload=payload,
        evidence_sha256=digest,
    )
    fired = False

    def insert_winner(connection, cursor, statement, parameters, context, executemany):
        nonlocal fired
        if not fired and "INSERT" in statement and "command_operations" in statement:
            fired = True
            connection.execute(insert(CommandOperation).values(**values))

    event.listen(schema_db.engine.sync_engine, "before_cursor_execute", insert_winner)
    try:
        if divergent:
            with pytest.raises(RepositoryConflictError):
                async with schema_db.transaction() as session:
                    await enqueue(session, scope, request="race", correlation=correlation)
        else:
            async with schema_db.transaction() as session:
                result = await enqueue(session, scope, request="race", correlation=correlation)
                assert (
                    result.disposition.value == "EXISTING"
                    and result.command.command_id == winner_id
                )
        assert fired
    finally:
        event.remove(schema_db.engine.sync_engine, "before_cursor_execute", insert_winner)


@pytest.mark.asyncio
async def test_omitting_predecessor_cannot_skip_selected_reconciliation(schema_db):
    from types import SimpleNamespace

    from hmt_crypto.persistence.repository_locks import LockedRiskState, _mint_lock

    scope, _, _ = await seed_scope(schema_db)
    async with schema_db.transaction() as session:
        first = await enqueue(session, scope)
    async with schema_db.transaction() as session:
        from hmt_crypto.persistence.repository_locks import _admit

        await _admit(session)
        _mint_lock(
            LockedRiskState,
            session,
            scope,
            20,
            uuid4(),
            SimpleNamespace(reconciliation_run_id=uuid4(), reconciliation_run_version=1),
        )
        seen = []
        event.listen(
            schema_db.engine.sync_engine,
            "before_cursor_execute",
            lambda *args: seen.append(args[2]),
        )
        with pytest.raises(RepositoryStateError):
            await CommandRepository().lock_by_identity(session, scope, first.command.command_id)
        assert not seen
