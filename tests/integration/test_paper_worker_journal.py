"""Durable append-only journal behavior for offline execution."""

from datetime import UTC, datetime, timedelta
from importlib import import_module
from importlib.util import find_spec
from uuid import UUID

import pytest
import pytest_asyncio
from schema_test_support import account
from schema_test_support import schema_db as schema_db
from sqlalchemy import delete, func, select

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.paper_execution_models import (
    PaperExecutionEvent,
    PaperExecutionHead,
)
from hmt_crypto.persistence.repository_contracts import AccountScope
from hmt_crypto.worker.paper_repository import (
    JournalEvent,
    PaperJournalConflict,
    PaperJournalRepository,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 6, 8, tzinfo=UTC)


@pytest_asyncio.fixture
async def paper_db(schema_db):
    """Add only the isolated paper tables to the existing disposable schema."""

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


async def _scope(database: object, *, mode: str = "BACKTEST") -> AccountScope:
    owner = account(
        id=UUID("00000000-0000-0000-0000-000000000111"),
        mode=mode,
        profile_name=f"paper-{mode.lower()}",
        created_at=NOW,
        updated_at=NOW,
    )
    async with database.transaction() as session:  # type: ignore[attr-defined]
        session.add(owner)
    return AccountScope(
        account_id=owner.id,
        region=owner.region,
        environment=owner.environment,
        mode=owner.mode,
    )


def test_paper_journal_has_isolated_model_and_repository_modules() -> None:
    """Accidentally removing the durable boundary must fail integration checks."""

    assert find_spec("hmt_crypto.persistence.paper_execution_models") is not None
    assert find_spec("hmt_crypto.worker.paper_repository") is not None


def test_paper_journal_exposes_only_head_and_append_only_event_models() -> None:
    """Adding venue-shaped projections instead of the isolated journal is a safety bug."""

    module = import_module("hmt_crypto.persistence.paper_execution_models")

    assert hasattr(module, "PaperExecutionHead")
    assert hasattr(module, "PaperExecutionEvent")


def test_repository_exposes_transactional_claim_and_append_contracts() -> None:
    """Removing claim/CAS receipts would permit ambiguous concurrent replay."""

    module = import_module("hmt_crypto.worker.paper_repository")

    assert all(
        hasattr(module, name)
        for name in (
            "JournalEvent",
            "PaperJournalClaim",
            "PaperJournalConflict",
            "PaperJournalHead",
            "PaperJournalRepository",
        )
    )


@pytest.mark.asyncio
async def test_intent_is_durable_idempotent_and_scope_exact(paper_db) -> None:
    """Changing replay payload or tenant scope must never alias an existing intent."""

    scope = await _scope(paper_db)
    repository = PaperJournalRepository()
    payload = {"intent_version": 1, "contracts": 2, "price": "100.0"}
    async with paper_db.transaction() as session:
        first = await repository.ensure_intent(
            session,
            scope,
            request_id="request-1",
            intent_payload=payload,
            occurred_at=NOW,
        )
    async with paper_db.transaction() as session:
        replay = await repository.ensure_intent(
            session,
            scope,
            request_id="request-1",
            intent_payload=payload,
            occurred_at=NOW + timedelta(seconds=1),
        )
        with pytest.raises(PaperJournalConflict):
            await repository.ensure_intent(
                session,
                scope,
                request_id="request-1",
                intent_payload={**payload, "contracts": 3},
                occurred_at=NOW,
            )

    assert first.created is True and replay.created is False
    assert first.head == replay.head
    assert first.head.scope == scope
    async with paper_db.transaction() as session:
        stored = await repository.load_events(session, scope, "request-1")
    assert [(item.sequence, item.event_kind, item.payload) for item in stored] == [
        (1, "INTENT", payload)
    ]


@pytest.mark.asyncio
async def test_claim_is_cas_exclusive_and_expiry_recoverable(paper_db) -> None:
    """Removing CAS/expiry would let two workers project the same fill."""

    scope = await _scope(paper_db)
    repository = PaperJournalRepository()
    async with paper_db.transaction() as session:
        await repository.ensure_intent(
            session,
            scope,
            request_id="request-claim",
            intent_payload={"intent_version": 1},
            occurred_at=NOW,
        )
    owner_a = UUID("00000000-0000-0000-0000-0000000000a1")
    owner_b = UUID("00000000-0000-0000-0000-0000000000b2")
    async with paper_db.transaction() as session:
        first = await repository.claim(
            session,
            scope,
            "request-claim",
            owner_id=owner_a,
            now=NOW + timedelta(seconds=1),
            duration=timedelta(seconds=10),
        )
    async with paper_db.transaction() as session:
        blocked = await repository.claim(
            session,
            scope,
            "request-claim",
            owner_id=owner_b,
            now=NOW + timedelta(seconds=2),
            duration=timedelta(seconds=10),
        )
    async with paper_db.transaction() as session:
        recovered = await repository.claim(
            session,
            scope,
            "request-claim",
            owner_id=owner_b,
            now=NOW + timedelta(seconds=12),
            duration=timedelta(seconds=10),
        )

    assert first is not None
    assert blocked is None
    assert recovered is not None and recovered.owner_id == owner_b
    assert recovered.generation == first.generation + 1


@pytest.mark.asyncio
async def test_append_is_monotonic_duplicate_safe_and_immutable(paper_db) -> None:
    """Replaying an event key must not duplicate effects or permit evidence mutation."""

    scope = await _scope(paper_db)
    repository = PaperJournalRepository()
    owner = UUID("00000000-0000-0000-0000-0000000000a1")
    async with paper_db.transaction() as session:
        await repository.ensure_intent(
            session,
            scope,
            request_id="request-append",
            intent_payload={"intent_version": 1},
            occurred_at=NOW,
        )
    async with paper_db.transaction() as session:
        claim = await repository.claim(
            session,
            scope,
            "request-append",
            owner_id=owner,
            now=NOW + timedelta(seconds=1),
            duration=timedelta(minutes=1),
        )
        assert claim is not None
        first = await repository.append(
            session,
            claim,
            events=(
                JournalEvent(
                    event_kind="RISK_APPROVED",
                    logical_key="risk-v1",
                    occurred_at=NOW + timedelta(seconds=1),
                    payload={"decision_sha256": "a" * 64},
                ),
                JournalEvent(
                    event_kind="RESERVATION",
                    logical_key="reservation-v1",
                    occurred_at=NOW + timedelta(seconds=1),
                    payload={"contracts": 2},
                ),
            ),
            status="PREPARED",
            recorded_at=NOW + timedelta(seconds=1),
        )
    async with paper_db.transaction() as session:
        renewed = await repository.claim(
            session,
            scope,
            "request-append",
            owner_id=owner,
            now=NOW + timedelta(seconds=2),
            duration=timedelta(minutes=1),
        )
        assert renewed is not None
        replay = await repository.append(
            session,
            renewed,
            events=(
                JournalEvent(
                    event_kind="RISK_APPROVED",
                    logical_key="risk-v1",
                    occurred_at=NOW + timedelta(seconds=1),
                    payload={"decision_sha256": "a" * 64},
                ),
            ),
            status="PREPARED",
            recorded_at=NOW + timedelta(seconds=2),
        )
    async with paper_db.transaction() as session:
        rows = await repository.load_events(session, scope, "request-append")
        count = await session.scalar(select(func.count()).select_from(PaperExecutionEvent))
        stored = await session.scalar(
            select(PaperExecutionEvent).where(PaperExecutionEvent.event_kind == "RESERVATION")
        )
        assert stored is not None
        stored.payload_sha256 = "b" * 64
        with pytest.raises(PersistenceValueError):
            await session.flush()

    assert first.appended == 2
    assert replay.appended == 0
    assert count == 3
    assert [item.sequence for item in rows] == [1, 2, 3]


@pytest.mark.asyncio
async def test_duplicate_event_key_with_changed_payload_fails_closed(paper_db) -> None:
    """An idempotency-key collision must not silently replace economic evidence."""

    scope = await _scope(paper_db)
    repository = PaperJournalRepository()
    owner = UUID("00000000-0000-0000-0000-0000000000a1")
    async with paper_db.transaction() as session:
        await repository.ensure_intent(
            session,
            scope,
            request_id="request-conflict",
            intent_payload={"intent_version": 1},
            occurred_at=NOW,
        )
    async with paper_db.transaction() as session:
        claim = await repository.claim(
            session,
            scope,
            "request-conflict",
            owner_id=owner,
            now=NOW + timedelta(seconds=1),
            duration=timedelta(minutes=1),
        )
        assert claim is not None
        await repository.append(
            session,
            claim,
            events=(JournalEvent("COMMAND", "command-v1", NOW, {"contracts": 2}),),
            status="PREPARED",
            recorded_at=NOW + timedelta(seconds=1),
        )
    async with paper_db.transaction() as session:
        renewed = await repository.claim(
            session,
            scope,
            "request-conflict",
            owner_id=owner,
            now=NOW + timedelta(seconds=2),
            duration=timedelta(minutes=1),
        )
        assert renewed is not None
        with pytest.raises(PaperJournalConflict):
            await repository.append(
                session,
                renewed,
                events=(JournalEvent("COMMAND", "command-v1", NOW, {"contracts": 3}),),
                status="PREPARED",
                recorded_at=NOW + timedelta(seconds=2),
            )
