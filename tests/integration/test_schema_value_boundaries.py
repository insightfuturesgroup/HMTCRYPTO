"""Exact reconstruction, ownership and safe representations at storage boundaries."""

import json
from datetime import timedelta, timezone
from decimal import Decimal
from hashlib import sha256

import pytest
from schema_test_support import NOW, account, credential, product, schema_api, signal, snapshot
from schema_test_support import schema_db as schema_db
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from hmt_crypto.persistence.errors import PersistenceError

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_product_round_trip_preserves_exact_fields_assets_and_missing_provenance(schema_db):
    api = schema_api()
    evidence = snapshot()
    original = product()
    row = api.CatalogueProduct.from_product(snapshot_id=evidence.id, product=original)
    original.underlying_asset.__dict__["symbol"] = "CALLER-MUTATION"
    async with schema_db.transaction() as session:
        session.add(evidence)
        await session.flush()
        session.add(row)
    async with schema_db.transaction() as session:
        stored = (await session.scalars(select(api.CatalogueProduct))).one()
        restored = stored.to_product()
        assert restored.id == 1101
        assert restored.symbol == "XBTUSD_CAT"
        assert restored.contract_value == Decimal("0.001")
        assert restored.tick_size == Decimal("0.5")
        assert restored.underlying_asset.symbol == "XBT"
        assert restored.settling_asset.id == 12
        assert restored.model_dump() == product().model_dump()
        assert restored.model_fields_set == product().model_fields_set
        assert '"contract_value":"0.001"' in stored.product_payload
        restored.underlying_asset.__dict__["symbol"] = "RETURNED-MUTATION"
        assert stored.to_product().underlying_asset.symbol == "XBT"
    missing = product().model_dump(exclude={"only_reduce_only_orders_allowed"})
    from hmt_crypto.domain import Product

    partial = Product.model_validate(missing)
    restored = api.CatalogueProduct.from_product(
        snapshot_id=evidence.id, product=partial
    ).to_product()
    assert "only_reduce_only_orders_allowed" not in restored.model_fields_set
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.execute(delete(api.CatalogueSnapshot))


@pytest.mark.asyncio
async def test_product_unique_identity_and_exact_symbol_are_per_snapshot(schema_db):
    api = schema_api()
    first, second = snapshot(), snapshot(revision=2)
    async with schema_db.transaction() as session:
        session.add_all([first, second])
    async with schema_db.transaction() as session:
        session.add_all(
            [
                api.CatalogueProduct.from_product(snapshot_id=s.id, product=product())
                for s in (first, second)
            ]
        )
    for change in ({}, {"id": 999}, {"symbol": "OTHER"}):
        candidate = product().model_copy(update=change)
        with pytest.raises(PersistenceError):
            async with schema_db.transaction() as session:
                session.add(
                    api.CatalogueProduct.from_product(snapshot_id=first.id, product=candidate)
                )


@pytest.mark.parametrize(
    "changes",
    [
        {"tick_size": Decimal("0")},
        {"contract_value": Decimal("NaN")},
        {"tick_size": 0.5},
        {"contract_value": Decimal("1e20")},
        {"contract_value": Decimal("1e-19")},
        {"symbol": "x" * 129},
    ],
)
def test_product_storage_revalidates_mutated_domain_and_exact_storage_envelope(changes):
    from uuid import uuid4

    value = product()
    value.__dict__.update(changes)
    with pytest.raises(PersistenceError):
        schema_api().CatalogueProduct.from_product(snapshot_id=uuid4(), product=value)


def test_product_reconstruction_rejects_corruption_and_identity_disagreement():
    from uuid import uuid4

    api = schema_api()
    row = api.CatalogueProduct.from_product(snapshot_id=uuid4(), product=product())
    row.product_id = 999
    with pytest.raises(PersistenceError):
        row.to_product()
    row.product_id = 1101
    row.product_payload = '{"values":{"id":1101}}'
    with pytest.raises(PersistenceError):
        row.to_product()


@pytest.mark.asyncio
async def test_signal_canonical_sanitized_owned_payload_hash_and_utc_round_trip(schema_db):
    owner = account()
    payload = {"z": ["buy"], "api_secret": "SECRET-INTENT", "size": Decimal("1.2500")}
    row = signal(
        owner, intent=payload, generated_at=NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))
    )
    payload["z"].append("CALLER-MUTATION")
    assert row.intent_payload == '{"api_secret":"[REDACTED]","size":"1.25","z":["buy"]}'
    assert row.intent_sha256 == sha256(row.intent_payload.encode()).hexdigest()
    assert "SECRET-INTENT" not in repr(row)
    async with schema_db.transaction() as session:
        session.add(owner)
    async with schema_db.transaction() as session:
        session.add(row)
    async with schema_db.transaction() as session:
        stored = (await session.scalars(select(schema_api().Signal))).one()
        assert stored.generated_at == NOW
        assert stored.recorded_at == NOW
        assert stored.intent_payload == row.intent_payload


@pytest.mark.parametrize("payload", [{"x": 0.5}, {"x": Decimal("Infinity")}, {"x": "a" * 17000}])
def test_signal_rejects_inexact_or_unbounded_intent(payload):
    with pytest.raises(PersistenceError):
        signal(account(), intent=payload)


@pytest.mark.asyncio
async def test_credential_fingerprint_and_markers_never_leak_in_safe_views_or_sqlalchemy_errors(
    schema_db,
):
    owner = account()
    fingerprint = "0123456789abcdef" * 4
    marker = "SECRET-CREDENTIAL-MARKER"
    row = credential(owner, fingerprint_sha256=fingerprint, credential_slot=marker)
    assert fingerprint not in repr(row)
    assert marker not in repr(row)
    assert marker not in json.dumps(row.to_safe_dict(), default=str)
    assert fingerprint not in json.dumps(row.to_safe_dict(), default=str)
    async with schema_db.transaction() as session:
        session.add(owner)
    async with schema_db.transaction() as session:
        session.add(row)
    # Inspect the actual SQLAlchemy exception before Database's safe error conversion.
    async with schema_db.engine.connect() as connection:
        with pytest.raises(IntegrityError) as caught:
            await connection.execute(
                schema_api()
                .CredentialVersion.__table__.insert()
                .values(
                    id=row.id,
                    **{
                        key: getattr(row, key)
                        for key in (
                            "account_id",
                            "region",
                            "environment",
                            "mode",
                            "credential_version",
                            "credential_slot",
                            "recorded_at",
                        )
                    },
                    fingerprint_sha256=fingerprint,
                )
            )
        assert fingerprint not in str(caught.value) + repr(caught.value)
        assert marker not in str(caught.value) + repr(caught.value)
    async with schema_db.transaction() as session:
        loaded = await session.get(schema_api().CredentialVersion, row.id)
        assert fingerprint not in repr(loaded)
        assert fingerprint not in json.dumps(loaded.to_safe_dict(), default=str)


@pytest.mark.asyncio
async def test_naive_timestamp_is_rejected_without_partial_commit(schema_db):
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            session.add(account(created_at=NOW.replace(tzinfo=None)))
