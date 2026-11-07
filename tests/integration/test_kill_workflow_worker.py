"""SQLite acceptance for the crash-resumable PAPER global-kill worker."""

from __future__ import annotations

import asyncio
import gc
import json
import weakref
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import event, func, insert, select, update

from hmt_crypto.config import Settings
from hmt_crypto.oms.kill_switch import (
    KillAction,
    KillEvent,
    apply_kill_event,
    record_kill_event,
)
from hmt_crypto.oms.kill_worker import (
    KillActionInvocation,
    KillActionResult,
    KillWorkerError,
    KillWorkflowWorker,
)
from hmt_crypto.oms.paper_kill_backend import DurablePaperKillActionBackend
from hmt_crypto.persistence.command_models import CommandOperation
from hmt_crypto.persistence.command_repository import CommandRepository
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.errors import PersistenceError, PersistenceValueError
from hmt_crypto.persistence.identity_models import Account
from hmt_crypto.persistence.kill_workflow_models import KillWorkflow, KillWorkflowEvent
from hmt_crypto.persistence.kill_workflow_repository import (
    KillWorkflowRepository,
    KillWorkflowRepositoryError,
)
from hmt_crypto.persistence.models import metadata
from hmt_crypto.persistence.paper_execution_models import PaperExecutionEvent
from hmt_crypto.persistence.repository_contracts import AccountScope, CommandCorrelation
from hmt_crypto.worker.paper_repository import JournalEvent, PaperJournalRepository

_SEED_TIME = datetime.now(UTC) - timedelta(minutes=2)
_OWNER = UUID("b0000000-0000-4000-8000-000000000002")

_FLATTEN_PLAN = {
    KillAction.PERSIST_RISK_HALT: KillEvent.RISK_HALT_PERSISTED,
    KillAction.CANCEL_RISK_INCREASING_ORDERS: KillEvent.RISK_INCREASING_CANCEL_COMPLETED,
    KillAction.FLATTEN_POSITIONS_REDUCE_ONLY: KillEvent.FLATTEN_COMPLETED,
    KillAction.CANCEL_REMAINING_OPEN_ORDERS: KillEvent.REMAINDER_CANCEL_COMPLETED,
    KillAction.RECONCILE_EXCHANGE_TRUTH: KillEvent.RECONCILED_FLAT,
    KillAction.FINAL_RECONCILIATION: KillEvent.FINAL_RECONCILED_SAFE,
}

_CANCEL_PLAN = {
    KillAction.PERSIST_RISK_HALT: KillEvent.RISK_HALT_PERSISTED,
    KillAction.CANCEL_RISK_INCREASING_ORDERS: KillEvent.RISK_INCREASING_CANCEL_COMPLETED,
    KillAction.CANCEL_REMAINING_OPEN_ORDERS: KillEvent.REMAINDER_CANCEL_COMPLETED,
    KillAction.RECONCILE_EXCHANGE_TRUTH: KillEvent.RECONCILED_RETAINED,
    KillAction.REESTABLISH_PROTECTION: KillEvent.PROTECTION_SUBMITTED,
    KillAction.VERIFY_PROTECTION: KillEvent.PROTECTION_VERIFIED,
    KillAction.FINAL_RECONCILIATION: KillEvent.FINAL_RECONCILED_SAFE,
}


async def _database(
    tmp_path: Path,
    *,
    flatten: bool,
    available_at: datetime = _SEED_TIME,
) -> tuple[Database, AccountScope, UUID]:
    settings = Settings(
        DATABASE_URL=SecretStr(f"sqlite+aiosqlite:///{tmp_path / f'kill-{uuid4()}.db'}"),
        DATABASE_SINGLE_PROCESS=True,
    )
    database = Database(settings)
    async with database.engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    command_id = uuid4()
    scope = AccountScope(
        account_id=uuid4(),
        region="india",
        environment="testnet",
        mode="PAPER",
    )
    async with database.transaction() as session:
        session.add(
            Account(
                id=scope.account_id,
                region=scope.region,
                environment=scope.environment,
                mode=scope.mode,
                profile_name="paper-kill",
                external_user_id=None,
                external_account_id=None,
                identity_verified_at=None,
                current_credential_version=None,
                version=1,
                created_at=_SEED_TIME,
                updated_at=_SEED_TIME,
            )
        )
    async with database.transaction() as session:
        session.add(
            CommandOperation.from_evidence(
                evidence={"flatten": flatten, "preview_id": uuid4()},
                id=command_id,
                **scope.model_dump(),
                request_id=str(command_id),
                operation_kind="GLOBAL_KILL",
                correlation_id=command_id,
                status="QUEUED",
                available_at=available_at,
                claim_owner=None,
                claim_generation=0,
                claim_expires_at=None,
                attempt_count=0,
                leader_ref=None,
                leader_generation=None,
                recovery_ref=None,
                recovery_generation=None,
                version=1,
                created_at=_SEED_TIME,
                updated_at=_SEED_TIME,
                completed_at=None,
            )
        )
    return database, scope, command_id


async def _expire_claim(database: Database, command_id: UUID) -> None:
    observed = datetime.now(UTC)
    async with database.transaction() as session:
        await session.execute(
            update(CommandOperation)
            .where(CommandOperation.id == command_id)
            .values(
                updated_at=observed - timedelta(seconds=2),
                claim_expires_at=observed - timedelta(seconds=1),
            )
        )


@pytest.mark.asyncio
async def test_flatten_restarts_each_step_and_completes_only_after_final_reconcile(
    tmp_path: Path,
) -> None:
    database, scope, command_id = await _database(tmp_path, flatten=True)
    backend = DurablePaperKillActionBackend(database, scope)
    authority = backend.action_authority()
    try:
        results = []
        for _ in range(6):
            worker = KillWorkflowWorker(
                database,
                scope,
                owner_id=_OWNER,
                authority=authority,
            )
            results.append(await worker.run_once())

        assert [item.action for item in results] == list(_FLATTEN_PLAN)
        assert results[-1].disposition == "COMPLETED"
        async with database.transaction() as session:
            command = await session.get(CommandOperation, command_id)
            workflow = await session.get(KillWorkflow, command_id)
            events = list(
                (
                    await session.execute(
                        select(KillWorkflowEvent)
                        .where(KillWorkflowEvent.workflow_id == command_id)
                        .order_by(KillWorkflowEvent.sequence)
                    )
                ).scalars()
            )
            assert command is not None and command.status == "COMPLETED"
            assert command.claim_owner is None and command.completed_at is not None
            assert workflow is not None and workflow.state == "COMPLETED"
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PaperExecutionEvent)
                    .where(PaperExecutionEvent.event_kind == "KILL_ACTION")
                )
                == 6
            )
            assert [item.event_kind for item in events] == [
                event.value for event in _FLATTEN_PLAN.values()
            ]
    finally:
        await database.aclose()


@pytest.mark.asyncio
async def test_unresolved_action_persists_incomplete_and_fails_command(tmp_path: Path) -> None:
    database, scope, command_id = await _database(tmp_path, flatten=False)
    backend = DurablePaperKillActionBackend(database, scope)
    worker = KillWorkflowWorker(
        database,
        scope,
        owner_id=_OWNER,
        authority=backend.action_authority(),
    )
    try:
        for _ in range(3):
            assert (await worker.run_once()).disposition == "ADVANCED"
        repository = PaperJournalRepository()
        injected_at = datetime.now(UTC)
        async with database.transaction() as session:
            await repository.ensure_intent(
                session,
                scope,
                request_id="concurrent-risk-order",
                intent_payload={"paper_intent_schema_version": 1},
                occurred_at=injected_at,
            )
        async with database.transaction() as session:
            claim = await repository.claim(
                session,
                scope,
                "concurrent-risk-order",
                owner_id=uuid4(),
                now=injected_at + timedelta(microseconds=1),
                duration=timedelta(minutes=1),
            )
            assert claim is not None
            await repository.append(
                session,
                claim,
                events=(
                    JournalEvent(
                        "COMMAND",
                        "injected-order",
                        injected_at,
                        {
                            "order_id": "injected-order",
                            "operation": "PLACE_ORDER",
                            "product_id": 1101,
                            "side": "BUY",
                            "contracts": 1,
                            "reduce_only": False,
                        },
                    ),
                ),
                status="COMPLETE",
                recorded_at=injected_at + timedelta(microseconds=1),
            )
        terminal = await worker.run_once()
        assert terminal.disposition == "INCOMPLETE"
        async with database.transaction() as session:
            command = await session.get(CommandOperation, command_id)
            workflow = await session.get(KillWorkflow, command_id)
            assert command is not None and command.status == "FAILED"
            assert command.completed_at is not None
            assert workflow is not None and workflow.state == "INCOMPLETE"
    finally:
        await database.aclose()


@pytest.mark.asyncio
async def test_exact_enqueue_replay_survives_global_kill_requeue(tmp_path: Path) -> None:
    database, scope, command_id = await _database(tmp_path, flatten=False)
    backend = DurablePaperKillActionBackend(database, scope)
    worker = KillWorkflowWorker(
        database,
        scope,
        owner_id=_OWNER,
        authority=backend.action_authority(),
    )
    try:
        assert (await worker.run_once()).disposition == "ADVANCED"
        async with database.transaction() as session:
            command = await session.get(CommandOperation, command_id)
            assert command is not None
            payload = json.loads(command.evidence_payload)
            payload["preview_id"] = UUID(payload["preview_id"])
            replay = await CommandRepository().enqueue_or_replay(
                session,
                scope,
                request_id=str(command_id),
                operation_kind="GLOBAL_KILL",
                correlation=CommandCorrelation(correlation_id=command_id),
                evidence=payload,
            )
        assert replay.disposition.value == "EXISTING"
        assert replay.command.command_id == command_id
        assert replay.command.status == "QUEUED"
    finally:
        await database.aclose()


@pytest.mark.asyncio
async def test_same_owner_competing_worker_cannot_repeat_a_live_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, scope, _ = await _database(tmp_path, flatten=False)
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    original = DurablePaperKillActionBackend.execute

    async def blocked_execute(
        self: DurablePaperKillActionBackend,
        invocation: KillActionInvocation,
    ) -> KillActionResult:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return await original(self, invocation)

    monkeypatch.setattr(DurablePaperKillActionBackend, "execute", blocked_execute)
    backend = DurablePaperKillActionBackend(database, scope)
    authority = backend.action_authority()
    first = KillWorkflowWorker(database, scope, owner_id=_OWNER, authority=authority)
    second = KillWorkflowWorker(database, scope, owner_id=_OWNER, authority=authority)
    try:
        pending = asyncio.create_task(first.run_once())
        await entered.wait()
        assert (await second.run_once()).disposition == "IDLE"
        assert calls == 1
        release.set()
        assert (await pending).disposition == "ADVANCED"
    finally:
        release.set()
        await database.aclose()


@pytest.mark.asyncio
async def test_cancelled_worker_resumes_same_inflight_operation_without_reexecution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, scope, command_id = await _database(tmp_path, flatten=False)
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    original = DurablePaperKillActionBackend.execute

    async def blocked_execute(
        self: DurablePaperKillActionBackend,
        invocation: KillActionInvocation,
    ) -> KillActionResult:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return await original(self, invocation)

    monkeypatch.setattr(DurablePaperKillActionBackend, "execute", blocked_execute)
    backend = DurablePaperKillActionBackend(database, scope)
    authority = backend.action_authority()
    authority_reference = weakref.ref(authority)
    first = KillWorkflowWorker(
        database,
        scope,
        owner_id=_OWNER,
        authority=authority,
        claim_duration=timedelta(seconds=5),
        action_timeout=timedelta(seconds=1),
    )
    try:
        pending = asyncio.create_task(first.run_once())
        await entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        async with database.transaction() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(KillWorkflowEvent)
                .where(KillWorkflowEvent.workflow_id == command_id)
            )
            assert count == 0

        del pending, first, authority, backend
        gc.collect()
        replacement_backend = DurablePaperKillActionBackend(database, scope)
        replacement_authority = replacement_backend.action_authority()
        assert authority_reference() is replacement_authority
        await _expire_claim(database, command_id)
        release.set()
        resumed = KillWorkflowWorker(
            database,
            scope,
            owner_id=uuid4(),
            authority=replacement_authority,
            claim_duration=timedelta(seconds=5),
            action_timeout=timedelta(seconds=1),
        )
        assert (await resumed.run_once()).disposition == "ADVANCED"
        assert calls == 1
    finally:
        release.set()
        await database.aclose()


@pytest.mark.asyncio
async def test_long_cancellation_absorbing_backend_is_bounded_and_not_blindly_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, scope, command_id = await _database(tmp_path, flatten=False)
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    original = DurablePaperKillActionBackend.execute

    async def long_execute(
        self: DurablePaperKillActionBackend,
        invocation: KillActionInvocation,
    ) -> KillActionResult:
        nonlocal calls
        calls += 1
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return await original(self, invocation)

    monkeypatch.setattr(DurablePaperKillActionBackend, "execute", long_execute)
    backend = DurablePaperKillActionBackend(database, scope)
    authority = backend.action_authority()
    worker = KillWorkflowWorker(
        database,
        scope,
        owner_id=_OWNER,
        authority=authority,
        claim_duration=timedelta(seconds=5),
        action_timeout=timedelta(milliseconds=20),
    )
    try:
        started = asyncio.get_running_loop().time()
        with pytest.raises(KillWorkerError):
            await worker.run_once()
        elapsed = asyncio.get_running_loop().time() - started
        assert elapsed < 0.5
        await entered.wait()
        assert (await worker.run_once()).disposition == "IDLE"
        assert calls == 1

        await _expire_claim(database, command_id)
        release.set()
        resumed = KillWorkflowWorker(
            database,
            scope,
            owner_id=uuid4(),
            authority=authority,
            claim_duration=timedelta(seconds=5),
            action_timeout=timedelta(seconds=1),
        )
        assert (await resumed.run_once()).disposition == "ADVANCED"
        assert calls == 1
    finally:
        release.set()
        await database.aclose()


@pytest.mark.asyncio
async def test_action_failure_is_not_retried_inside_one_worker_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, scope, _ = await _database(tmp_path, flatten=False)
    calls: list[KillActionInvocation] = []

    async def failing_execute(
        self: DurablePaperKillActionBackend,
        invocation: KillActionInvocation,
    ) -> KillActionResult:
        del self
        calls.append(invocation)
        raise OSError("synthetic backend loss")

    monkeypatch.setattr(DurablePaperKillActionBackend, "execute", failing_execute)
    backend = DurablePaperKillActionBackend(database, scope)
    worker = KillWorkflowWorker(
        database,
        scope,
        owner_id=_OWNER,
        authority=backend.action_authority(),
    )
    try:
        with pytest.raises(KillWorkerError):
            await worker.run_once()
        assert len(calls) == 1
    finally:
        await database.aclose()


@pytest.mark.asyncio
async def test_post_commit_backend_loss_replays_durable_receipt_without_duplicate_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, scope, command_id = await _database(tmp_path, flatten=False)
    calls = 0
    original = DurablePaperKillActionBackend.execute

    async def lose_first_response(
        self: DurablePaperKillActionBackend,
        invocation: KillActionInvocation,
    ) -> KillActionResult:
        nonlocal calls
        calls += 1
        result = await original(self, invocation)
        if calls == 1:
            raise OSError("synthetic response loss after commit")
        return result

    monkeypatch.setattr(DurablePaperKillActionBackend, "execute", lose_first_response)
    backend = DurablePaperKillActionBackend(database, scope)
    authority = backend.action_authority()
    first = KillWorkflowWorker(
        database,
        scope,
        owner_id=_OWNER,
        authority=authority,
        claim_duration=timedelta(seconds=5),
        action_timeout=timedelta(seconds=1),
    )
    try:
        with pytest.raises(KillWorkerError):
            await first.run_once()
        await _expire_claim(database, command_id)
        resumed = KillWorkflowWorker(
            database,
            scope,
            owner_id=uuid4(),
            authority=DurablePaperKillActionBackend(database, scope).action_authority(),
            claim_duration=timedelta(seconds=5),
            action_timeout=timedelta(seconds=1),
        )
        assert (await resumed.run_once()).disposition == "ADVANCED"
        assert calls == 2
        async with database.transaction() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PaperExecutionEvent)
                    .where(PaperExecutionEvent.event_kind == "KILL_ACTION")
                )
                == 1
            )
            command = await session.get(CommandOperation, command_id)
            assert command is not None and command.status == "QUEUED"
    finally:
        await database.aclose()


@pytest.mark.asyncio
async def test_persisted_cas_replay_does_not_duplicate_or_strand_claim(tmp_path: Path) -> None:
    database, scope, command_id = await _database(tmp_path, flatten=False)
    repository = KillWorkflowRepository()
    try:
        async with database.transaction() as session:
            claim = await repository.claim_active(
                session,
                scope,
                owner_id=_OWNER,
                duration=timedelta(seconds=30),
            )
            assert claim is not None
            request = await repository.start_or_load(session, scope, claim)
        evidence = record_kill_event(
            request_id=request.request_id,
            event_id=uuid4(),
            expected_transition_count=request.transition_count,
            event=KillEvent.RISK_HALT_PERSISTED,
        )
        decision = apply_kill_event(request, evidence)
        async with database.transaction() as session:
            first = await repository.apply_and_finalize(session, scope, claim, decision)
        assert first is not None and first.transition_count == 1

        async with database.transaction() as session:
            renewed = await repository.claim_active(
                session,
                scope,
                owner_id=_OWNER,
                duration=timedelta(seconds=30),
            )
            assert renewed is not None
            replay = await repository.apply_and_finalize(session, scope, renewed, decision)
            assert replay is None
        async with database.transaction() as session:
            command = await session.get(CommandOperation, command_id)
            count = await session.scalar(
                select(func.count())
                .select_from(KillWorkflowEvent)
                .where(KillWorkflowEvent.workflow_id == command_id)
            )
            assert command is not None and command.status == "QUEUED"
            assert command.claim_owner is None and command.claim_expires_at is None
            assert count == 1
    finally:
        await database.aclose()


@pytest.mark.asyncio
async def test_apply_resamples_database_time_after_workflow_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, scope, command_id = await _database(tmp_path, flatten=False)
    repository = KillWorkflowRepository()
    try:
        async with database.transaction() as session:
            claim = await repository.claim_active(
                session,
                scope,
                owner_id=_OWNER,
                duration=timedelta(seconds=30),
            )
            assert claim is not None
            request = await repository.start_or_load(session, scope, claim)
        evidence = record_kill_event(
            request_id=request.request_id,
            event_id=uuid4(),
            expected_transition_count=request.transition_count,
            event=KillEvent.RISK_HALT_PERSISTED,
        )
        decision = apply_kill_event(request, evidence)
        workflow_lock_seen = False

        def observe_statement(*args: object) -> None:
            nonlocal workflow_lock_seen
            statement = args[2]
            if type(statement) is str and "kill_workflows" in statement:
                workflow_lock_seen = True

        async def delayed_database_now(session: object) -> datetime:
            del session
            offset = timedelta(seconds=1 if workflow_lock_seen else -1)
            return claim.expires_at + offset

        event.listen(database.engine.sync_engine, "before_cursor_execute", observe_statement)
        monkeypatch.setattr(
            "hmt_crypto.persistence.kill_workflow_repository._database_now",
            delayed_database_now,
        )
        try:
            with pytest.raises(KillWorkflowRepositoryError):
                async with database.transaction() as session:
                    await repository.apply_and_finalize(session, scope, claim, decision)
        finally:
            event.remove(database.engine.sync_engine, "before_cursor_execute", observe_statement)
        assert workflow_lock_seen
        async with database.transaction() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(KillWorkflowEvent)
                .where(KillWorkflowEvent.workflow_id == command_id)
            )
            assert count == 0
    finally:
        await database.aclose()


@pytest.mark.asyncio
async def test_start_resamples_database_time_after_workflow_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, scope, _ = await _database(tmp_path, flatten=False)
    repository = KillWorkflowRepository()
    try:
        async with database.transaction() as session:
            claim = await repository.claim_active(
                session,
                scope,
                owner_id=_OWNER,
                duration=timedelta(seconds=30),
            )
            assert claim is not None
        workflow_lock_seen = False

        def observe_statement(*args: object) -> None:
            nonlocal workflow_lock_seen
            statement = args[2]
            if type(statement) is str and "kill_workflows" in statement:
                workflow_lock_seen = True

        async def delayed_database_now(session: object) -> datetime:
            del session
            offset = timedelta(seconds=1 if workflow_lock_seen else -1)
            return claim.expires_at + offset

        event.listen(database.engine.sync_engine, "before_cursor_execute", observe_statement)
        monkeypatch.setattr(
            "hmt_crypto.persistence.kill_workflow_repository._database_now",
            delayed_database_now,
        )
        try:
            with pytest.raises(KillWorkflowRepositoryError):
                async with database.transaction() as session:
                    await repository.start_or_load(session, scope, claim)
        finally:
            event.remove(database.engine.sync_engine, "before_cursor_execute", observe_statement)
        assert workflow_lock_seen
        async with database.transaction() as session:
            assert await repository.load(session, scope, claim.command_id) is None
    finally:
        await database.aclose()


@pytest.mark.asyncio
async def test_database_clock_controls_availability_and_lease(tmp_path: Path) -> None:
    future = datetime.now(UTC) + timedelta(days=365)
    database, scope, command_id = await _database(
        tmp_path,
        flatten=False,
        available_at=future,
    )
    repository = KillWorkflowRepository()
    try:
        async with database.transaction() as session:
            assert (
                await repository.claim_active(
                    session,
                    scope,
                    owner_id=_OWNER,
                    duration=timedelta(seconds=5),
                )
                is None
            )
        async with database.transaction() as session:
            await session.execute(
                update(CommandOperation)
                .where(CommandOperation.id == command_id)
                .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        before = datetime.now(UTC)
        async with database.transaction() as session:
            claim = await repository.claim_active(
                session,
                scope,
                owner_id=_OWNER,
                duration=timedelta(seconds=5),
            )
        after = datetime.now(UTC)
        assert claim is not None
        assert before + timedelta(seconds=4) <= claim.expires_at
        assert claim.expires_at <= after + timedelta(seconds=6)
    finally:
        await database.aclose()


@pytest.mark.asyncio
async def test_forged_terminal_workflow_cannot_complete_active_command(tmp_path: Path) -> None:
    database, scope, command_id = await _database(tmp_path, flatten=False)
    backend = DurablePaperKillActionBackend(database, scope)
    async with database.transaction() as session:
        session.add(
            KillWorkflow(
                id=command_id,
                **scope.model_dump(),
                command_id=command_id,
                kill_mode="CANCEL_ONLY",
                state="COMPLETED",
                transition_count=5,
                version=6,
                created_at=_SEED_TIME,
                updated_at=_SEED_TIME,
                completed_at=_SEED_TIME,
            )
        )
    worker = KillWorkflowWorker(
        database,
        scope,
        owner_id=_OWNER,
        authority=backend.action_authority(),
    )
    try:
        with pytest.raises(KillWorkerError):
            await worker.run_once()
        async with database.transaction() as session:
            command = await session.get(CommandOperation, command_id)
            assert command is not None and command.status == "QUEUED"
            assert command.completed_at is None
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PaperExecutionEvent)
                    .where(PaperExecutionEvent.event_kind == "KILL_ACTION")
                )
                == 0
            )
    finally:
        await database.aclose()


@pytest.mark.asyncio
async def test_workflow_identity_must_equal_command_id_in_database(tmp_path: Path) -> None:
    database, scope, command_id = await _database(tmp_path, flatten=False)
    try:
        with pytest.raises(PersistenceError):
            async with database.transaction() as session:
                await session.execute(
                    insert(KillWorkflow).values(
                        id=uuid4(),
                        **scope.model_dump(),
                        command_id=command_id,
                        kill_mode="CANCEL_ONLY",
                        state="REQUESTED",
                        transition_count=0,
                        version=1,
                        created_at=_SEED_TIME,
                        updated_at=_SEED_TIME,
                        completed_at=None,
                    )
                )
    finally:
        await database.aclose()


@pytest.mark.asyncio
async def test_transition_events_are_orm_append_only(tmp_path: Path) -> None:
    database, scope, command_id = await _database(tmp_path, flatten=False)
    backend = DurablePaperKillActionBackend(database, scope)
    worker = KillWorkflowWorker(
        database,
        scope,
        owner_id=_OWNER,
        authority=backend.action_authority(),
    )
    try:
        assert (await worker.run_once()).disposition == "ADVANCED"
        with pytest.raises(PersistenceValueError):
            async with database.transaction() as session:
                event = (
                    await session.execute(
                        select(KillWorkflowEvent).where(KillWorkflowEvent.workflow_id == command_id)
                    )
                ).scalar_one()
                event.event_kind = KillEvent.FINAL_RECONCILED_SAFE.value
        with pytest.raises(PersistenceValueError):
            async with database.transaction() as session:
                event = (
                    await session.execute(
                        select(KillWorkflowEvent).where(KillWorkflowEvent.workflow_id == command_id)
                    )
                ).scalar_one()
                await session.delete(event)
    finally:
        await database.aclose()


def test_authority_is_exactly_scope_bound_and_live_is_forbidden(tmp_path: Path) -> None:
    settings = Settings(
        DATABASE_URL=SecretStr(f"sqlite+aiosqlite:///{tmp_path / 'unused.db'}"),
        DATABASE_SINGLE_PROCESS=True,
    )
    database = Database(settings)
    paper = AccountScope(
        account_id=uuid4(),
        region="india",
        environment="testnet",
        mode="PAPER",
    )
    other = paper.model_copy(update={"account_id": uuid4()})
    live = paper.model_copy(update={"mode": "LIVE"})
    authority = DurablePaperKillActionBackend(database, paper).action_authority()
    with pytest.raises(KillWorkerError):
        KillWorkflowWorker(database, other, owner_id=_OWNER, authority=authority)
    with pytest.raises(KillWorkerError):
        DurablePaperKillActionBackend(database, live)
