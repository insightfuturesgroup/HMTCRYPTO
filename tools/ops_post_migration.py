"""Grant retention EXECUTE and verify reviewed schema invariants."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import re
import ssl
import sys
from pathlib import Path
from typing import Any

import asyncpg
from sqlalchemy.engine import URL

EXPECTED_HEAD = "phase34_exact_decimal_v1"
EXPECTED_FKS = 189
VERSION_TABLE = "public.hmt_crypto_alembic_version"
FUNCTIONS = (
    "hmt_crypto.guard_account_reconciliation_checkpoints_v1()",
    "hmt_crypto.guard_api_audit_payload_mutation()",
    "hmt_crypto.guard_credential_identity_binding()",
    "hmt_crypto.purge_expired_audit_payload(uuid,text)",
    "hmt_crypto.reject_append_only_mutation()",
    "hmt_crypto.reject_platform_audit_mutation()",
)

DIAGNOSTIC_STEPS = (
    "alembic revision",
    "retention role",
    "retention function",
    "restrictive foreign keys",
    "phase33 browser EXECUTE",
    "Data API exposed schemas",
)


class DiagnosticFailure(RuntimeError):
    """A bounded diagnostic failure with no connection details."""


def _redact_first_line(error: Exception) -> str:
    first_line = str(error).splitlines()[0] if str(error) else "no server message"
    patterns = (
        (r"(?i)(password|passwd|token|secret)\s*[:=]\s*\S+", r"\1=[REDACTED]"),
        (r"\b[a-z]{20}\b", "[REDACTED_PROJECT_REF]"),
        (r"\b(?:db\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+\b", "[REDACTED_HOST]"),
    )
    for pattern, replacement in patterns:
        first_line = re.sub(pattern, replacement, first_line)
    return first_line[:300]


def _diagnostic_failure(step: str, error: Exception) -> None:
    sqlstate = getattr(error, "sqlstate", None) or "NONE"
    print(
        f"FAIL: {step} | {type(error).__name__} | SQLSTATE {sqlstate} | {_redact_first_line(error)}"
    )


async def diagnose(connection: Any) -> bool:
    """Run independent, read-only checks and return whether required checks pass."""
    passed = True

    try:
        revision = await connection.fetchval(f"SELECT version_num FROM {VERSION_TABLE}")
        if revision != EXPECTED_HEAD:
            raise DiagnosticFailure("unexpected migration revision")
        print(f"PASS: alembic revision | {EXPECTED_HEAD}")
    except Exception as error:
        passed = False
        _diagnostic_failure(DIAGNOSTIC_STEPS[0], error)

    try:
        role_exists = await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='hmt_audit_retention')"
        )
        if not role_exists:
            raise DiagnosticFailure("role exists: no")
        print("PASS: retention role | exists: yes")
    except Exception as error:
        passed = False
        _diagnostic_failure(DIAGNOSTIC_STEPS[1], error)

    try:
        function_row = await connection.fetchrow(
            """SELECT owner_role.rolname,
                      CASE WHEN retention_role.oid IS NULL THEN false
                           ELSE has_function_privilege(
                             retention_role.oid, p.oid, 'EXECUTE'
                           ) END
               FROM pg_proc p
               JOIN pg_roles owner_role ON owner_role.oid=p.proowner
               LEFT JOIN pg_roles retention_role
                 ON retention_role.rolname='hmt_audit_retention'
               WHERE p.oid=to_regprocedure(
                 'hmt_crypto.purge_expired_audit_payload(uuid,text)'
               )"""
        )
        if function_row is None:
            raise DiagnosticFailure("function exists: no")
        owner, retention_execute = function_row
        if not retention_execute:
            raise DiagnosticFailure(f"function exists: yes; owner: {owner}; retention EXECUTE: no")
        print(f"PASS: retention function | exists: yes | owner: {owner} | retention EXECUTE: yes")
    except Exception as error:
        passed = False
        _diagnostic_failure(DIAGNOSTIC_STEPS[2], error)

    try:
        total, restrictive = await connection.fetchrow(
            """SELECT count(*)::integer,
                      count(*) FILTER (
                        WHERE c.confupdtype='r' AND c.confdeltype='r'
                      )::integer
               FROM pg_constraint c
               JOIN pg_namespace n ON n.oid=c.connamespace
               WHERE n.nspname='hmt_crypto' AND c.contype='f'"""
        )
        if (total, restrictive) != (EXPECTED_FKS, EXPECTED_FKS):
            raise DiagnosticFailure(
                f"total: {total}; restrictive: {restrictive}; expected: {EXPECTED_FKS}"
            )
        print(f"PASS: restrictive foreign keys | {restrictive}/{total}")
    except Exception as error:
        passed = False
        _diagnostic_failure(DIAGNOSTIC_STEPS[3], error)

    try:
        function_count, public_count, anon_count, authenticated_count = await connection.fetchrow(
            """SELECT
                 count(DISTINCT p.oid)::integer,
                 count(*) FILTER (WHERE a.grantee=0)::integer,
                 count(*) FILTER (WHERE r.rolname='anon')::integer,
                 count(*) FILTER (WHERE r.rolname='authenticated')::integer
               FROM unnest($1::text[]) s
               JOIN pg_proc p ON p.oid=to_regprocedure(s)
               CROSS JOIN LATERAL
                 aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
               LEFT JOIN pg_roles r ON r.oid=a.grantee
               WHERE a.privilege_type='EXECUTE'""",
            list(FUNCTIONS),
        )
        counts = (public_count, anon_count, authenticated_count)
        if function_count != len(FUNCTIONS) or counts != (0, 0, 0):
            raise DiagnosticFailure(
                f"functions: {function_count}/{len(FUNCTIONS)}; PUBLIC: {public_count}; "
                f"anon: {anon_count}; authenticated: {authenticated_count}"
            )
        print("PASS: phase33 browser EXECUTE | PUBLIC=0 | anon=0 | authenticated=0")
    except Exception as error:
        passed = False
        _diagnostic_failure(DIAGNOSTIC_STEPS[4], error)

    print(
        "SKIPPED: Data API exposed schemas | Management API state unavailable; no token requested"
    )
    return passed


async def apply_and_verify(connection: Any) -> None:
    await connection.execute(
        "GRANT EXECUTE ON FUNCTION "
        "hmt_crypto.purge_expired_audit_payload(uuid, text) TO hmt_audit_retention"
    )
    if await connection.fetchval(f"SELECT version_num FROM {VERSION_TABLE}") != EXPECTED_HEAD:
        raise RuntimeError("unexpected migration revision")
    total, restrictive = await connection.fetchrow(
        """SELECT count(*)::integer,
                  count(*) FILTER (WHERE c.confupdtype='r' AND c.confdeltype='r')::integer
           FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
           WHERE n.nspname='hmt_crypto' AND c.contype='f'"""
    )
    if (total, restrictive) != (EXPECTED_FKS, EXPECTED_FKS):
        raise RuntimeError("foreign key verification failed")
    present = await connection.fetchval(
        "SELECT count(*)::integer FROM unnest($1::text[]) s WHERE to_regprocedure(s) IS NOT NULL",
        list(FUNCTIONS),
    )
    forbidden = await connection.fetchval(
        """SELECT count(*)::integer FROM unnest($1::text[]) s
           JOIN pg_proc p ON p.oid=to_regprocedure(s)
           CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
           LEFT JOIN pg_roles r ON r.oid=a.grantee
           WHERE a.privilege_type='EXECUTE'
             AND (a.grantee=0 OR r.rolname IN ('anon','authenticated'))""",
        list(FUNCTIONS),
    )
    retention = await connection.fetchval(
        "SELECT has_function_privilege('hmt_audit_retention', "
        "'hmt_crypto.purge_expired_audit_payload(uuid,text)', 'EXECUTE')"
    )
    if (present, forbidden, retention) != (len(FUNCTIONS), 0, True):
        raise RuntimeError("function ACL verification failed")


async def connect(*, diagnostic: bool = False) -> bool:
    project = getpass.getpass("Supabase project reference (hidden): ").strip()
    if not re.fullmatch(r"[a-z]{20}", project):
        raise RuntimeError("invalid project reference")
    ca = Path(input("Absolute project CA certificate path: ").strip())
    if not ca.is_absolute() or not ca.is_file():
        raise RuntimeError("CA file missing")
    owner_input = getpass.getpass("Database owner password (hidden): ")
    if not owner_input:
        raise RuntimeError("owner password required")
    url = URL.create(
        "postgresql",
        username="postgres",
        **{"password": owner_input},
        host=f"db.{project}.supabase.co",
        port=5432,
        database="postgres",
    ).render_as_string(False)
    connection = await asyncpg.connect(
        url, ssl=ssl.create_default_context(cafile=str(ca)), timeout=20
    )
    try:
        if diagnostic:
            return await diagnose(connection)
        await apply_and_verify(connection)
    finally:
        await connection.close()
    return True


async def run() -> None:
    await connect()
    print(f"PASS: current revision is {EXPECTED_HEAD}")
    print(f"PASS: {EXPECTED_FKS} foreign keys are restrictive")
    print("PASS: six phase33 functions have zero browser EXECUTE grants")
    print("PASS: retention role has purge EXECUTE")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help="run independent read-only checks without grants or DDL",
    )
    args = parser.parse_args()
    if not sys.stdin.isatty():
        print("STOP: run interactively")
        return 1
    try:
        if args.diagnostic:
            return 0 if asyncio.run(connect(diagnostic=True)) else 1
        asyncio.run(run())
        return 0
    except Exception as error:
        if args.diagnostic:
            for step in DIAGNOSTIC_STEPS[:-1]:
                _diagnostic_failure(step, error)
            print(
                "SKIPPED: Data API exposed schemas | Management API state unavailable; "
                "no token requested"
            )
            return 1
        print("STOP: post-migration grant or verification failed; diagnostics suppressed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
