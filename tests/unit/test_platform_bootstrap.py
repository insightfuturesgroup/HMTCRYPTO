"""First-admin creation materializes its FK parent before audit evidence."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import pytest

from hmt_crypto.api import platform_bootstrap
from hmt_crypto.api.settings import ControlRuntimeSettings
from hmt_crypto.persistence.models import PlatformAuditEvent, PlatformUser


class _Result:
    def scalar_one_or_none(self) -> None:
        return None


class _Session:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def execute(self, _statement: object) -> _Result:
        return _Result()

    async def scalar(self, _statement: object) -> int:
        return 0

    def add(self, value: object) -> None:
        if type(value) is PlatformUser:
            self.events.append("user")
        elif type(value) is PlatformAuditEvent:
            self.events.append("audit")
        else:
            raise AssertionError("unexpected bootstrap model")

    async def flush(self) -> None:
        self.events.append("flush")


class _Database:
    def __init__(self) -> None:
        self.session = _Session()
        self.closed = False

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_Session]:
        yield self.session

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_first_admin_flushes_user_before_fk_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _Database()
    monkeypatch.setattr(platform_bootstrap, "Database", lambda _config: database)

    profile_id, created = await platform_bootstrap.bootstrap_first_admin(
        ControlRuntimeSettings(),
        auth_user_id=UUID("5842cd80-e350-4972-95b5-e155dddbcfd8"),
        email="admin@example.com",
        full_name="Admin User",
    )

    assert profile_id.int != 0
    assert created is True
    assert database.session.events == ["user", "flush", "audit"]
    assert database.closed is True
