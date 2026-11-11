"""Alembic environment for explicit owner-run PostgreSQL migrations only."""

from __future__ import annotations

import asyncio
import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from hmt_crypto.persistence.migration_configuration import (
    MigrationConfigurationError,
    load_migration_url,
    migration_connect_args,
    offline_migration_url,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _safe_sqlstate(error: BaseException) -> str | None:
    """Extract only a standards-bounded SQLSTATE; never retain server diagnostics."""

    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        candidate = getattr(current, "sqlstate", None)
        if isinstance(candidate, str) and re.fullmatch(r"[0-9A-Z]{5}", candidate):
            return candidate
        candidate = getattr(current, "pgcode", None)
        if isinstance(candidate, str) and re.fullmatch(r"[0-9A-Z]{5}", candidate):
            return candidate
        current = current.__cause__ or current.__context__
    return None


def run_migrations_offline() -> None:
    """Render PostgreSQL SQL without connecting or exposing the owner DSN."""

    url = None
    configuration_failed = False
    rendering_failed = False
    try:
        url = offline_migration_url(load_migration_url())
        context.configure(
            url=url,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            version_table="hmt_crypto_alembic_version",
            version_table_schema="public",
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    except MigrationConfigurationError:
        configuration_failed = True
    except BaseException:
        rendering_failed = True
    url = None
    if configuration_failed:
        raise MigrationConfigurationError() from None
    if rendering_failed:
        message = "Offline migration rendering failed without exposing diagnostics"
        raise RuntimeError(message) from None


def _run_on_connection(connection: Connection) -> None:
    context.configure(
        connection=connection,
        version_table="hmt_crypto_alembic_version",
        version_table_schema="public",
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = None
    connection = None
    configuration_failed = False
    migration_failed = False
    failure_sqlstate = None
    url = None
    try:
        url = load_migration_url()
        engine = create_async_engine(
            url,
            echo=False,
            hide_parameters=True,
            poolclass=NullPool,
            connect_args=migration_connect_args(url),
        )
        async with engine.connect() as connection:
            await connection.run_sync(_run_on_connection)
    except MigrationConfigurationError:
        configuration_failed = True
    except BaseException as error:
        migration_failed = True
        failure_sqlstate = _safe_sqlstate(error)
    finally:
        connection = None
        if engine is not None:
            try:
                await engine.dispose()
            except BaseException:
                migration_failed = True
    engine = None
    url = None
    if configuration_failed:
        raise MigrationConfigurationError() from None
    if migration_failed:
        message = "Database migration failed without exposing server diagnostics"
        if failure_sqlstate is not None:
            message = f"{message} (SQLSTATE {failure_sqlstate})"
        raise RuntimeError(message) from None


def run_migrations_online() -> None:
    """Run migrations through asyncpg without storing the DSN in Alembic config."""

    asyncio.run(_run_online())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
