"""Credential storage cannot retain values PostgreSQL may echo in failing rows."""

import asyncio
import json
import socket
import ssl
import traceback

import asyncpg
import pytest
from schema_test_support import account, credential, schema_api
from schema_test_support import schema_db as schema_db
from sqlalchemy import insert, select, text
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.engine.base import Connection
from sqlalchemy.exc import DBAPIError

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


def _render_exception_graph(error):
    """Inspect all exposed links, even contexts suppressed by standard rendering."""
    pending, seen, rendered = [error], set(), []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        rendered.extend(
            [
                str(current),
                repr(current),
                repr(vars(current)),
                repr(getattr(current, "statement", None)),
                repr(getattr(current, "params", None)),
            ]
        )
        for name in ("__cause__", "__context__", "orig"):
            linked = getattr(current, name, None)
            if isinstance(linked, BaseException):
                pending.append(linked)
            else:
                rendered.append(repr(linked))
    return "\n".join(rendered)


@pytest.mark.asyncio
@pytest.mark.parametrize("driver", ["postgresql", "sqlite"])
@pytest.mark.parametrize("disconnected", [False, True])
async def test_offline_driver_tracebacks_discard_secret_diagnostic_chains(
    tmp_path, driver, disconnected
):
    fingerprint = "0123456789abcdef" * 4
    marker = "SECRET-CREDENTIAL-MARKER"
    detail = f"Failing row contains ({fingerprint}, {marker})."
    sqlite_path = tmp_path / "offline.db"
    url = (
        "postgresql+asyncpg://invalid.example/offline"
        if driver == "postgresql"
        else f"sqlite+aiosqlite:///{sqlite_path}"
    )
    database = Database(Settings(_env_file=None, DATABASE_URL=url))
    try:
        dialect = database.engine.dialect
        if driver == "postgresql":
            cause = asyncpg.CheckViolationError("credential constraint failed")
            cause.detail = detail
            kind = (
                AsyncAdapt_asyncpg_dbapi.InterfaceError
                if disconnected
                else AsyncAdapt_asyncpg_dbapi.IntegrityError
            )
            message = "connection is closed" if disconnected else "constraint failed"
            adapted = kind(f"{message}: {cause}")
        else:
            cause = RuntimeError(detail)
            kind = (
                dialect.loaded_dbapi.ProgrammingError
                if disconnected
                else dialect.loaded_dbapi.IntegrityError
            )
            message = (
                "Cannot operate on a closed database." if disconnected else "constraint failed"
            )
            adapted = kind(f"{message} {detail}")
        cause.__context__ = ValueError(detail)
        assert fingerprint in str(adapted) and marker in str(adapted)
        try:
            raise adapted from cause
        except type(adapted) as original:
            with pytest.raises(DBAPIError) as caught:
                # Installed SQLAlchemy performs classification, dispatches the
                # registered listener, then actually raises replacement from e.
                Connection._handle_dbapi_exception_noconnection(
                    original,
                    dialect,
                    database.engine.sync_engine,
                )
        exposed = caught.value
        rendered = "".join(traceback.format_exception(exposed)) + _render_exception_graph(exposed)
        assert fingerprint not in rendered
        assert marker not in rendered
        assert exposed.connection_invalidated is disconnected
        assert exposed.orig is not adapted
        assert exposed.statement is None
        assert exposed.params is None
        with pytest.raises(PersistenceError) as normalized:
            async with database.transaction():
                raise exposed
        assert fingerprint not in "".join(traceback.format_exception(normalized.value))
        assert marker not in "".join(traceback.format_exception(normalized.value))
        assert normalized.value.__suppress_context__
        assert not sqlite_path.exists()
    finally:
        await database.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("driver", ["postgresql", "sqlite"])
@pytest.mark.parametrize(
    "kind",
    [
        "filename",
        OSError,
        ConnectionError,
        ConnectionAbortedError,
        ConnectionRefusedError,
        ConnectionResetError,
        BrokenPipeError,
        TimeoutError,
        socket.gaierror,
    ],
)
@pytest.mark.parametrize("disconnected", [False, True])
async def test_native_connection_slots_cannot_escape_actual_raise_path(
    tmp_path, driver, kind, disconnected
):
    fingerprint = "0123456789abcdef" * 4
    marker = "SECRET-NATIVE-CONNECTION-MARKER"
    detail = f"{marker}: {fingerprint}"
    if kind == "filename":
        native = OSError(5, detail, marker, None, marker)
    elif kind is socket.gaierror:
        native = socket.gaierror(socket.EAI_NONAME, detail)
    else:
        native = kind(5, detail)
    native.add_note(detail)
    url = (
        "postgresql+asyncpg://invalid.example/offline"
        if driver == "postgresql"
        else f"sqlite+aiosqlite:///{tmp_path}/offline.db"
    )
    database = Database(Settings(_env_file=None, DATABASE_URL=url))
    try:
        try:
            raise native from RuntimeError(detail)
        except OSError as original:
            with pytest.raises(DBAPIError) as caught:
                # No connection exists; explicitly supplied classification must
                # survive just as already-classified pooled failures do.
                Connection._handle_dbapi_exception_noconnection(
                    original,
                    database.engine.dialect,
                    database.engine.sync_engine,
                    is_disconnect=disconnected,
                )
        exposed = caught.value
        rendered = "".join(traceback.format_exception(exposed)) + _render_exception_graph(exposed)
        assert fingerprint not in rendered
        assert marker not in rendered
        assert exposed.connection_invalidated is disconnected
        assert exposed.statement is None and exposed.params is None
        assert all(
            getattr(native, field) is None
            for field in ("errno", "strerror", "filename", "filename2")
        )
        assert not (tmp_path / "offline.db").exists()
    finally:
        await database.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [asyncpg.CheckViolationError, asyncpg.InvalidPasswordError])
async def test_native_asyncpg_server_and_auth_details_are_safe_before_adaptation(kind):
    marker = "SECRET-NATIVE-SERVER-MARKER"
    native = kind("server failure")
    native.detail = marker
    native.hint = marker
    database = Database(
        Settings(_env_file=None, DATABASE_URL="postgresql+asyncpg://invalid.example/offline")
    )
    try:
        try:
            raise native from ValueError(marker)
        except kind as original:
            with pytest.raises(DBAPIError) as caught:
                Connection._handle_dbapi_exception_noconnection(
                    original,
                    database.engine.dialect,
                    database.engine.sync_engine,
                )
        rendered = "".join(traceback.format_exception(caught.value)) + _render_exception_graph(
            caught.value
        )
        assert marker not in rendered
        assert not caught.value.connection_invalidated
    finally:
        await database.aclose()


class _UnadmittedOSError(OSError):
    """A third-party subclass without a supported diagnostic slot contract."""


@pytest.mark.asyncio
@pytest.mark.parametrize("driver", ["postgresql", "sqlite"])
@pytest.mark.parametrize(
    "kind",
    [
        ValueError,
        RuntimeError,
        asyncio.CancelledError,
        KeyboardInterrupt,
        PersistenceError,
        ssl.SSLError,
        _UnadmittedOSError,
    ],
)
async def test_unadmitted_exceptions_keep_identity_diagnostics_and_control_flow(
    tmp_path, driver, kind
):
    error = kind() if kind is PersistenceError else kind("unrelated diagnostic")
    error.add_note("preserve this note")
    cause = RuntimeError("preserve cause")
    original_args, original_attributes = error.args, dict(vars(error))
    url = (
        "postgresql+asyncpg://invalid.example/offline"
        if driver == "postgresql"
        else f"sqlite+aiosqlite:///{tmp_path}/offline.db"
    )
    database = Database(Settings(_env_file=None, DATABASE_URL=url))
    try:
        try:
            raise error from cause
        except BaseException as original:
            with pytest.raises(kind) as caught:
                Connection._handle_dbapi_exception_noconnection(
                    original,
                    database.engine.dialect,
                    database.engine.sync_engine,
                )
        assert caught.value is error
        assert error.args == original_args
        assert vars(error) == original_attributes
        assert error.__cause__ is cause
        if isinstance(error, OSError):
            with pytest.raises(PersistenceError) as normalized:
                async with database.transaction():
                    raise error
            assert "unrelated diagnostic" not in "".join(
                traceback.format_exception(normalized.value)
            )
            assert normalized.value.__suppress_context__
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
