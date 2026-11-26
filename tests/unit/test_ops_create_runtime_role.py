"""Operator bootstrap tests use fake values and never a database connection."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

SPEC = importlib.util.spec_from_file_location(
    "ops_create_runtime_role", Path(__file__).parents[2] / "tools/ops_create_runtime_role.py"
)
assert SPEC is not None and SPEC.loader is not None
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


def test_dry_run_does_not_prompt_connect_or_read_ca(monkeypatch, capsys):
    forbidden = Mock(side_effect=AssertionError("unexpected operator action"))
    monkeypatch.setattr(sys, "argv", ["ops_create_runtime_role.py", "--dry-run"])
    monkeypatch.setattr(helper.getpass, "getpass", forbidden)
    monkeypatch.setattr(helper.subprocess, "run", forbidden)
    monkeypatch.setattr(helper.ssl, "create_default_context", forbidden)
    assert helper.main() == 0
    forbidden.assert_not_called()
    assert "DRY RUN" in capsys.readouterr().out


def test_connection_env_is_scoped_and_tls_verified(monkeypatch):
    monkeypatch.setenv("PGOPTIONS", "unsafe-inherited-value")
    result = helper.connection_environment(
        "aws-0-ap-south-1.pooler.supabase.com", "a" * 20, Path("/fake/ca.crt"), "fake-owner"
    )
    assert result["PGSSLMODE"] == "verify-full"
    assert result["PGPORT"] == "5432"
    assert result["PGUSER"] == "postgres." + "a" * 20
    assert "PGOPTIONS" not in result
    assert result["PSQL_HISTORY"] == "/dev/null"


@pytest.mark.parametrize("host", ["localhost", "host?sslmode=disable", "https://fake.example"])
def test_rejects_non_pooler_host(host):
    with pytest.raises(ValueError):
        helper.connection_environment(host, "a" * 20, Path("/fake/ca.crt"), "fake-owner")


@pytest.mark.parametrize(
    "password,confirmation",
    [("short", "short"), ("x" * 20, "y" * 20), ("x" * 20 + "\n", "x" * 20 + "\n")],
)
def test_password_validation(password, confirmation):
    with pytest.raises(ValueError):
        helper.validate_password(password, confirmation)


def test_password_accepts_special_characters():
    fake = "fake-$-!-'-%-runtime-password"
    helper.validate_password(fake, fake)


def test_sql_command_has_no_credential_arguments(monkeypatch):
    fake = "fake-owner-password"
    run = Mock(return_value=type("Result", (), {"returncode": 0, "stdout": "t\n"})())
    monkeypatch.setattr(helper.subprocess, "run", run)
    assert helper.run_sql("/fake/psql", {"PGPASSWORD": fake}, "SELECT 1") == "t"
    args, kwargs = run.call_args
    assert fake not in str(args)
    assert kwargs["env"]["PGPASSWORD"] == fake
    assert kwargs["capture_output"] is True


def test_private_pty_supplies_two_hidden_passwords(tmp_path):
    fake_psql = tmp_path / "fake_psql"
    fake_psql.write_text(
        f"#!{sys.executable}\n"
        "import os, sys, termios\n"
        "assert not (termios.tcgetattr(0)[3] & termios.ECHO)\n"
        "for prompt in ('Enter new password:', 'Enter it again:'):\n"
        "    os.write(1, prompt.encode())\n"
        "    assert sys.stdin.readline().strip() == 'fake-runtime-password-only'\n"
    )
    fake_psql.chmod(0o700)
    helper.set_password(str(fake_psql), {"PATH": "/usr/bin:/bin"}, "fake-runtime-password-only")


def test_role_plan_is_restricted():
    for attribute in ("NOSUPERUSER", "NOCREATEDB", "NOCREATEROLE", "NOREPLICATION", "NOBYPASSRLS"):
        assert attribute in helper.ROLE_SQL
    assert "GRANT CONNECT" in helper.ROLE_SQL
    assert "GRANT ALL" not in helper.ROLE_SQL
    assert "PASSWORD" not in helper.ROLE_SQL


def test_existing_role_does_not_reset_without_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["ops_create_runtime_role.py"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(helper.shutil, "which", lambda _: "/fake/psql")
    monkeypatch.delenv("HMT_SUPABASE_PROJECT_REF", raising=False)
    ca = tmp_path / "fake-ca"
    ca.write_text("fake-only")
    prompts = iter(["aws-0-ap-south-1.pooler.supabase.com", "a" * 20, str(ca), "fake-owner"])
    monkeypatch.setattr(helper.getpass, "getpass", lambda _: next(prompts))
    monkeypatch.setattr(helper.ssl, "create_default_context", Mock())
    confirmations = iter(["", "APPLY"])
    monkeypatch.setattr("builtins.input", lambda _: next(confirmations))
    sql = Mock(side_effect=["t", ""])
    monkeypatch.setattr(helper, "run_sql", sql)
    password = Mock()
    monkeypatch.setattr(helper, "set_password", password)
    assert helper.main() == 0
    password.assert_not_called()
    assert sql.call_args_list[-1].args[-1] == helper.ROLE_SQL


def test_failures_do_not_print_exception_details(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ops_create_runtime_role.py"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(helper.shutil, "which", lambda _: "/fake/psql")
    monkeypatch.setattr(helper.getpass, "getpass", Mock(side_effect=RuntimeError("fake-sensitive")))
    assert helper.main() == 1
    assert "fake-sensitive" not in capsys.readouterr().out
