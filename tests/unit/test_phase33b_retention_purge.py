"""Offline contract for the forward-only retention purge rewrite."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.safety
PATH = (
    Path(__file__).parents[2]
    / "migrations/versions"
    / "phase33b_retention_purge_v1_remove_audit_purge_update_privilege_requirement.py"
)


def migration():
    specification = importlib.util.spec_from_file_location("phase33b", PATH)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_upgrade_uses_atomic_delete_returning_with_fixed_definer_boundary(monkeypatch):
    module = migration()
    execute = Mock()
    monkeypatch.setattr(module.op, "execute", execute)
    module.upgrade()
    sql = "\n".join(str(call.args[0]) for call in execute.call_args_list)
    assert "DELETE FROM hmt_crypto.api_audit_payloads" in sql
    assert "RETURNING payload.* INTO source_payload" in sql
    assert "FOR UPDATE" not in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog" in sql
    assert "GRANT UPDATE" not in sql
    assert sql.count("DELETE FROM hmt_crypto.api_audit_payloads") == 1
    assert "WHERE role.rolname = 'hmt_audit_retention'" in sql
    assert "FROM PUBLIC" in sql


def test_downgrade_restores_frozen_purge_definition(monkeypatch):
    module = migration()
    execute = Mock()
    monkeypatch.setattr(module.op, "execute", execute)
    module.downgrade()
    sql = "\n".join(str(call.args[0]) for call in execute.call_args_list)
    assert "FOR UPDATE" in sql
    assert sql.count("DELETE FROM hmt_crypto.api_audit_payloads") == 1
    assert "SECURITY DEFINER" not in sql
    assert module.down_revision == "phase33_private_function_acl_v1"
