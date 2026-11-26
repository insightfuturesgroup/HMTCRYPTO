"""Operator-only runtime-role bootstrap; no credentials in argv, files or output."""

import argparse
import getpass
import os
import pty
import re
import select
import shutil
import signal
import ssl
import subprocess
import sys
import termios
import time
from pathlib import Path

ROLE_SQL = """
DO $$ BEGIN
IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hmt_crypto_app') THEN
  CREATE ROLE hmt_crypto_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION NOBYPASSRLS;
END IF;
IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hmt_crypto_app'
  AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
  AND NOT rolreplication AND NOT rolbypassrls) THEN
  RAISE EXCEPTION 'Runtime role attributes require operator review';
END IF;
EXECUTE format('GRANT CONNECT ON DATABASE %I TO hmt_crypto_app', current_database());
END $$;
"""


def connection_environment(host, project, ca, owner_password):
    if not re.fullmatch(r"[a-z0-9-]+\.pooler\.supabase\.com", host):
        raise ValueError("Session pooler hostname required")
    if not re.fullmatch(r"[a-z]{20}", project):
        raise ValueError("Project reference format")
    if not owner_password or "\x00" in owner_password:
        raise ValueError("Owner password format")
    return {
        "PATH": os.defpath,
        "HOME": "/nonexistent",
        "LC_ALL": "C",
        "PGHOST": host,
        "PGPORT": "5432",
        "PGDATABASE": "postgres",
        "PGUSER": f"postgres.{project}",
        "PGPASSWORD": owner_password,
        "PGSSLMODE": "verify-full",
        "PGSSLROOTCERT": str(ca),
        "PGCONNECT_TIMEOUT": "15",
        "PSQL_HISTORY": "/dev/null",
    }


def validate_password(password, confirmation):
    if password != confirmation:
        raise ValueError("Password confirmation differs")
    if not 20 <= len(password) <= 1024 or any(c in password for c in "\r\n\x00"):
        raise ValueError("Password length or control characters")


def run_sql(psql, environment, sql):
    result = subprocess.run(
        [psql, "-X", "-w", "-q", "-t", "-A", "-v", "ON_ERROR_STOP=1"],
        input=sql,
        text=True,
        capture_output=True,
        timeout=30,
        env=environment,
    )
    if result.returncode:
        raise RuntimeError("Database step failed")
    return result.stdout.strip()


def set_password(psql, environment, password):
    """Let psql's own \u005c\u005cpassword perform client-side password encryption.

    A private PTY supplies its two hidden prompts. Child output is never forwarded;
    terminal echo is disabled before exec. No plaintext ALTER ROLE is constructed.
    """
    pid, fd = pty.fork()
    if pid == 0:
        try:
            settings = termios.tcgetattr(0)
            settings[3] &= ~(termios.ECHO | termios.ECHONL)
            termios.tcsetattr(0, termios.TCSANOW, settings)
            os.execve(
                psql, [psql, "-X", "-w", "-q", "-c", "\\password hmt_crypto_app"], environment
            )
        finally:
            os._exit(1)
    sent = 0
    buffer = b""
    deadline = time.monotonic() + 45
    reaped = False
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select.select([fd], [], [], 0.25)
            if readable:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    chunk = b""
                buffer = (buffer + chunk)[-8192:]
                prompt = b"Enter new password" if sent == 0 else b"Enter it again"
                if sent < 2 and prompt in buffer:
                    os.write(fd, password.encode("utf-8") + b"\n")
                    sent += 1
                    buffer = b""
            child, status = os.waitpid(pid, os.WNOHANG)
            if child:
                reaped = True
                if sent == 2 and os.waitstatus_to_exitcode(status) == 0:
                    return
                raise RuntimeError("Password step failed")
        child, status = os.waitpid(pid, os.WNOHANG)
        if child:
            reaped = True
            if sent == 2 and os.waitstatus_to_exitcode(status) == 0:
                return
        raise RuntimeError("Password step incomplete")
    finally:
        os.close(fd)
        if not reaped:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print("PLAN: verify TLS; create restricted runtime role if absent; grant CONNECT.")
    print("PLAN: set a password only for a new role or after explicit reset confirmation.")
    print("No migrations, account configuration, credentials for exchanges, or gate changes.")
    if args.dry_run:
        print("DRY RUN: no prompts, connections, file reads, or changes performed.")
        return 0
    if not sys.stdin.isatty():
        print("STOP: run interactively in your own terminal.")
        return 1
    psql = shutil.which("psql")
    if psql is None:
        print("STOP: install libpq and add its bin directory to PATH.")
        return 1
    try:
        host = getpass.getpass("Session pooler hostname (hidden): ").strip()
        project = (
            os.environ.get("HMT_SUPABASE_PROJECT_REF")
            or getpass.getpass("Project reference (hidden): ").strip()
        )
        ca = (
            Path(
                getpass.getpass("CA path (hidden; Enter uses ~/hmt-secrets/supabase-ca.crt): ")
                or "~/hmt-secrets/supabase-ca.crt"
            )
            .expanduser()
            .resolve()
        )
        if not ca.is_file():
            raise ValueError("CA file missing")
        ssl.create_default_context(cafile=str(ca))
        owner = getpass.getpass("Owner postgres password (hidden): ")
        environment = connection_environment(host, project, ca, owner)
        exists = run_sql(
            psql,
            environment,
            "SELECT EXISTS (SELECT FROM pg_roles WHERE rolname='hmt_crypto_app');",
        )
        if exists not in {"t", "f"}:
            raise RuntimeError("Unexpected role lookup result")
        reset = exists == "f"
        if not reset:
            reset = (
                input("Role exists. Reset its password? Type RESET, otherwise leave unchanged: ")
                == "RESET"
            )
        password = None
        if reset:
            password = getpass.getpass("New runtime password (hidden; minimum 20 characters): ")
            validate_password(password, getpass.getpass("Confirm runtime password (hidden): "))
        if input("Apply the displayed role/CONNECT plan? Type APPLY: ") != "APPLY":
            print("CANCELLED: no changes performed.")
            return 0
        run_sql(psql, environment, ROLE_SQL)
        if password is not None:
            set_password(psql, environment, password)
        print(
            "PASS: restricted runtime role and CONNECT verified; requested password step completed."
        )
        return 0
    except (Exception, KeyboardInterrupt):
        print(
            "STOP: role setup failed. Check connection, TLS, owner permissions, and input locally."
        )
        print(
            "No server diagnostics were printed. "
            "A new role may exist without a password; rerun safely."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
