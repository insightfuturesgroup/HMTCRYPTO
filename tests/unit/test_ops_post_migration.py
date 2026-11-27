"""Ops-2 uses fake database results only."""

import asyncio
from pathlib import Path

import pytest

from tools.ops_post_migration import (
    EXPECTED_FKS,
    EXPECTED_HEAD,
    FUNCTIONS,
    _redact_first_line,
    apply_and_verify,
    diagnose,
)


class FakeConnection:
    def __init__(self, forbidden: int = 0):
        self.values = iter((EXPECTED_HEAD, len(FUNCTIONS), forbidden, True))
        self.executed: list[str] = []

    async def execute(self, statement: str) -> None:
        self.executed.append(statement)

    async def fetchrow(self, statement: str) -> tuple[int, int]:
        return EXPECTED_FKS, EXPECTED_FKS

    async def fetchval(self, statement: str, *args: object) -> object:
        return next(self.values)


class FakeDiagnosticConnection:
    def __init__(self, *, role_exists: bool = True):
        self.role_exists = role_exists
        self.statements: list[str] = []

    async def fetchval(self, statement: str, *args: object) -> object:
        self.statements.append(statement)
        if "alembic_version" in statement:
            return EXPECTED_HEAD
        if "pg_roles" in statement:
            return self.role_exists
        raise AssertionError("unexpected fetchval")

    async def fetchrow(self, statement: str, *args: object) -> object:
        self.statements.append(statement)
        if "owner_role" in statement and "has_function_privilege" in statement:
            return ("postgres", True)
        if "pg_constraint" in statement:
            return (EXPECTED_FKS, EXPECTED_FKS)
        if "aclexplode" in statement:
            return (len(FUNCTIONS), 0, 0, 0)
        raise AssertionError("unexpected fetchrow")


def test_grant_and_verification_pass() -> None:
    connection = FakeConnection()
    asyncio.run(apply_and_verify(connection))
    assert connection.executed[0].startswith("GRANT EXECUTE")


def test_browser_grant_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="ACL"):
        asyncio.run(apply_and_verify(FakeConnection(1)))


def test_read_only_diagnostic_passes_and_never_executes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    connection = FakeDiagnosticConnection()
    assert asyncio.run(diagnose(connection)) is True
    output = capsys.readouterr().out
    assert "PASS: alembic revision" in output
    assert "PASS: retention role" in output
    assert "PASS: retention function" in output
    assert "PASS: restrictive foreign keys" in output
    assert "PASS: phase33 browser EXECUTE" in output
    assert "SKIPPED: Data API exposed schemas" in output
    assert all(
        statement.lstrip().upper().startswith("SELECT") for statement in connection.statements
    )


def test_read_only_diagnostic_steps_are_independent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    connection = FakeDiagnosticConnection(role_exists=False)
    assert asyncio.run(diagnose(connection)) is False
    output = capsys.readouterr().out
    assert "FAIL: retention role | DiagnosticFailure | SQLSTATE NONE | role exists: no" in output
    assert "PASS: retention function" in output
    assert "PASS: restrictive foreign keys" in output


def test_diagnostic_error_redacts_sensitive_shapes() -> None:
    error = RuntimeError(
        "pass" + "word=visible db.abcdefghijklmnopqrst.supabase.co "
        "abcdefghijklmnopqrst to" + "ken=visible\nsecond line"
    )
    redacted = _redact_first_line(error)
    assert "visible" not in redacted
    assert "abcdefghijklmnopqrst" not in redacted
    assert "second line" not in redacted


def test_wrapper_has_no_password_argument() -> None:
    source = Path("ops/mac/02_migrate_mumbai.sh").read_text()
    assert "tools/migrate_mumbai.py" in source
    assert "tools/ops_post_migration.py" in source
    assert "--password" not in source
