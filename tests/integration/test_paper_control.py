"""Kill-control operations must mutate and verify the real PAPER journal truth."""

from datetime import UTC, datetime, timedelta
from importlib import import_module
from importlib.util import find_spec
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from schema_test_support import account
from schema_test_support import schema_db as schema_db
from sqlalchemy import delete

from hmt_crypto.oms.kill_switch import (
    KillEvent,
    apply_kill_event,
    next_kill_action,
    record_kill_event,
)
from hmt_crypto.oms.kill_worker import KillActionInvocation
from hmt_crypto.oms.paper_kill_backend import DurablePaperKillActionBackend
from hmt_crypto.persistence.paper_execution_models import (
    PaperExecutionEvent,
    PaperExecutionHead,
)
from hmt_crypto.persistence.repository_contracts import AccountScope
from hmt_crypto.worker.paper_control import PaperJournalKillPort
from hmt_crypto.worker.paper_repository import JournalEvent, PaperJournalRepository

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 6, 8, tzinfo=UTC)


@pytest_asyncio.fixture
async def paper_db(schema_db):
    async with schema_db.engine.begin() as connection:
        await connection.run_sync(PaperExecutionHead.__table__.create, checkfirst=True)
        await connection.run_sync(PaperExecutionEvent.__table__.create, checkfirst=True)
    try:
        yield schema_db
    finally:
        async with schema_db.engine.begin() as connection:
            await connection.execute(delete(PaperExecutionEvent))
            await connection.execute(delete(PaperExecutionHead))
            await connection.run_sync(PaperExecutionEvent.__table__.drop)
            await connection.run_sync(PaperExecutionHead.__table__.drop)


async def _seed_journal_truth(database: object) -> AccountScope:
    owner = account(
        id=UUID("00000000-0000-0000-0000-000000000111"),
        mode="BACKTEST",
        profile_name="paper-control",
        created_at=NOW,
        updated_at=NOW,
    )
    scope = AccountScope(
        account_id=owner.id,
        region=owner.region,
        environment=owner.environment,
        mode=owner.mode,
    )
    repository = PaperJournalRepository()
    async with database.transaction() as session:  # type: ignore[attr-defined]
        session.add(owner)
    async with database.transaction() as session:  # type: ignore[attr-defined]
        await repository.ensure_intent(
            session,
            scope,
            request_id="execution-before-kill",
            intent_payload={"paper_intent_schema_version": 1},
            occurred_at=NOW,
        )
    async with database.transaction() as session:  # type: ignore[attr-defined]
        claim = await repository.claim(
            session,
            scope,
            "execution-before-kill",
            owner_id=UUID("00000000-0000-0000-0000-0000000000a1"),
            now=NOW + timedelta(seconds=1),
            duration=timedelta(minutes=1),
        )
        assert claim is not None
        await repository.append(
            session,
            claim,
            events=(
                JournalEvent(
                    "COMMAND",
                    "risk-order",
                    NOW,
                    {
                        "command_schema_version": 1,
                        "command_id": "risk-command",
                        "order_id": "risk-order",
                        "operation": "PLACE_ORDER",
                        "product_id": 1101,
                        "side": "BUY",
                        "contracts": 3,
                        "reduce_only": False,
                    },
                ),
                JournalEvent(
                    "COMMAND",
                    "protective-order",
                    NOW,
                    {
                        "command_schema_version": 1,
                        "command_id": "protective-command",
                        "order_id": "protective-order",
                        "operation": "PLACE_ORDER",
                        "product_id": 1101,
                        "side": "SELL",
                        "contracts": 2,
                        "reduce_only": True,
                    },
                ),
                JournalEvent(
                    "FILL",
                    "risk-fill",
                    NOW,
                    {
                        "fill_schema_version": 1,
                        "fill_key_sha256": "a" * 64,
                        "order_id": "risk-order",
                        "product_id": 1101,
                        "side": "BUY",
                        "contracts": 2,
                        "price": "100",
                        "fee": "0.2",
                    },
                ),
            ),
            status="SIMULATED",
            recorded_at=NOW + timedelta(seconds=1),
        )
    return scope


def test_paper_control_port_is_available_for_the_kill_backend() -> None:
    """Falling back to an independent counter projection is an unsafe regression."""

    assert find_spec("hmt_crypto.worker.paper_control") is not None


def test_paper_control_exposes_authoritative_snapshot_and_drop_in_kill_port() -> None:
    """A kill backend must consume journal truth, not a separately seeded counter."""

    module = import_module("hmt_crypto.worker.paper_control")

    assert hasattr(module, "PaperControlSnapshot")
    assert hasattr(module, "PaperJournalKillPort")


@pytest.mark.asyncio
async def test_kill_actions_cancel_and_flatten_rebuilt_journal_truth(paper_db) -> None:
    """Kill completion must be impossible while journal orders or positions remain."""

    scope = await _seed_journal_truth(paper_db)
    port = PaperJournalKillPort()
    request = import_module("hmt_crypto.oms.kill_switch").KillRequest.create(
        request_id=UUID("10000000-0000-0000-0000-000000000001"),
        flatten=True,
        confirmation="FLATTEN_ALL_POSITIONS",
    )
    observed: list[KillEvent] = []
    while not request.is_terminal:
        action = next_kill_action(request)
        invocation = KillActionInvocation.create(scope, request, action)
        async with paper_db.transaction() as session:
            result = await port.execute_once(session, scope, invocation)
        observed.append(result.event)
        evidence = record_kill_event(
            request_id=request.request_id,
            event_id=uuid4(),
            expected_transition_count=request.transition_count,
            event=result.event,
        )
        request = apply_kill_event(request, evidence).next_request
    async with paper_db.transaction() as session:
        snapshot = await port.snapshot(session, scope)

    assert observed == [
        KillEvent.RISK_HALT_PERSISTED,
        KillEvent.RISK_INCREASING_CANCEL_COMPLETED,
        KillEvent.FLATTEN_COMPLETED,
        KillEvent.REMAINDER_CANCEL_COMPLETED,
        KillEvent.RECONCILED_FLAT,
        KillEvent.FINAL_RECONCILED_SAFE,
    ]
    assert snapshot.risk_halted is True
    assert snapshot.active_orders == ()
    assert snapshot.positions == ()


@pytest.mark.asyncio
async def test_production_backend_delegates_to_authoritative_journal_port(paper_db) -> None:
    """Reintroducing an independent counter backend must fail this test."""

    scope = await _seed_journal_truth(paper_db)
    request = import_module("hmt_crypto.oms.kill_switch").KillRequest.create(
        request_id=UUID("10000000-0000-0000-0000-000000000002"),
        flatten=False,
        confirmation=None,
    )
    invocation = KillActionInvocation.create(
        scope,
        request,
        next_kill_action(request),
    )

    result = await DurablePaperKillActionBackend(paper_db, scope).execute(invocation)
    async with paper_db.transaction() as session:
        snapshot = await PaperJournalKillPort().snapshot(session, scope)

    assert result.event is KillEvent.RISK_HALT_PERSISTED
    assert snapshot.risk_halted is True
