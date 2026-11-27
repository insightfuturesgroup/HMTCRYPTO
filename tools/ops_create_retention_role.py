"""Create and verify the NOLOGIN audit-retention role without exposing credentials."""

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

ROLE_NAME = "hmt_audit_retention"
FUNCTION_SCHEMA = "hmt_crypto"
FUNCTION_NAME = "purge_expired_audit_payload"
FORBIDDEN_ROLES = ("PUBLIC", "anon", "authenticated", "hmt_crypto_app")

CREATE_AND_CHECK_ROLE_SQL = """
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='hmt_audit_retention') THEN
    CREATE ROLE hmt_audit_retention NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
      NOREPLICATION NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (
    SELECT FROM pg_roles
    WHERE rolname='hmt_audit_retention'
      AND NOT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb
      AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls
  ) THEN
    RAISE EXCEPTION 'retention role attributes require operator review';
  END IF;
END $$
"""

ROLE_QUERY = """
SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
FROM pg_roles WHERE rolname='hmt_audit_retention'
"""

FUNCTION_QUERY = """
SELECT p.oid, n.nspname, p.proname, pg_get_function_identity_arguments(p.oid), owner.rolname
FROM pg_proc p
JOIN pg_namespace n ON n.oid=p.pronamespace
JOIN pg_roles owner ON owner.oid=p.proowner
WHERE n.nspname='hmt_crypto' AND p.proname='purge_expired_audit_payload'
ORDER BY p.oid
"""


def role_is_safe(row: Any) -> bool:
    return row is not None and tuple(row) == (False, False, False, False, False, False)


def function_label(row: Any) -> str:
    return f"{row[1]}.{row[2]}({row[3]})"


async def resolve_function(connection: Any) -> Any:
    rows = await connection.fetch(FUNCTION_QUERY)
    if len(rows) != 1:
        raise RuntimeError("expected exactly one purge function signature")
    return rows[0]


async def inspect_plan(connection: Any) -> None:
    role = await connection.fetchrow(ROLE_QUERY)
    if role is None:
        print("PASS: role inspection | absent; apply would create safe NOLOGIN role")
    elif role_is_safe(role):
        print("PASS: role inspection | existing role attributes are safe")
    else:
        raise RuntimeError("existing retention role has unsafe attributes")
    function = await resolve_function(connection)
    print(f"PASS: purge function resolved | {function_label(function)} | owner {function[4]}")
    print(
        "DRY RUN: read only; would create or verify the role and grant catalog-resolved "
        "EXECUTE in one transaction"
    )


async def apply_role(connection: Any) -> None:
    async with connection.transaction():
        await connection.execute(CREATE_AND_CHECK_ROLE_SQL)
        function = await resolve_function(connection)
        grant_sql = await connection.fetchval(
            """SELECT format(
                 'GRANT EXECUTE ON FUNCTION %I.%I(%s) TO hmt_audit_retention',
                 $1::text, $2::text, $3::text
               )""",
            function[1],
            function[2],
            function[3],
        )
        await connection.execute(grant_sql)
    print("PASS: retention role created or already safe")
    print(f"PASS: retention EXECUTE granted | {function_label(function)}")
    print("PASS: transaction committed")


async def verify_role(connection: Any) -> None:
    role = await connection.fetchrow(ROLE_QUERY)
    if not role_is_safe(role):
        raise RuntimeError("retention role missing or unsafe")
    function = await resolve_function(connection)
    function_oid = function[0]
    owner = function[4]
    grantees = set(
        await connection.fetch(
            """SELECT CASE WHEN acl.grantee=0 THEN 'PUBLIC' ELSE grantee.rolname END
               FROM pg_proc p
               CROSS JOIN LATERAL
                 aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) acl
               LEFT JOIN pg_roles grantee ON grantee.oid=acl.grantee
               WHERE p.oid=$1::oid AND acl.privilege_type='EXECUTE'""",
            function_oid,
        )
    )
    normalized_grantees = {row[0] for row in grantees}
    if normalized_grantees != {owner, ROLE_NAME}:
        raise RuntimeError("unexpected function EXECUTE grantee")
    privileges = {}
    for role_name in FORBIDDEN_ROLES[1:]:
        privileges[role_name] = await connection.fetchval(
            """SELECT coalesce((
                 SELECT has_function_privilege(oid, $1::oid, 'EXECUTE')
                 FROM pg_roles WHERE rolname=$2::text
               ), false)""",
            function_oid,
            role_name,
        )
    public_execute = "PUBLIC" in normalized_grantees
    membership = await connection.fetchval(
        """SELECT EXISTS (
             SELECT 1 FROM pg_roles member_role CROSS JOIN pg_roles granted_role
             WHERE member_role.rolname='hmt_crypto_app'
               AND granted_role.rolname='hmt_audit_retention'
               AND pg_has_role(member_role.oid, granted_role.oid, 'MEMBER')
           )"""
    )
    if public_execute or any(privileges.values()):
        raise RuntimeError("forbidden role has purge EXECUTE")
    if membership:
        raise RuntimeError("runtime role is a retention-role member")
    print("PASS: retention role exists with safe attributes")
    print(f"PASS: purge function resolved | {function_label(function)} | owner {owner}")
    print("PASS: EXECUTE grantees are owner and hmt_audit_retention only")
    print("PASS: PUBLIC, anon, authenticated, hmt_crypto_app have no EXECUTE")
    print("PASS: hmt_crypto_app is not a member of hmt_audit_retention")


def read_connection_inputs() -> tuple[str, str, Path, str]:
    host = getpass.getpass("Session pooler hostname (hidden): ").strip()
    project = getpass.getpass("Project reference (hidden): ").strip()
    ca = Path(
        getpass.getpass("CA path (hidden; Enter uses ~/hmt-secrets/supabase-ca.crt): ")
        or "~/hmt-secrets/supabase-ca.crt"
    ).expanduser()
    owner_input = getpass.getpass("Owner postgres password (hidden): ")
    if not re.fullmatch(r"[a-z0-9-]+\.pooler\.supabase\.com", host):
        raise ValueError("invalid session pooler hostname")
    if not re.fullmatch(r"[a-z]{20}", project):
        raise ValueError("invalid project reference")
    if not ca.is_absolute() or not ca.is_file():
        raise ValueError("CA file missing")
    ssl.create_default_context(cafile=str(ca))
    if not owner_input or any(character in owner_input for character in "\r\n\x00"):
        raise ValueError("invalid owner password")
    return host, project, ca, owner_input


async def run(mode: str) -> None:
    host, project, ca, owner_input = read_connection_inputs()
    url = URL.create(
        "postgresql",
        username=f"postgres.{project}",
        **{"password": owner_input},
        host=host,
        port=5432,
        database="postgres",
    ).render_as_string(False)
    connection = await asyncpg.connect(
        url, ssl=ssl.create_default_context(cafile=str(ca)), timeout=20
    )
    try:
        if mode == "dry-run":
            await inspect_plan(connection)
        elif mode == "apply":
            if input("Type APPLY to create or verify the role and grant EXECUTE: ") != "APPLY":
                print("CANCELLED: no changes performed")
                return
            await apply_role(connection)
        else:
            await verify_role(connection)
    finally:
        await connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if not sys.stdin.isatty():
        print("STOP: run interactively in your own terminal")
        return 1
    mode = "dry-run" if args.dry_run else "apply" if args.apply else "verify"
    print(f"PLAN: ops-2b {mode}; retention role and purge EXECUTE only")
    print("No migrations, membership grants, passwords, gates, or server changes")
    try:
        asyncio.run(run(mode))
        return 0
    except (Exception, KeyboardInterrupt):
        print(f"STOP: ops-2b {mode} failed; diagnostics suppressed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
