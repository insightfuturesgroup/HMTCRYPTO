"""Lazy async database ownership; no connections, files or engines at import.

Application code uses one ``transaction()`` per concurrent task and never shares
its session. Schema setup/migrations are explicit and are never run at startup.
SQLite is development-only, single-process PAPER/BACKTEST, with separate pooled
connections. PostgreSQL pooler/TLS/role suitability requires deployment evidence.
"""

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Any

from pydantic import ValidationError
from sqlalchemy import MetaData, event
from sqlalchemy.engine import Connection, ExceptionContext
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from hmt_crypto.config import ExecutionMode, Settings
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.persistence.configuration import database_url
from hmt_crypto.persistence.errors import PersistenceError

APP_SCHEMA = "hmt_crypto"
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Application-only ORM metadata shared with the future Alembic history."""

    metadata = MetaData(schema=APP_SCHEMA, naming_convention=NAMING_CONVENTION)


def _sqlite_connect(dbapi_connection: Any, connection_record: Any) -> None:
    # Explicit BEGIN avoids sqlite3's legacy nontransactional SELECT/DDL behavior
    # on Python 3.11 as well as later Python versions. Enable FK on EVERY connection.
    dbapi_connection.isolation_level = None
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def _sqlite_begin(connection: Connection) -> None:
    connection.exec_driver_sql("BEGIN")


def _safe_driver_error(context: ExceptionContext) -> DBAPIError | None:
    # SQLAlchemy classifies disconnects before this listener, then unconditionally
    # raises any replacement "from" the original driver error. A safe wrapper
    # alone therefore leaves raw DETAIL/messages reachable in formatted chains.
    original = context.original_exception
    if (
        context.dialect.driver not in {"asyncpg", "aiosqlite"}
        or not isinstance(original, Exception)
        or isinstance(original, PersistenceError)
    ):
        return None
    error = context.sqlalchemy_exception
    kind = type(error) if isinstance(error, DBAPIError) else DBAPIError
    replacement = kind(
        None,
        None,
        PersistenceError(),
        hide_parameters=True,
        connection_invalidated=context.is_disconnect,
    )
    # Both supported adapters expose ordinary mutable exception diagnostics.
    # Discard fields/notes and detach *all* nested chains before SQLAlchemy can
    # attach this object as the replacement's cause. Never render the raw error.
    original.args = ("Database operation failed",)
    vars(original).clear()
    original.__cause__ = None
    original.__context__ = None
    original.__suppress_context__ = True
    original.__traceback__ = None
    return replacement


async def _complete_cleanup(task: asyncio.Task[None]) -> None:
    """Retain ownership until cleanup finishes, then propagate caller cancellation."""
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            if task.cancelled():
                raise
            cancellation = error
        except Exception:
            if cancellation is not None:
                raise cancellation from None
            raise
        else:
            if cancellation is not None:
                raise cancellation from None
            return


class Database:
    """Own a lazy engine, task-local transactions and explicit asynchronous disposal.

    Construct explicitly after loading settings. This validates backend policy
    before creating the engine; the first actual operation opens the database.
    No migration URL is used for runtime connections. Never log a raw SQLAlchemy
    URL, driver exception or literal SQL containing secrets.
    """

    def __init__(self, settings: Settings) -> None:
        try:
            if type(settings) is not Settings:
                raise ValueError
            # Revalidate even model_copy/model_construct inputs before admission.
            owned = Settings.model_validate(settings.model_dump())
            url = database_url(owned.database_url)
        except (ValidationError, ValueError):
            raise ConfigurationError() from None
        if url.drivername == "sqlite+aiosqlite" and (
            owned.execution_mode not in (ExecutionMode.PAPER, ExecutionMode.BACKTEST)
            or not owned.database_single_process
        ):
            raise ConfigurationError()
        options: dict[str, Any] = {
            "echo": False,
            "hide_parameters": True,
            "pool_size": 5,
            "max_overflow": 0,
        }
        if url.drivername == "sqlite+aiosqlite":
            options["execution_options"] = {"schema_translate_map": {APP_SCHEMA: None}}
        else:
            options["connect_args"] = {"server_settings": {"timezone": "UTC"}}
        try:
            self._engine = create_async_engine(url, **options)
        except Exception:
            raise ConfigurationError() from None
        event.listen(self._engine.sync_engine, "handle_error", _safe_driver_error, retval=True)
        if url.drivername == "sqlite+aiosqlite":
            event.listen(self._engine.sync_engine, "connect", _sqlite_connect)
            event.listen(self._engine.sync_engine, "begin", _sqlite_begin)
        self._sessions = async_sessionmaker(
            self._engine, expire_on_commit=False, close_resets_only=False
        )
        self._active = 0
        self._closed = False
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None

    @property
    def engine(self) -> AsyncEngine:
        """Expose the engine for explicit schema tooling; callers own those connections.

        Runtime work must use ``transaction()`` so sessions are tracked and closed.
        External schema-tool connections must be drained before owner disposal.
        """
        if self._closing:
            raise PersistenceError()
        return self._engine

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """Own one session/transaction: commit success, roll back failure/cancellation.

        Database errors become stable safe errors. Caller/domain exceptions and
        cancellation retain their type. The yielded session cannot be reopened
        after exit; use a fresh transaction for subsequent/concurrent work.
        Caller cancellation waits for owned rollback/commit and session closure;
        disposal remains blocked throughout cleanup, independent of SQLAlchemy's
        context-manager cancellation behavior.
        """
        if self._closing:
            raise PersistenceError()
        self._active += 1
        try:
            session = self._sessions()
            transaction = session.begin()
            entered = False
            exit_error: tuple[
                type[BaseException] | None, BaseException | None, TracebackType | None
            ] = (None, None, None)
            try:
                await transaction.__aenter__()
                entered = True
                yield session
            except BaseException:
                # Only exceptions from this entry/body can request rollback.
                # An outer except handler may leave an unrelated exc_info active.
                exit_error = sys.exc_info()
                raise
            finally:

                async def finish() -> None:
                    try:
                        if entered:
                            await transaction.__aexit__(*exit_error)
                    finally:
                        await session.close()

                # Own the task and await its actual completion. A shield alone
                # can return cancellation while the protected task still runs.
                await _complete_cleanup(asyncio.create_task(finish()))
        except (SQLAlchemyError, OSError):
            raise PersistenceError() from None
        finally:
            self._active -= 1

    async def aclose(self) -> None:
        """Dispose idempotently after transactions drain; permit cleanup retry.

        An active transaction prevents disposal. While closing, admission is
        denied. Shield disposal from caller cancellation and allow a later call
        to await that same cleanup task.
        """
        if self._closed:
            return
        if self._active:
            raise PersistenceError()
        self._closing = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._engine.dispose())
        try:
            await asyncio.shield(self._close_task)
        except Exception:
            self._close_task = None
            raise PersistenceError() from None
        self._closed = True
