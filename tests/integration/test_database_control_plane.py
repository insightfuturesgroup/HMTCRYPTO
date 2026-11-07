"""SQLite acceptance for the durable operator control-plane adapter."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from hmt_crypto.api.account_bootstrap import bootstrap_account
from hmt_crypto.api.control_plane import KillCommand
from hmt_crypto.api.database_control_plane import DatabaseControlPlane
from hmt_crypto.api.operator_audit import DatabaseOperatorAuditSink, OperatorAuditRecord
from hmt_crypto.config import Settings
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.dev_schema import initialize_development_schema
from hmt_crypto.persistence.models import Account, OperatorAuditEvent, metadata
from hmt_crypto.persistence.repository_contracts import AccountScope


@pytest.mark.asyncio
async def test_account_bootstrap_is_idempotent_and_returns_safe_identity(
    tmp_path: Path,
) -> None:
    settings = Settings(
        DATABASE_URL=SecretStr(f"sqlite+aiosqlite:///{tmp_path / 'bootstrap.db'}"),
        DATABASE_SINGLE_PROCESS=True,
    )
    assert await initialize_development_schema(settings) == len(metadata.tables)

    first = await bootstrap_account(settings, profile_name="paper-primary")
    second = await bootstrap_account(settings, profile_name="paper-primary")
    assert first.created is True
    assert second.created is False
    assert second.account_id == first.account_id
    assert second.profile_name == "paper-primary"


@pytest.mark.asyncio
async def test_account_bootstrap_concurrent_callers_converge_without_integrity_error(
    tmp_path: Path,
) -> None:
    settings = Settings(
        DATABASE_URL=SecretStr(f"sqlite+aiosqlite:///{tmp_path / 'bootstrap-race.db'}"),
        DATABASE_SINGLE_PROCESS=True,
    )
    assert await initialize_development_schema(settings) == len(metadata.tables)

    results = await asyncio.gather(
        *(bootstrap_account(settings, profile_name="paper-race") for _ in range(8))
    )

    assert len({item.account_id for item in results}) == 1
    assert sum(item.created for item in results) == 1


@pytest.mark.asyncio
async def test_database_control_plane_is_conservative_and_kill_is_durable(
    tmp_path: Path,
) -> None:
    account_id = uuid4()
    settings = Settings(
        DATABASE_URL=SecretStr(f"sqlite+aiosqlite:///{tmp_path / 'control.db'}"),
        DATABASE_SINGLE_PROCESS=True,
    )
    database = Database(settings)
    async with database.engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    now = datetime.now(UTC)
    async with database.transaction() as session:
        session.add(
            Account(
                id=account_id,
                region="india",
                environment="testnet",
                mode="PAPER",
                profile_name="paper-primary",
                external_user_id=None,
                external_account_id=None,
                identity_verified_at=None,
                current_credential_version=None,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )

    control = DatabaseControlPlane(database, settings, account_id)
    try:
        health = await control.health()
        assert health.live is True
        assert health.ready is False
        assert {item.component for item in health.components} == {
            "database",
            "private_stream",
            "reconciliation",
        }

        dashboard = await control.dashboard()
        assert dashboard.reconciliation_state == "UNKNOWN"
        assert dashboard.risk_halted is True
        assert dashboard.positions == ()
        assert dashboard.open_orders == ()

        impact = await control.preview_kill(flatten=True)
        assert (impact.open_order_count, impact.position_count) == (None, None)
        assert impact.evidence_state == "UNKNOWN"
        assert impact.evidence_observed_at is None
        assert await control.command_status(uuid4()) is None

        request_id = uuid4()
        preview_id = uuid4()
        receipt = await control.enqueue_kill(
            KillCommand(
                command_id=request_id,
                request_id=request_id,
                preview_id=preview_id,
                flatten=True,
                accepted_at=now,
            )
        )
        assert receipt.command_id == request_id
        assert receipt.state == "QUEUED"
        assert await control.active_kill() == receipt
        assert await control.command_status(receipt.command_id) == receipt

        replay = await control.enqueue_kill(
            KillCommand(
                command_id=request_id,
                request_id=request_id,
                preview_id=preview_id,
                flatten=True,
                accepted_at=now,
            )
        )
        assert replay == receipt

        competing_id = uuid4()
        competing = await control.enqueue_kill(
            KillCommand(
                command_id=competing_id,
                request_id=competing_id,
                preview_id=uuid4(),
                flatten=False,
                accepted_at=now,
            )
        )
        assert competing == receipt
    finally:
        await control.aclose()


@pytest.mark.asyncio
async def test_database_operator_audit_sink_appends_secret_free_scoped_evidence(
    tmp_path: Path,
) -> None:
    account_id = uuid4()
    settings = Settings(
        DATABASE_URL=SecretStr(f"sqlite+aiosqlite:///{tmp_path / 'operator-audit.db'}"),
        DATABASE_SINGLE_PROCESS=True,
    )
    database = Database(settings)
    async with database.engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    now = datetime.now(UTC)
    async with database.transaction() as session:
        session.add(
            Account(
                id=account_id,
                region="india",
                environment="testnet",
                mode="PAPER",
                profile_name="paper-audit",
                external_user_id=None,
                external_account_id=None,
                identity_verified_at=None,
                current_credential_version=None,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
    scope = AccountScope(
        account_id=account_id,
        region="india",
        environment="testnet",
        mode="PAPER",
    )
    sink = DatabaseOperatorAuditSink(database, scope)
    session_id = uuid4()
    await sink.append(
        OperatorAuditRecord(
            event_kind="KILL_PREVIEW",
            outcome="SUCCEEDED",
            occurred_at=now,
            session_id=session_id,
            action_variant="CANCEL_AND_FLATTEN",
        )
    )
    try:
        async with database.transaction() as session:
            row = (await session.execute(select(OperatorAuditEvent))).scalar_one()
            assert row.account_id == account_id
            assert row.session_id == session_id
            assert row.event_kind == "KILL_PREVIEW"
            assert row.action_variant == "CANCEL_AND_FLATTEN"
            assert set(row.__table__.columns.keys()) >= {
                "event_kind",
                "outcome",
                "session_id",
                "correlation_id",
            }
            assert "password" not in row.__table__.columns
            assert "peer" not in row.__table__.columns
    finally:
        await database.aclose()
