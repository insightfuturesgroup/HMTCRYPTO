"""Portable checks remain active for Core writes bypassing ORM constructors."""

from datetime import timedelta
from uuid import uuid4

import pytest
from schema_test_support import NOW, account, credential, product, schema_api, signal, snapshot
from schema_test_support import schema_db as schema_db
from sqlalchemy import insert, select, text

from hmt_crypto.persistence.errors import PersistenceError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"credential_version": 0},
        {"credential_slot": ""},
        {"credential_slot": "x" * 129},
        {"observed_external_account_id": "unbound-account"},
        {"recorded_at": NOW.replace(tzinfo=None)},
        {"credential_version": True},
        {"credential_version": 1.5},
    ],
)
async def test_credential_metadata_checks_and_exact_boundaries(schema_db, changes):
    owner = account()
    async with schema_db.transaction() as session:
        session.add(owner)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            session.add(credential(owner, **changes))


@pytest.mark.parametrize("fingerprint", ["SECRET-NOT-A-FINGERPRINT", "A" * 64, "g" * 64])
def test_invalid_fingerprint_hint_has_safe_validation_error(fingerprint):
    with pytest.raises(PersistenceError) as caught:
        credential(account(), fingerprint_hint=fingerprint)
    assert fingerprint not in str(caught.value) + repr(caught.value)


@pytest.mark.asyncio
async def test_only_safe_hint_round_trips_and_raw_sql_hex_check_cannot_be_bypassed(schema_db):
    api = schema_api()
    owner = account()
    row = credential(owner)
    async with schema_db.transaction() as session:
        session.add(owner)
    async with schema_db.transaction() as session:
        session.add(row)
    async with schema_db.transaction() as session:
        loaded = await session.get(api.CredentialVersion, row.id)
        assert loaded.to_safe_dict()["credential_version"] == 1
        assert loaded.fingerprint_hint == "a" * 12
        assert "a" * 64 not in repr(vars(loaded))
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.execute(
                text("UPDATE credential_versions SET fingerprint_hint = :invalid"),
                {"invalid": "g" * 12},
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"signal_key": ""},
        {"signal_key": "x" * 129},
        {"product_id": 0},
        {"source": ""},
        {"intent_sha256": "g" * 64},
        {"intent_payload": "x" * 16385},
        {"generated_at": NOW + timedelta(seconds=1)},
        {"product_id": True},
    ],
)
async def test_signal_checks_survive_core_insert_without_intent_factory(schema_db, changes):
    api = schema_api()
    owner = account()
    async with schema_db.transaction() as session:
        session.add(owner)
    row = signal(owner)
    values = {column.name: getattr(row, column.name) for column in api.Signal.__table__.columns}
    values.update(changes)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.execute(insert(api.Signal.__table__).values(**values))


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["hash", "unsanitized", "noncanonical"])
async def test_direct_orm_signal_cannot_bypass_canonical_sanitized_hash_contract(schema_db, change):
    owner = account()
    async with schema_db.transaction() as session:
        session.add(owner)
    row = signal(owner)
    if change == "hash":
        row.intent_sha256 = "b" * 64
    elif change == "unsanitized":
        row.intent_payload = '{"api_secret":"SECRET-MARKER"}'
    else:
        row.intent_payload = '{ "side" : "buy" }'
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            session.add(row)


@pytest.mark.asyncio
async def test_snapshot_product_payload_is_bounded_for_core_writes(schema_db):
    api = schema_api()
    publication = snapshot()
    async with schema_db.transaction() as session:
        session.add(publication)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.execute(
                insert(api.CatalogueProduct.__table__).values(
                    id=uuid4(),
                    snapshot_id=publication.id,
                    product_id=1101,
                    symbol="XBTUSD_CAT",
                    product_payload="x" * 16385,
                )
            )
    async with schema_db.transaction() as session:
        session.add(
            api.CatalogueProduct.from_product(snapshot_id=publication.id, product=product())
        )


@pytest.mark.asyncio
async def test_empty_schema_drop_recreate_keeps_constraints(schema_db):
    api = schema_api()
    async with schema_db.engine.begin() as connection:
        await connection.run_sync(api.metadata.drop_all)
        await connection.run_sync(api.metadata.create_all)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            session.add(account(mode="untrusted"))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bounded", "hash"])
@pytest.mark.parametrize("channel", ["orm", "core", "raw"])
@pytest.mark.parametrize("suffix", ["", "hidden-invalid-suffix" * 50])
async def test_embedded_nul_cannot_hide_identifier_or_hash_suffix(schema_db, kind, channel, suffix):
    api = schema_api()
    model = api.Account if kind == "bounded" else api.CatalogueSnapshot
    column = "profile_name" if kind == "bounded" else "content_sha256"
    valid = "fixture" if kind == "bounded" else "a" * 64
    invalid = valid + "\x00" + suffix
    row = account() if kind == "bounded" else snapshot()
    if channel == "raw":
        async with schema_db.transaction() as session:
            session.add(row)
        with pytest.raises(PersistenceError):
            async with schema_db.transaction() as session:
                await session.execute(
                    text(f"UPDATE {model.__tablename__} SET {column} = :invalid"),
                    {"invalid": invalid},
                )
        async with schema_db.transaction() as session:
            assert (await session.scalars(select(getattr(model, column)))).all() == [valid]
        return
    setattr(row, column, invalid)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            if channel == "orm":
                session.add(row)
            else:
                values = {field.name: getattr(row, field.name) for field in model.__table__.columns}
                await session.execute(insert(model.__table__).values(**values))
    async with schema_db.transaction() as session:
        assert (await session.scalars(select(model))).all() == []
