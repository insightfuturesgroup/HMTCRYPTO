"""Secret-safe URL parsing and exchange-credential-free database projection."""

from dataclasses import dataclass
from typing import Literal

from pydantic import SecretStr
from sqlalchemy.engine import URL, make_url

DatabaseExecutionMode = Literal["BACKTEST", "PAPER", "LIVE"]


@dataclass(frozen=True, slots=True)
class DatabaseRuntimeConfig:
    """Minimum exchange-credential-free configuration accepted by the database owner."""

    execution_mode: DatabaseExecutionMode
    database_url: SecretStr
    database_single_process: bool
    database_ca_file: str | None = None

    def __post_init__(self) -> None:
        mode: object = self.execution_mode
        url_value: object = self.database_url
        single_process: object = self.database_single_process
        ca_file: object = self.database_ca_file
        if (
            type(mode) is not str
            or mode not in {"BACKTEST", "PAPER", "LIVE"}
            or type(url_value) is not SecretStr
            or type(single_process) is not bool
            or (ca_file is not None and (type(ca_file) is not str or not ca_file))
        ):
            raise ValueError("invalid database runtime configuration") from None
        database_url(url_value)


def database_url(value: SecretStr) -> URL:
    """Parse only the supported async dialects without exposing invalid input.

    SQLite requires a file: query/shared-cache modes and connection-local memory
    databases cannot provide our separate-session isolation. PostgreSQL URLs must
    identify an actual host/database; endpoint and
    direct-versus-session-pooler selection remain deployment responsibilities.
    """
    try:
        if type(value) is not SecretStr:
            raise ValueError
        url = make_url(value.get_secret_value())
        if url.drivername == "sqlite+aiosqlite":
            valid = not (url.host or url.username or url.password or url.port or url.query)
            valid = valid and bool(url.database) and url.database != ":memory:"
        elif url.drivername == "postgresql+asyncpg":
            valid = bool(url.host and url.database)
            valid = valid and (url.port is None or 1 <= url.port <= 65535)
        else:
            valid = False
        if not valid:
            raise ValueError
        return url
    except Exception:
        raise ValueError("Database URL must use a supported explicit async dialect") from None


__all__ = ["DatabaseExecutionMode", "DatabaseRuntimeConfig", "database_url"]
