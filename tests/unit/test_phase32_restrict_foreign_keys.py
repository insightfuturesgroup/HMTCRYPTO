"""Corrective foreign-key migration and ORM metadata contracts."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic.config import Config
from alembic.script import ScriptDirectory

from hmt_crypto.persistence import models

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations" / "versions" / "phase32_restrict_foreign_keys_v1.py"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("phase32_restrict_foreign_keys_v1", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_orm_foreign_key_has_explicit_restrict_actions() -> None:
    foreign_keys = [fk for table in models.metadata.tables.values() for fk in table.foreign_keys]
    assert foreign_keys
    assert all(fk.onupdate == "RESTRICT" and fk.ondelete == "RESTRICT" for fk in foreign_keys)


def test_phase32_uses_not_valid_then_validate_and_restores_prior_actions(monkeypatch) -> None:
    migration = _load_migration()
    rendered: list[str] = []
    monkeypatch.setattr(migration.op, "execute", lambda statement: rendered.append(str(statement)))

    migration.upgrade()
    upgrade_sql = "\n".join(rendered).upper()
    assert "ON UPDATE RESTRICT" in upgrade_sql
    assert "THEN 'CASCADE'" not in upgrade_sql
    assert "NOT VALID" in upgrade_sql
    assert "VALIDATE CONSTRAINT" in upgrade_sql

    rendered.clear()
    migration.downgrade()
    downgrade_sql = "\n".join(rendered).upper()
    assert "ON UPDATE NO ACTION" in downgrade_sql
    assert "THEN 'CASCADE'" in downgrade_sql
    assert "NOT VALID" in downgrade_sql
    assert "VALIDATE CONSTRAINT" in downgrade_sql


def test_migration_chain_has_one_head_after_phase32() -> None:
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert script.get_heads() == ["phase33b_retention_purge_v1"]
    assert script.get_revision("phase33b_retention_purge_v1").down_revision == (
        "phase33_private_function_acl_v1"
    )
    assert script.get_revision("phase33_private_function_acl_v1").down_revision == (
        "phase32_restrict_foreign_keys_v1"
    )
