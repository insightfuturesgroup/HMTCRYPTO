"""Disposable fixture cleanup must preserve the actual setup failure."""

import asyncio
import importlib
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.schema import DropSchema

pytestmark = [pytest.mark.safety, pytest.mark.asyncio]


def fixture_with_fake_engine(monkeypatch, *, setup_failure=None, cleanup_failure=None):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "integration"))
    module = importlib.import_module("test_repository_postgresql_acceptance")
    statements = []

    async def execute(statement):
        statements.append(statement)
        if isinstance(statement, DropSchema):
            if cleanup_failure is not None:
                raise cleanup_failure
            if setup_failure is not None and not statement.if_exists:
                raise RuntimeError("disposable schema does not exist")

    connection = SimpleNamespace(execute=execute, run_sync=AsyncMock(side_effect=setup_failure))

    @asynccontextmanager
    async def begin():
        yield connection

    engine = SimpleNamespace(begin=begin, dispose=AsyncMock())
    monkeypatch.setattr(module, "_acceptance_url", lambda: object())
    monkeypatch.setattr(
        module, "_DisposableRepositoryDatabase", lambda *args: SimpleNamespace(engine=engine)
    )
    return module.postgres_repository_db.__wrapped__(), engine, statements


@pytest.mark.parametrize("failure_type", [RuntimeError, asyncio.CancelledError])
async def test_setup_failure_is_not_replaced_by_missing_schema(monkeypatch, failure_type):
    failure = failure_type("original offline setup failure")
    fixture, engine, statements = fixture_with_fake_engine(monkeypatch, setup_failure=failure)
    with pytest.raises(failure_type) as captured:
        await anext(fixture)
    assert captured.value is failure
    assert any(
        isinstance(statement, DropSchema) and statement.if_exists for statement in statements
    )
    engine.dispose.assert_awaited_once()


async def test_successful_fixture_drops_schema_and_disposes_engine(monkeypatch):
    fixture, engine, statements = fixture_with_fake_engine(monkeypatch)
    await anext(fixture)
    await fixture.aclose()
    assert any(
        isinstance(statement, DropSchema) and statement.if_exists for statement in statements
    )
    engine.dispose.assert_awaited_once()


async def test_cleanup_failure_is_reported_but_still_disposes_engine(monkeypatch):
    failure = RuntimeError("offline cleanup failure")
    fixture, engine, _ = fixture_with_fake_engine(monkeypatch, cleanup_failure=failure)
    await anext(fixture)
    with pytest.raises(RuntimeError) as captured:
        await fixture.aclose()
    assert captured.value is failure
    engine.dispose.assert_awaited_once()
