"""The owner-controlled Alembic history is frozen, complete and offline-renderable."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
VERSIONS = MIGRATIONS / "versions"
BASELINE = VERSIONS / "phase2_baseline_v1.py"
HARDENING = VERSIONS / "phase2_hardening_v1.py"
CONTROL = VERSIONS / "phase5_control_v1.py"
RECONCILIATION_CHECKPOINT = VERSIONS / "phase5_reconcile_checkpoint_v1.py"
RUNTIME_GRANTS = VERSIONS / "phase5_runtime_grants_v1_grant_least_privilege_runtime_access.py"
MANIFEST = MIGRATIONS / "schema_manifest.json"
EXPECTED_TABLES = {
    "accounts",
    "api_audit_events",
    "api_audit_payloads",
    "balance_snapshots",
    "catalogue_products",
    "catalogue_snapshots",
    "command_operations",
    "credential_versions",
    "exit_intents",
    "fill_order_links",
    "fills",
    "funding_snapshots",
    "leadership_leases",
    "order_attempt_events",
    "order_attempts",
    "order_transitions",
    "orders",
    "pnl_snapshots",
    "position_protection_orders",
    "position_protections",
    "position_snapshots",
    "protection_authorizations",
    "reconciliation_acknowledgements",
    "reconciliation_drifts",
    "reconciliation_resources",
    "reconciliation_runs",
    "recovery_incarnations",
    "risk_decisions",
    "risk_halt_events",
    "risk_halts",
    "risk_rate_consumptions",
    "risk_reservations",
    "risk_state",
    "scheduler_state",
    "signals",
    "wallet_transactions",
    "worker_heartbeats",
}


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical_fingerprint(statements: tuple[str, ...]) -> str:
    payload = json.dumps(statements, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def test_revision_chain_extends_the_frozen_phase2_history() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    revisions = list(script.walk_revisions(base="base", head="heads"))
    assert [(revision.revision, revision.down_revision) for revision in revisions] == [
        ("phase20_account_archive_v1", "phase19_notifications_v1"),
        ("phase19_notifications_v1", "phase18_market_candles_v1"),
        ("phase18_market_candles_v1", "phase17_platform_sessions_v1"),
        ("phase17_platform_sessions_v1", "phase16_live_order_actions_v1"),
        ("phase16_live_order_actions_v1", "phase15_tradingview_v1"),
        ("phase15_tradingview_v1", "phase14_gtt_trigger_state_v1"),
        ("phase14_gtt_trigger_state_v1", "phase13e_uncertain_recovery_v1"),
        ("phase13e_uncertain_recovery_v1", "phase13d_live_submission_outcomes_v1"),
        ("phase13d_live_submission_outcomes_v1", "phase13c_live_order_wire_v1"),
        ("phase13c_live_order_wire_v1", "phase13b_live_submission_events_v1"),
        ("phase13b_live_submission_events_v1", "phase13_live_submission_queue_v1"),
        ("phase13_live_submission_queue_v1", "phase12_live_order_admission_v1"),
        ("phase12_live_order_admission_v1", "phase11_live_risk_controls_v1"),
        ("phase11_live_risk_controls_v1", "phase10_private_worker_lease_v1"),
        ("phase10_private_worker_lease_v1", "phase9_private_state_projection_v1"),
        ("phase9_private_state_projection_v1", "phase8_version_width_v1"),
        ("phase8_version_width_v1", "phase8_private_reconciliation_v1"),
        ("phase8_private_reconciliation_v1", "phase7_live_control_v1"),
        ("phase7_live_control_v1", "phase6_multi_user_platform_v1"),
        ("phase6_multi_user_platform_v1", "phase5_runtime_grants_v1"),
        ("phase5_runtime_grants_v1", "phase5_reconcile_checkpoint_v1"),
        ("phase5_reconcile_checkpoint_v1", "phase5_control_v1"),
        ("phase5_control_v1", "phase2_hardening_v1"),
        ("phase2_hardening_v1", "phase2_baseline_v1"),
        ("phase2_baseline_v1", None),
    ]
    assert script.get_current_head() == "phase20_account_archive_v1"
    assert len("phase8_version_width_v1") <= 32
    assert max(len(revision.revision) for revision in revisions) <= 128


def test_runtime_grants_are_data_only_and_reversible() -> None:
    migration = _load(RUNTIME_GRANTS, "least_privilege_runtime_grants")
    upgrade: list[str] = []
    migration._execute = upgrade.append
    migration.upgrade()

    rendered = "\n".join(upgrade).upper()
    assert "GRANT SELECT, INSERT, UPDATE ON ALL TABLES" in rendered
    assert "GRANT ALL" not in rendered
    assert "GRANT DELETE" not in rendered
    assert "GRANT EXECUTE" not in rendered
    assert "REVOKE DELETE ON ALL TABLES" in rendered
    assert "REVOKE ALL ON ALL FUNCTIONS" in rendered
    assert "REVOKE CREATE ON SCHEMA" in rendered

    downgrade: list[str] = []
    migration._execute = downgrade.append
    migration.downgrade()
    assert "REVOKE SELECT, INSERT, UPDATE ON ALL TABLES" in "\n".join(downgrade).upper()


def test_built_wheel_contains_the_operator_migration_history(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(tmp_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    wheels = tuple(tmp_path.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as archive:
        packaged = set(archive.namelist())
    assert {
        "hmt_crypto_migrations/alembic.ini",
        "hmt_crypto_migrations/migrations/env.py",
        "hmt_crypto_migrations/migrations/script.py.mako",
        "hmt_crypto_migrations/migrations/schema_manifest.json",
        "hmt_crypto_migrations/migrations/versions/phase2_baseline_v1.py",
        "hmt_crypto_migrations/migrations/versions/phase2_hardening_v1.py",
        "hmt_crypto_migrations/migrations/versions/phase5_control_v1.py",
        "hmt_crypto_migrations/migrations/versions/phase5_reconcile_checkpoint_v1.py",
    } <= packaged


def test_frozen_baseline_has_all_tables_and_no_current_model_import() -> None:
    source = BASELINE.read_text(encoding="utf-8")
    lowered = source.lower()
    assert "hmt_crypto.persistence" not in source
    assert "metadata.create_all" not in lowered
    assert "target_metadata" not in lowered
    assert "importlib" not in lowered
    baseline = _load(BASELINE, "frozen_phase2_baseline")
    statements = baseline.UPGRADE_STATEMENTS
    assert statements[0] == "CREATE SCHEMA hmt_crypto"
    created = {
        statement.split("CREATE TABLE hmt_crypto.", 1)[1].split()[0]
        for statement in statements
        if statement.startswith("CREATE TABLE hmt_crypto.")
    }
    assert created == EXPECTED_TABLES
    assert len(created) == 37
    assert any(
        statement.startswith("ALTER TABLE hmt_crypto.accounts ADD CONSTRAINT")
        for statement in statements
    )


def test_manifest_and_frozen_sql_have_an_exact_pinned_fingerprint() -> None:
    baseline = _load(BASELINE, "fingerprinted_phase2_baseline")
    manifest: dict[str, Any] = json.loads(MANIFEST.read_text(encoding="utf-8"))
    actual = _canonical_fingerprint(baseline.UPGRADE_STATEMENTS)
    assert actual == baseline.BASELINE_SHA256
    assert actual == manifest["upgrade_sha256"]
    assert set(manifest["tables"]) == EXPECTED_TABLES
    assert manifest["schema"] == "hmt_crypto"
    assert manifest["table_count"] == 37
    # This literal changes only when a reviewed new revision or baseline regeneration is intended.
    assert actual == "a61198d3aba414f7708013adc094e6ae1953c8099ca26efc0cc9e957d8083270"


def test_manifest_freezes_baseline_downgrade_and_both_revision_sources() -> None:
    baseline = _load(BASELINE, "fully_fingerprinted_phase2_baseline")
    manifest: dict[str, Any] = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected_downgrade = _canonical_fingerprint(
        (baseline.EMPTY_SCHEMA_GUARD, *baseline.DOWNGRADE_STATEMENTS)
    )
    assert manifest["baseline_downgrade_sha256"] == expected_downgrade
    assert manifest["baseline_source_sha256"] == hashlib.sha256(BASELINE.read_bytes()).hexdigest()
    assert manifest["hardening_source_sha256"] == hashlib.sha256(HARDENING.read_bytes()).hexdigest()
    assert manifest["head_revision"] == "phase2_hardening_v1"
    assert manifest["format_version"] == 2
    assert expected_downgrade == "a6cd5a1a0fb008ebecbfbb9da8645026fc1b436ca462dc4b20f4122e8688d946"
    assert manifest["baseline_source_sha256"] == (
        "86a75e6a27e049861c9fab7096d2cbbc06076a44a2e42d55dd00465cdb749209"
    )
    assert manifest["hardening_source_sha256"] == (
        "a75d38cfd0594435cb9ab1780a23c0b4fc5215a1543f24f1af6e3b61d4983179"
    )


def test_hardening_function_sql_has_no_unintended_text_bind_parameters() -> None:
    hardening = _load(HARDENING, "bind_safe_phase2_hardening")
    for statement in (
        hardening.REJECT_MUTATION_FUNCTION,
        hardening.PAYLOAD_MUTATION_GUARD,
        hardening.PURGE_PAYLOADS_FUNCTION,
        hardening.REVOKE_BROWSER_ACCESS,
    ):
        assert not sa.text(statement).compile().params


def test_frozen_history_verifier_has_no_rewrite_mode(tmp_path: Path) -> None:
    copied_tool = tmp_path / "tools" / "generate_migration_baseline.py"
    copied_tool.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "tools" / "generate_migration_baseline.py", copied_tool)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [sys.executable, str(copied_tool), "--write"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert not (tmp_path / "migrations" / "versions" / "phase2_baseline_v1.py").exists()


def test_frozen_history_verifier_does_not_import_current_orm_models() -> None:
    script = r"""import builtins
import runpy
import sys

real_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == "hmt_crypto.persistence.models":
        raise AssertionError("historical verifier imported live ORM metadata")
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded
sys.argv = ["tools/generate_migration_baseline.py"]
runpy.run_path("tools/generate_migration_baseline.py", run_name="__main__")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_online_failure_traceback_does_not_retain_owner_credentials() -> None:
    script = r"""import runpy
from alembic import context
from alembic.config import Config
import sqlalchemy.ext.asyncio

secret = "online-migration-traceback-secret-canary"
context.config = Config("alembic.ini")
context.is_offline_mode = lambda: False

def fail_engine(*args, **kwargs):
    raise OSError("synthetic connect failure")

sqlalchemy.ext.asyncio.create_async_engine = fail_engine
try:
    runpy.run_path("migrations/env.py", run_name="migration_traceback_probe")
except RuntimeError as error:
    traceback = error.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_code.co_filename.endswith("migrations/env.py"):
            for value in traceback.tb_frame.f_locals.values():
                if value == secret or getattr(value, "password", None) == secret:
                    raise AssertionError("migration traceback retained owner credentials")
        traceback = traceback.tb_next
else:
    raise AssertionError("synthetic online failure was not sanitized")
"""
    environment = dict(os.environ)
    environment["DATABASE_MIGRATION_URL"] = (
        "postgresql+asyncpg://migration_owner:online-migration-traceback-secret-canary@"
        "db.example/hmt_crypto"
    )
    environment["DATABASE_URL"] = (
        "postgresql+asyncpg://hmt_runtime:runtime-secret@db.example/hmt_crypto"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_online_failure_reports_only_bounded_sqlstate() -> None:
    script = r"""import runpy
from alembic import context
from alembic.config import Config
import sqlalchemy.ext.asyncio

context.config = Config("alembic.ini")
context.is_offline_mode = lambda: False

class SafeDatabaseFailure(Exception):
    sqlstate = "42501"

def fail_engine(*args, **kwargs):
    raise SafeDatabaseFailure("server-message-secret-canary")

sqlalchemy.ext.asyncio.create_async_engine = fail_engine
try:
    runpy.run_path("migrations/env.py", run_name="migration_sqlstate_probe")
except RuntimeError as error:
    rendered = str(error)
    assert "SQLSTATE 42501" in rendered
    assert "server-message-secret-canary" not in rendered
    assert error.__cause__ is None and error.__context__ is None
else:
    raise AssertionError("synthetic online failure was not sanitized")
"""
    environment = dict(os.environ)
    environment["DATABASE_MIGRATION_URL"] = (
        "postgresql+asyncpg://migration_owner:sqlstate-secret@db.example/hmt_crypto"
    )
    environment["DATABASE_URL"] = (
        "postgresql+asyncpg://hmt_runtime:runtime-secret@db.example/hmt_crypto"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("stage", ["create", "connect", "dispose"])
def test_online_base_exception_paths_are_recursively_secret_safe(stage: str) -> None:
    script = f"""import asyncio
import runpy
from alembic import context
from alembic.config import Config
import sqlalchemy.ext.asyncio

stage = {stage!r}
secret = "online-base-exception-secret-canary"
context.config = Config("alembic.ini")
context.is_offline_mode = lambda: False

class FakeConnection:
    async def run_sync(self, operation):
        return None

class FakeContext:
    async def __aenter__(self):
        if stage == "connect":
            raise asyncio.CancelledError()
        return FakeConnection()
    async def __aexit__(self, exc_type, exc, traceback):
        return False

class FakeEngine:
    def connect(self):
        return FakeContext()
    async def dispose(self):
        if stage == "dispose":
            raise asyncio.CancelledError()

def fake_engine(*args, **kwargs):
    if stage == "create":
        raise KeyboardInterrupt()
    return FakeEngine()

sqlalchemy.ext.asyncio.create_async_engine = fake_engine
try:
    runpy.run_path("migrations/env.py", run_name="migration_base_exception_probe")
except BaseException as error:
    if type(error) is not RuntimeError:
        raise AssertionError(f"unsanitized {{type(error).__name__}} at {{stage}}")
    if error.__cause__ is not None or error.__context__ is not None:
        raise AssertionError("sanitized migration error retained an exception link")
    traceback = error.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_code.co_filename.endswith("migrations/env.py"):
            for value in traceback.tb_frame.f_locals.values():
                if value == secret or getattr(value, "password", None) == secret:
                    raise AssertionError("migration traceback retained owner credentials")
        traceback = traceback.tb_next
else:
    raise AssertionError("synthetic BaseException was not sanitized")
"""
    environment = dict(os.environ)
    environment["DATABASE_MIGRATION_URL"] = (
        "postgresql+asyncpg://migration_owner:online-base-exception-secret-canary@"
        "db.example/hmt_crypto"
    )
    environment["DATABASE_URL"] = (
        "postgresql+asyncpg://hmt_runtime:runtime-secret@db.example/hmt_crypto"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_offline_failure_traceback_does_not_retain_owner_credentials() -> None:
    script = r"""import contextlib
import runpy
from alembic import context
from alembic.config import Config

secret = "offline-migration-traceback-secret-canary"
context.config = Config("alembic.ini")
context.is_offline_mode = lambda: True
context.configure = lambda **kwargs: None
context.begin_transaction = contextlib.nullcontext

def fail_render():
    raise KeyboardInterrupt()

context.run_migrations = fail_render
try:
    runpy.run_path("migrations/env.py", run_name="migration_offline_traceback_probe")
except RuntimeError as error:
    traceback = error.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_code.co_filename.endswith("migrations/env.py"):
            for value in traceback.tb_frame.f_locals.values():
                if value == secret or getattr(value, "password", None) == secret:
                    raise AssertionError("offline traceback retained owner credentials")
        traceback = traceback.tb_next
else:
    raise AssertionError("synthetic offline failure was not sanitized")
"""
    environment = dict(os.environ)
    environment["DATABASE_MIGRATION_URL"] = (
        "postgresql+asyncpg://migration_owner:offline-migration-traceback-secret-canary@"
        "db.example/hmt_crypto"
    )
    environment["DATABASE_URL"] = (
        "postgresql+asyncpg://hmt_runtime:runtime-secret@db.example/hmt_crypto"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_hardening_is_invoker_only_and_revokes_browser_roles() -> None:
    source = HARDENING.read_text(encoding="utf-8")
    upper = source.upper()
    assert "SECURITY DEFINER" not in upper
    assert "SECURITY INVOKER" in upper
    assert "REVOKE ALL ON SCHEMA HMT_CRYPTO FROM PUBLIC" in upper
    assert "ANON" in upper
    assert "AUTHENTICATED" in upper
    assert "PURGE_EXPIRED_AUDIT_PAYLOAD" in upper
    assert "HMT_AUDIT_RETENTION" in upper
    assert "API_AUDIT_PAYLOADS" in upper
    hardening = _load(HARDENING, "phase2_hardening")
    assert {
        "api_audit_events",
        "order_attempt_events",
        "order_transitions",
        "fills",
        "risk_decisions",
        "risk_rate_consumptions",
    } <= set(hardening.APPEND_ONLY_TABLES)


def test_retention_purge_matches_the_existing_database_owned_contract() -> None:
    hardening = _load(HARDENING, "phase2_retention_hardening")
    purge = hardening.PURGE_PAYLOADS_FUNCTION
    signature = "purge_expired_audit_payload(\n    p_owning_event_id uuid,\n    p_payload_slot text"
    assert signature in purge
    assert "SECURITY INVOKER" in purge
    assert "SECURITY DEFINER" not in purge
    assert "current_user <> 'hmt_audit_retention'" in purge
    assert purge.index("FOR UPDATE") < purge.index("sampled_at := clock_timestamp()")
    assert purge.index("INSERT INTO hmt_crypto.api_audit_events") < purge.index(
        "DELETE FROM hmt_crypto.api_audit_payloads"
    )
    assert '"stage":"MAINTENANCE_ROOT/v1"' in purge
    assert '"stage":"PAYLOAD_PURGE/v1"' in purge
    assert "RETURN QUERY SELECT" in purge
    for forbidden_parameter in (
        "p_actor_role",
        "p_audit_recorded_at",
        "p_payload_text",
        "p_root_id",
        "p_purge_id",
    ):
        assert forbidden_parameter not in purge


def test_revisions_require_explicit_empty_dev_acknowledgement_for_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HMT_CRYPTO_ALLOW_EMPTY_DOWNGRADE", raising=False)
    baseline = _load(BASELINE, "guarded_baseline_revision")
    with pytest.raises(RuntimeError, match="empty development database"):
        baseline.downgrade()


def test_hardening_revision_refuses_even_an_acknowledged_partial_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "HMT_CRYPTO_ALLOW_EMPTY_DOWNGRADE",
        "I_ACKNOWLEDGE_EMPTY_DEV_DATABASE",
    )
    hardening = _load(HARDENING, "irreversible_hardening_revision")
    with pytest.raises(RuntimeError, match="irreversible"):
        hardening.downgrade()


def test_offline_upgrade_renders_without_importing_models_or_leaking_dsn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "offline-migration-secret"
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        f"postgresql+asyncpg://owner:{secret}@db.example:5432/hmt_crypto",
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://hmt_runtime:runtime-secret@db.example:5432/hmt_crypto",
    )
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head", sql=True)
    rendered = capsys.readouterr().out
    assert "CREATE TABLE public.hmt_crypto_alembic_version" in rendered
    assert "CREATE TABLE hmt_crypto_alembic_version" not in rendered
    assert "CREATE SCHEMA hmt_crypto" in rendered
    assert rendered.count("CREATE TABLE hmt_crypto.") == 68
    assert "CREATE TABLE hmt_crypto.kill_workflows" in rendered
    assert "CREATE TABLE hmt_crypto.kill_workflow_events" in rendered
    assert "CREATE TABLE hmt_crypto.paper_execution_heads" in rendered
    assert "CREATE TABLE hmt_crypto.paper_execution_events" in rendered
    assert "CREATE TABLE hmt_crypto.account_reconciliation_checkpoints" in rendered
    assert "CREATE TABLE hmt_crypto.live_order_actions" in rendered
    assert "CREATE TABLE hmt_crypto.platform_sessions" in rendered
    assert "CREATE TABLE hmt_crypto.market_candles" in rendered
    assert "CREATE TABLE hmt_crypto.platform_notifications" in rendered
    assert (
        "ALTER TABLE public.hmt_crypto_alembic_version ALTER COLUMN version_num "
        "TYPE VARCHAR(128)" in rendered
    )
    assert "CREATE TABLE hmt_crypto.tradingview_signals" in rendered
    assert "CONSTRAINT ck_kill_workflows_identity_matches_command" in rendered
    assert "ON hmt_crypto.kill_workflow_events" in rendered
    assert "CREATE TRIGGER" in rendered
    assert secret not in rendered
