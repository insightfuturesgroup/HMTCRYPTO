"""Interactive owner migration helper; never prints exception messages or URLs."""

import getpass
import os
import re
import ssl
import subprocess
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL

from hmt_crypto.api.settings import ControlRuntimeSettings
from hmt_crypto.persistence.migration_configuration import MigrationConfigurationError


def build_environment(
    ca: Path, pooler: str, owner_password: str, runtime_password: str
) -> dict[str, str]:
    project = "pwnbqwzwfmzlddhndgtm"
    return {
        "DATABASE_MIGRATION_URL": URL.create(
            "postgresql+asyncpg",
            username="postgres",
            password=owner_password,
            host=f"db.{project}.supabase.co",
            port=5432,
            database="postgres",
        ).render_as_string(hide_password=False),
        "DATABASE_URL": URL.create(
            "postgresql+asyncpg",
            username=f"hmt_crypto_app.{project}",
            password=runtime_password,
            host=pooler,
            port=5432,
            database="postgres",
        ).render_as_string(hide_password=False),
        "DATABASE_MIGRATION_CA_FILE": str(ca),
        "DATABASE_CA_FILE": str(ca),
        "DATABASE_SINGLE_PROCESS": "false",
        "EXECUTION_MODE": "LIVE",
        "DELTA_REGION": "india",
        "DELTA_ENV": "prod",
        "PRODUCTION_WRITES_FORBIDDEN": "true",
        "HMT_LIVE_ORDER_DISPATCH_ENABLED": "false",
        "HMT_PRIVATE_READ_ONLY": "true",
    }


def failure_diagnostic(error: Exception) -> str:
    # Class names are bounded; exception messages are never included in output.
    name = type(error).__name__
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
        name = "Exception"
    if isinstance(error, MigrationConfigurationError):
        hint = (
            "guard admission: verify direct owner, distinct runtime role, "
            "target and CA configuration"
        )
    elif isinstance(error, ssl.SSLError):
        hint = "TLS: verify project CA, certificate hostname and clock; do not bypass verification"
    elif isinstance(error, OSError):
        hint = "connection: verify DNS, IPv6/direct IPv4 route and port 5432"
    elif re.fullmatch(
        r"Database migration failed without exposing server diagnostics \(SQLSTATE 28[A-Z0-9]{3}\)",
        str(error),
    ):
        hint = "authentication: verify owner credentials and login permission"
    else:
        # env.py intentionally suppresses driver details; do not undo that boundary.
        hint = (
            "details redacted: check connection/TLS/auth, role grants and migration prerequisites"
        )
    return f"Migration failed [{name}]: {hint}."


def run() -> None:
    if sys.version_info[:3] != (3, 13, 10) or not sys.stdin.isatty():
        raise RuntimeError("Use Python 3.13.10 in an interactive terminal")
    root = Path.cwd()
    expected = input("Reviewed full commit SHA: ").strip()
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], text=True)
    if len(expected) != 40 or expected != actual or dirty.strip():
        raise RuntimeError("Require clean reviewed checkout")
    if input("Type APP STOPPED to acknowledge API/workers stopped: ") != "APP STOPPED":
        raise RuntimeError("Stop application before migration")
    ca = Path(input("Absolute project CA certificate path: ").strip())
    if not ca.is_absolute() or not ca.is_file():
        raise RuntimeError("CA file missing")
    pooler = input("Exact session-pooler hostname from project dashboard: ").strip()
    if not re.fullmatch(r"[a-z0-9-]+\.pooler\.supabase\.com", pooler):
        raise RuntimeError("Invalid session-pooler hostname")
    owner_password = getpass.getpass("Database owner password: ")
    runtime_password = getpass.getpass("hmt_crypto_app password: ")
    if not owner_password or not runtime_password:
        raise RuntimeError("Passwords required")
    values = build_environment(ca, pooler, owner_password, runtime_password)
    previous = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        ControlRuntimeSettings(_env_file=None).database_runtime_config()
        config = Config(str(root / "alembic.ini"))
        command.upgrade(config, "head")
        command.current(config)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        print(failure_diagnostic(error), file=sys.stderr)
        sys.exit(1)
