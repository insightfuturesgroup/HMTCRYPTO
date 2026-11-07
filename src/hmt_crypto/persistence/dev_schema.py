"""Explicit SQLite development-schema initialization; never a production migration."""

from __future__ import annotations

from hmt_crypto.config import ExecutionMode, Settings
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.persistence.configuration import database_url
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.models import metadata


async def initialize_development_schema(settings: Settings) -> int:
    """Create the current schema only for single-process SQLite PAPER/BACKTEST."""
    if (
        type(settings) is not Settings
        or settings.execution_mode is ExecutionMode.LIVE
        or not settings.database_single_process
        or database_url(settings.database_url).drivername != "sqlite+aiosqlite"
    ):
        raise ConfigurationError(context={"operation": "development_schema"}) from None
    database = Database(settings)
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        return len(metadata.tables)
    finally:
        await database.aclose()


__all__ = ["initialize_development_schema"]
