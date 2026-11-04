"""Bounded preparation/evidence primitives, without dispatch authority."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from repository_test_support import seed_scope
from schema_test_support import schema_db as schema_db
from sqlalchemy import select, update
from test_command_repository import enqueue

from hmt_crypto.persistence.attempt_event_repository import AttemptEventRepository
from hmt_crypto.persistence.command_repository import CommandRepository
from hmt_crypto.persistence.execution_evidence import EventEvidence
from hmt_crypto.persistence.models import CommandOperation, OrderAttempt
from hmt_crypto.persistence.repository_clock import RepositoryClock
from hmt_crypto.persistence.repository_contracts import AttemptEventProposal, EventAppendResult
from hmt_crypto.persistence.repository_errors import (
    RepositoryConflictError,
    RepositoryInvariantError,
    RepositoryLimitError,
    RepositoryStateError,
)


async def claimed(database, scope):
    async with database.transaction() as session:
        await enqueue(session, scope)
        return await CommandRepository().claim_next(
            session, scope, owner_id=uuid4(), duration=timedelta(hours=1), leadership=None
        )


async def allocate(database, scope, claim):
    async with database.transaction() as session:
        cmd = await CommandRepository().lock_by_identity(session, scope, claim.command_id)
        repo = AttemptEventRepository()
        order = await repo.lock_order_parent(session, cmd, order_id=None)
        current = CommandRepository().refine_current_claim(cmd, claim, leadership=None)
        attempts = await repo.lock_attempt_set(session, current, order, last_lock=order)
        sampled = await RepositoryClock().sample_after_locks(session, last_lock=attempts)
        return await repo.allocate_attempt_at(
            session,
            current,
            order,
            attempts,
            leadership=None,
            sampled_at=sampled,
            credential_version=None,
        )


async def append(database, scope, command_id, attempt_id, proposal):
    async with database.transaction() as session:
        cmd = await CommandRepository().lock_by_identity(session, scope, command_id)
        repo = AttemptEventRepository()
        attempt = await repo.lock_attempt(session, cmd, attempt_id)
        stream = await repo.lock_event_stream(session, cmd, attempt, proposal=proposal)
        if isinstance(stream, EventAppendResult):
            return stream
        sampled = await RepositoryClock().sample_after_locks(session, last_lock=stream)
        return await repo.append_event_at(session, cmd, attempt, stream, sampled_at=sampled)


@pytest.mark.asyncio
async def test_two_attempts_share_trace_have_distinct_ids_and_atomic_counter(schema_db):
    scope, _, _ = await seed_scope(schema_db)
    initial = await claimed(schema_db, scope)
    first = await allocate(schema_db, scope, initial.claim)
    second = await allocate(schema_db, scope, first.claim)
    assert first.attempt.attempt_id != second.attempt.attempt_id
    assert (
        first.attempt.correlation_id
        == second.attempt.correlation_id
        == initial.command.correlation_id
    )
    assert (
        second.attempt.attempt_number,
        second.command.attempt_count,
        second.command.version,
    ) == (2, 2, 4)
    assert second.attempt.prepared_at == second.attempt.recorded_at == second.command.updated_at
    assert second.attempt.backend == "SIMULATOR" and second.attempt.simulator_kind == "PAPER"
    assert second.attempt.original_claim_owner is None
    async with schema_db.transaction() as session:
        cmd = await CommandRepository().lock_by_identity(session, scope, second.command.command_id)
        current = CommandRepository().refine_current_claim(cmd, second.claim, leadership=None)
        with pytest.raises(RepositoryStateError):
            await CommandRepository().lock_unreserved_terminalization(session, current)


@pytest.mark.asyncio
async def test_neutral_order_lock_does_not_refine_terminal_claim(schema_db):
    scope, _, _ = await seed_scope(schema_db)
    initial = await claimed(schema_db, scope)
    async with schema_db.transaction() as session:
        await session.execute(
            update(CommandOperation).values(
                status="FAILED",
                claim_owner=None,
                claim_expires_at=None,
                completed_at=initial.command.updated_at,
            )
        )
    async with schema_db.transaction() as session:
        cmd = await CommandRepository().lock_by_identity(session, scope, initial.claim.command_id)
        order = await AttemptEventRepository().lock_order_parent(session, cmd, order_id=None)
        assert order.rank == 40
        with pytest.raises(RepositoryStateError):
            await AttemptEventRepository().lock_attempt_set(session, cmd, order, last_lock=order)


@pytest.mark.asyncio
async def test_event_replay_survives_terminal_and_conflict_precedes_lifecycle(
    schema_db, monkeypatch
):
    scope, _, _ = await seed_scope(schema_db)
    prepared = await allocate(schema_db, scope, (await claimed(schema_db, scope)).claim)
    proposal = AttemptEventProposal(
        event_key="result",
        event_kind="SIMULATED_ACCEPTED",
        evidence=EventEvidence(outcome="ACCEPTED"),
    )
    first = await append(
        schema_db, scope, prepared.claim.command_id, prepared.attempt.attempt_id, proposal
    )
    assert first.event.sequence == 1 and first.disposition.value == "APPENDED"
    async with schema_db.transaction() as session:
        await session.execute(
            update(CommandOperation).values(
                status="FAILED",
                claim_owner=None,
                claim_expires_at=None,
                completed_at=prepared.command.updated_at,
            )
        )

    def forbidden():
        pytest.fail("Replay allocated UUID")

    monkeypatch.setattr("hmt_crypto.persistence.attempt_event_repository.uuid4", forbidden)
    assert (
        await append(
            schema_db, scope, prepared.claim.command_id, prepared.attempt.attempt_id, proposal
        )
    ).event == first.event
    conflict = AttemptEventProposal(
        event_key="result", event_kind="UNCERTAIN", evidence=EventEvidence(outcome="UNKNOWN")
    )
    with pytest.raises(RepositoryConflictError):
        await append(
            schema_db, scope, prepared.claim.command_id, prepared.attempt.attempt_id, conflict
        )
    fresh = proposal.model_copy(update={"event_key": "new"})
    with pytest.raises(RepositoryStateError):
        await append(
            schema_db, scope, prepared.claim.command_id, prepared.attempt.attempt_id, fresh
        )


@pytest.mark.asyncio
async def test_attempt_64_limit_and_projection_mismatch(schema_db):
    scope, _, _ = await seed_scope(schema_db)
    result = await claimed(schema_db, scope)
    for _ in range(64):
        result = await allocate(schema_db, scope, result.claim)
    with pytest.raises(RepositoryLimitError):
        await allocate(schema_db, scope, result.claim)
    async with schema_db.transaction() as session:
        await session.execute(update(CommandOperation).values(attempt_count=63))
    with pytest.raises(RepositoryInvariantError):
        await allocate(schema_db, scope, result.claim)


@pytest.mark.asyncio
async def test_event_64_limit_and_sequence_gap(schema_db):
    scope, _, _ = await seed_scope(schema_db)
    prepared = await allocate(schema_db, scope, (await claimed(schema_db, scope)).claim)
    for number in range(64):
        proposal = AttemptEventProposal(
            event_key=f"event-{number}",
            event_kind="UNCERTAIN",
            evidence=EventEvidence(outcome="UNKNOWN"),
        )
        result = await append(
            schema_db, scope, prepared.claim.command_id, prepared.attempt.attempt_id, proposal
        )
        assert result.event.sequence == number + 1
    with pytest.raises(RepositoryLimitError):
        await append(
            schema_db,
            scope,
            prepared.claim.command_id,
            prepared.attempt.attempt_id,
            proposal.model_copy(update={"event_key": "65"}),
        )
    assert (
        await append(
            schema_db, scope, prepared.claim.command_id, prepared.attempt.attempt_id, proposal
        )
    ).event == result.event


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_allocation_rollback_restores_counter_and_insert(schema_db, failure):
    scope, _, _ = await seed_scope(schema_db)
    initial = await claimed(schema_db, scope)
    with pytest.raises(failure):
        async with schema_db.transaction() as session:
            cmd = await CommandRepository().lock_by_identity(
                session, scope, initial.claim.command_id
            )
            repo = AttemptEventRepository()
            order = await repo.lock_order_parent(session, cmd, order_id=None)
            current = CommandRepository().refine_current_claim(cmd, initial.claim, leadership=None)
            attempts = await repo.lock_attempt_set(session, current, order, last_lock=order)
            sampled = await RepositoryClock().sample_after_locks(session, last_lock=attempts)
            await repo.allocate_attempt_at(
                session,
                current,
                order,
                attempts,
                leadership=None,
                sampled_at=sampled,
                credential_version=None,
            )
            raise failure()
    async with schema_db.transaction() as session:
        assert (await session.execute(select(OrderAttempt))).scalars().all() == []
        row = await session.get(CommandOperation, initial.claim.command_id)
        assert row.attempt_count == 0 and row.version == 2


@pytest.mark.asyncio
async def test_backend_outcome_validation_precedes_key_lookup(schema_db):
    scope, _, _ = await seed_scope(schema_db)
    prepared = await allocate(schema_db, scope, (await claimed(schema_db, scope)).claim)
    proposal = AttemptEventProposal(
        event_key="venue",
        event_kind="RESPONSE_ACCEPTED",
        evidence=EventEvidence(outcome="ACCEPTED"),
    )
    with pytest.raises(RepositoryStateError):
        await append(
            schema_db, scope, prepared.claim.command_id, prepared.attempt.attempt_id, proposal
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption", ["attempt_gap", "attempt_projection", "event_gap", "event_outcome"]
)
async def test_stored_gap_projection_and_event_evidence_fail_closed(schema_db, corruption):
    from hmt_crypto.persistence.execution_evidence import encode_evidence
    from hmt_crypto.persistence.models import OrderAttemptEvent

    scope, _, _ = await seed_scope(schema_db)
    prepared = await allocate(schema_db, scope, (await claimed(schema_db, scope)).claim)
    proposal = AttemptEventProposal(
        event_key="first", event_kind="UNCERTAIN", evidence=EventEvidence(outcome="UNKNOWN")
    )
    await append(schema_db, scope, prepared.claim.command_id, prepared.attempt.attempt_id, proposal)
    async with schema_db.transaction() as session:
        if corruption == "attempt_gap":
            await session.execute(update(OrderAttempt).values(attempt_number=2))
        elif corruption == "attempt_projection":
            await session.execute(update(CommandOperation).values(attempt_count=0))
        elif corruption == "event_gap":
            await session.execute(update(OrderAttemptEvent).values(sequence=2))
        else:
            payload, digest = encode_evidence("event", "UNCERTAIN", 1, {"outcome": "ACCEPTED"})
            await session.execute(
                update(OrderAttemptEvent).values(evidence_payload=payload, evidence_sha256=digest)
            )
    with pytest.raises(RepositoryInvariantError):
        await append(
            schema_db,
            scope,
            prepared.claim.command_id,
            prepared.attempt.attempt_id,
            proposal.model_copy(update={"event_key": "next"}),
        )


@pytest.mark.asyncio
async def test_late_evidence_survives_expired_claim_and_future_observation_fails(schema_db):
    from hmt_crypto.persistence.errors import PersistenceValueError

    scope, _, _ = await seed_scope(schema_db)
    prepared = await allocate(schema_db, scope, (await claimed(schema_db, scope)).claim)
    async with schema_db.transaction() as session:
        await session.execute(
            update(CommandOperation).values(
                updated_at=prepared.command.created_at,
                claim_expires_at=prepared.command.created_at + timedelta(microseconds=1),
            )
        )
    proposal = AttemptEventProposal(
        event_key="late", event_kind="UNCERTAIN", evidence=EventEvidence(outcome="UNKNOWN")
    )
    first = await append(
        schema_db, scope, prepared.claim.command_id, prepared.attempt.attempt_id, proposal
    )
    assert first.event.sequence == 1
    with pytest.raises(PersistenceValueError):
        await append(
            schema_db,
            scope,
            prepared.claim.command_id,
            prepared.attempt.attempt_id,
            proposal.model_copy(
                update={
                    "event_key": "future",
                    "observed_at": prepared.command.created_at + timedelta(days=2),
                }
            ),
        )


@pytest.mark.asyncio
async def test_unknown_commit_recovery_reads_original_ordinal_without_allocation(
    schema_db, monkeypatch
):
    scope, _, _ = await seed_scope(schema_db)
    initial = await claimed(schema_db, scope)
    original_ordinal = initial.command.attempt_count + 1

    class LostAcknowledgement(Exception):
        pass

    async def prepare_with_lost_ack():
        await allocate(schema_db, scope, initial.claim)
        raise LostAcknowledgement  # The caller did not get the receipt or UUID.

    with pytest.raises(LostAcknowledgement):
        await prepare_with_lost_ack()

    def forbidden():
        pytest.fail("Recovery read allocated replacement UUID")

    monkeypatch.setattr("hmt_crypto.persistence.attempt_event_repository.uuid4", forbidden)
    async with schema_db.transaction() as session:
        cmd = await CommandRepository().lock_by_identity(session, scope, initial.claim.command_id)
        with session.no_autoflush:
            rows = (
                (
                    await session.execute(
                        select(OrderAttempt)
                        .filter_by(**scope.model_dump(), command_id=initial.claim.command_id)
                        .order_by(OrderAttempt.attempt_number, OrderAttempt.id)
                        .limit(65)
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == cmd._row.attempt_count == original_ordinal == 1
        recovered = rows[original_ordinal - 1]
        assert recovered.attempt_number == original_ordinal
        assert recovered.correlation_id == initial.command.correlation_id
        assert recovered.evidence_sha256 == initial.command.evidence_sha256
        attempt = await AttemptEventRepository().lock_attempt(session, cmd, recovered.id)
        assert attempt.identity == recovered.id
    # This proves read-only primitives; the Task 2.4 recovery coordinator remains deferred.


@pytest.mark.asyncio
async def test_place_requires_exact_reservation_row_predecessor(schema_db):
    from risk_reservation_schema_support import reservation_graph
    from schema_test_support import scope as row_scope

    from hmt_crypto.persistence.command_repository import _claim
    from hmt_crypto.persistence.models import RiskReservation
    from hmt_crypto.persistence.repository_contracts import AccountScope
    from hmt_crypto.persistence.repository_locks import _Handle, _mint_lock

    graph = await reservation_graph(schema_db)
    owner, cmd, row, reservation = graph[0], graph[6], graph[7], graph[-1]
    scope = AccountScope(**row_scope(owner))
    async with schema_db.transaction() as session:
        session.add(reservation)
    async with schema_db.transaction() as session:
        neutral = await CommandRepository().lock_by_identity(session, scope, cmd.id)
        order = await AttemptEventRepository().lock_order_parent(session, neutral, order_id=row.id)
        current = CommandRepository().refine_current_claim(
            neutral, _claim(neutral._row), leadership=None
        )
        with pytest.raises(RepositoryStateError):
            await AttemptEventRepository().lock_attempt_set(
                session, current, order, last_lock=order
            )
        with session.no_autoflush:
            stored = (
                await session.execute(
                    select(RiskReservation)
                    .where(RiskReservation.id == reservation.id)
                    .with_for_update()
                )
            ).scalar_one()
        predecessor = _mint_lock(_Handle, session, scope, 50, stored.id, stored)
        assert predecessor.identity != cmd.id
        attempts = await AttemptEventRepository().lock_attempt_set(
            session, current, order, last_lock=predecessor
        )
        assert attempts.rank == 60


@pytest.mark.asyncio
async def test_any_reservation_blocks_generic_terminalization(schema_db):
    from risk_reservation_schema_support import reservation_graph
    from schema_test_support import scope as row_scope

    from hmt_crypto.persistence.command_repository import _claim
    from hmt_crypto.persistence.repository_contracts import AccountScope

    graph = await reservation_graph(schema_db)
    owner, cmd, reservation = graph[0], graph[6], graph[-1]
    scope = AccountScope(**row_scope(owner))
    async with schema_db.transaction() as session:
        session.add(reservation)
    async with schema_db.transaction() as session:
        neutral = await CommandRepository().lock_by_identity(session, scope, cmd.id)
        current = CommandRepository().refine_current_claim(
            neutral, _claim(neutral._row), leadership=None
        )
        with pytest.raises(RepositoryStateError):
            await CommandRepository().lock_unreserved_terminalization(session, current)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["attempt", "event"])
async def test_count_and_retrieval_mismatch_uses_real_insert_hook(schema_db, kind):
    from sqlalchemy import event, insert

    from hmt_crypto.persistence.models import OrderAttemptEvent

    scope, _, _ = await seed_scope(schema_db)
    prepared = await allocate(schema_db, scope, (await claimed(schema_db, scope)).claim)
    proposal = AttemptEventProposal(
        event_key="existing", event_kind="UNCERTAIN", evidence=EventEvidence(outcome="UNKNOWN")
    )
    await append(schema_db, scope, prepared.claim.command_id, prepared.attempt.attempt_id, proposal)
    model = OrderAttempt if kind == "attempt" else OrderAttemptEvent
    async with schema_db.transaction() as session:
        row = (await session.execute(select(model))).scalar_one()
        values = {column.name: getattr(row, column.name) for column in model.__table__.columns}
    values["id"] = uuid4()
    if kind == "attempt":
        values["attempt_number"] = 2
    else:
        values.update(sequence=2, event_key="injected")
    fired = False

    def inject(connection, cursor, statement, parameters, context, executemany):
        nonlocal fired
        if not fired and "count(" in statement and model.__tablename__ in statement:
            fired = True
            connection.execute(insert(model).values(**values))

    event.listen(schema_db.engine.sync_engine, "after_cursor_execute", inject)
    try:
        with pytest.raises(RepositoryInvariantError):
            if kind == "attempt":
                await allocate(schema_db, scope, prepared.claim)
            else:
                await append(
                    schema_db,
                    scope,
                    prepared.claim.command_id,
                    prepared.attempt.attempt_id,
                    proposal.model_copy(update={"event_key": "fresh"}),
                )
        assert fired
    finally:
        event.remove(schema_db.engine.sync_engine, "after_cursor_execute", inject)


@pytest.mark.asyncio
async def test_stored_order_attempt_without_order_fails_parent_validation(schema_db):
    from execution_test_support import attempt as fixture_attempt
    from risk_reservation_schema_support import reservation_graph
    from schema_test_support import scope as row_scope

    from hmt_crypto.persistence.repository_contracts import AccountScope

    graph = await reservation_graph(schema_db)
    owner, cmd = graph[0], graph[6]
    scope = AccountScope(**row_scope(owner))
    malformed = fixture_attempt(owner, cmd, correlation_id=cmd.correlation_id)
    async with schema_db.transaction() as session:
        session.add(malformed)
        await session.flush()
        await session.execute(
            update(CommandOperation).where(CommandOperation.id == cmd.id).values(attempt_count=1)
        )
    async with schema_db.transaction() as session:
        neutral = await CommandRepository().lock_by_identity(session, scope, cmd.id)
        with pytest.raises(RepositoryInvariantError):
            await AttemptEventRepository().lock_attempt(session, neutral, malformed.id)


@pytest.mark.asyncio
async def test_attempt_set_rejects_substituted_neutral_command_or_older_predecessor(schema_db):
    from hmt_crypto.persistence.repository_locks import LockedCommand, LockedOrderParent, _mint_lock

    scope, _, _ = await seed_scope(schema_db)
    claim = (await claimed(schema_db, scope)).claim
    async with schema_db.transaction() as session:
        first = await CommandRepository().lock_by_identity(session, scope, claim.command_id)
        second = _mint_lock(LockedCommand, session, scope, 30, first.identity, first._row)
        order = await AttemptEventRepository().lock_order_parent(session, second, order_id=None)
        wrong = CommandRepository().refine_current_claim(first, claim, leadership=None)
        with pytest.raises(RepositoryStateError):
            await AttemptEventRepository().lock_attempt_set(session, wrong, order, last_lock=order)
        current = CommandRepository().refine_current_claim(second, claim, leadership=None)
        _mint_lock(LockedOrderParent, session, scope, 40, None, None, data=second)
        with pytest.raises(RepositoryStateError):
            await AttemptEventRepository().lock_attempt_set(
                session, current, order, last_lock=order
            )
