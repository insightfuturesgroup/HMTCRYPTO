"""Credential-gated raw-SQL acceptance against an explicitly disposable PostgreSQL DB.

Set ``HMT_CRYPTO_DECIMAL_POSTGRES_URL`` to a ``postgresql+asyncpg`` URL and
``HMT_CRYPTO_DECIMAL_POSTGRES_DISPOSABLE=true`` to run. Setting
``HMT_CRYPTO_REQUIRE_DECIMAL_POSTGRES=true`` makes a missing URL fail instead of
skip. Its normalized target must differ from ``DATABASE_URL`` and
``DATABASE_MIGRATION_URL``, independent of credentials, driver and query spelling.
"""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import CheckConstraint, Column, Integer, MetaData, Table, column, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.schema import CreateSchema

from hmt_crypto.persistence.configuration import database_url
from hmt_crypto.persistence.db import APP_SCHEMA, NAMING_CONVENTION
from hmt_crypto.persistence.models import metadata
from hmt_crypto.persistence.types import ExactDecimal, exact_decimal_checks

URL_ENV = "HMT_CRYPTO_DECIMAL_POSTGRES_URL"
REQUIRED_ENV = "HMT_CRYPTO_REQUIRE_DECIMAL_POSTGRES"
DISPOSABLE_ENV = "HMT_CRYPTO_DECIMAL_POSTGRES_DISPOSABLE"
RUNTIME_URL_ENVS = ("DATABASE_URL", "DATABASE_MIGRATION_URL")


class _RuntimeDatabaseSources(BaseSettings):
    """Load only database targets with runtime's case-insensitive env/dotenv precedence."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    runtime_url: SecretStr | None = Field(default=None, alias="DATABASE_URL")
    migration_url: SecretStr | None = Field(default=None, alias="DATABASE_MIGRATION_URL")


VALID_38_18 = (
    "99999999999999999999.999999999999999999",
    "-99999999999999999999.999999999999999999",
    "0.000000000000000001",
    "1.2300000000000000000",
)
INVALID_38_18 = (
    "100000000000000000000",
    "-100000000000000000000",
    "0.0000000000000000001",
    "-0.0000000000000000001",
    "1.0000000000000000001",
    "-1.0000000000000000001",
    "99999999999999999999.9999999999999999999",
    "-99999999999999999999.9999999999999999999",
    "CAST('NaN' AS NUMERIC)",
    "CAST('Infinity' AS NUMERIC)",
    "CAST('-Infinity' AS NUMERIC)",
)


def _database_target(url: URL) -> tuple[str, int, str]:
    try:
        if not url.host or not url.database:
            raise ValueError
        return (url.host.lower().rstrip("."), url.port or 5432, url.database)
    except (TypeError, ValueError):
        pytest.fail("PostgreSQL database target is not safely comparable", pytrace=False)


def _configured_database_urls() -> tuple[tuple[URL, bool], ...]:
    """Resolve runtime targets without loading unrelated exchange configuration."""
    try:
        settings = _RuntimeDatabaseSources()
        resolved: list[tuple[URL, bool]] = []
        for value in (settings.runtime_url, settings.migration_url):
            if value is None:
                continue
            parsed = make_url(value.get_secret_value())
            if parsed.get_backend_name() in {"postgres", "postgresql"}:
                _database_target(parsed)
                resolved.append((parsed, parsed.drivername == "postgresql+asyncpg"))
            else:
                resolved.append((database_url(value), True))
        return tuple(resolved)
    except Exception:
        pytest.fail("Runtime database configuration is not safely comparable", pytrace=False)


def _acceptance_url() -> URL:
    required_setting = os.environ.get(REQUIRED_ENV, "false").lower()
    if required_setting not in {"true", "false"}:
        pytest.fail(f"{REQUIRED_ENV} must be true or false", pytrace=False)
    raw_url = os.environ.get(URL_ENV)
    if not raw_url:
        if required_setting == "true":
            pytest.fail("Disposable PostgreSQL Decimal acceptance URL is required", pytrace=False)
        pytest.skip("Disposable PostgreSQL Decimal acceptance is not configured")
    if os.environ.get(DISPOSABLE_ENV, "false").lower() != "true":
        pytest.fail(
            "PostgreSQL Decimal acceptance database is not confirmed disposable", pytrace=False
        )
    try:
        url = make_url(raw_url)
    except Exception:
        pytest.fail("PostgreSQL Decimal acceptance URL is invalid", pytrace=False)
    if url.drivername != "postgresql+asyncpg":
        pytest.fail("PostgreSQL Decimal acceptance requires postgresql+asyncpg", pytrace=False)
    target = _database_target(url)
    runtime_urls = _configured_database_urls()
    for runtime_url, _ in runtime_urls:
        if (
            runtime_url.get_backend_name() in {"postgres", "postgresql"}
            and _database_target(runtime_url) == target
        ):
            pytest.fail(
                "PostgreSQL Decimal acceptance must not use a runtime database target",
                pytrace=False,
            )
    if any(not supported for _, supported in runtime_urls):
        pytest.fail("Runtime database configuration is not safely comparable", pytrace=False)
    return url


def _probe_table() -> Table:
    probe_metadata = MetaData(schema=APP_SCHEMA, naming_convention=NAMING_CONVENTION)
    amount_type = ExactDecimal(38, 18)
    positive_type = ExactDecimal(8, 4)
    nonnegative_type = ExactDecimal(6, 3)
    return Table(
        "decimal_acceptance_probe",
        probe_metadata,
        Column("id", Integer, primary_key=True),
        Column("amount", amount_type),
        Column("positive_amount", positive_type, nullable=False),
        Column("nonnegative_amount", nonnegative_type, nullable=False),
        *exact_decimal_checks("amount", amount_type),
        *exact_decimal_checks("positive_amount", positive_type),
        *exact_decimal_checks("nonnegative_amount", nonnegative_type),
        CheckConstraint(column("positive_amount") > 0, name="positive_amount_positive"),
        CheckConstraint(column("nonnegative_amount") >= 0, name="nonnegative_amount_nonnegative"),
    )


def _qualified(connection: AsyncConnection, schema: str, table: str) -> str:
    preparer = connection.dialect.identifier_preparer
    return f"{preparer.quote_schema(schema)}.{preparer.quote(table)}"


async def _assert_integrity_rejected(connection: AsyncConnection, statement: str) -> None:
    savepoint = await connection.begin_nested()
    try:
        with pytest.raises(IntegrityError):
            await connection.exec_driver_sql(statement)
    finally:
        if savepoint.is_active:
            await savepoint.rollback()


def _decimal_columns() -> set[tuple[str, str]]:
    return {
        (table.name, field.name)
        for table in metadata.tables.values()
        for field in table.c
        if isinstance(field.type, ExactDecimal)
    }


def _decimal_constraint_names(
    connection: AsyncConnection, tables: tuple[Table, ...]
) -> set[tuple[str, str]]:
    expected = set()
    for table in tables:
        for constraint in table.constraints:
            if isinstance(constraint, CheckConstraint) and "_decimal_" in str(constraint.name):
                expected.add(
                    (
                        table.name,
                        connection.dialect.identifier_preparer.format_constraint(constraint),
                    )
                )
    return expected


def test_required_postgres_acceptance_fails_without_disposable_url(monkeypatch):
    monkeypatch.setenv(REQUIRED_ENV, "true")
    monkeypatch.delenv(URL_ENV, raising=False)
    monkeypatch.delenv(DISPOSABLE_ENV, raising=False)
    with pytest.raises(pytest.fail.Exception, match="acceptance URL is required"):
        _acceptance_url()


@pytest.mark.parametrize("runtime_variable", RUNTIME_URL_ENVS)
def test_postgres_acceptance_rejects_equivalent_runtime_target(monkeypatch, runtime_variable):
    monkeypatch.setenv(
        URL_ENV,
        "postgresql+asyncpg://acceptance@DB.EXAMPLE:5432/hmt_test?ssl=require",
    )
    monkeypatch.setenv(DISPOSABLE_ENV, "true")
    monkeypatch.setenv(runtime_variable, "postgresql://runtime@db.example/hmt_test?role=app")
    for other in set(RUNTIME_URL_ENVS) - {runtime_variable}:
        monkeypatch.delenv(other, raising=False)
    with pytest.raises(pytest.fail.Exception, match="runtime database target"):
        _acceptance_url()


@pytest.mark.parametrize("runtime_variable", ("database_url", "DaTaBaSe_MiGrAtIoN_Url"))
def test_postgres_acceptance_rejects_case_insensitive_runtime_target(
    monkeypatch: pytest.MonkeyPatch, runtime_variable: str
) -> None:
    monkeypatch.setenv(
        URL_ENV,
        "postgresql+asyncpg://acceptance@DB.EXAMPLE:5432/hmt_test?ssl=require",
    )
    monkeypatch.setenv(DISPOSABLE_ENV, "true")
    for name in tuple(os.environ):
        if name.lower() in {value.lower() for value in RUNTIME_URL_ENVS}:
            monkeypatch.delenv(name)
    monkeypatch.setenv(runtime_variable, "postgresql+asyncpg://runtime@db.example/hmt_test")
    with pytest.raises(pytest.fail.Exception, match="runtime database target"):
        _acceptance_url()


@pytest.mark.parametrize("runtime_variable", RUNTIME_URL_ENVS)
def test_postgres_acceptance_rejects_dotenv_only_runtime_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, runtime_variable: str
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        f"{runtime_variable}=postgresql+asyncpg://runtime@db.example/hmt_test\n"
    )
    monkeypatch.setenv(
        URL_ENV,
        "postgresql+asyncpg://acceptance@DB.EXAMPLE:5432/hmt_test?ssl=require",
    )
    monkeypatch.setenv(DISPOSABLE_ENV, "true")
    for name in tuple(os.environ):
        if name.lower() in {value.lower() for value in RUNTIME_URL_ENVS}:
            monkeypatch.delenv(name)
    with pytest.raises(pytest.fail.Exception, match="runtime database target"):
        _acceptance_url()


@pytest.mark.parametrize("runtime_variable", ("database_url", "DaTaBaSe_MiGrAtIoN_Url"))
def test_postgres_acceptance_fails_closed_for_postgres_runtime_alias(
    monkeypatch: pytest.MonkeyPatch, runtime_variable: str
) -> None:
    monkeypatch.setenv(
        URL_ENV,
        "postgresql+asyncpg://acceptance@DB.EXAMPLE:5432/hmt_test?ssl=require",
    )
    monkeypatch.setenv(DISPOSABLE_ENV, "true")
    for name in tuple(os.environ):
        if name.lower() in {value.lower() for value in RUNTIME_URL_ENVS}:
            monkeypatch.delenv(name)
    monkeypatch.setenv(runtime_variable, "postgres://runtime@db.example/hmt_test")
    with pytest.raises(
        pytest.fail.Exception,
        match="runtime database target",
    ):
        _acceptance_url()


def test_postgres_acceptance_fails_closed_without_leaking_invalid_runtime_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_value = "private-invalid-runtime-dsn"
    monkeypatch.setenv(
        URL_ENV,
        "postgresql+asyncpg://acceptance@db.example:5432/hmt_test",
    )
    monkeypatch.setenv(DISPOSABLE_ENV, "true")
    monkeypatch.setenv("database_url", private_value)
    with pytest.raises(pytest.fail.Exception) as error:
        _acceptance_url()
    assert str(error.value) == "Runtime database configuration is not safely comparable"
    assert private_value not in str(error.value)


@pytest.mark.asyncio
async def test_raw_postgres_decimal_boundary_and_catalogue_contract():
    url = _acceptance_url()
    engine = create_async_engine(
        url,
        echo=False,
        hide_parameters=True,
        pool_size=1,
        max_overflow=0,
    )
    schema = f"hmt_decimal_acceptance_{uuid4().hex}"
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(CreateSchema(schema))
                translated = await connection.execution_options(
                    schema_translate_map={APP_SCHEMA: schema}
                )
                await translated.run_sync(metadata.create_all)
                probe = _probe_table()
                await translated.run_sync(probe.create)

                probe_name = _qualified(translated, schema, probe.name)
                for row_id, value in enumerate(VALID_38_18, start=1):
                    await translated.exec_driver_sql(
                        f"INSERT INTO {probe_name} "
                        "(id, amount, positive_amount, nonnegative_amount) "
                        f"VALUES ({row_id}, {value}, 0.0001, 0)"
                    )
                await translated.exec_driver_sql(
                    f"INSERT INTO {probe_name} "
                    "(id, amount, positive_amount, nonnegative_amount) "
                    "VALUES (90, NULL, 1, 0)"
                )
                assert await translated.scalar(text(f"SELECT count(*) FROM {probe_name}")) == 5

                for row_id, value in enumerate(INVALID_38_18, start=100):
                    await _assert_integrity_rejected(
                        translated,
                        f"INSERT INTO {probe_name} "
                        "(id, amount, positive_amount, nonnegative_amount) "
                        f"VALUES ({row_id}, {value}, 1, 0)",
                    )
                assert await translated.scalar(text(f"SELECT count(*) FROM {probe_name}")) == 5

                await translated.exec_driver_sql(
                    f"INSERT INTO {probe_name} "
                    "(id, amount, positive_amount, nonnegative_amount) "
                    "VALUES (91, 1, 1, 0)"
                )
                for value in INVALID_38_18:
                    await _assert_integrity_rejected(
                        translated,
                        f"UPDATE {probe_name} SET amount = {value} WHERE id = 91",
                    )
                assert await translated.scalar(
                    text(f"SELECT amount = 1 FROM {probe_name} WHERE id = 91")
                )
                await _assert_integrity_rejected(
                    translated,
                    f"INSERT INTO {probe_name} (id, amount, nonnegative_amount) VALUES (92, 1, 0)",
                )
                await _assert_integrity_rejected(
                    translated,
                    f"INSERT INTO {probe_name} "
                    "(id, amount, positive_amount, nonnegative_amount) "
                    "VALUES (92, 1, 0, 0)",
                )
                await _assert_integrity_rejected(
                    translated,
                    f"INSERT INTO {probe_name} "
                    "(id, amount, positive_amount, nonnegative_amount) "
                    "VALUES (92, 1, 1, -0.001)",
                )

                account_id = "00000000-0000-4000-8000-000000000001"
                account_name = _qualified(translated, schema, "accounts")
                await translated.exec_driver_sql(
                    f"INSERT INTO {account_name} "
                    "(id, region, environment, mode, profile_name, version, "
                    "created_at, updated_at) "
                    f"VALUES ('{account_id}', 'india', 'testnet', 'PAPER', 'decimal-acceptance', "
                    "1, TIMESTAMPTZ '2026-11-04 00:00:00+00', "
                    "TIMESTAMPTZ '2026-11-04 00:00:00+00')"
                )
                fill_name = _qualified(translated, schema, "fills")
                valid_fill_id = "00000000-0000-4000-8000-000000000002"
                fill_columns = (
                    "id, account_id, region, environment, mode, exchange_fill_id, product_id, "
                    "size, price, side, fill_type, source, requested_at, received_at, recorded_at, "
                    "evidence_schema_version, evidence_payload, evidence_sha256"
                )
                fill_tail = (
                    f"'{account_id}', 'india', 'testnet', 'PAPER', 'accepted-fill', 1101, "
                    "99999999999999999999.999999999999999999, "
                    "1.2300000000000000000, 'buy', 'normal', 'VENUE', "
                    "TIMESTAMPTZ '2026-11-04 00:00:00+00', "
                    "TIMESTAMPTZ '2026-11-04 00:00:00+00', "
                    "TIMESTAMPTZ '2026-11-04 00:00:00+00', 1, '{}', "
                    f"'{'a' * 64}'"
                )
                await translated.exec_driver_sql(
                    f"INSERT INTO {fill_name} ({fill_columns}) "
                    f"VALUES ('{valid_fill_id}', {fill_tail})"
                )
                for value in INVALID_38_18:
                    await _assert_integrity_rejected(
                        translated,
                        f"UPDATE {fill_name} SET price = {value} WHERE id = '{valid_fill_id}'",
                    )
                assert await translated.scalar(
                    text(f"SELECT price = 1.23 FROM {fill_name} WHERE id = '{valid_fill_id}'")
                )
                await translated.exec_driver_sql(
                    f"UPDATE {fill_name} SET price = NULL WHERE id = '{valid_fill_id}'"
                )
                assert await translated.scalar(
                    text(f"SELECT price IS NULL FROM {fill_name} WHERE id = '{valid_fill_id}'")
                )

                invalid_fill_id = "00000000-0000-4000-8000-000000000003"
                for value in INVALID_38_18:
                    invalid_tail = fill_tail.replace("'accepted-fill'", "'rejected-fill'").replace(
                        "1.2300000000000000000", value
                    )
                    await _assert_integrity_rejected(
                        translated,
                        f"INSERT INTO {fill_name} ({fill_columns}) "
                        f"VALUES ('{invalid_fill_id}', {invalid_tail})",
                    )
                null_size_tail = fill_tail.replace("'accepted-fill'", "'null-size-fill'").replace(
                    "99999999999999999999.999999999999999999", "NULL"
                )
                await _assert_integrity_rejected(
                    translated,
                    f"INSERT INTO {fill_name} ({fill_columns}) "
                    f"VALUES ('{invalid_fill_id}', {null_size_tail})",
                )
                assert await translated.scalar(text(f"SELECT count(*) = 1 FROM {fill_name}"))

                numeric_rows = (
                    await translated.execute(
                        text(
                            "SELECT cls.relname, att.attname, att.atttypmod, "
                            "pg_catalog.format_type(att.atttypid, att.atttypmod) "
                            "FROM pg_catalog.pg_attribute AS att "
                            "JOIN pg_catalog.pg_class AS cls ON cls.oid = att.attrelid "
                            "JOIN pg_catalog.pg_namespace AS ns ON ns.oid = cls.relnamespace "
                            "JOIN pg_catalog.pg_type AS typ ON typ.oid = att.atttypid "
                            "WHERE ns.nspname = :schema AND typ.typname = 'numeric' "
                            "AND att.attnum > 0 AND NOT att.attisdropped"
                        ),
                        {"schema": schema},
                    )
                ).all()
                expected_numeric = _decimal_columns() | {
                    (probe.name, "amount"),
                    (probe.name, "positive_amount"),
                    (probe.name, "nonnegative_amount"),
                }
                assert {(row[0], row[1]) for row in numeric_rows} == expected_numeric
                assert all(row[2] == -1 and row[3] == "numeric" for row in numeric_rows)

                constraint_rows = (
                    await translated.execute(
                        text(
                            "SELECT cls.relname, con.conname, con.convalidated "
                            "FROM pg_catalog.pg_constraint AS con "
                            "JOIN pg_catalog.pg_class AS cls ON cls.oid = con.conrelid "
                            "JOIN pg_catalog.pg_namespace AS ns ON ns.oid = cls.relnamespace "
                            "WHERE ns.nspname = :schema AND con.contype = 'c'"
                        ),
                        {"schema": schema},
                    )
                ).all()
                actual_constraints = {(row[0], row[1]): row[2] for row in constraint_rows}
                expected_constraints = _decimal_constraint_names(
                    translated, (*metadata.tables.values(), probe)
                )
                assert len(expected_constraints) == 102
                assert expected_constraints <= set(actual_constraints)
                assert all(actual_constraints[name] for name in expected_constraints)
            finally:
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await engine.dispose()
