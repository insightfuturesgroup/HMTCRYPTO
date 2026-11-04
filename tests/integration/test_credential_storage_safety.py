"""Credential storage cannot retain values PostgreSQL may echo in failing rows."""

import json

import asyncpg
import pytest
from schema_test_support import account, credential, schema_api
from schema_test_support import schema_db as schema_db
from sqlalchemy import insert, select, text
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.engine.base import ExceptionContextImpl
from sqlalchemy.exc import IntegrityError

from hmt_crypto.config import Settings
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.errors import PersistenceError


def test_credential_storage_accepts_only_safe_hint_without_full_fingerprint_column():
    api = schema_api()
    assert "fingerprint_sha256" not in api.CredentialVersion.__table__.columns
    row = credential(account(), fingerprint_hint="0123456789ab")
    safe = row.to_safe_dict()
    assert safe["fingerprint_hint"] == "0123456789ab"
    assert safe["credential_slot"] == "DELTA_INDIA_TESTNET"
    assert "0123456789ab" * 5 not in json.dumps(safe)


def test_arbitrary_credential_slot_marker_is_rejected_before_it_can_be_retained():
    marker = "SECRET-CREDENTIAL-MARKER"
    with pytest.raises(PersistenceError) as caught:
        credential(account(), credential_slot=marker)
    assert marker not in str(caught.value) + repr(caught.value)


@pytest.mark.asyncio
async def test_offline_postgres_detail_cannot_escape_database_error_boundary():
    fingerprint = "0123456789abcdef" * 4
    marker = "SECRET-CREDENTIAL-MARKER"
    driver_error = asyncpg.CheckViolationError("credential constraint failed")
    driver_error.detail = f"Failing row contains ({fingerprint}, {marker})."
    # Use the installed dialect's actual DBAPI exception type/formatting. There
    # is no connection, fake SQL transport, or assertion about a mock's calls.
    adapted = AsyncAdapt_asyncpg_dbapi.IntegrityError(f"{type(driver_error)}: {driver_error}")
    original = IntegrityError(
        "INSERT INTO credential_versions ...", None, adapted, hide_parameters=True
    )
    assert fingerprint in str(original) and marker in str(original)
    database = Database(
        Settings(
            _env_file=None,
            DATABASE_URL="postgresql+asyncpg://invalid.example/offline",
        )
    )
    try:
        context = ExceptionContextImpl(
            adapted,
            original,
            database.engine.sync_engine,
            database.engine.dialect,
            None,
            None,
            original.statement,
            None,
            None,
            False,
            True,
            False,
        )
        exposed = original
        for handler in database.engine.sync_engine.dialect.dispatch.handle_error:
            replacement = handler(context)
            if replacement is not None:
                exposed = replacement
        rendered = str(exposed) + repr(exposed)
        assert fingerprint not in rendered
        assert marker not in rendered
        assert exposed.orig is not adapted
        assert exposed.params is None
        with pytest.raises(PersistenceError) as caught:
            # Beginning a lazy transaction does not connect. Preserve the public
            # normalization contract even if an unsafe wrapper reaches this API.
            async with database.transaction():
                raise original
        assert fingerprint not in str(caught.value) + repr(caught.value)
        assert marker not in str(caught.value) + repr(caught.value)
        assert caught.value.__suppress_context__
    finally:
        await database.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["india", "global"])
@pytest.mark.parametrize("environment", ["testnet", "prod"])
async def test_credential_slot_is_derived_from_exact_venue_scope(schema_db, region, environment):
    api = schema_api()
    owner = account(region=region, environment=environment)
    async with schema_db.transaction() as session:
        session.add(owner)
    row = credential(owner, fingerprint_hint=None)
    del row.credential_slot
    async with schema_db.transaction() as session:
        session.add(row)
    async with schema_db.transaction() as session:
        stored = await session.get(api.CredentialVersion, row.id)
        assert stored.credential_slot == f"DELTA_{region.upper()}_{environment.upper()}"
        assert stored.fingerprint_hint is None


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core", "raw"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("fingerprint_hint", "0123456789abcdef" * 4),
        ("fingerprint_hint", "a" * 12 + "\x00hidden-suffix"),
        ("credential_slot", "SECRET-CREDENTIAL-MARKER"),
        ("credential_slot", "DELTA_GLOBAL_PROD"),
    ],
)
async def test_credential_hint_and_slot_reject_unsafe_or_mismatched_writes(
    schema_db, channel, field, value
):
    api = schema_api()
    owner = account()
    async with schema_db.transaction() as session:
        session.add(owner)
    row = credential(owner)
    if channel == "raw":
        async with schema_db.transaction() as session:
            session.add(row)
        with pytest.raises(PersistenceError):
            async with schema_db.transaction() as session:
                await session.execute(
                    text(f"UPDATE credential_versions SET {field} = :bad"), {"bad": value}
                )
        async with schema_db.transaction() as session:
            stored = (await session.scalars(select(api.CredentialVersion))).one()
            assert stored.fingerprint_hint == "a" * 12
            assert stored.credential_slot == "DELTA_INDIA_TESTNET"
        return
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            if channel == "orm":
                setattr(row, field, value)
                session.add(row)
            else:
                values = {
                    column.name: getattr(row, column.name)
                    for column in api.CredentialVersion.__table__.columns
                }
                values[field] = value
                await session.execute(insert(api.CredentialVersion.__table__).values(**values))
