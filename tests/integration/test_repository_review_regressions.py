"""Public E1 regressions from the independent live review."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from repository_test_support import fence, seed_scope
from schema_test_support import schema_db as schema_db
from sqlalchemy import event, select, update
from test_attempt_event_repository import allocate, append, claimed
from test_command_repository import enqueue

from hmt_crypto.persistence.attempt_event_repository import AttemptEventRepository
from hmt_crypto.persistence.command_repository import CommandRepository, _claim
from hmt_crypto.persistence.execution_evidence import EventEvidence
from hmt_crypto.persistence.leadership_repository import LeadershipRepository, _receipt
from hmt_crypto.persistence.models import (
    CommandOperation,
    LeadershipLease,
    OrderAttempt,
    OrderAttemptEvent,
)
from hmt_crypto.persistence.repository_clock import RepositoryClock
from hmt_crypto.persistence.repository_contracts import (
    AccountScope,
    AttemptEventProposal,
    AttemptEventReceipt,
    AttemptReceipt,
    CommandReceipt,
    LeaseSnapshot,
)
from hmt_crypto.persistence.repository_errors import RepositoryInvariantError, RepositoryStateError


def proposal(key="event"):
    return AttemptEventProposal(
        event_key=key, event_kind="UNCERTAIN", evidence=EventEvidence(outcome="UNKNOWN")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,dto,identity",
    [
        (LeadershipLease, LeaseSnapshot, "lease_id"),
        (CommandOperation, CommandReceipt, "command_id"),
        (OrderAttempt, AttemptReceipt, "attempt_id"),
        (OrderAttemptEvent, AttemptEventReceipt, "event_id"),
    ],
)
async def test_native_asyncpg_uuid_materialization(schema_db, model, dto, identity):
    from asyncpg.pgproto.pgproto import UUID as NativeUUID
    from pydantic import ValidationError
    from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg
    from sqlalchemy.orm.attributes import set_committed_value

    scope, _, _ = await seed_scope(schema_db)
    result = await allocate(schema_db, scope, (await claimed(schema_db, scope)).claim)
    await append(schema_db, scope, result.claim.command_id, result.attempt.attempt_id, proposal())
    assert (
        model.__table__.c.id.type.dialect_impl(PGDialect_asyncpg()).result_processor(
            PGDialect_asyncpg(), None
        )
        is None
    )
    async with schema_db.transaction() as s:
        row = (await s.execute(select(model))).scalar_one()
        for col in model.__table__.columns:
            value = getattr(row, col.name)
            if type(value) is UUID:
                set_committed_value(row, col.name, NativeUUID(str(value)))
        receipt = _receipt(dto, scope, row, identity)
        assert type(getattr(receipt, identity)) is UUID
        if model is CommandOperation:
            assert type(_claim(row).owner_id) is UUID
        with pytest.raises(ValidationError):
            AccountScope(**(scope.model_dump() | {"account_id": NativeUUID(str(scope.account_id))}))


@pytest.mark.asyncio
@pytest.mark.parametrize("breakage", ["expunge", "expire", "replace", "delete"])
@pytest.mark.parametrize("path", ["terminal", "allocation", "event", "leadership"])
async def test_no_authority_for_unowned_orm_rows(schema_db, breakage, path):
    scope, lease_id, recovery_id = await seed_scope(schema_db)
    initial = await claimed(schema_db, scope)
    if path == "event":
        prepared = await allocate(schema_db, scope, initial.claim)
    async with schema_db.transaction() as s:
        commands = CommandRepository()
        attempts = AttemptEventRepository()
        leases = LeadershipRepository()
        if path == "leadership":
            held = await leases.acquire(
                s,
                scope,
                lease_id=lease_id,
                recovery_incarnation_id=recovery_id,
                owner_id=uuid4(),
                duration=timedelta(minutes=1),
            )
            lock = await leases.lock_scope(
                s, scope, lease_id=lease_id, recovery_incarnation_id=recovery_id
            )
            row = lock._row
        else:
            cmd = await commands.lock_by_identity(s, scope, initial.claim.command_id)
            row = cmd._row
            if path == "terminal":
                current = commands.refine_current_claim(cmd, initial.claim, leadership=None)
                lock = await commands.lock_unreserved_terminalization(s, current)
            elif path == "allocation":
                order = await attempts.lock_order_parent(s, cmd, order_id=None)
                current = commands.refine_current_claim(cmd, initial.claim, leadership=None)
                lock = await attempts.lock_attempt_set(s, current, order, last_lock=order)
            else:
                attempt = await attempts.lock_attempt(s, cmd, prepared.attempt.attempt_id)
                lock = await attempts.lock_event_stream(s, cmd, attempt, proposal=proposal())
        if path != "leadership":
            sampled = await RepositoryClock().sample_after_locks(s, last_lock=lock)
        identity = row.id
        if breakage == "expire":
            s.expire(row)
        elif breakage == "delete":
            await s.delete(row)
        else:
            s.expunge(row)
            if breakage == "replace":
                assert (await s.get(type(row), identity)) is not row
        seen = []
        event.listen(s.sync_session, "do_orm_execute", lambda *args: seen.append(1))
        with pytest.raises(RepositoryStateError):
            if path == "terminal":
                await commands.terminalize_unreserved_at(
                    s, lock, leadership=None, sampled_at=sampled, terminal_status="FAILED"
                )
            elif path == "allocation":
                await attempts.allocate_attempt_at(
                    s,
                    current,
                    order,
                    lock,
                    leadership=None,
                    sampled_at=sampled,
                    credential_version=None,
                )
            elif path == "event":
                await attempts.append_event_at(s, cmd, attempt, lock, sampled_at=sampled)
            else:
                leases.refine_current(lock, fence(held))
        assert not seen
        if breakage == "delete":
            s.expunge(row)
    async with schema_db.transaction() as s:
        persisted = await s.get(CommandOperation, initial.claim.command_id)
        assert persisted.status == "CLAIMED"
        assert persisted.attempt_count == (1 if path == "event" else 0)


@pytest.mark.asyncio
async def test_nested_boundaries_and_outstanding_sample(schema_db, monkeypatch):
    scope, _, _ = await seed_scope(schema_db)
    initial = await claimed(schema_db, scope)
    async with schema_db.transaction() as s:
        repo = CommandRepository()
        clock = RepositoryClock()
        cmd = await repo.lock_by_identity(s, scope, initial.claim.command_id)
        current = repo.refine_current_claim(cmd, initial.claim, leadership=None)
        terminal = await repo.lock_unreserved_terminalization(s, current)
        sample = await clock.sample_after_locks(s, last_lock=terminal)
        seen = []
        event.listen(s.sync_session, "do_orm_execute", lambda *args: seen.append(1))
        with monkeypatch.context() as patch:
            patch.setattr(
                "hmt_crypto.persistence.command_repository.uuid4",
                lambda: pytest.fail("unexpected UUID"),
            )
            with pytest.raises(RepositoryStateError):
                await enqueue(s, scope, request="other")
        async with s.begin_nested():
            with pytest.raises(RepositoryStateError):
                await repo.terminalize_unreserved_at(
                    s, terminal, leadership=None, sampled_at=sample, terminal_status="FAILED"
                )
            with pytest.raises(RepositoryStateError):
                await enqueue(s, scope, request="nested")
        assert not seen
        clock.discard_sample(s, last_lock=terminal, sampled_at=sample)
        with pytest.raises(RepositoryStateError):
            await repo.terminalize_unreserved_at(
                s, terminal, leadership=None, sampled_at=sample, terminal_status="FAILED"
            )
        fresh = await clock.sample_after_locks(s, last_lock=terminal)
        assert (
            await repo.terminalize_unreserved_at(
                s, terminal, leadership=None, sampled_at=fresh, terminal_status="FAILED"
            )
        ).status == "FAILED"
    async with schema_db.transaction() as s:
        assert (await enqueue(s, scope, request="after")).command.status == "QUEUED"


@pytest.mark.asyncio
async def test_nested_entry_before_sql_without_outstanding_sample(schema_db):
    scope, _, _ = await seed_scope(schema_db)
    async with schema_db.transaction() as s:
        seen = []
        event.listen(s.sync_session, "do_orm_execute", lambda *args: seen.append(1))
        async with s.begin_nested():
            with pytest.raises(RepositoryStateError):
                await enqueue(s, scope)
        assert not seen
        assert (await enqueue(s, scope)).command.status == "QUEUED"


@pytest.mark.asyncio
async def test_equal_rank_total_order_and_failed_validation_poison(schema_db):
    scope, _, _ = await seed_scope(schema_db)
    async with schema_db.transaction() as s:
        a = await enqueue(s, scope, request="a")
        b = await enqueue(s, scope, request="b")
    lo, hi = sorted([a.command.command_id, b.command.command_id])
    repo = CommandRepository()
    async with schema_db.transaction() as s:
        low = await repo.lock_by_identity(s, scope, lo)
        high = await repo.lock_by_identity(s, scope, hi, last_lock=low)
        seen = []
        event.listen(s.sync_session, "do_orm_execute", lambda *args: seen.append(1))
        with pytest.raises(RepositoryStateError):
            await repo.lock_by_identity(s, scope, lo, last_lock=high)
        assert not seen
    async with schema_db.transaction() as s:
        await s.execute(
            update(CommandOperation)
            .where(CommandOperation.id == hi)
            .values(evidence_sha256="a" * 64)
        )
    async with schema_db.transaction() as s:
        with pytest.raises(RepositoryInvariantError):
            await repo.lock_by_identity(s, scope, hi)
        seen = []
        event.listen(s.sync_session, "do_orm_execute", lambda *args: seen.append(1))
        with pytest.raises(RepositoryStateError):
            await repo.lock_by_identity(s, scope, lo)
        assert not seen
    async with schema_db.transaction() as s:
        assert (await repo.lock_by_identity(s, scope, lo)).identity == lo


def test_constructed_hostile_nested_evidence_fails_without_callbacks_or_text():
    from hmt_crypto.persistence.errors import PersistenceValueError
    from hmt_crypto.persistence.leadership_repository import _input

    class Hostile(str):
        def __ne__(self, other):
            raise RuntimeError("synthetic-private-outcome")

        def __eq__(self, other):
            raise RuntimeError("synthetic-private-outcome")

        def __str__(self):
            raise RuntimeError("synthetic-private-outcome")

    data = proposal().model_copy(
        update={"evidence": EventEvidence.model_construct(outcome=Hostile("UNKNOWN"))}
    )
    with pytest.raises(PersistenceValueError) as err:
        _input(AttemptEventProposal, data)
    assert "synthetic-private" not in str(err.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["valid", "other_order"])
async def test_place_attempt_requires_command_origin(schema_db, change):
    from types import SimpleNamespace

    from execution_test_support import attempt, command, order
    from risk_reservation_schema_support import reservation_graph
    from schema_test_support import scope as row_scope

    graph = await reservation_graph(schema_db)
    owner, cmd, original = graph[0], graph[6], graph[7]
    scope = AccountScope(**row_scope(owner))
    async with schema_db.transaction() as s:
        other = command(owner, request_id="other-origin")
        s.add(other)
        await s.flush()
        substitute = order(
            owner,
            other,
            SimpleNamespace(id=original.catalogue_snapshot_id),
            client_order_id="substituted",
        )
        s.add(substitute)
        await s.flush()
        prepared = attempt(
            owner,
            cmd,
            original if change == "valid" else substitute,
            correlation_id=cmd.correlation_id,
        )
        s.add(prepared)
    async with schema_db.transaction() as s:
        neutral = await CommandRepository().lock_by_identity(s, scope, cmd.id)
        if change == "valid":
            assert (
                await AttemptEventRepository().lock_attempt(s, neutral, prepared.id)
            ).identity == prepared.id
        else:
            with pytest.raises(RepositoryInvariantError):
                await AttemptEventRepository().lock_attempt(s, neutral, prepared.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "valid",
        "sequence",
        "outcome",
        "observed",
        "ingester",
        "reconciliation",
        "leader",
        "recovery",
    ],
)
async def test_neutral_dispatch_started_history(schema_db, change):
    from hmt_crypto.persistence.execution_evidence import encode_evidence

    scope, _, _ = await seed_scope(schema_db)
    prepared = await allocate(schema_db, scope, (await claimed(schema_db, scope)).claim)
    previous = await append(
        schema_db, scope, prepared.claim.command_id, prepared.attempt.attempt_id, proposal()
    )
    source = previous.event.model_dump(exclude={"scope", "event_id"})
    source.update(id=previous.event.event_id, **scope.model_dump())
    from hmt_crypto.persistence.risk_rate_consumption_evidence import derive_dispatch_event_key

    source["event_kind"] = "DISPATCH_STARTED"
    source["event_key"] = derive_dispatch_event_key(prepared.attempt.attempt_id)
    source["evidence_payload"], source["evidence_sha256"] = encode_evidence(
        "event",
        "DISPATCH_STARTED",
        1,
        {"outcome": "ACCEPTED" if change == "outcome" else "UNKNOWN"},
    )
    if change == "observed":
        source["observed_at"] = source["recorded_at"]
    if change == "reconciliation":
        source["reconciliation_ref"] = uuid4()
    for kind, ref, gen in [
        ("ingester", "ingester_id", "ingester_generation"),
        ("leader", "ingester_leader_ref", "ingester_leader_generation"),
        ("recovery", "ingester_recovery_ref", "ingester_recovery_generation"),
    ]:
        if change == kind:
            source.update({ref: uuid4(), gen: 1})
    async with schema_db.transaction() as s:
        if change == "sequence":
            source.update(sequence=2, id=uuid4(), event_key="second")
            s.add(OrderAttemptEvent(**source))
        else:
            await s.execute(
                update(OrderAttemptEvent)
                .where(OrderAttemptEvent.id == previous.event.event_id)
                .values(**source)
            )
    if change == "valid":
        assert (
            await append(
                schema_db,
                scope,
                prepared.claim.command_id,
                prepared.attempt.attempt_id,
                proposal("later"),
            )
        ).event.sequence == 2
    else:
        with pytest.raises(RepositoryInvariantError):
            await append(
                schema_db,
                scope,
                prepared.claim.command_id,
                prepared.attempt.attempt_id,
                proposal("later"),
            )


@pytest.mark.asyncio
async def test_live_fk_parent_locks_before_sample(schema_db):
    from schema_test_support import credential
    from sqlalchemy.dialects import postgresql

    from hmt_crypto.persistence.errors import PersistenceValueError
    from hmt_crypto.persistence.models import Account

    scope, lease_id, recovery_id = await seed_scope(schema_db, mode="LIVE")
    owner_id = uuid4()
    async with schema_db.transaction() as s:
        owner = await s.get(Account, scope.account_id)
        s.add(credential(owner))
        await s.flush()
        held = await LeadershipRepository().acquire(
            s,
            scope,
            lease_id=lease_id,
            recovery_incarnation_id=recovery_id,
            owner_id=owner_id,
            duration=timedelta(minutes=5),
        )
        await enqueue(s, scope)
    async with schema_db.transaction() as s:
        initial = await CommandRepository().claim_next(
            s, scope, owner_id=owner_id, duration=timedelta(minutes=2), leadership=fence(held)
        )
    for version in (2, 1):
        async with schema_db.transaction() as s:
            lease = await LeadershipRepository().lock_scope(
                s, scope, lease_id=lease_id, recovery_incarnation_id=recovery_id
            )
            current_lease = LeadershipRepository().refine_current(lease, fence(held))
            cmd = await CommandRepository().lock_by_identity(
                s, scope, initial.claim.command_id, last_lock=lease
            )
            current = CommandRepository().refine_current_claim(
                cmd, initial.claim, leadership=current_lease
            )
            repo = AttemptEventRepository()
            order = await repo.lock_order_parent(s, cmd, order_id=None)
            seen = []
            event.listen(
                s.sync_session,
                "do_orm_execute",
                lambda ctx, seen=seen: seen.append(
                    str(ctx.statement.compile(dialect=postgresql.dialect()))
                ),
            )
            with pytest.raises(PersistenceValueError):
                await repo.lock_attempt_set(s, current, order, last_lock=order)
            assert not seen
            aset = await repo.lock_attempt_set(
                s, current, order, last_lock=order, credential_version=1
            )
            parent_sql = [stmt for stmt in seen if "FOR KEY SHARE" in stmt]
            assert (
                len(parent_sql) == 2
                and "accounts" in parent_sql[0]
                and "credential_versions" in parent_sql[1]
            )
            sample = await RepositoryClock().sample_after_locks(s, last_lock=aset)

            allocation = repo.allocate_attempt_at(
                s,
                current,
                order,
                aset,
                leadership=current_lease,
                sampled_at=sample,
                credential_version=version,
            )

            if version == 2:
                with pytest.raises(PersistenceValueError):
                    await allocation
            else:
                result = await allocation
                assert result.attempt.backend == "VENUE" and result.attempt.credential_version == 1
                assert result.attempt.original_claim_owner == owner_id
                assert result.attempt.original_leader_ref == lease_id
                assert result.attempt.original_recovery_ref == recovery_id


@pytest.mark.asyncio
async def test_simulator_and_fresh_event_take_account_key_share_before_time(schema_db):
    from sqlalchemy.dialects import postgresql

    scope, _, _ = await seed_scope(schema_db)
    claimed_row = await claimed(schema_db, scope)
    seen = []

    def track(s):
        event.listen(
            s.sync_session,
            "do_orm_execute",
            lambda ctx, seen=seen: seen.append(
                str(ctx.statement.compile(dialect=postgresql.dialect()))
            ),
        )

    async with schema_db.transaction() as s:
        cmd = await CommandRepository().lock_by_identity(s, scope, claimed_row.claim.command_id)
        order = await AttemptEventRepository().lock_order_parent(s, cmd, order_id=None)
        current = CommandRepository().refine_current_claim(cmd, claimed_row.claim, leadership=None)
        track(s)
        repo = AttemptEventRepository()
        aset = await repo.lock_attempt_set(s, current, order, last_lock=order)
        assert sum("FOR KEY SHARE" in stmt and "accounts" in stmt for stmt in seen) == 1
        sample = await RepositoryClock().sample_after_locks(s, last_lock=aset)
        prepared = await repo.allocate_attempt_at(
            s, current, order, aset, leadership=None, sampled_at=sample, credential_version=None
        )
    for replay in (False, True):
        async with schema_db.transaction() as s:
            cmd = await CommandRepository().lock_by_identity(s, scope, claimed_row.claim.command_id)
            attempt = await repo.lock_attempt(s, cmd, prepared.attempt.attempt_id)
            seen.clear()
            track(s)
            stream = await repo.lock_event_stream(s, cmd, attempt, proposal=proposal())
            assert sum("FOR KEY SHARE" in stmt and "accounts" in stmt for stmt in seen) == (
                0 if replay else 1
            )
            if not replay:
                sample = await RepositoryClock().sample_after_locks(s, last_lock=stream)
                await repo.append_event_at(s, cmd, attempt, stream, sampled_at=sample)


@pytest.mark.asyncio
async def test_attempt_uuid_order_and_namespace_before_sql(schema_db):
    from hmt_crypto.persistence.repository_locks import _before_lock

    scope, _, _ = await seed_scope(schema_db)
    initial = await claimed(schema_db, scope)
    a = await allocate(schema_db, scope, initial.claim)
    b = await allocate(schema_db, scope, a.claim)
    lo, hi = sorted([a.attempt.attempt_id, b.attempt.attempt_id])
    repo = AttemptEventRepository()
    async with schema_db.transaction() as s:
        cmd = await CommandRepository().lock_by_identity(s, scope, initial.claim.command_id)
        await repo.lock_attempt(s, cmd, lo)
        await repo.lock_attempt(s, cmd, hi)
        seen = []
        event.listen(s.sync_session, "do_orm_execute", lambda *args: seen.append(1))
        with pytest.raises(RepositoryStateError):
            await repo.lock_attempt(s, cmd, lo)
        with pytest.raises(RepositoryStateError):
            _before_lock(s, scope, 60, "0_account", scope.account_id)
        assert not seen


@pytest.mark.asyncio
async def test_public_lock_entry_accepts_keyword_session(schema_db):
    scope, _, _ = await seed_scope(schema_db)
    initial = await claimed(schema_db, scope)
    async with schema_db.transaction() as s:
        assert (
            await CommandRepository().lock_by_identity(
                session=s, scope=scope, command_id=initial.claim.command_id
            )
        ).identity == initial.claim.command_id


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["refinement", "mutation"])
async def test_caught_post_lock_validation_failure_poisons_outer_authority(schema_db, stage):
    from hmt_crypto.persistence.repository_errors import FenceRejectedError
    from hmt_crypto.persistence.repository_locks import _sample

    scope, _, _ = await seed_scope(schema_db)
    initial = await claimed(schema_db, scope)
    async with schema_db.transaction() as s:
        repo = CommandRepository()
        cmd = await repo.lock_by_identity(s, scope, initial.claim.command_id)
        with pytest.raises(FenceRejectedError):
            if stage == "refinement":
                repo.refine_current_claim(
                    cmd, initial.claim.model_copy(update={"version": 1}), leadership=None
                )
            else:
                current = repo.refine_current_claim(cmd, initial.claim, leadership=None)
                final = await repo.lock_unreserved_terminalization(s, current)
                expired = _sample(s, final, initial.claim.expires_at, False)
                await repo.terminalize_unreserved_at(
                    s, final, leadership=None, sampled_at=expired, terminal_status="FAILED"
                )
        with pytest.raises(RepositoryStateError):
            await enqueue(s, scope, request="caught")
    async with schema_db.transaction() as s:
        assert (
            await repo.lock_by_identity(s, scope, initial.claim.command_id)
        ).identity == initial.claim.command_id


@pytest.mark.asyncio
async def test_detached_lease_cannot_publish_a_renewal(schema_db, monkeypatch):
    scope, lease_id, recovery_id = await seed_scope(schema_db)
    leases = LeadershipRepository()
    async with schema_db.transaction() as s:
        held = await leases.acquire(
            s,
            scope,
            lease_id=lease_id,
            recovery_incarnation_id=recovery_id,
            owner_id=uuid4(),
            duration=timedelta(minutes=1),
        )
    sample = RepositoryClock.sample_after_locks

    async def expunge_after_sample(self, session, *, last_lock):
        result = await sample(self, session, last_lock=last_lock)
        session.expunge_all()
        return result

    monkeypatch.setattr(RepositoryClock, "sample_after_locks", expunge_after_sample)
    async with schema_db.transaction() as s:
        with pytest.raises(RepositoryStateError):
            await leases.renew(s, scope, fence(held), duration=timedelta(minutes=3))
    async with schema_db.transaction() as s:
        lease = await s.get(LeadershipLease, lease_id)
        assert lease.version == held.version and lease.expires_at == held.expires_at


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["session", "connection"])
@pytest.mark.parametrize("finish", ["commit", "rollback"])
@pytest.mark.parametrize(
    "stage",
    [
        "command",
        "refinement",
        "terminal",
        "clock",
        "consume",
        "discard",
        "event_key",
        "append",
        "enqueue",
        "lease_refinement",
    ],
)
async def test_physical_savepoint_rejects_before_authority_and_preserves_outer(
    schema_db, monkeypatch, owner, finish, stage
):
    scope, lease_id, recovery_id = await seed_scope(schema_db)
    original = await claimed(schema_db, scope)
    commands = CommandRepository()
    attempts = AttemptEventRepository()
    clock = RepositoryClock()
    leases = LeadershipRepository()
    if stage in ("event_key", "append"):
        prepared = await allocate(schema_db, scope, original.claim)
    if stage == "lease_refinement":
        async with schema_db.transaction() as setup:
            held = await leases.acquire(
                setup,
                scope,
                lease_id=lease_id,
                recovery_incarnation_id=recovery_id,
                owner_id=uuid4(),
                duration=timedelta(minutes=1),
            )
    async with schema_db.transaction() as session:
        conn = await session.connection()
        physical_root = conn.get_transaction()
        session_root = session.get_transaction()
        if stage == "lease_refinement":
            lease = await leases.lock_scope(
                session, scope, lease_id=lease_id, recovery_incarnation_id=recovery_id
            )
        elif stage not in ("command", "enqueue"):
            cmd = await commands.lock_by_identity(session, scope, original.claim.command_id)
            if stage in ("terminal", "clock", "consume", "discard"):
                current = commands.refine_current_claim(cmd, original.claim, leadership=None)
                if stage != "terminal":
                    terminal = await commands.lock_unreserved_terminalization(session, current)
                    if stage in ("consume", "discard"):
                        sample = await clock.sample_after_locks(session, last_lock=terminal)
            if stage in ("event_key", "append"):
                attempt = await attempts.lock_attempt(session, cmd, prepared.attempt.attempt_id)
                if stage == "append":
                    stream = await attempts.lock_event_stream(
                        session, cmd, attempt, proposal=proposal()
                    )
                    sample = await clock.sample_after_locks(session, last_lock=stream)

        async def enter():
            if stage == "command":
                return await commands.lock_by_identity(session, scope, original.claim.command_id)
            if stage == "refinement":
                return commands.refine_current_claim(cmd, original.claim, leadership=None)
            if stage == "lease_refinement":
                return leases.refine_current(lease, fence(held))
            if stage == "terminal":
                return await commands.lock_unreserved_terminalization(session, current)
            if stage == "clock":
                return await clock.sample_after_locks(session, last_lock=terminal)
            if stage == "consume":
                return await commands.terminalize_unreserved_at(
                    session, terminal, leadership=None, sampled_at=sample, terminal_status="FAILED"
                )
            if stage == "discard":
                return clock.discard_sample(session, last_lock=terminal, sampled_at=sample)
            if stage == "event_key":
                return await attempts.lock_event_stream(session, cmd, attempt, proposal=proposal())
            if stage == "append":
                return await attempts.append_event_at(
                    session, cmd, attempt, stream, sampled_at=sample
                )
            return await enqueue(session, scope, request="savepoint-request")

        def authority():
            state = session.info.get("_repository_locks", {})
            return (
                state.get("progress", 0),
                set(state.get("handles", {})),
                set(state.get("samples", {})),
                state.get("last"),
                state.get("poisoned", False),
                dict(session.info.get("_repository_event_key", {})),
            )

        before = authority()
        assert not conn.in_nested_transaction() and not session.in_nested_transaction()
        sp = await (session if owner == "session" else conn).begin_nested()
        # A Session savepoint is materialized on its bound connection at first use.
        if owner == "session":
            await session.connection()
        assert conn.in_nested_transaction() and session.in_nested_transaction() == (
            owner == "session"
        )
        sql = []

        def record(*args):
            sql.append(args[2])

        def forbidden_uuid():
            pytest.fail("Repository generated a UUID inside a savepoint")

        event.listen(conn.sync_connection, "before_cursor_execute", record)
        try:
            with monkeypatch.context() as patch:
                patch.setattr("hmt_crypto.persistence.command_repository.uuid4", forbidden_uuid)
                patch.setattr(
                    "hmt_crypto.persistence.attempt_event_repository.uuid4", forbidden_uuid
                )
                with pytest.raises(RepositoryStateError):
                    await enter()
            assert not sql and authority() == before
            assert not session.new and not session.dirty and not session.deleted
        finally:
            event.remove(conn.sync_connection, "before_cursor_execute", record)
            await getattr(sp, finish)()
        assert conn.get_transaction() is physical_root and session.get_transaction() is session_root
        assert not conn.in_nested_transaction() and not session.in_nested_transaction()
        result = await enter()
        if stage == "clock":
            clock.discard_sample(session, last_lock=terminal, sampled_at=result)
        if stage == "consume":
            assert result.status == "FAILED" and result.version == 3
        if stage == "event_key":
            sampled = await clock.sample_after_locks(session, last_lock=result)
            assert (
                await attempts.append_event_at(session, cmd, attempt, result, sampled_at=sampled)
            ).event.sequence == 1
        if stage == "append":
            assert result.event.sequence == 1


@pytest.mark.asyncio
async def test_physical_savepoint_admission_preserves_cancellation(schema_db, monkeypatch):
    import asyncio

    scope, _, _ = await seed_scope(schema_db)
    original = await claimed(schema_db, scope)
    async with schema_db.transaction() as session:
        seen = []
        event.listen(session.sync_session, "do_orm_execute", lambda *args: seen.append(1))

        async def cancelled(*args, **kwargs):
            raise asyncio.CancelledError()

        with monkeypatch.context() as patch:
            patch.setattr(session, "connection", cancelled)
            with pytest.raises(asyncio.CancelledError):
                await CommandRepository().lock_by_identity(
                    session, scope, original.claim.command_id
                )
        assert not seen and not session.info.get("_repository_event_key")
        assert not session.info.get("_repository_locks", {}).get("handles")
        assert (
            await CommandRepository().lock_by_identity(session, scope, original.claim.command_id)
        ).identity == original.claim.command_id
