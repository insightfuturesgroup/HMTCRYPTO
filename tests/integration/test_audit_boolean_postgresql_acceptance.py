"""Compare audit CHECKs with actual migration-created PostgreSQL constraints."""

import asyncio
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from test_migration_postgresql_acceptance import ROOT, _acceptance_url, _drop_disposable_objects

from hmt_crypto.persistence._api_audit_schema_checks import api_audit_checks
from hmt_crypto.persistence.migration_configuration import migration_connect_args

pytestmark = [pytest.mark.integration, pytest.mark.safety]

CASES = (
    (
        "descriptor_slot",
        {"retention_mode": "SUMMARY_ONLY"},
        {
            "retention_mode": "RETAIN_SANITIZED_SNAPSHOT",
            "event_kind": "SIMULATOR_OUTCOME",
            "entry_possible": False,
            "payload_slot": "RESPONSE",
        },
    ),
    (
        "terminal_truth_shape",
        {"event_kind": "VENUE_ENTERED", "entry_possible": True},
        {"event_kind": "VENUE_ENTERED", "entry_possible": False},
    ),
    (
        "transfer_prior_bundle",
        {},
        {
            "prior_dispatch_event_id": "00000000-0000-0000-0000-000000000001",
            "prior_consumption_id": "00000000-0000-0000-0000-000000000002",
            "prior_bytes_sent_possible": False,
        },
    ),
)


async def exercise(url, name, valid, invalid):
    engine = create_async_engine(
        url, hide_parameters=True, connect_args=migration_connect_args(url)
    )
    check = next(item for item in api_audit_checks() if item.name == name)
    compiled = str(check.sqltext.compile(dialect=postgresql.dialect()))
    constraint_name = f"ck_api_audit_events_{name}"
    canonical = {}
    try:
        async with engine.begin() as connection:
            definition = await connection.scalar(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'hmt_crypto.api_audit_events'::regclass "
                    "AND conname = :name"
                ),
                {"name": constraint_name},
            )
            assert definition is not None
            for source in ("orm", "migration"):
                table = f"audit_boolean_{source}"
                # Copy the migration's parsed CHECK expression, not its deparsed SQL.
                # Re-parsing pg_get_constraintdef can redistribute equivalent array
                # casts and is not an idempotent text-normalization operation.
                options = " INCLUDING CONSTRAINTS" if source == "migration" else ""
                await connection.execute(
                    text(f"CREATE TEMP TABLE {table} (LIKE hmt_crypto.api_audit_events{options})")
                )
                if source == "migration":
                    unrelated = await connection.scalars(
                        text(
                            "SELECT conname FROM pg_constraint "
                            "WHERE conrelid = to_regclass(:table) AND conname <> :name"
                        ),
                        {"table": table, "name": constraint_name},
                    )
                    for other in unrelated:
                        quoted = connection.dialect.identifier_preparer.quote(other)
                        await connection.execute(
                            text(f"ALTER TABLE {table} DROP CONSTRAINT {quoted}")
                        )
                # Isolate this predicate from unrelated NOT NULL and variant constraints.
                columns = await connection.scalars(
                    text(
                        "SELECT attname FROM pg_attribute WHERE attrelid = "
                        "'hmt_crypto.api_audit_events'::regclass AND attnum > 0 "
                        "AND NOT attisdropped"
                    )
                )
                for column in columns:
                    quoted = connection.dialect.identifier_preparer.quote(column)
                    await connection.execute(
                        text(f"ALTER TABLE {table} ALTER COLUMN {quoted} DROP NOT NULL")
                    )
                if source == "orm":
                    await connection.execute(
                        text(
                            f"ALTER TABLE {table} ADD CONSTRAINT {constraint_name} "
                            f"CHECK ({compiled})"
                        )
                    )
                normalized = await connection.scalar(
                    text(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conrelid = to_regclass(:table) AND conname = :name"
                    ),
                    {"table": table, "name": constraint_name},
                )
                assert normalized == definition
                canonical[source] = normalized
                if valid:
                    fields = ", ".join(valid)
                    binds = ", ".join(f":{field}" for field in valid)
                    await connection.execute(
                        text(f"INSERT INTO {table} ({fields}) VALUES ({binds})"), valid
                    )
                else:
                    await connection.execute(text(f"INSERT INTO {table} DEFAULT VALUES"))
                async with connection.begin_nested() as savepoint:
                    fields = ", ".join(invalid)
                    binds = ", ".join(f":{field}" for field in invalid)
                    with pytest.raises(IntegrityError) as rejected:
                        await connection.execute(
                            text(f"INSERT INTO {table} ({fields}) VALUES ({binds})"), invalid
                        )
                    assert rejected.value.orig.sqlstate == "23514"
                    await savepoint.rollback()
            assert canonical["orm"] == canonical["migration"]
    finally:
        await engine.dispose()


@pytest.mark.parametrize("name,valid,invalid", CASES, ids=[case[0] for case in CASES])
def test_audit_boolean_checks_match_migrated_postgres(monkeypatch, name, valid, invalid):
    url = _acceptance_url()
    ca_file = os.environ.get("HMT_CRYPTO_MIGRATION_ACCEPTANCE_CA_FILE")
    if ca_file is not None:
        monkeypatch.setenv("DATABASE_MIGRATION_CA_FILE", ca_file)
    monkeypatch.setenv("DATABASE_MIGRATION_URL", url.render_as_string(hide_password=False))
    runtime = url.set(username="hmt_runtime_acceptance", password="unused-runtime-secret")
    monkeypatch.setenv("DATABASE_URL", runtime.render_as_string(hide_password=False))
    config = Config(str(ROOT / "alembic.ini"))
    try:
        command.upgrade(config, "head")
        asyncio.run(exercise(url, name, valid, invalid))
    finally:
        asyncio.run(_drop_disposable_objects(url, drop_retention_role=False))
