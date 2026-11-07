"""Opt-in Alembic acceptance against a separately acknowledged disposable database.

Set ``HMT_CRYPTO_MIGRATION_ACCEPTANCE_URL`` to a direct
``postgresql+asyncpg`` owner URL and set
``HMT_CRYPTO_MIGRATION_ACCEPTANCE_DISPOSABLE=true``.  Its normalized database
target must differ from both configured runtime and migration targets.  Set
``HMT_CRYPTO_REQUIRE_MIGRATION_POSTGRES=true`` to turn the default skip into a
failure in an environment where this evidence is required.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from api_audit_payload_schema_support import public_payload, public_root, retained_public_event
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from hmt_crypto.persistence.api_audit_models import ApiAuditEvent
from hmt_crypto.persistence.api_audit_payload_models import ApiAuditPayload
from hmt_crypto.persistence.api_audit_purge_contract import (
    decode_audit_payload_purge_result,
)
from hmt_crypto.persistence.migration_configuration import (
    MigrationConfigurationError,
    comparable_database_target,
    migration_connect_args,
)

URL_ENV = "HMT_CRYPTO_MIGRATION_ACCEPTANCE_URL"
DISPOSABLE_ENV = "HMT_CRYPTO_MIGRATION_ACCEPTANCE_DISPOSABLE"
REQUIRED_ENV = "HMT_CRYPTO_REQUIRE_MIGRATION_POSTGRES"
DOWNGRADE_ENV = "HMT_CRYPTO_ALLOW_EMPTY_DOWNGRADE"
DOWNGRADE_ACK = "I_ACKNOWLEDGE_EMPTY_DEV_DATABASE"
ROOT = Path(__file__).resolve().parents[2]
PROBE_SNAPSHOT_ID = UUID("00000000-0000-0000-0000-00000000a001")


class _ConfiguredDatabases(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    runtime_url: SecretStr | None = Field(default=None, alias="DATABASE_URL")
    migration_url: SecretStr | None = Field(default=None, alias="DATABASE_MIGRATION_URL")


def _target(url: URL) -> tuple[str, int, str]:
    try:
        if url.host is None or url.database is None:
            raise ValueError
        return url.host.lower().rstrip("."), url.port or 5432, url.database
    except (TypeError, ValueError):
        pytest.fail("Migration acceptance target is not safely comparable", pytrace=False)


def _try_configured_targets() -> set[tuple[str, ...]] | None:
    try:
        configured = _ConfiguredDatabases()
        values = (configured.runtime_url, configured.migration_url)
        targets: set[tuple[str, ...]] = set()
        for value in values:
            if value is None:
                continue
            url = make_url(value.get_secret_value())
            if url.get_backend_name() in {"postgres", "postgresql"}:
                targets.add(comparable_database_target(url))
        return targets
    except BaseException:
        return None


def _configured_targets() -> set[tuple[str, ...]]:
    result = _try_configured_targets()
    if result is None:
        pytest.fail("Configured database targets are not safely comparable", pytrace=False)
    return result


def _acceptance_admission() -> tuple[URL | None, str, bool]:
    """Return an admitted URL or a secret-free failure/skip decision."""

    required = os.environ.get(REQUIRED_ENV, "false").lower()
    if required not in {"true", "false"}:
        return None, f"{REQUIRED_ENV} must be true or false", False
    raw = os.environ.get(URL_ENV)
    if raw is None:
        if required == "true":
            return None, "Disposable migration acceptance URL is required", False
        return None, "Disposable migration acceptance is not configured", True
    if os.environ.get(DISPOSABLE_ENV, "false").lower() != "true":
        return None, "Migration acceptance database is not confirmed disposable", False
    try:
        url = make_url(raw)
    except BaseException:
        return None, "Migration acceptance URL is invalid", False
    if url.drivername != "postgresql+asyncpg" or url.username is None or url.password in (None, ""):
        return None, "Migration acceptance requires a complete asyncpg owner URL", False
    if url.query:
        return None, "Migration acceptance forbids driver query parameters", False
    try:
        host, port, _ = _target(url)
        target = comparable_database_target(url)
    except MigrationConfigurationError:
        return None, "Migration acceptance URL is invalid", False
    if port == 6543 or host.endswith(".pooler.supabase.com"):
        return None, "Migration acceptance requires a direct PostgreSQL target", False
    configured = _try_configured_targets()
    if configured is None:
        return None, "Configured database targets are not safely comparable", False
    if target in configured:
        return None, "Migration acceptance must not use a configured database target", False
    return url, "", False


def _acceptance_url() -> URL:
    result: URL | None = None
    message = "Migration acceptance admission failed"
    skip = False
    try:
        result, message, skip = _acceptance_admission()
    except BaseException:
        pass
    if result is None:
        if skip:
            pytest.skip(message)
        pytest.fail(message, pytrace=False)
    return result


def _acceptance_traceback_retains_secret(error: BaseException, secret: str) -> bool:
    def retains(value: object, *, depth: int, seen: set[int]) -> bool:
        if id(value) in seen or depth > 8:
            return False
        seen.add(id(value))
        if type(value) is str:
            return secret in value
        if isinstance(value, SecretStr):
            return secret in value.get_secret_value()
        if getattr(value, "password", None) == secret:
            return True
        if isinstance(value, dict):
            return any(
                retains(item, depth=depth + 1, seen=seen) for pair in value.items() for item in pair
            )
        if isinstance(value, (list, tuple, set, frozenset)):
            return any(retains(item, depth=depth + 1, seen=seen) for item in value)
        attributes = getattr(value, "__dict__", None)
        return type(attributes) is dict and any(
            retains(item, depth=depth + 1, seen=seen) for item in attributes.values()
        )

    pending = [error]
    seen_errors: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen_errors:
            continue
        seen_errors.add(id(current))
        pending.extend(
            linked for linked in (current.__cause__, current.__context__) if linked is not None
        )
        if any(retains(value, depth=0, seen=set()) for value in current.args):
            return True
        traceback = current.__traceback__
        while traceback is not None:
            if traceback.tb_frame.f_code.co_name in {
                "_acceptance_url",
                "_configured_targets",
                "_target",
            } and any(
                retains(value, depth=0, seen=set())
                for value in traceback.tb_frame.f_locals.values()
            ):
                return True
            traceback = traceback.tb_next
    return False


async def _schema_evidence(
    url: URL,
) -> tuple[int, int, bool, bool, bool, str | None, int, int]:
    engine = create_async_engine(
        url,
        hide_parameters=True,
        connect_args=migration_connect_args(url),
    )
    try:
        async with engine.connect() as connection:
            table_count = await connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'hmt_crypto' AND table_type = 'BASE TABLE'"
                )
            )
            trigger_count = await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_catalog.pg_trigger AS trigger "
                    "JOIN pg_catalog.pg_class AS relation ON relation.oid = trigger.tgrelid "
                    "JOIN pg_catalog.pg_namespace AS namespace "
                    "ON namespace.oid = relation.relnamespace "
                    "WHERE namespace.nspname = 'hmt_crypto' "
                    "AND NOT trigger.tgisinternal"
                )
            )
            kill_event_trigger = await connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_trigger AS trigger "
                    "JOIN pg_catalog.pg_class AS relation ON relation.oid = trigger.tgrelid "
                    "JOIN pg_catalog.pg_namespace AS namespace "
                    "ON namespace.oid = relation.relnamespace "
                    "WHERE namespace.nspname = 'hmt_crypto' "
                    "AND relation.relname = 'kill_workflow_events' "
                    "AND trigger.tgname = 'hmt_crypto_append_only' "
                    "AND NOT trigger.tgisinternal)"
                )
            )
            paper_event_trigger = await connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_trigger AS trigger "
                    "JOIN pg_catalog.pg_class AS relation ON relation.oid = trigger.tgrelid "
                    "JOIN pg_catalog.pg_namespace AS namespace "
                    "ON namespace.oid = relation.relnamespace "
                    "WHERE namespace.nspname = 'hmt_crypto' "
                    "AND relation.relname = 'paper_execution_events' "
                    "AND trigger.tgname = 'hmt_crypto_append_only' "
                    "AND NOT trigger.tgisinternal)"
                )
            )
            invoker = await connection.scalar(
                text(
                    "SELECT NOT procedure.prosecdef FROM pg_catalog.pg_proc AS procedure "
                    "JOIN pg_catalog.pg_namespace AS namespace "
                    "ON namespace.oid = procedure.pronamespace "
                    "WHERE namespace.nspname = 'hmt_crypto' "
                    "AND procedure.proname = 'purge_expired_audit_payload'"
                )
            )
            revision = await connection.scalar(
                text("SELECT version_num FROM public.hmt_crypto_alembic_version")
            )
            forbidden_privileges = await connection.scalar(
                text(
                    """WITH forbidden_roles AS (
                        SELECT 0::oid AS oid
                        UNION
                        SELECT oid FROM pg_catalog.pg_roles
                        WHERE rolname IN ('anon', 'authenticated')
                    ), object_grants AS (
                        SELECT acl.grantee
                        FROM pg_catalog.pg_namespace AS namespace
                        CROSS JOIN LATERAL pg_catalog.aclexplode(
                            COALESCE(namespace.nspacl, ARRAY[]::aclitem[])
                        ) AS acl
                        WHERE namespace.nspname = 'hmt_crypto'
                        UNION ALL
                        SELECT acl.grantee
                        FROM pg_catalog.pg_class AS relation
                        JOIN pg_catalog.pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        CROSS JOIN LATERAL pg_catalog.aclexplode(
                            COALESCE(relation.relacl, ARRAY[]::aclitem[])
                        ) AS acl
                        WHERE namespace.nspname = 'hmt_crypto'
                           OR (
                               namespace.nspname = 'public'
                               AND relation.relname = 'hmt_crypto_alembic_version'
                           )
                        UNION ALL
                        SELECT acl.grantee
                        FROM pg_catalog.pg_proc AS procedure
                        JOIN pg_catalog.pg_namespace AS namespace
                          ON namespace.oid = procedure.pronamespace
                        CROSS JOIN LATERAL pg_catalog.aclexplode(
                            COALESCE(
                                procedure.proacl,
                                pg_catalog.acldefault('f', procedure.proowner)
                            )
                        ) AS acl
                        WHERE namespace.nspname = 'hmt_crypto'
                        UNION ALL
                        SELECT acl.grantee
                        FROM pg_catalog.pg_default_acl AS defaults
                        JOIN pg_catalog.pg_namespace AS namespace
                          ON namespace.oid = defaults.defaclnamespace
                        CROSS JOIN LATERAL pg_catalog.aclexplode(defaults.defaclacl) AS acl
                        WHERE namespace.nspname = 'hmt_crypto'
                    )
                    SELECT count(*)
                    FROM object_grants
                    JOIN forbidden_roles ON forbidden_roles.oid = object_grants.grantee"""
                )
            )
            policies = await connection.scalar(
                text("SELECT count(*) FROM pg_catalog.pg_policies WHERE schemaname = 'hmt_crypto'")
            )
            return (
                int(table_count or 0),
                int(trigger_count or 0),
                bool(invoker),
                bool(kill_event_trigger),
                bool(paper_event_trigger),
                revision,
                int(forbidden_privileges or 0),
                int(policies or 0),
            )
    finally:
        await engine.dispose()


async def _schema_exists(url: URL) -> bool:
    engine = create_async_engine(
        url,
        hide_parameters=True,
        connect_args=migration_connect_args(url),
    )
    try:
        async with engine.connect() as connection:
            result = await connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace "
                    "WHERE nspname = 'hmt_crypto')"
                )
            )
            return bool(result)
    finally:
        await engine.dispose()


async def _version(url: URL) -> str | None:
    engine = create_async_engine(
        url,
        hide_parameters=True,
        connect_args=migration_connect_args(url),
    )
    try:
        async with engine.connect() as connection:
            return await connection.scalar(
                text("SELECT version_num FROM public.hmt_crypto_alembic_version")
            )
    finally:
        await engine.dispose()


async def _insert_data_bearing_probe(url: URL) -> None:
    engine = create_async_engine(
        url,
        hide_parameters=True,
        connect_args=migration_connect_args(url),
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """INSERT INTO hmt_crypto.catalogue_snapshots (
                        id, region, environment, revision, source, content_sha256,
                        fetched_at, validated_at, fresh_until, product_count
                    ) VALUES (
                        :identifier, 'india', 'testnet', 1, 'migration-acceptance', :digest,
                        now() - interval '2 seconds', now() - interval '1 second',
                        now() + interval '1 hour', 1
                    )"""
                ),
                {"identifier": PROBE_SNAPSHOT_ID, "digest": "a" * 64},
            )
    finally:
        await engine.dispose()


async def _delete_data_bearing_probe(url: URL) -> None:
    engine = create_async_engine(
        url,
        hide_parameters=True,
        connect_args=migration_connect_args(url),
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM hmt_crypto.catalogue_snapshots WHERE id = :identifier"),
                {"identifier": PROBE_SNAPSHOT_ID},
            )
    finally:
        await engine.dispose()


def _row_values(row: Any) -> dict[str, object]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


async def _exercise_append_only_and_retention_purge(url: URL) -> None:
    engine = create_async_engine(
        url,
        hide_parameters=True,
        connect_args=migration_connect_args(url),
    )
    source_event = retained_public_event()
    payload = public_payload()
    try:
        async with engine.begin() as connection:
            role_exists = await connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_roles "
                    "WHERE rolname = 'hmt_audit_retention')"
                )
            )
            assert role_exists is False, "disposable cluster already has the retention role"
            await connection.execute(
                ApiAuditEvent.__table__.insert(),
                [_row_values(public_root()), _row_values(source_event)],
            )
            await connection.execute(ApiAuditPayload.__table__.insert(), _row_values(payload))

            mutation_blocked = False
            try:
                async with connection.begin_nested():
                    await connection.execute(
                        text(
                            "UPDATE hmt_crypto.api_audit_events "
                            "SET event_kind = event_kind WHERE id = :identifier"
                        ),
                        {"identifier": source_event.id},
                    )
            except DBAPIError:
                mutation_blocked = True
            assert mutation_blocked, "append-only trigger admitted an UPDATE"

            for statement in (
                "CREATE ROLE hmt_audit_retention NOLOGIN",
                "GRANT USAGE ON SCHEMA hmt_crypto TO hmt_audit_retention",
                "GRANT SELECT, DELETE ON hmt_crypto.api_audit_payloads TO hmt_audit_retention",
                "GRANT INSERT ON hmt_crypto.api_audit_events TO hmt_audit_retention",
                "GRANT EXECUTE ON FUNCTION "
                "hmt_crypto.purge_expired_audit_payload(uuid, text) "
                "TO hmt_audit_retention",
                "GRANT hmt_audit_retention TO CURRENT_USER",
                "SET ROLE hmt_audit_retention",
            ):
                await connection.execute(text(statement))
            result = await connection.execute(
                text(
                    "SELECT * FROM hmt_crypto.purge_expired_audit_payload("
                    ":owning_event_id, :payload_slot)"
                ),
                {"owning_event_id": source_event.id, "payload_slot": "REQUEST"},
            )
            returned = dict(result.mappings().one())
            await connection.execute(text("RESET ROLE"))
            receipt = decode_audit_payload_purge_result(returned)
            assert receipt.owning_event_id == source_event.id
            assert receipt.actor_role_name == "hmt_audit_retention"
            remaining = await connection.scalar(
                text(
                    "SELECT count(*) FROM hmt_crypto.api_audit_payloads "
                    "WHERE owning_event_id = :identifier"
                ),
                {"identifier": source_event.id},
            )
            maintenance = await connection.scalar(
                text(
                    "SELECT count(*) FROM hmt_crypto.api_audit_events "
                    "WHERE source_event_id = :identifier "
                    "AND event_kind IN ('MAINTENANCE_ROOT', 'PAYLOAD_PURGED')"
                ),
                {"identifier": source_event.id},
            )
            assert remaining == 0
            assert maintenance == 2
    finally:
        await engine.dispose()


async def _drop_disposable_objects(url: URL, *, drop_retention_role: bool) -> None:
    engine = create_async_engine(
        url,
        hide_parameters=True,
        connect_args=migration_connect_args(url),
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA IF EXISTS hmt_crypto CASCADE"))
            await connection.execute(text("DROP TABLE IF EXISTS public.hmt_crypto_alembic_version"))
            if drop_retention_role:
                await connection.execute(text("REVOKE hmt_audit_retention FROM CURRENT_USER"))
                await connection.execute(text("DROP ROLE hmt_audit_retention"))
    finally:
        await engine.dispose()


def test_required_acceptance_fails_without_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REQUIRED_ENV, "true")
    monkeypatch.delenv(URL_ENV, raising=False)
    with pytest.raises(pytest.fail.Exception, match="acceptance URL is required"):
        _acceptance_url()


def test_acceptance_failure_does_not_retain_raw_disposable_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "acceptance-traceback-secret-canary"
    monkeypatch.setenv(
        URL_ENV,
        f"postgresql+asyncpg://owner:{secret}@disposable.example/disposable",
    )
    monkeypatch.setenv(DISPOSABLE_ENV, "false")
    with pytest.raises(pytest.fail.Exception) as captured:
        _acceptance_url()
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert not _acceptance_traceback_retains_secret(captured.value, secret)


def test_acceptance_config_parse_failure_is_recursively_secret_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "configured-target-traceback-secret-canary"
    monkeypatch.setenv(
        URL_ENV,
        "postgresql+asyncpg://owner:acceptance-secret@disposable.example/disposable",
    )
    monkeypatch.setenv(DISPOSABLE_ENV, "true")
    monkeypatch.setenv(
        "DATABASE_URL",
        f"postgresql+asyncpg://runtime:{secret}@[/",
    )
    with pytest.raises(pytest.fail.Exception) as captured:
        _acceptance_url()
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert not _acceptance_traceback_retains_secret(captured.value, secret)


def test_acceptance_rejects_configured_database_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        URL_ENV,
        "postgresql+asyncpg://owner:acceptance-secret@DB.EXAMPLE:5432/disposable",
    )
    monkeypatch.setenv(DISPOSABLE_ENV, "true")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://runtime:runtime-secret@db.example/disposable?ssl=require",
    )
    monkeypatch.delenv("DATABASE_MIGRATION_URL", raising=False)
    with pytest.raises(pytest.fail.Exception, match="configured database target"):
        _acceptance_url()


def test_acceptance_rejects_effective_configured_target_hidden_by_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        URL_ENV,
        "postgresql+asyncpg://owner:acceptance-secret@disposable.example/disposable",
    )
    monkeypatch.setenv(DISPOSABLE_ENV, "true")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://runtime:runtime-secret@safe.example/disposable?"
        "host=disposable.example",
    )
    monkeypatch.delenv("DATABASE_MIGRATION_URL", raising=False)
    with pytest.raises(pytest.fail.Exception, match="not safely comparable"):
        _acceptance_url()


def test_acceptance_rejects_same_supabase_project_through_pooler_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        URL_ENV,
        "postgresql+asyncpg://postgres:acceptance-secret@db.projectref.supabase.co/postgres",
    )
    monkeypatch.setenv(DISPOSABLE_ENV, "true")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://hmt_runtime.projectref:runtime-secret@"
        "aws-0-ap-south-1.pooler.supabase.com:5432/other_database",
    )
    monkeypatch.delenv("DATABASE_MIGRATION_URL", raising=False)
    with pytest.raises(pytest.fail.Exception, match="configured database target"):
        _acceptance_url()


def test_acceptance_rejects_driver_query_tls_downgrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        URL_ENV,
        "postgresql+asyncpg://owner:acceptance-secret@db.example/disposable?ssl=disable",
    )
    monkeypatch.setenv(DISPOSABLE_ENV, "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_MIGRATION_URL", raising=False)
    with pytest.raises(pytest.fail.Exception, match="driver query parameters"):
        _acceptance_url()


@pytest.mark.integration
def test_migration_security_and_downgrade_contracts_on_disposable_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = _acceptance_url()
    monkeypatch.setenv("DATABASE_MIGRATION_URL", url.render_as_string(hide_password=False))
    runtime_url = url.set(username="hmt_runtime_acceptance", password="unused-runtime-secret")
    monkeypatch.setenv("DATABASE_URL", runtime_url.render_as_string(hide_password=False))
    config = Config(str(ROOT / "alembic.ini"))
    retention_role_created = False
    try:
        command.upgrade(config, "phase2_baseline_v1")
        asyncio.run(_insert_data_bearing_probe(url))
        monkeypatch.setenv(DOWNGRADE_ENV, DOWNGRADE_ACK)
        with pytest.raises(RuntimeError, match="without exposing server diagnostics"):
            command.downgrade(config, "base")
        assert asyncio.run(_version(url)) == "phase2_baseline_v1"
        asyncio.run(_delete_data_bearing_probe(url))
        command.downgrade(config, "base")
        assert asyncio.run(_schema_exists(url)) is False

        command.upgrade(config, "head")
        evidence = asyncio.run(_schema_evidence(url))
        assert evidence == (
            43,
            29,
            True,
            True,
            True,
            "phase5_reconcile_checkpoint_v1",
            0,
            0,
        )
        asyncio.run(_exercise_append_only_and_retention_purge(url))
        retention_role_created = True
        with pytest.raises(RuntimeError, match="without exposing server diagnostics"):
            command.downgrade(config, "phase2_baseline_v1")
        assert asyncio.run(_version(url)) == "phase2_hardening_v1"
    finally:
        asyncio.run(_drop_disposable_objects(url, drop_retention_role=retention_role_created))
    assert asyncio.run(_schema_exists(url)) is False
