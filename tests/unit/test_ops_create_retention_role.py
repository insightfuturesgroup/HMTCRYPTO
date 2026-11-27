"""Fake-only tests for the ops-2b retention-role tool."""

import asyncio
from pathlib import Path

import pytest

from tools.ops_create_retention_role import (
    CREATE_AND_CHECK_ROLE_SQL,
    ROLE_QUERY,
    apply_role,
    inspect_plan,
    verify_role,
)


class FakeTransaction:
    def __init__(self, connection: "FakeConnection"):
        self.connection = connection

    async def __aenter__(self) -> None:
        self.connection.events.append("BEGIN")

    async def __aexit__(self, *args: object) -> None:
        self.connection.events.append("COMMIT")


class FakeConnection:
    function = (1234, "hmt_crypto", "purge_expired_audit_payload", "uuid, text", "postgres")

    def __init__(self, *, role: tuple[bool, ...] | None = None):
        self.role = role
        self.events: list[str] = []

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    async def execute(self, statement: str, *args: object) -> None:
        self.events.append(statement)

    async def fetch(self, statement: str, *args: object) -> list[tuple[object, ...]]:
        self.events.append(statement)
        if "pg_get_function_identity_arguments" in statement:
            return [self.function]
        if "aclexplode" in statement:
            return [("postgres",), ("hmt_audit_retention",)]
        raise AssertionError("unexpected fetch")

    async def fetchrow(self, statement: str, *args: object) -> tuple[bool, ...] | None:
        self.events.append(statement)
        if statement == ROLE_QUERY:
            return self.role
        raise AssertionError("unexpected fetchrow")

    async def fetchval(self, statement: str, *args: object) -> object:
        self.events.append(statement)
        if "SELECT format" in statement:
            return (
                "GRANT EXECUTE ON FUNCTION "
                "hmt_crypto.purge_expired_audit_payload(uuid, text) "
                "TO hmt_audit_retention"
            )
        if "has_function_privilege" in statement or "pg_has_role" in statement:
            return False
        raise AssertionError("unexpected fetchval")


SAFE_ROLE = (False, False, False, False, False, False)


def test_dry_run_is_read_only(capsys: pytest.CaptureFixture[str]) -> None:
    connection = FakeConnection()
    asyncio.run(inspect_plan(connection))
    assert "DRY RUN: read only" in capsys.readouterr().out
    assert all(event.lstrip().upper().startswith("SELECT") for event in connection.events)


def test_apply_uses_one_transaction_and_catalog_signature() -> None:
    connection = FakeConnection()
    asyncio.run(apply_role(connection))
    assert connection.events[0] == "BEGIN"
    assert CREATE_AND_CHECK_ROLE_SQL in connection.events
    assert any(event.startswith("GRANT EXECUTE ON FUNCTION") for event in connection.events)
    assert connection.events[-1] == "COMMIT"
    assert not any("GRANT " in event and "EXECUTE" not in event for event in connection.events)


def test_verify_accepts_only_owner_and_retention_role(
    capsys: pytest.CaptureFixture[str],
) -> None:
    connection = FakeConnection(role=SAFE_ROLE)
    asyncio.run(verify_role(connection))
    output = capsys.readouterr().out
    assert "PASS: EXECUTE grantees are owner and hmt_audit_retention only" in output
    assert "PASS: hmt_crypto_app is not a member" in output


def test_existing_unsafe_role_fails_closed() -> None:
    connection = FakeConnection(role=(True, False, False, False, False, False))
    with pytest.raises(RuntimeError, match="unsafe"):
        asyncio.run(inspect_plan(connection))


def test_operator_entrypoints_keep_owner_input_out_of_argv_env_and_output() -> None:
    source = Path("tools/ops_create_retention_role.py").read_text()
    wrapper = Path("ops/mac/02b_create_retention_role.sh").read_text()
    assert 'getpass.getpass("Owner postgres password (hidden): "' in source
    assert "os.environ" not in source
    assert "PGPASSWORD" not in source
    assert "print(owner_input" not in source
    assert "sys.argv" not in source
    assert "tools/ops_create_retention_role.py" in wrapper
