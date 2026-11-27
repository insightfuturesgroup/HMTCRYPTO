"""Offline checks for the private-function ACL migration contract."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.safety
PATH = Path(__file__).parents[2] / "migrations/versions/phase33_private_function_acl_v1.py"


def migration():
    spec = importlib.util.spec_from_file_location("phase33", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_revokes_existing_and_global_and_schema_defaults(monkeypatch):
    module = migration()
    execute = Mock()
    monkeypatch.setattr(module.op, "execute", execute)
    module.upgrade()
    sql = "\n".join(str(call.args[0]) for call in execute.call_args_list)
    assert "REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA hmt_crypto FROM PUBLIC" in sql
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE %I " in sql
    assert "IN SCHEMA hmt_crypto REVOKE EXECUTE ON FUNCTIONS" in sql
    assert "'anon', 'authenticated'" in sql
    assert "pg_roles" in sql
    assert "GRANT EXECUTE" not in sql


def test_downgrade_restores_only_repository_defined_prior_public_function(monkeypatch):
    module = migration()
    execute = Mock()
    monkeypatch.setattr(module.op, "execute", execute)
    module.downgrade()
    sql = "\n".join(str(call.args[0]) for call in execute.call_args_list)
    assert "REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA hmt_crypto FROM PUBLIC" in sql
    assert "REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA hmt_crypto FROM %I" in sql
    assert "GRANT EXECUTE ON FUNCTION " in sql
    assert "guard_account_reconciliation_checkpoints_v1() TO PUBLIC" in sql
    assert "GRANT EXECUTE ON ALL FUNCTIONS" not in sql
    assert module.down_revision == "phase32_restrict_foreign_keys_v1"
