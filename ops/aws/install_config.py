"""Atomically install locked deployment configuration under root-only /etc/hmt."""

from __future__ import annotations

import base64
import getpass
import hashlib
import os
import re
import ssl
import sys
from pathlib import Path
from urllib.parse import urlsplit


def render_files(values: dict[str, str]) -> dict[str, str]:
    common = (
        f"DATABASE_URL={values['runtime_url']}\n"
        "DATABASE_CA_FILE=/run/secrets/supabase-prod-ca.crt\n"
        "DATABASE_SINGLE_PROCESS=false\nEXECUTION_MODE=LIVE\nDELTA_REGION=india\n"
        "DELTA_ENV=prod\nPRODUCTION_WRITES_FORBIDDEN=true\nHMT_PRIVATE_READ_ONLY=true\n"
    )
    api = common + (
        f"HMT_API_OPERATOR_PASSWORD_HASH={values['operator_hash']}\n"
        "HMT_API_ALLOWED_ORIGIN=https://crypto.helpmetrade.club\n"
        "HMT_API_COOKIE_SECURE=true\nHMT_API_ALLOW_INSECURE_LOOPBACK=false\n"
        "HMT_API_DATABASE_CONTROL_ENABLED=true\nHMT_API_EMBED_PAPER_WORKER=false\n"
        "HMT_API_MULTI_USER_ENABLED=true\n"
        f"HMT_SUPABASE_URL={values['supabase_url']}\n"
        f"HMT_SUPABASE_PUBLISHABLE_KEY={values['publishable_key']}\n"
        f"HMT_SUPABASE_SECRET_KEY={values['secret_key']}\n"
        "HMT_AWS_REGION=ap-south-1\nHMT_DELTA_SECRET_PREFIX=hmt-crypto/delta\n"
        "HMT_RECOVERY_WARNING_SECONDS=15\nHMT_RECOVERY_CRITICAL_SECONDS=60\n"
        "HMT_API_ACCOUNT_ID=00000000-0000-4000-8000-000000000001\n"
        "HMT_API_SESSION_IDLE_SECONDS=900\nHMT_API_SESSION_ABSOLUTE_SECONDS=28800\n"
        "HMT_API_MAX_SESSIONS=8\nHMT_API_LOGIN_ATTEMPTS_PER_FIVE_MINUTES=10\n"
        "HMT_API_MAX_REQUEST_BYTES=32768\n"
    )
    worker = common + (
        "HMT_LIVE_ORDER_DISPATCH_ENABLED=false\nHMT_LIVE_ORDER_QUALIFICATION_SHA256=\n"
        "HMT_WORKER_KIND=market\nHMT_MARKET_REFRESH_SECONDS=15\n"
        "HMT_MARKET_CATALOGUE_SECONDS=900\nHMT_AWS_REGION=ap-south-1\n"
        "HMT_DELTA_SECRET_PREFIX=hmt-crypto/delta\nHMT_PRIVATE_INTERVAL_SECONDS=30\n"
        "HMT_PRIVATE_LEASE_SECONDS=90\nHMT_OPERATIONAL_RETENTION_DAYS=7\n"
        "HMT_RECOVERY_WARNING_SECONDS=15\nHMT_RECOVERY_CRITICAL_SECONDS=60\n"
    )
    return {
        "api.env": api,
        "worker.env": worker,
        "migration.env": (
            f"DATABASE_URL={values['runtime_url']}\n"
            f"DATABASE_MIGRATION_URL={values['migration_url']}\n"
            "DATABASE_MIGRATION_CA_FILE=/run/secrets/supabase-prod-ca.crt\n"
        ),
        "compose.env": f"HMT_TLS_EMAIL={values['tls_email']}\n",
    }


def _hidden(label: str) -> str:
    value = getpass.getpass(label).strip()
    if not value or "\n" in value or "\r" in value:
        raise ValueError
    return value


def _write_atomic(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            os.fchown(descriptor, 0, 0)
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        os.chown(path, 0, 0)
    finally:
        temporary.unlink(missing_ok=True)


def _verify_install(etc: Path, repo: Path, files: dict[str, str]) -> None:
    directory = etc.stat()
    if directory.st_mode & 0o777 != 0o700 or (directory.st_uid, directory.st_gid) != (0, 0):
        raise RuntimeError
    required = {"supabase-prod-ca.crt", *files}
    for name in required:
        metadata = (etc / name).stat()
        if metadata.st_mode & 0o777 != 0o600 or (metadata.st_uid, metadata.st_gid) != (0, 0):
            raise RuntimeError
    links = {
        repo / ".env": etc / "compose.env",
        repo / "deploy/ec2/api.env": etc / "api.env",
        repo / "deploy/ec2/worker.env": etc / "worker.env",
        repo / "deploy/ec2/migration.env": etc / "migration.env",
    }
    if any(not link.is_symlink() or link.resolve() != target for link, target in links.items()):
        raise RuntimeError
    joined = "\n".join(files.values())
    required_closed = (
        "PRODUCTION_WRITES_FORBIDDEN=true",
        "HMT_PRIVATE_READ_ONLY=true",
        "HMT_LIVE_ORDER_DISPATCH_ENABLED=false",
        "HMT_LIVE_ORDER_QUALIFICATION_SHA256=\n",
    )
    if any(value not in joined for value in required_closed):
        raise RuntimeError


def main() -> int:
    try:
        if os.geteuid() != 0 or not sys.stdin.isatty():
            raise RuntimeError
        repo = Path("/opt/hmt-crypto")
        ca_b64 = _hidden("Supabase CA base64 from Mac (hidden): ")
        expected = _hidden("CA SHA256 from Mac (hidden): ").lower()
        ca = base64.b64decode(ca_b64, validate=True)
        if (
            not re.fullmatch(r"[0-9a-f]{64}", expected)
            or hashlib.sha256(ca).hexdigest() != expected
        ):
            raise ValueError
        ssl.create_default_context(cadata=ca.decode("ascii"))
        values = {
            "runtime_url": _hidden("Runtime session-pooler DATABASE_URL (hidden): "),
            "migration_url": _hidden("Direct owner DATABASE_MIGRATION_URL (hidden): "),
            "operator_hash": _hidden("Argon2 operator hash (hidden): "),
            "supabase_url": _hidden("Supabase project URL (hidden): "),
            "publishable_key": _hidden("Supabase publishable key (hidden): "),
            "secret_key": _hidden("Supabase server secret key (hidden): "),
            "tls_email": _hidden("TLS contact email (hidden): "),
        }
        runtime = urlsplit(values["runtime_url"].replace("postgresql+asyncpg", "postgresql", 1))
        migration = urlsplit(values["migration_url"].replace("postgresql+asyncpg", "postgresql", 1))
        project_host = urlsplit(values["supabase_url"]).hostname
        if (
            runtime.port != 5432
            or not runtime.hostname
            or not runtime.hostname.endswith(".pooler.supabase.com")
            or not project_host
            or not project_host.endswith(".supabase.co")
            or migration.hostname != f"db.{project_host}"
        ):
            raise ValueError
        old_umask = os.umask(0o077)
        etc = Path("/etc/hmt")
        legacy_run = Path("/run/hmt")
        etc.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(etc, 0o700)
        os.chown(etc, 0, 0)
        files = render_files(values)
        _write_atomic(etc / "supabase-prod-ca.crt", ca)
        for name, content in files.items():
            _write_atomic(etc / name, content.encode())
        for link, target in {
            repo / ".env": etc / "compose.env",
            repo / "deploy/ec2/api.env": etc / "api.env",
            repo / "deploy/ec2/worker.env": etc / "worker.env",
            repo / "deploy/ec2/migration.env": etc / "migration.env",
        }.items():
            link.unlink(missing_ok=True)
            link.symlink_to(target)
        if legacy_run.is_symlink():
            legacy_run.unlink()
        else:
            for name in (*files, "supabase-prod-ca.crt"):
                (legacy_run / name).unlink(missing_ok=True)
            try:
                legacy_run.rmdir()
            except (FileNotFoundError, OSError):
                pass
        _verify_install(etc, repo, files)
        os.umask(old_umask)
        print("PASS: /etc/hmt mode 0700 owner root:root")
        print("PASS: supabase-prod-ca.crt mode 0600 owner root:root")
        for name in ("compose.env", "api.env", "worker.env", "migration.env"):
            print(f"PASS: {name} mode 0600 owner root:root")
        print("PASS: checkout links target /etc/hmt; legacy /run/hmt links removed")
        print("PASS: production writes, dispatch and qualification gates closed")
        return 0
    except Exception:
        print("FAIL: configuration install failed; input values suppressed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
