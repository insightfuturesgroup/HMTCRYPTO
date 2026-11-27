"""Exercise real endpoint bodies with isolated persistence and auth fakes."""

from __future__ import annotations

import ast
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from hmt_crypto.persistence import models

pytestmark = [pytest.mark.safety, pytest.mark.asyncio]


def endpoint(name, session, actor):
    source = Path("src/hmt_crypto/api/platform_api.py").read_text()
    node = next(
        n
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.AsyncFunctionDef) and n.name == name
    )
    node.decorator_list = []

    @asynccontextmanager
    async def transaction():
        yield session

    namespace = dict(vars(models))
    namespace.update(
        datetime=datetime,
        UTC=UTC,
        uuid4=uuid4,
        select=select,
        sql_delete=delete,
        database=SimpleNamespace(transaction=transaction),
        principal=AsyncMock(return_value=actor),
        admin=AsyncMock(return_value=actor),
        account_permission=AsyncMock(return_value="manage"),
        append_audit=AsyncMock(),
        _request_id=lambda request: "probe",
    )
    exec(
        "from __future__ import annotations\nimport hashlib, secrets\n"
        "from datetime import timedelta",
        namespace,
    )
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
            "<endpoint-flow>",
            "exec",
        ),
        namespace,
    )
    return namespace[name]


async def test_archive_account_preserves_settings_mappings_and_links():
    account = SimpleNamespace(id=uuid4(), archived_at=None, profile_name="probe")
    children = [object(), object(), object()]
    session = SimpleNamespace(
        get=AsyncMock(return_value=account),
        scalar=AsyncMock(
            side_effect=[SimpleNamespace(entries_enabled=False, exits_enabled=False), None]
        ),
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: [])),
    )
    actor = SimpleNamespace(last_login_at=datetime.now(UTC))
    result = await endpoint("archive_account", session, actor)(account.id, object())
    assert result["archived"] is True
    assert account.archived_at is not None
    assert len(children) == 3
    assert all(not statement.args[0].is_delete for statement in session.execute.call_args_list)


async def test_rotate_webhook_key_revokes_old_row_without_deleting():
    actor = SimpleNamespace(profile_id=uuid4(), last_login_at=datetime.now(UTC))
    old = SimpleNamespace(revoked_at=None)
    configuration = SimpleNamespace(created_by_user_id=actor.profile_id)
    added = []
    session = SimpleNamespace(
        get=AsyncMock(return_value=configuration),
        add=added.append,
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: [old])),
    )
    result = await endpoint("rotate_tradingview_configuration_key", session, actor)(
        uuid4(), object()
    )
    assert old.revoked_at is not None
    assert len(added) == 1
    assert added[0].revoked_at is None
    assert added[0].key_sha256 != result["webhookKey"]
    assert all(not statement.args[0].is_delete for statement in session.execute.call_args_list)


async def test_edit_configuration_explicitly_replaces_only_links():
    actor = SimpleNamespace(profile_id=uuid4(), role="admin")
    configuration = SimpleNamespace(created_by_user_id=actor.profile_id, enabled=True)
    added = []
    session = SimpleNamespace(
        get=AsyncMock(return_value=configuration), add=added.append, execute=AsyncMock()
    )
    account_id = uuid4()
    body = SimpleNamespace(account_ids=[account_id], name=None, enabled=None)
    await endpoint("update_tradingview_configuration", session, actor)(uuid4(), body, object())
    statement = session.execute.call_args.args[0]
    assert statement.is_delete
    assert statement.table.name == "tradingview_configuration_accounts"
    assert len(added) == 1
    assert added[0].account_id == account_id
