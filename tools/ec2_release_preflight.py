"""Secret-safe, read-only preflight for the locked EC2 production deployment."""

from __future__ import annotations

import argparse
import json
import re
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID


class PreflightError(RuntimeError):
    """A release invariant is absent or unsafe."""


def _env(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PreflightError(f"cannot read protected file: {path}") from exc
    values: dict[str, str] = {}
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise PreflightError(f"invalid environment line: {path}:{number}")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise PreflightError(f"invalid or duplicate environment key: {path}:{number}")
        values[key] = value.strip().strip("'\"")
    return values


def _require(values: dict[str, str], key: str, expected: str | None = None) -> str:
    value = values.get(key, "")
    if not value or "replace" in value.casefold():
        raise PreflightError(f"missing or placeholder setting: {key}")
    if expected is not None and value != expected:
        raise PreflightError(f"unsafe setting: {key}")
    return value


def _protected(path: Path, *, ca: bool = False) -> None:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise PreflightError(f"missing protected file: {path}") from exc
    allowed = {0o600} if not ca else {0o600, 0o640, 0o644}
    if mode not in allowed:
        raise PreflightError(f"unsafe file mode: {path}")


def _database_url(value: str, *, direct: bool) -> tuple[str, str]:
    parsed = urlsplit(value.replace("postgresql+asyncpg://", "postgresql://", 1))
    if parsed.scheme != "postgresql" or not parsed.hostname or not parsed.username:
        raise PreflightError("invalid database URL")
    if direct:
        if not parsed.hostname.startswith("db.") or parsed.query:
            raise PreflightError("migration URL must be the query-free direct endpoint")
    elif not parsed.hostname.endswith(".pooler.supabase.com") or parsed.port != 5432:
        raise PreflightError("runtime URL must use the Supabase session pooler on port 5432")
    return parsed.username, parsed.hostname


def verify(repo: Path, config: Path, expected_commit: str, *, compose: bool) -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{7,40}", expected_commit):
        raise PreflightError("expected commit must be a lowercase Git SHA")
    api_path = config / "api.env"
    worker_path = config / "worker.env"
    migration_path = config / "migration.env"
    compose_path = config / "compose.env"
    ca_path = config / "supabase-prod-ca.crt"
    for path in (api_path, worker_path, migration_path, compose_path):
        _protected(path)
    _protected(ca_path, ca=True)

    api = _env(api_path)
    worker = _env(worker_path)
    migration = _env(migration_path)
    compose_env = _env(compose_path)
    for values in (api, worker):
        _require(values, "EXECUTION_MODE", "LIVE")
        _require(values, "DELTA_REGION", "india")
        _require(values, "DELTA_ENV", "prod")
        _require(values, "PRODUCTION_WRITES_FORBIDDEN", "true")
        _database_url(_require(values, "DATABASE_URL"), direct=False)
    _require(api, "HMT_API_ALLOWED_ORIGIN", "https://crypto.helpmetrade.club")
    _require(api, "HMT_API_COOKIE_SECURE", "true")
    _require(api, "HMT_API_ALLOW_INSECURE_LOOPBACK", "false")
    _require(api, "HMT_API_DATABASE_CONTROL_ENABLED", "true")
    _require(api, "HMT_API_EMBED_PAPER_WORKER", "false")
    _require(api, "HMT_API_MULTI_USER_ENABLED", "true")
    supabase_host = urlsplit(_require(api, "HMT_SUPABASE_URL")).hostname
    if supabase_host is None or not supabase_host.endswith(".supabase.co"):
        raise PreflightError("invalid Supabase project URL")
    project_ref = supabase_host.removesuffix(".supabase.co")
    _require(api, "HMT_SUPABASE_PUBLISHABLE_KEY")
    _require(api, "HMT_SUPABASE_SECRET_KEY")
    _require(api, "HMT_AWS_REGION", "ap-southeast-2")
    UUID(_require(api, "HMT_API_ACCOUNT_ID"))
    _require(worker, "HMT_LIVE_ORDER_DISPATCH_ENABLED", "false")
    _require(worker, "PRODUCTION_WRITES_FORBIDDEN", "true")
    if worker.get("HMT_LIVE_ORDER_QUALIFICATION_SHA256", ""):
        raise PreflightError("qualification digest must be empty during locked deployment")
    _require(worker, "HMT_AWS_REGION", "ap-southeast-2")
    runtime_user, _ = _database_url(_require(migration, "DATABASE_URL"), direct=False)
    _, migration_host = _database_url(_require(migration, "DATABASE_MIGRATION_URL"), direct=True)
    if (
        not runtime_user.endswith(f".{project_ref}")
        or migration_host != f"db.{project_ref}.supabase.co"
        or api["DATABASE_URL"] != worker["DATABASE_URL"]
        or api["DATABASE_URL"] != migration["DATABASE_URL"]
    ):
        raise PreflightError("Supabase project identity does not match across protected files")
    _require(migration, "DATABASE_MIGRATION_CA_FILE", "/run/secrets/supabase-prod-ca.crt")
    _require(compose_env, "HMT_TLS_EMAIL")

    git = ["git", "-c", f"safe.directory={repo}"]
    head = subprocess.run(
        [*git, "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    if not head.startswith(expected_commit):
        raise PreflightError("checkout does not match the reviewed commit")
    for args in ([*git, "diff", "--quiet"], [*git, "diff", "--cached", "--quiet"]):
        if subprocess.run(args, cwd=repo, check=False).returncode != 0:
            raise PreflightError("tracked checkout has local changes")
    if compose:
        completed = subprocess.run(
            ["sudo", "docker", "compose", "-f", "docker-compose.ec2.yml", "config", "--quiet"],
            cwd=repo,
            check=False,
        )
        if completed.returncode != 0:
            raise PreflightError("Docker Compose configuration is invalid")
    return {
        "status": "PASS",
        "reviewedCommit": head,
        "dispatchEnabled": False,
        "productionWritesForbidden": True,
        "entryGatesExpected": "disabled",
        "secretsPrinted": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path("/opt/hmt-crypto"))
    parser.add_argument("--config", type=Path, default=Path("/etc/hmt"))
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--skip-compose", action="store_true")
    args = parser.parse_args()
    try:
        result = verify(
            args.repo.resolve(),
            args.config.resolve(),
            args.expected_commit,
            compose=not args.skip_compose,
        )
    except (OSError, ValueError, subprocess.SubprocessError, PreflightError) as exc:
        print(json.dumps({"status": "FAIL", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
