"""Bootstrap the sole initial admin and locked account through stdin-only inputs."""

from __future__ import annotations

import getpass
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from uuid import UUID

ADMIN_CODE = """import asyncio,json,sys
from uuid import UUID
from hmt_crypto.cli_runtime import platform_admin_bootstrap_payload
d=json.load(sys.stdin)
r=asyncio.run(platform_admin_bootstrap_payload(auth_user_id=UUID(d['auth']),email=d['email'],full_name=d['name']))
print(json.dumps(r))
"""


def replace_account_id(source: str, account_id: str) -> str:
    UUID(account_id)
    updated, count = re.subn(
        r"(?m)^HMT_API_ACCOUNT_ID=[^\r\n]*$", f"HMT_API_ACCOUNT_ID={account_id}", source
    )
    if count != 1:
        raise ValueError("account setting missing")
    return updated


def _run(args: list[str], *, input_value: str | None = None) -> dict[str, object]:
    result = subprocess.run(
        args, input=input_value, capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode:
        raise RuntimeError
    for line in reversed(result.stdout.splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError


def main() -> int:
    try:
        if os.geteuid() != 0 or not sys.stdin.isatty():
            raise RuntimeError
        auth = getpass.getpass("Supabase Auth user UUID (hidden): ").strip()
        email = getpass.getpass("Confirmed Auth email (hidden): ").strip()
        name = getpass.getpass("Admin full name (hidden): ").strip()
        UUID(auth)
        if "@" not in email or not name or any("\n" in value for value in (email, name)):
            raise ValueError
        compose = ["docker", "compose", "-f", "docker-compose.ec2.yml"]
        admin = _run(
            [*compose, "run", "--rm", "--no-deps", "-T", "api", "python", "-c", ADMIN_CODE],
            input_value=json.dumps({"auth": auth, "email": email, "name": name}),
        )
        if admin.get("role") != "admin":
            raise RuntimeError
        account = _run(
            [
                *compose,
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "api",
                "hmt-crypto",
                "account",
                "bootstrap",
                "--profile",
                "delta-india-primary",
                "--confirm-locked-live",
            ]
        )
        account_id = str(account.get("account_id", ""))
        if account.get("entries_enabled") is not False or account.get("exits_enabled") is not False:
            raise RuntimeError
        path = Path("/run/hmt/api.env")
        updated = replace_account_id(path.read_text(), account_id)
        descriptor = os.open(path, os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            os.write(descriptor, updated.encode())
        finally:
            os.close(descriptor)
        subprocess.run(
            [*compose, "up", "-d", "--no-deps", "--no-build", "--force-recreate", "api"],
            check=True,
            timeout=120,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("PASS: sole initial admin bound to supplied Auth user")
        print("PASS: locked account bootstrapped with entry and exit gates disabled")
        print("PASS: API account binding updated in tmpfs and API recreated")
        return 0
    except Exception:
        print("STOP: admin/account bootstrap failed; inputs and diagnostics suppressed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
