"""Ops-2 uses fake database results only."""

import asyncio
from pathlib import Path

import pytest

from tools.ops_post_migration import EXPECTED_FKS, EXPECTED_HEAD, FUNCTIONS, apply_and_verify


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


def test_grant_and_verification_pass() -> None:
    connection = FakeConnection()
    asyncio.run(apply_and_verify(connection))
    assert connection.executed[0].startswith("GRANT EXECUTE")


def test_browser_grant_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="ACL"):
        asyncio.run(apply_and_verify(FakeConnection(1)))


def test_wrapper_has_no_password_argument() -> None:
    source = Path("ops/mac/02_migrate_mumbai.sh").read_text()
    assert "tools/migrate_mumbai.py" in source
    assert "tools/ops_post_migration.py" in source
    assert "--password" not in source
