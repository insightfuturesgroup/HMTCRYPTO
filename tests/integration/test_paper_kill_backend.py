"""Durable PAPER/BACKTEST kill-action backend acceptance tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from hmt_crypto.config import Settings
from hmt_crypto.oms.kill_switch import KillAction, KillRequest
from hmt_crypto.oms.kill_worker import KillActionInvocation, KillWorkerError, KillWorkflowWorker
from hmt_crypto.oms.paper_kill_backend import DurablePaperKillActionBackend
from hmt_crypto.persistence.command_models import CommandOperation
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.identity_models import Account
from hmt_crypto.persistence.kill_workflow_models import KillWorkflowEvent
from hmt_crypto.persistence.models import metadata
from hmt_crypto.persistence.paper_execution_models import PaperExecutionEvent
from hmt_crypto.persistence.repository_contracts import AccountScope
from hmt_crypto.worker.paper_control import PaperJournalKillPort
from hmt_crypto.worker.paper_repository import JournalEvent, PaperJournalRepository

_SEED_TIME = datetime.now(UTC) - timedelta(minutes=2)
_OWNER = UUID("c0000000-0000-4000-8000-000000000001")


async def _database(
    tmp_path: Path,
    *,
    flatten: bool,
    risk_orders: int = 0,
    other_orders: int = 0,
    position_contracts: int = 0,
) -> tuple[Database, AccountScope, UUID]:
    settings = Settings(
        DATABASE_URL=SecretStr(f"sqlite+aiosqlite:///{tmp_path / f'paper-{uuid4()}.db'}"),
        DATABASE_SINGLE_PROCESS=True,
    )
    database = Database(settings)
    async with database.engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    scope = AccountScope(
        account_id=uuid4(),
        region="india",
        environment="testnet",
        mode="PAPER",
    )
    command_id = uuid4()
    async with database.transaction() as session:
        session.add(
            Account(
                id=scope.account_id,
                region=scope.region,
                environment=scope.environment,
                mode=scope.mode,
                profile_name="durable-paper-kill",
                external_user_id=None,
                external_account_id=None,
                identity_verified_at=None,
                current_credential_version=None,
                version=1,
                created_at=_SEED_TIME,
                updated_at=_SEED_TIME,
            )
        )
    if risk_orders or other_orders or position_contracts:
        repository = PaperJournalRepository()
        seed_events: list[JournalEvent] = []
        for index in range(risk_orders):
            seed_events.append(
                JournalEvent(
                    "COMMAND",
                    f"risk-order-{index}",
                    _SEED_TIME,
                    {
                        "order_id": f"risk-order-{index}",
                        "operation": "PLACE_ORDER",
                        "product_id": 1101,
                        "side": "BUY",
                        "contracts": 1,
                        "reduce_only": False,
                    },
                )
            )
        for index in range(other_orders):
            seed_events.append(
                JournalEvent(
                    "COMMAND",
                    f"protective-order-{index}",
                    _SEED_TIME,
                    {
                        "order_id": f"protective-order-{index}",
                        "operation": "PLACE_ORDER",
                        "product_id": 1101,
                        "side": "SELL",
                        "contracts": 1,
                        "reduce_only": True,
                    },
                )
            )
        if position_contracts:
            seed_events.append(
                JournalEvent(
                    "FILL",
                    "seed-position",
                    _SEED_TIME,
                    {
                        "fill_schema_version": 1,
                        "fill_key_sha256": "a" * 64,
                        "product_id": 1101,
                        "side": "BUY",
                        "contracts": position_contracts,
                        "price": "100",
                        "fee": "0.1",
                    },
                )
            )
        async with database.transaction() as session:
            await repository.ensure_intent(
                session,
                scope,
                request_id="seed-paper-truth",
                intent_payload={"paper_intent_schema_version": 1},
                occurred_at=_SEED_TIME,
            )
        async with database.transaction() as session:
            claim = await repository.claim(
                session,
                scope,
                "seed-paper-truth",
                owner_id=_OWNER,
                now=_SEED_TIME + timedelta(seconds=1),
                duration=timedelta(minutes=1),
            )
            assert claim is not None
            await repository.append(
                session,
                claim,
                events=tuple(seed_events),
                status="COMPLETE",
                recorded_at=_SEED_TIME + timedelta(seconds=1),
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
                available_at=_SEED_TIME,
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


@pytest.mark.asyncio
async def test_backend_replays_operation_key_without_second_state_mutation(tmp_path: Path) -> None:
    database, scope, _ = await _database(
        tmp_path,
        flatten=False,
        risk_orders=2,
    )
    backend = DurablePaperKillActionBackend(database, scope)
    request = KillRequest.create(request_id=uuid4(), flatten=False, confirmation=None)
    invocation = KillActionInvocation.create(scope, request, KillAction.PERSIST_RISK_HALT)
    try:
        first = await backend.execute(invocation)
        replay = await backend.execute(invocation)
        assert replay == first
        async with database.transaction() as session:
            snapshot = await PaperJournalKillPort().snapshot(session, scope)
            receipt_count = await session.scalar(
                select(func.count())
                .select_from(PaperExecutionEvent)
                .where(PaperExecutionEvent.event_kind == "KILL_ACTION")
            )
            assert snapshot.risk_halted is True
            assert receipt_count == 1
    finally:
        await database.aclose()


@pytest.mark.asyncio
async def test_flatten_workflow_restarts_every_step_against_durable_paper_state(
    tmp_path: Path,
) -> None:
    database, scope, command_id = await _database(
        tmp_path,
        flatten=True,
        risk_orders=2,
        other_orders=3,
        position_contracts=4,
    )
    try:
        results = []
        for _ in range(6):
            backend = DurablePaperKillActionBackend(database, scope)
            worker = KillWorkflowWorker(
                database,
                scope,
                owner_id=_OWNER,
                authority=backend.action_authority(),
            )
            results.append(await worker.run_once())
        assert results[-1].disposition == "COMPLETED"
        async with database.transaction() as session:
            snapshot = await PaperJournalKillPort().snapshot(session, scope)
            command = await session.get(CommandOperation, command_id)
            receipts = await session.scalar(
                select(func.count())
                .select_from(PaperExecutionEvent)
                .where(PaperExecutionEvent.event_kind == "KILL_ACTION")
            )
            events = await session.scalar(select(func.count()).select_from(KillWorkflowEvent))
            assert snapshot.risk_halted is True
            assert snapshot.active_orders == ()
            assert snapshot.positions == ()
            assert command is not None and command.status == "COMPLETED"
            assert receipts == events == 6
    finally:
        await database.aclose()


@pytest.mark.asyncio
async def test_cancel_only_reestablishes_and_verifies_retained_protection(tmp_path: Path) -> None:
    database, scope, command_id = await _database(
        tmp_path,
        flatten=False,
        risk_orders=1,
        other_orders=1,
        position_contracts=2,
    )
    backend = DurablePaperKillActionBackend(database, scope)
    try:
        result = None
        for _ in range(7):
            result = await KillWorkflowWorker(
                database,
                scope,
                owner_id=_OWNER,
                authority=backend.action_authority(),
            ).run_once()
        assert result is not None and result.disposition == "COMPLETED"
        async with database.transaction() as session:
            snapshot = await PaperJournalKillPort().snapshot(session, scope)
            command = await session.get(CommandOperation, command_id)
            assert [item.signed_contracts for item in snapshot.positions] == [2]
            assert snapshot.active_orders == ()
            assert snapshot.protection_established is True
            assert snapshot.protection_verified is True
            assert command is not None and command.status == "COMPLETED"
    finally:
        await database.aclose()


def test_live_scope_cannot_construct_durable_paper_backend(tmp_path: Path) -> None:
    settings = Settings(
        DATABASE_URL=SecretStr(f"sqlite+aiosqlite:///{tmp_path / 'unused.db'}"),
        DATABASE_SINGLE_PROCESS=True,
    )
    database = Database(settings)
    live = AccountScope(
        account_id=uuid4(),
        region="india",
        environment="testnet",
        mode="LIVE",
    )
    with pytest.raises(KillWorkerError):
        DurablePaperKillActionBackend(database, live)
