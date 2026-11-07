"""Fail-closed owner connection admission for explicit Alembic commands.

This module does not read application settings or dotenv files.  Migrations are
an operator action and accept exactly ``DATABASE_MIGRATION_URL`` from the process
environment.  The returned URL must never be logged or included in an error.
"""

from __future__ import annotations

import os
import ssl
from typing import Any

from sqlalchemy.engine import URL, make_url

_ERROR_MESSAGE = "Database migration connection is not safely configured"
_IDENTITY_QUERY_KEYS = frozenset({"database", "host", "password", "port", "user"})

DatabaseIdentity = tuple[str, str, int, str]
DatabaseTarget = tuple[str, ...]


class MigrationConfigurationError(RuntimeError):
    """A stable, secret-free migration configuration failure."""

    def __init__(self) -> None:
        super().__init__(_ERROR_MESSAGE)


def _parse(raw: str) -> URL:
    result: URL | None = None
    try:
        if not raw or raw != raw.strip() or any(character in raw for character in "\r\n\x00"):
            raise ValueError
        result = make_url(raw)
    except BaseException:
        pass
    if result is None:
        del raw
        raise MigrationConfigurationError() from None
    return result


def _normalized_parts(
    username: object,
    host: object,
    port: object,
    database: object,
) -> DatabaseIdentity:
    try:
        if type(username) is not str or type(host) is not str or type(database) is not str:
            raise ValueError
        normalized_host = host.lower().rstrip(".")
        normalized_username = username.lower()
        normalized_port = 5432 if port is None else port
        if (
            type(normalized_port) is not int
            or not normalized_username
            or not normalized_host
            or not database
            or not 1 <= normalized_port <= 65535
        ):
            raise ValueError
        return normalized_username, normalized_host, normalized_port, database
    except (TypeError, ValueError):
        raise MigrationConfigurationError() from None


def _normalized_identity(url: URL) -> DatabaseIdentity:
    return _normalized_parts(url.username, url.host, url.port, url.database)


def _resolved_asyncpg_identity(url: URL) -> DatabaseIdentity:
    """Resolve exactly what SQLAlchemy's asyncpg dialect would pass to the driver."""

    if url.drivername != "postgresql+asyncpg" or url.password in (None, ""):
        raise MigrationConfigurationError()
    authority = _normalized_identity(url)
    dialect_type = url.get_dialect(_is_async=True)
    positional, arguments = dialect_type().create_connect_args(url)
    if positional or not isinstance(arguments, dict):
        raise MigrationConfigurationError()
    if _IDENTITY_QUERY_KEYS.intersection(url.query):
        raise MigrationConfigurationError()
    if type(arguments.get("password")) is not str or not arguments["password"]:
        raise MigrationConfigurationError()
    effective = _normalized_parts(
        arguments.get("user"),
        arguments.get("host"),
        arguments.get("port"),
        arguments.get("database"),
    )
    if effective != authority:
        raise MigrationConfigurationError()
    return effective


def _validate_owner_url(url: URL) -> None:
    if url.drivername != "postgresql+asyncpg" or url.password in (None, ""):
        raise MigrationConfigurationError()
    _, host, port, _ = _normalized_identity(url)
    if port == 6543 or host.endswith(".pooler.supabase.com"):
        raise MigrationConfigurationError()
    if url.query:
        raise MigrationConfigurationError()


def _runtime_identity() -> DatabaseIdentity:
    names = [name for name in os.environ if name.upper() == "DATABASE_URL"]
    if len(names) != 1:
        raise MigrationConfigurationError()
    raw = os.environ[names[0]]
    runtime = _parse(raw)
    return _resolved_asyncpg_identity(runtime)


def _role_name(identity: DatabaseIdentity) -> str:
    username, host, _, _ = identity
    if host.endswith(".pooler.supabase.com") and "." in username:
        return username.rsplit(".", maxsplit=1)[0]
    return username


def _supabase_project(identity: DatabaseIdentity) -> str | None:
    username, host, _, _ = identity
    direct_prefix = "db."
    direct_suffix = ".supabase.co"
    if host.startswith(direct_prefix) and host.endswith(direct_suffix):
        project = host[len(direct_prefix) : -len(direct_suffix)]
        if not project or "." in project:
            raise MigrationConfigurationError()
        return project
    if host.endswith(".pooler.supabase.com"):
        if host == "pooler.supabase.com" or "." not in username:
            raise MigrationConfigurationError()
        project = username.rsplit(".", maxsplit=1)[1]
        if not project or "." in project:
            raise MigrationConfigurationError()
        return project
    return None


def _database_target(identity: DatabaseIdentity) -> DatabaseTarget:
    project = _supabase_project(identity)
    if project is not None:
        return "supabase", project
    _, host, port, database = identity
    return "postgresql", host, str(port), database


def _ca_file() -> str | None:
    names = [name for name in os.environ if name.upper() == "DATABASE_MIGRATION_CA_FILE"]
    if not names:
        return None
    if names != ["DATABASE_MIGRATION_CA_FILE"]:
        raise MigrationConfigurationError()
    value = os.environ["DATABASE_MIGRATION_CA_FILE"]
    if not value or value != value.strip() or any(character in value for character in "\r\n\x00"):
        raise MigrationConfigurationError()
    return value


def _load_migration_url() -> URL:
    names = [name for name in os.environ if name.upper() == "DATABASE_MIGRATION_URL"]
    if names != ["DATABASE_MIGRATION_URL"]:
        raise MigrationConfigurationError()
    raw = os.environ["DATABASE_MIGRATION_URL"]
    migration = _parse(raw)
    _validate_owner_url(migration)
    runtime_identity = _runtime_identity()
    migration_identity = _normalized_identity(migration)
    if (
        runtime_identity[3] != migration_identity[3]
        or _database_target(migration_identity) != _database_target(runtime_identity)
        or _role_name(runtime_identity) == _role_name(migration_identity)
    ):
        raise MigrationConfigurationError()
    return migration


def load_migration_url() -> URL:
    """Load the explicit direct owner URL without retaining rejected secrets."""

    result: URL | None = None
    try:
        result = _load_migration_url()
    except BaseException:
        pass
    if result is None:
        raise MigrationConfigurationError() from None
    return result


def comparable_database_target(url: URL) -> DatabaseTarget:
    """Return a secret-free effective target key for destructive-test admission.

    Supabase direct and pooler URLs for one project intentionally collapse to
    the same key regardless of database spelling; a disposable acceptance run
    must never operate anywhere inside a configured project.
    """

    result: DatabaseTarget | None = None
    try:
        result = _database_target(_resolved_asyncpg_identity(url))
    except BaseException:
        pass
    if result is None:
        del url
        raise MigrationConfigurationError() from None
    return result


def _offline_migration_url(url: URL) -> URL:
    _validate_owner_url(url)
    return URL.create(
        "postgresql",
        username=url.username,
        host=url.host,
        port=url.port,
        database=url.database,
    )


def offline_migration_url(url: URL) -> URL:
    """Return the synchronous PostgreSQL dialect URL used for offline SQL.

    Non-secret endpoint components are copied through SQLAlchemy's URL API;
    the password is deliberately discarded and escaped delimiters are never
    manipulated as text.
    """

    result: URL | None = None
    try:
        result = _offline_migration_url(url)
    except BaseException:
        pass
    if result is None:
        del url
        raise MigrationConfigurationError() from None
    return result


def _migration_connect_args(url: URL) -> dict[str, Any]:
    _validate_owner_url(url)
    tls = ssl.create_default_context(cafile=_ca_file())
    return {"server_settings": {"timezone": "UTC"}, "ssl": tls}


def migration_connect_args(url: URL) -> dict[str, Any]:
    """Build asyncpg-native arguments with mandatory peer and hostname verification."""

    result: dict[str, Any] | None = None
    try:
        result = _migration_connect_args(url)
    except BaseException:
        pass
    if result is None:
        del url
        raise MigrationConfigurationError() from None
    return result


__all__ = [
    "MigrationConfigurationError",
    "comparable_database_target",
    "load_migration_url",
    "migration_connect_args",
    "offline_migration_url",
]
