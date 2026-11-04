"""Secret-safe URL parsing shared by settings and database construction."""

from pydantic import SecretStr
from sqlalchemy.engine import URL, make_url


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
