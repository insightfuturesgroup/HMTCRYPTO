"""Private function grants and owner defaults survive a reversible migration."""

import asyncio
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from test_migration_postgresql_acceptance import ROOT, _acceptance_url, _drop_disposable_objects

from hmt_crypto.persistence.migration_configuration import migration_connect_args

pytestmark = [pytest.mark.integration, pytest.mark.safety]

ACL_QUERY = """SELECT p.oid::regprocedure::text,
    CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE r.rolname END,
    a.privilege_type, a.is_grantable
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
JOIN LATERAL aclexplode(NULLIF(coalesce(p.proacl, acldefault('f',p.proowner)),
    ARRAY[]::aclitem[])) a ON true
LEFT JOIN pg_roles r ON r.oid = a.grantee
WHERE n.nspname = 'hmt_crypto'
ORDER BY 1,2,3,4"""

PHASE32_BROWSER_ACL = [
    (
        "hmt_crypto.guard_account_reconciliation_checkpoints_v1()",
        "PUBLIC",
        "EXECUTE",
        False,
    )
]


async def evidence(url, *, check_private=False):
    engine = create_async_engine(
        url, hide_parameters=True, connect_args=migration_connect_args(url)
    )
    try:
        async with engine.begin() as connection:
            rows = [tuple(row) for row in (await connection.execute(text(ACL_QUERY))).all()]
            if check_private:
                assert not [row for row in rows if row[1] in {"PUBLIC", "anon", "authenticated"}]
                await connection.execute(
                    text(
                        "CREATE FUNCTION hmt_crypto.phase33_future_probe() RETURNS integer "
                        "LANGUAGE sql AS 'SELECT 1'"
                    )
                )
                future = (await connection.execute(text(ACL_QUERY))).all()
                assert not [row for row in future if row[1] in {"PUBLIC", "anon", "authenticated"}]
                await connection.execute(text("DROP FUNCTION hmt_crypto.phase33_future_probe()"))
                await connection.execute(
                    text("CREATE TEMP TABLE phase33_trigger_probe(id integer)")
                )
                await connection.execute(
                    text(
                        "CREATE TRIGGER phase33_append_only BEFORE UPDATE OR DELETE "
                        "ON phase33_trigger_probe FOR EACH ROW "
                        "EXECUTE FUNCTION hmt_crypto.reject_append_only_mutation()"
                    )
                )
                await connection.execute(text("INSERT INTO phase33_trigger_probe VALUES (1)"))
                await connection.execute(
                    text("""DO $$ BEGIN BEGIN
                    UPDATE phase33_trigger_probe SET id = 2;
                    RAISE EXCEPTION 'append-only trigger failed';
                    EXCEPTION WHEN SQLSTATE '55000' THEN NULL; END; END $$""")
                )
            return rows
    finally:
        await engine.dispose()


def test_private_function_acl_upgrade_downgrade_postgres(monkeypatch):
    url = _acceptance_url()
    ca = os.environ.get("HMT_CRYPTO_MIGRATION_ACCEPTANCE_CA_FILE")
    if ca is not None:
        monkeypatch.setenv("DATABASE_MIGRATION_CA_FILE", ca)
    monkeypatch.setenv("DATABASE_MIGRATION_URL", url.render_as_string(hide_password=False))
    runtime = url.set(username="hmt_runtime_acceptance", password="unused-runtime-secret")
    monkeypatch.setenv("DATABASE_URL", runtime.render_as_string(hide_password=False))
    config = Config(str(ROOT / "alembic.ini"))
    try:
        command.upgrade(config, "phase32_restrict_foreign_keys_v1")
        before = asyncio.run(evidence(url))
        assert [row for row in before if row[1] in {"PUBLIC", "anon", "authenticated"}] == (
            PHASE32_BROWSER_ACL
        )
        command.upgrade(config, "head")
        after = asyncio.run(evidence(url, check_private=True))
        assert not [row for row in after if row[1] in {"PUBLIC", "anon", "authenticated"}]
        command.downgrade(config, "phase32_restrict_foreign_keys_v1")
        assert asyncio.run(evidence(url)) == before
        command.upgrade(config, "head")
        asyncio.run(evidence(url, check_private=True))
    finally:
        asyncio.run(_drop_disposable_objects(url, drop_retention_role=False))
