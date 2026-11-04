"""Constraint and transaction acceptance for the five foundational tables."""

from uuid import uuid4

import pytest
from schema_test_support import (
    HASH,
    NOW,
    account,
    credential,
    schema_api,
    signal,
    snapshot,
)
from schema_test_support import schema_db as schema_db
from sqlalchemy import MetaData, delete, insert, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable

from hmt_crypto.persistence.errors import PersistenceError

pytestmark = pytest.mark.integration


def test_postgres_ddl_has_private_scoped_named_restrict_constraints():
    api = schema_api()
    dialect = postgresql.dialect()
    # AddConstraint changes inline-DDL eligibility; compile an owned metadata copy.
    metadata = MetaData(
        schema=api.metadata.schema, naming_convention=api.metadata.naming_convention
    )
    for table in api.metadata.tables.values():
        table.to_metadata(metadata)
    ddl = "\n".join(
        str(CreateTable(table).compile(dialect=dialect)) for table in metadata.tables.values()
    )
    assert set(table.name for table in api.metadata.tables.values()) == {
        "accounts",
        "credential_versions",
        "catalogue_snapshots",
        "catalogue_products",
        "signals",
        "command_operations",
        "orders",
        "order_attempts",
        "order_attempt_events",
        "order_transitions",
        "fills",
        "fill_order_links",
        "exit_intents",
        "protection_authorizations",
        "position_protections",
        "position_protection_orders",
        "recovery_incarnations",
        "leadership_leases",
        "worker_heartbeats",
        "scheduler_state",
        "reconciliation_runs",
        "reconciliation_resources",
        "reconciliation_drifts",
        "reconciliation_acknowledgements",
        "position_snapshots",
        "balance_snapshots",
        "wallet_transactions",
        "funding_snapshots",
        "pnl_snapshots",
        "risk_state",
        "risk_halts",
        "risk_halt_events",
        "risk_decisions",
        "risk_reservations",
        "risk_rate_consumptions",
    }
    assert "CREATE TABLE hmt_crypto.accounts" in ddl
    assert "TIMESTAMP WITH TIME ZONE" in ddl
    assert "UUID" in ddl
    assert "CREATE TYPE" not in ddl
    for table in metadata.tables.values():
        for constraint in table.constraints:
            assert constraint.name
        for foreign_key in table.foreign_key_constraints:
            assert foreign_key.ondelete == "RESTRICT"
            if foreign_key.referred_table.name in {"accounts", "credential_versions"}:
                assert {"region", "environment", "mode"} <= set(foreign_key.column_keys)
            if foreign_key.use_alter:
                assert "ALTER TABLE" in str(AddConstraint(foreign_key).compile(dialect=dialect))
        for index in table.indexes:
            assert index.name
            assert "CREATE" in str(CreateIndex(index).compile(dialect=dialect))
    assert "FOREIGN KEY(account_id, region, environment, mode)" in ddl


@pytest.mark.asyncio
async def test_unknown_identities_coexist_but_profiles_and_live_principals_are_unique(schema_db):
    first = account(mode="LIVE")
    second = account(mode="LIVE", profile_name="second")
    async with schema_db.transaction() as session:
        session.add_all([first, second, account(mode="PAPER")])
    for conflicting in (
        account(mode="LIVE"),
        account(id=first.id, mode="PAPER", profile_name="different"),
    ):
        with pytest.raises(PersistenceError):
            async with schema_db.transaction() as session:
                session.add(conflicting)
    verified = dict(mode="LIVE", external_user_id="user-7", identity_verified_at=NOW)
    async with schema_db.transaction() as session:
        session.add(account(profile_name="verified", **verified))
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            session.add(account(profile_name="duplicate-principal", **verified))
    async with schema_db.transaction() as session:
        session.add_all(
            [
                account(profile_name="elsewhere", environment="prod", **verified),
                account(
                    profile_name="paper-user", external_user_id="user-7", identity_verified_at=NOW
                ),
            ]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value", [("region", "global"), ("environment", "prod"), ("mode", "LIVE")]
)
@pytest.mark.parametrize("kind", ["credential", "signal"])
async def test_account_owned_evidence_rejects_every_scope_mismatch(schema_db, field, value, kind):
    owner = account()
    async with schema_db.transaction() as session:
        session.add(owner)
    row = (
        credential(owner, **{field: value})
        if kind == "credential"
        else signal(owner, **{field: value})
    )
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            session.add(row)


@pytest.mark.asyncio
async def test_current_credential_requires_same_account_scope_and_existing_version(schema_db):
    api = schema_api()
    owner, other = account(), account(profile_name="other")
    async with schema_db.transaction() as session:
        session.add_all([owner, other])
    async with schema_db.transaction() as session:
        session.add(credential(other))
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.execute(
                api.Account.__table__.update()
                .where(api.Account.id == owner.id)
                .values(current_credential_version=1)
            )
    async with schema_db.transaction() as session:
        session.add(credential(owner))
    async with schema_db.transaction() as session:
        row = await session.get(api.Account, owner.id)
        row.current_credential_version = 1
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.execute(
                delete(api.CredentialVersion).where(api.CredentialVersion.account_id == owner.id)
            )


@pytest.mark.asyncio
async def test_evidence_uniqueness_is_scoped_and_deletion_is_restricted(schema_db):
    api = schema_api()
    owner, other = account(), account(profile_name="other")
    async with schema_db.transaction() as session:
        session.add_all([owner, other])
    async with schema_db.transaction() as session:
        session.add_all([credential(owner), credential(other), signal(owner), signal(other)])
    for row in (credential(owner), signal(owner)):
        with pytest.raises(PersistenceError):
            async with schema_db.transaction() as session:
                session.add(row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.execute(delete(api.Account).where(api.Account.id == owner.id))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"region": "mars"},
        {"environment": "unknown"},
        {"mode": "live"},
        {"version": 0},
        {"profile_name": ""},
        {"profile_name": "x" * 129},
        {"external_user_id": "unverified"},
        {"identity_verified_at": NOW},
        {"external_account_id": "account-without-user"},
        {"current_credential_version": 0},
    ],
)
async def test_account_checks_fail_closed(schema_db, changes):
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            session.add(account(**changes))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"revision": 0},
        {"product_count": 0},
        {"content_sha256": "A" * 64},
        {"content_sha256": "g" * 64},
        {"content_sha256": "abc"},
        {"source": ""},
        {"fresh_until": NOW},
    ],
)
async def test_snapshot_database_checks_reject_invalid_raw_values(schema_db, changes):
    api = schema_api()
    row = snapshot()
    values = {
        column.name: getattr(row, column.name) for column in api.CatalogueSnapshot.__table__.columns
    }
    values.update(changes)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.execute(insert(api.CatalogueSnapshot.__table__).values(**values))


@pytest.mark.asyncio
async def test_catalogue_republication_allows_same_hash_but_not_same_revision(schema_db):
    async with schema_db.transaction() as session:
        session.add_all([snapshot(), snapshot(revision=2, content_sha256=HASH)])
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            session.add(snapshot())


@pytest.mark.asyncio
async def test_failed_batch_rolls_back_prior_evidence_and_database_remains_usable(schema_db):
    api = schema_api()
    owner = account()
    async with schema_db.transaction() as session:
        session.add(owner)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            session.add(signal(owner))
            await session.flush()
            session.add(credential(owner, credential_version=0))
    async with schema_db.transaction() as session:
        assert (await session.scalars(select(api.Signal))).all() == []
        session.add(signal(owner))
        assert (await session.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1


@pytest.mark.asyncio
async def test_orphan_snapshot_product_is_rejected(schema_db):
    from schema_test_support import product

    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            session.add(
                schema_api().CatalogueProduct.from_product(snapshot_id=uuid4(), product=product())
            )
