"""Grant retention EXECUTE and verify reviewed schema invariants."""

from __future__ import annotations

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
FUNCTIONS = (
    "hmt_crypto.guard_account_reconciliation_checkpoints_v1()",
    "hmt_crypto.guard_api_audit_payload_mutation()",
    "hmt_crypto.guard_credential_identity_binding()",
    "hmt_crypto.purge_expired_audit_payload(uuid,text)",
    "hmt_crypto.reject_append_only_mutation()",
    "hmt_crypto.reject_platform_audit_mutation()",
)


async def apply_and_verify(connection: Any) -> None:
    await connection.execute(
        "GRANT EXECUTE ON FUNCTION "
        "hmt_crypto.purge_expired_audit_payload(uuid, text) TO hmt_audit_retention"
    )
    if await connection.fetchval("SELECT version_num FROM alembic_version") != EXPECTED_HEAD:
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


async def run() -> None:
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
        await apply_and_verify(connection)
    finally:
        await connection.close()
    print(f"PASS: current revision is {EXPECTED_HEAD}")
    print(f"PASS: {EXPECTED_FKS} foreign keys are restrictive")
    print("PASS: six phase33 functions have zero browser EXECUTE grants")
    print("PASS: retention role has purge EXECUTE")


def main() -> int:
    if not sys.stdin.isatty():
        print("STOP: run interactively")
        return 1
    try:
        asyncio.run(run())
        return 0
    except Exception:
        print("STOP: post-migration grant or verification failed; diagnostics suppressed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
