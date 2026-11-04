"""Offline schema fixtures using real file-backed SQLite and sanitized products."""

import importlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, update

from hmt_crypto.config import Settings
from hmt_crypto.exchange.delta.wire_schemas import parse_product
from hmt_crypto.persistence.db import Database

NOW = datetime(2026, 11, 4, tzinfo=UTC)
HASH = "a" * 64


def schema_api():
    try:
        module = importlib.import_module("hmt_crypto.persistence.models")
    except ModuleNotFoundError:
        pytest.fail("Identity/catalogue/signal persistence schema is missing", pytrace=False)
    return SimpleNamespace(**{name: getattr(module, name) for name in module.__all__})


@pytest_asyncio.fixture
async def schema_db(tmp_path):
    api = schema_api()
    database = Database(
        Settings(_env_file=None, DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path}/schema.db")
    )
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(api.metadata.create_all)
        yield database
        async with database.engine.begin() as connection:
            # SQLite cannot ALTER away the cyclic current-credential FK. Empty
            # this disposable test DB explicitly before testing empty drop_all.
            await connection.execute(update(api.Account).values(current_credential_version=None))
            for model in (
                api.FillOrderLink,
                api.Fill,
                api.OrderTransition,
                api.OrderAttemptEvent,
                api.OrderAttempt,
                api.Order,
                api.CommandOperation,
                api.Signal,
                api.CatalogueProduct,
                api.CredentialVersion,
                api.Account,
                api.CatalogueSnapshot,
            ):
                await connection.execute(delete(model))
            await connection.run_sync(api.metadata.drop_all)
    finally:
        await database.aclose()


def account(**changes):
    values = dict(
        id=uuid4(),
        region="india",
        environment="testnet",
        mode="PAPER",
        profile_name="fixture",
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    values.update(changes)
    return schema_api().Account(**values)


def scope(owner):
    return dict(
        account_id=owner.id, region=owner.region, environment=owner.environment, mode=owner.mode
    )


def credential(owner, **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        credential_version=1,
        credential_slot=f"DELTA_{owner.region.upper()}_{owner.environment.upper()}",
        fingerprint_hint=HASH[:12],
        recorded_at=NOW,
    )
    values.update(changes)
    return schema_api().CredentialVersion(**values)


def snapshot(**changes):
    values = dict(
        id=uuid4(),
        region="india",
        environment="testnet",
        revision=1,
        source="delta_public_products_v2",
        content_sha256=HASH,
        fetched_at=NOW,
        validated_at=NOW,
        fresh_until=NOW + timedelta(minutes=5),
        product_count=1,
    )
    values.update(changes)
    return schema_api().CatalogueSnapshot(**values)


def product():
    path = Path(__file__).parents[1] / "fixtures/delta/products_catalog_valid.json"
    return parse_product(json.loads(path.read_text())[0], endpoint_id="persistence_fixture")


def signal(owner, **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        signal_key="fixture-intent",
        product_id=1101,
        source="fixture",
        generated_at=NOW,
        recorded_at=NOW,
        intent={"side": "buy"},
    )
    values.update(changes)
    return schema_api().Signal.from_intent(**values)
