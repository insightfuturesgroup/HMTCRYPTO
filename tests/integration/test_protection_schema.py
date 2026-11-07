"""Offline protection storage boundaries; never an execution authorization."""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from execution_test_support import command, order
from execution_test_support import execution_db as execution_db
from schema_test_support import NOW, account, product, schema_api, scope, snapshot
from schema_test_support import schema_db as schema_db
from sqlalchemy import UniqueConstraint, delete, insert, select, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import load_only
from sqlalchemy.schema import CreateIndex, CreateTable

from hmt_crypto.persistence.errors import PersistenceError

NEW_TABLES = {
    "exit_intents",
    "protection_authorizations",
    "position_protections",
    "position_protection_orders",
}


def test_exact_four_protection_tables_are_present():
    baseline = {
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
        "api_audit_events",
        "api_audit_payloads",
        "operator_audit_events",
        "kill_workflows",
        "kill_workflow_events",
        "paper_execution_heads",
        "paper_execution_events",
        "account_reconciliation_checkpoints",
    }
    assert {t.name for t in schema_api().metadata.tables.values()} - baseline == NEW_TABLES


def intent(owner, cmd, row=None, **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        command_id=cmd.id,
        entry_order_id=row.id if row else None,
        product_id=1101,
        intent_key="exit-1",
        purpose="STOP_LOSS",
        mechanism="NATIVE_REDUCE_ONLY",
        reduce_only=True,
        requested_contracts=2,
        requested_trigger_price=Decimal("90.125"),
        policy_version=1,
        state="REQUESTED",
        version=1,
        created_at=NOW,
        updated_at=NOW,
        terminal_at=None,
        evidence={"trigger_basis": "MARK_PRICE"},
    )
    values.update(changes)
    return schema_api().ExitIntent.from_evidence(**values)


def authorization(owner, requested, **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        product_id=1101,
        exit_intent_id=requested.id,
        policy_version=requested.policy_version,
        policy_sha256=requested.evidence_sha256,
        mechanism=requested.mechanism,
        backend="SIMULATOR",
        decision="PAPER_AUTHORIZED",
        authorization_version=1,
        verified_at=NOW,
        recorded_at=NOW,
        evidence={"basis": "SIMULATION"},
    )
    values.update(changes)
    return schema_api().ProtectionAuthorization.from_evidence(**values)


def protection(owner, auth=None, **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        product_id=1101,
        state="UNPROTECTED",
        version=1,
        observed_position_contracts=-2,
        required_contracts=2,
        verified_contracts=0,
        mechanism="NONE",
        authorization_id=None,
        authorization_exit_intent_id=None,
        authorization_policy_version=None,
        authorization_policy_sha256=None,
        authorization_backend=None,
        reconciliation_ref=None,
        reconciliation_generation=None,
        authorization_decision=None,
        qualification_ref=None,
        qualification_generation=None,
        risk_ref=None,
        risk_generation=None,
        verified_at=None,
        reason="AWAITING_VERIFICATION",
        created_at=NOW,
        updated_at=NOW,
    )
    if auth:
        values.update(
            mechanism=auth.mechanism,
            authorization_id=auth.id,
            authorization_exit_intent_id=auth.exit_intent_id,
            authorization_policy_version=auth.policy_version,
            authorization_policy_sha256=auth.policy_sha256,
            authorization_backend=auth.backend,
            authorization_decision=auth.decision,
        )
    values.update(changes)
    return schema_api().PositionProtection(**values)


def link(owner, projected, requested, row, **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        product_id=1101,
        protection_id=projected.id,
        exit_intent_id=requested.id,
        order_id=row.id,
        role="STOP_LOSS",
        recorded_at=NOW,
    )
    values.update(changes)
    return schema_api().PositionProtectionOrder(**values)


async def save(db, row, channel="orm"):
    async with db.transaction() as session:
        if channel == "orm":
            session.add(row)
        else:
            await session.execute(
                insert(row.__table__).values(
                    **{c.name: getattr(row, c.name) for c in row.__table__.columns}
                )
            )


async def graph(execution_db):
    db, owner, _, cmd, row, *_ = execution_db
    requested = intent(owner, cmd, row)
    await save(db, requested)
    auth = authorization(owner, requested)
    await save(db, auth)
    projected = protection(owner, auth)
    await save(db, projected)
    return db, owner, cmd, row, requested, auth, projected


def test_postgresql_named_constraints_and_product_candidates_compile():
    api = schema_api()
    candidate = ("id", "account_id", "region", "environment", "mode", "product_id")
    for name in NEW_TABLES:
        table = api.metadata.tables[f"hmt_crypto.{name}"]
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "TIMESTAMP WITH TIME ZONE" in ddl
        assert all(c.name for c in table.constraints)
        for index in table.indexes:
            assert index.name and str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        for fk in table.foreign_key_constraints:
            assert fk.ondelete == fk.onupdate == "RESTRICT"
        if name in {"exit_intents", "position_protections"}:
            assert candidate in {
                tuple(c.name for c in key.columns)
                for key in table.constraints
                if isinstance(key, UniqueConstraint)
            }
    exit_ddl = str(CreateTable(api.ExitIntent.__table__).compile(dialect=postgresql.dialect()))
    assert "NUMERIC" in exit_ddl and "NUMERIC(" not in exit_ddl


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_populated_graph_roundtrip_without_mutating_existing_truth(execution_db, channel):
    db, owner, _, cmd, row, *_ = execution_db
    await save(
        db,
        schema_api().Fill.from_evidence(
            id=uuid4(),
            **scope(owner),
            product_id=1101,
            exchange_fill_id="existing-fill",
            size=Decimal("0.125"),
            price=Decimal("100.25"),
            side="sell",
            fill_type="normal",
            commission=Decimal("-0.0125"),
            source="VENUE",
            requested_at=NOW,
            received_at=NOW,
            recorded_at=NOW,
            evidence={"role": "maker"},
        ),
    )
    tables = [t for t in schema_api().metadata.tables.values() if t.name not in NEW_TABLES]
    async with db.transaction() as session:
        before = {t.name: (await session.execute(select(t))).all() for t in tables}
    requested = intent(owner, cmd, row)
    await save(db, requested, channel)
    auth = authorization(owner, requested)
    await save(db, auth, channel)
    projected = protection(owner, auth)
    await save(db, projected, channel)
    await save(db, link(owner, projected, requested, row), channel)
    async with db.transaction() as session:
        assert before == {t.name: (await session.execute(select(t))).all() for t in tables}
        loaded = await session.get(schema_api().PositionProtection, projected.id)
        assert loaded.state == "UNPROTECTED" and loaded.verified_contracts == 0
        assert not hasattr(
            await session.get(schema_api().ProtectionAuthorization, auth.id),
            "production_dispatch_allowed",
        )
        assert (
            await session.get(schema_api().ExitIntent, requested.id)
        ).requested_trigger_price == Decimal("90.125")


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "kind,change",
    [
        ("intent", {"reduce_only": False}),
        ("intent", {"reduce_only": 1}),
        ("intent", {"purpose": "BRACKET_ACK"}),
        ("intent", {"mechanism": "UNKNOWN"}),
        ("intent", {"requested_contracts": True}),
        ("intent", {"requested_contracts": 0}),
        ("intent", {"requested_trigger_price": 0.1}),
        ("intent", {"requested_trigger_price": Decimal("-1")}),
        ("intent", {"requested_trigger_price": Decimal("1e-19")}),
        ("intent", {"requested_trigger_price": None}),
        ("intent", {"created_at": datetime(2026, 11, 4)}),
        ("intent", {"updated_at": NOW - timedelta(seconds=1)}),
        ("intent", {"terminal_at": NOW}),
        ("intent", {"policy_version": 0}),
        ("auth", {"decision": "PRODUCTION_AUTHORIZED"}),
        ("auth", {"backend": "VENUE"}),
        ("auth", {"decision": "TESTNET_NATIVE_VERIFIED"}),
        ("auth", {"verified_at": None}),
        ("auth", {"authorization_version": 0}),
        ("projection", {"state": "PROTECTED"}),
        ("projection", {"reason": "api_secret=marker"}),
        ("projection", {"required_contracts": True}),
        ("projection", {"verified_contracts": -1}),
        ("projection", {"verified_at": NOW}),
        ("projection", {"reconciliation_ref": uuid4()}),
        ("projection", {"risk_generation": 1}),
        ("projection", {"qualification_ref": uuid4()}),
        ("projection", {"state": "NO_REQUIRED_COVERAGE"}),
        ("projection", {"authorization_id": uuid4()}),
    ],
)
async def test_fail_closed_typed_and_structural_admission(execution_db, channel, kind, change):
    db, owner, cmd, _, requested, _, _ = await graph(execution_db)
    with pytest.raises(PersistenceError):
        row = {
            "intent": lambda: intent(owner, cmd, intent_key="second", **change),
            "auth": lambda: authorization(
                owner, requested, **dict({"authorization_version": 2}, **change)
            ),
            "projection": lambda: protection(owner, product_id=999, **change),
        }[kind]()
        await save(db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"required_contracts": 0},
        {"observed_position_contracts": None},
    ],
)
async def test_orm_coverage_inequalities_fail_closed(execution_db, changes):
    db, owner, *_ = execution_db
    with pytest.raises(PersistenceError):
        await save(db, protection(owner, **changes))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,position,required,verified",
    [
        ("UNKNOWN", None, None, None),
        ("NO_REQUIRED_COVERAGE", 0, 0, 0),
        ("UNPROTECTED", 2, 2, 0),
        ("UNPROTECTED", -2, 2, None),
    ],
)
async def test_unknown_zero_and_active_coverage_remain_distinct(
    execution_db, state, position, required, verified
):
    db, owner, *_ = execution_db
    await save(
        db,
        protection(
            owner,
            state=state,
            observed_position_contracts=position,
            required_contracts=required,
            verified_contracts=verified,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["intent", "auth"])
async def test_factories_own_policy_evidence_and_deny_secrets(execution_db, kind):
    db, owner, _, cmd, *_ = execution_db
    policy = {"trigger_basis": "MARK_PRICE"}
    requested = intent(owner, cmd, evidence=policy)
    policy["trigger_basis"] = "LAST_PRICE"
    await save(db, requested)
    evidence = {"basis": "SIMULATION"}
    auth = authorization(owner, requested, evidence=evidence)
    evidence["basis"] = "DENIED"
    await save(db, auth)
    assert requested.evidence_payload == '{"trigger_basis":"MARK_PRICE"}'
    assert auth.evidence_payload == '{"basis":"SIMULATION"}'
    for payload in (
        {"api_secret": "marker"},
        {"metadata": {}},
        {"raw_response": "marker"},
        {"signature": "marker"},
        [],
        "{}",
    ):
        with pytest.raises(PersistenceError):
            if kind == "intent":
                intent(owner, cmd, evidence=payload)
            else:
                authorization(owner, requested, evidence=payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("kind", ["intent", "auth"])
async def test_direct_payload_does_not_bypass_allowlist(execution_db, channel, kind):
    db, owner, _, cmd, *_ = execution_db
    requested = intent(owner, cmd)
    await save(db, requested)
    row = (
        intent(owner, cmd, intent_key="second")
        if kind == "intent"
        else authorization(owner, requested)
    )
    row.evidence_payload = '{"api_secret":"marker"}'
    with pytest.raises(PersistenceError):
        await save(db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["intent", "auth", "link"])
@pytest.mark.parametrize("action", ["update", "delete"])
async def test_orm_immutable_history_and_policy(execution_db, kind, action):
    db, owner, _, row, requested, auth, projected = await graph(execution_db)
    association = link(owner, projected, requested, row)
    await save(db, association)
    target = {"intent": requested, "auth": auth, "link": association}[kind]
    with pytest.raises(PersistenceError):
        async with db.transaction() as session:
            loaded = await session.get(type(target), target.id)
            if action == "delete":
                await session.delete(loaded)
            elif kind == "intent":
                loaded.requested_contracts = 1
            elif kind == "auth":
                loaded.decision = "DENIED"
            else:
                loaded.role = "TAKE_PROFIT"


@pytest.mark.asyncio
async def test_intent_lifecycle_is_mutable_but_does_not_rewrite_policy(execution_db):
    db, _, _, _, requested, _, _ = await graph(execution_db)
    async with db.transaction() as session:
        loaded = await session.get(schema_api().ExitIntent, requested.id)
        loaded.state, loaded.version, loaded.terminal_at = "CANCELLED", 2, NOW
    async with db.transaction() as session:
        assert (await session.get(schema_api().ExitIntent, requested.id)).state == "CANCELLED"


@pytest.mark.asyncio
async def test_populated_child_first_drop_recreate_and_rollback(execution_db):
    db, owner, _, row, requested, _, projected = await graph(execution_db)
    with pytest.raises(RuntimeError):
        async with db.transaction() as session:
            session.add(link(owner, projected, requested, row))
            await session.flush()
            raise RuntimeError("abort")
    async with db.transaction() as session:
        assert not (await session.scalars(select(schema_api().PositionProtectionOrder))).all()
        assert (
            (await session.execute(text("PRAGMA database_list"))).all()[0][2].endswith("schema.db")
        )
    await save(db, link(owner, projected, requested, row))
    async with db.engine.begin() as connection:
        for model in (
            schema_api().PositionProtectionOrder,
            schema_api().PositionProtection,
            schema_api().ProtectionAuthorization,
            schema_api().ExitIntent,
        ):
            await connection.run_sync(model.__table__.drop)
        await connection.run_sync(schema_api().metadata.create_all)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "parent,mismatch",
    [
        (parent, mismatch)
        for parent in (
            "command",
            "entry_order",
            "authorization_intent",
            "projection_authorization",
            "link_protection",
            "link_intent",
            "link_order",
        )
        for mismatch in ("account", "region", "environment", "mode", "product", "missing")
        # Commands have no product column; never invent a product FK for them.
        if (parent, mismatch) != ("command", "product")
    ],
)
async def test_every_reference_rejects_wrong_full_scope_product_or_missing_parent(
    execution_db, channel, parent, mismatch
):
    db, owner, cmd, local_order, requested, auth, projected = await graph(execution_db)
    other = account(
        **{
            "region": {"region": "global"},
            "environment": {"environment": "prod"},
            "mode": {"mode": "BACKTEST"},
        }.get(mismatch, {"profile_name": "other"})
    )
    foreign_owner = owner if mismatch in {"product", "missing"} else other
    if foreign_owner is other:
        await save(db, other)
    foreign_cmd = command(foreign_owner, request_id="foreign")
    await save(db, foreign_cmd)
    foreign_product = 999 if mismatch == "product" else 1101
    publication = snapshot(
        region=foreign_owner.region, environment=foreign_owner.environment, revision=2
    )
    await save(db, publication)
    await save(
        db,
        schema_api().CatalogueProduct.from_product(
            snapshot_id=publication.id, product=product().model_copy(update={"id": foreign_product})
        ),
    )
    foreign_order = order(
        foreign_owner,
        foreign_cmd,
        publication,
        product_id=foreign_product,
        client_order_id="foreign",
    )
    await save(db, foreign_order)
    foreign_intent = intent(
        foreign_owner, foreign_cmd, foreign_order, product_id=foreign_product, intent_key="foreign"
    )
    await save(db, foreign_intent)
    foreign_auth = authorization(
        foreign_owner,
        foreign_intent,
        product_id=foreign_product,
        decision="UNVERIFIED",
        verified_at=None,
        evidence={"basis": "UNVERIFIED"},
    )
    await save(db, foreign_auth)
    if foreign_product == 1101 and foreign_owner is owner:
        foreign_projection = projected
    else:
        foreign_projection = protection(foreign_owner, product_id=foreign_product)
        await save(db, foreign_projection)

    def foreign_id(obj):
        return uuid4() if mismatch == "missing" else obj.id

    if parent == "command":
        candidate = intent(owner, cmd, intent_key="candidate", command_id=foreign_id(foreign_cmd))
    elif parent == "entry_order":
        candidate = intent(
            owner, cmd, intent_key="candidate", entry_order_id=foreign_id(foreign_order)
        )
    elif parent == "authorization_intent":
        candidate = authorization(
            owner, requested, authorization_version=2, exit_intent_id=foreign_id(foreign_intent)
        )
    elif parent == "projection_authorization":
        # Preserve every other local identity; this must fail the exact auth FK.
        async with db.transaction() as session:
            await session.execute(
                delete(schema_api().PositionProtection).where(
                    schema_api().PositionProtection.id == projected.id
                )
            )
        candidate = protection(owner, auth, authorization_id=foreign_id(foreign_auth))
    else:
        field, foreign = {
            "link_protection": ("protection_id", foreign_projection),
            "link_intent": ("exit_intent_id", foreign_intent),
            "link_order": ("order_id", foreign_order),
        }[parent]
        candidate = link(owner, projected, requested, local_order, **{field: foreign_id(foreign)})
    with pytest.raises(PersistenceError):
        await save(db, candidate, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "field",
    [
        "authorization_id",
        "authorization_exit_intent_id",
        "authorization_policy_version",
        "authorization_policy_sha256",
        "authorization_backend",
        "authorization_decision",
    ],
)
async def test_optional_authorization_has_no_null_component_escape(execution_db, channel, field):
    db, owner, _, cmd, *_ = execution_db
    requested = intent(owner, cmd)
    await save(db, requested)
    auth = authorization(owner, requested)
    await save(db, auth)
    with pytest.raises(PersistenceError):
        await save(db, protection(owner, auth, **{field: None}), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("authorization_policy_version", 2),
        ("authorization_policy_sha256", "b" * 64),
        ("authorization_backend", "VENUE"),
        ("authorization_decision", "TESTNET_NATIVE_VERIFIED"),
        ("authorization_exit_intent_id", uuid4()),
        ("mechanism", "SYNTHETIC_MONITOR"),
    ],
)
async def test_projection_reference_binds_exact_backend_policy_and_decision(
    execution_db, channel, field, value
):
    db, owner, _, cmd, *_ = execution_db
    requested = intent(owner, cmd)
    await save(db, requested)
    auth = authorization(owner, requested)
    await save(db, auth)
    with pytest.raises(PersistenceError):
        await save(db, protection(owner, auth, **{field: value}), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "change",
    [{"policy_version": 2}, {"policy_sha256": "b" * 64}, {"mechanism": "SYNTHETIC_MONITOR"}],
)
async def test_authorization_binds_exact_exit_policy_identity(execution_db, channel, change):
    db, owner, _, cmd, *_ = execution_db
    requested = intent(owner, cmd)
    await save(db, requested)
    with pytest.raises(PersistenceError):
        await save(db, authorization(owner, requested, **change), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("role", ["STOP_LOSS", "TAKE_PROFIT", "BRACKET_ACK"])
async def test_link_role_identity_is_relational_and_unique(execution_db, channel, role):
    db, owner, _, row, requested, _, projected = await graph(execution_db)
    if role != "STOP_LOSS":
        with pytest.raises(PersistenceError):
            await save(db, link(owner, projected, requested, row, role=role), channel)
    else:
        await save(db, link(owner, projected, requested, row), channel)
        with pytest.raises(PersistenceError):
            await save(db, link(owner, projected, requested, row), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "decision,mechanism,basis",
    [
        ("TESTNET_NATIVE_VERIFIED", "NATIVE_REDUCE_ONLY", "TESTNET_VERIFICATION"),
        ("SYNTHETIC_AUTHORIZED", "SYNTHETIC_MONITOR", "EXPLICIT_SYNTHETIC_AUTHORIZATION"),
    ],
)
async def test_live_testnet_evidence_is_storable_without_automatic_protection(
    execution_db, channel, decision, mechanism, basis
):
    db, *_ = execution_db
    owner = account(mode="LIVE", profile_name="testnet")
    await save(db, owner)
    cmd = command(owner)
    await save(db, cmd)
    requested = intent(owner, cmd, mechanism=mechanism)
    await save(db, requested, channel)
    auth = authorization(
        owner, requested, backend="VENUE", decision=decision, evidence={"basis": basis}
    )
    await save(db, auth, channel)
    projected = protection(owner, auth)
    await save(db, projected, channel)
    async with db.transaction() as session:
        assert (await session.get(type(projected), projected.id)).state == "UNPROTECTED"
    if mechanism == "SYNTHETIC_MONITOR":
        with pytest.raises(PersistenceError):
            async with db.transaction() as session:
                await session.execute(
                    update(type(projected))
                    .where(type(projected).id == projected.id)
                    .values(
                        state="PROTECTED",
                        verified_contracts=2,
                        verified_at=NOW,
                        reconciliation_ref=uuid4(),
                        reconciliation_generation=1,
                    )
                )


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("decision,basis", [("DENIED", "DENIED"), ("UNVERIFIED", "UNVERIFIED")])
async def test_prod_can_record_denial_and_uncertainty_without_activation(
    execution_db, channel, decision, basis
):
    db, *_ = execution_db
    owner = account(mode="LIVE", environment="prod", profile_name="denial")
    await save(db, owner)
    cmd = command(owner)
    await save(db, cmd)
    requested = intent(owner, cmd)
    await save(db, requested, channel)
    auth = authorization(
        owner,
        requested,
        backend="VENUE",
        decision=decision,
        verified_at=None,
        evidence={"basis": basis},
    )
    await save(db, auth, channel)
    projected = protection(owner, auth)
    await save(db, projected, channel)
    assert not hasattr(auth, "production_dispatch_allowed")
    for activation in ("PRODUCTION_AUTHORIZED", "TESTNET_NATIVE_VERIFIED", "SYNTHETIC_AUTHORIZED"):
        with pytest.raises(PersistenceError):
            await save(
                db,
                authorization(
                    owner,
                    requested,
                    backend="VENUE",
                    decision=activation,
                    authorization_version=2,
                    evidence={"basis": "TESTNET_VERIFICATION"},
                ),
                channel,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_close_position_has_no_invented_trigger_semantics(execution_db, channel):
    db, owner, _, cmd, *_ = execution_db
    requested = intent(
        owner,
        cmd,
        purpose="CLOSE_POSITION",
        requested_trigger_price=None,
        evidence={"trigger_basis": "NONE"},
    )
    await save(db, requested, channel)
    with pytest.raises(PersistenceError):
        await save(
            db,
            intent(
                owner,
                cmd,
                intent_key="bad",
                purpose="CLOSE_POSITION",
                evidence={"trigger_basis": "NONE"},
            ),
            channel,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "model,field,value",
    [
        ("ExitIntent", "requested_trigger_price", "-1"),
        ("ExitIntent", "requested_trigger_price", "01"),
        ("ExitIntent", "evidence_payload", '{"api_secret":"marker"}'),
        ("ExitIntent", "created_at", "not-a-time"),
        ("ProtectionAuthorization", "evidence_payload", '{"signature":"marker"}'),
        ("PositionProtection", "observed_position_contracts", 1.5),
    ],
)
async def test_corrupted_stored_values_reject_typed_reads(
    execution_db, channel, model, field, value
):
    db, _, _, _, requested, auth, projected = await graph(execution_db)
    target = {
        "ExitIntent": requested,
        "ProtectionAuthorization": auth,
        "PositionProtection": projected,
    }[model]
    async with db.engine.connect() as connection:
        await connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        await connection.execute(
            text(f"UPDATE {target.__tablename__} SET {field}=:v WHERE id=:id"),
            {"v": value, "id": target.id.hex},
        )
        await connection.commit()
        await connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")
        await connection.commit()
    with pytest.raises(PersistenceError):
        async with db.transaction() as session:
            if channel == "orm":
                await session.get(type(target), target.id)
            else:
                (await session.execute(select(target.__table__))).all()


@pytest.mark.asyncio
async def test_insufficient_claim_is_rejected_on_orm_write_and_load(execution_db):
    db, owner, _, _, _, auth, projected = await graph(execution_db)
    # The narrow v1 boundary permits a caller-claimed PROTECTED projection only
    # with necessary evidence. It never derives this state from an acknowledgement.
    values = dict(
        state="PROTECTED",
        verified_contracts=1,
        verified_at=NOW,
        reconciliation_ref=uuid4(),
        reconciliation_generation=1,
    )
    with pytest.raises(PersistenceError):
        async with db.transaction() as session:
            loaded = await session.get(type(projected), projected.id)
            for field, value in values.items():
                setattr(loaded, field, value)
    async with db.transaction() as session:
        await session.execute(
            update(type(projected)).where(type(projected).id == projected.id).values(**values)
        )
    with pytest.raises(PersistenceError):
        async with db.transaction() as session:
            await session.get(type(projected), projected.id)


@pytest.mark.asyncio
async def test_unreferenced_exit_requested_policy_cannot_be_deleted(execution_db):
    db, owner, _, cmd, *_ = execution_db
    requested = intent(owner, cmd)
    await save(db, requested)
    with pytest.raises(PersistenceError):
        async with db.transaction() as session:
            await session.delete(await session.get(type(requested), requested.id))


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "state,field",
    [
        ("NO_REQUIRED_COVERAGE", "observed_position_contracts"),
        ("NO_REQUIRED_COVERAGE", "required_contracts"),
        ("NO_REQUIRED_COVERAGE", "verified_contracts"),
        ("VERIFYING", "verified_contracts"),
    ],
)
async def test_nullable_coverage_cannot_bypass_state_or_verification_time(
    execution_db, channel, state, field
):
    db, owner, _, _, _, auth, _ = await graph(execution_db)
    async with db.transaction() as session:
        await session.execute(delete(schema_api().PositionProtection))
    if state == "NO_REQUIRED_COVERAGE":
        candidate = protection(
            owner,
            state=state,
            observed_position_contracts=0,
            required_contracts=0,
            verified_contracts=0,
        )
    else:
        candidate = protection(
            owner,
            auth,
            state=state,
            verified_at=NOW,
            verified_contracts=2,
            reconciliation_ref=uuid4(),
            reconciliation_generation=1,
        )
    setattr(candidate, field, None)
    with pytest.raises(PersistenceError):
        await save(db, candidate, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("position", [0, 2])
async def test_zero_math_and_native_acknowledgement_cannot_claim_protected(
    execution_db, channel, position
):
    db, owner, _, _, _, auth, _ = await graph(execution_db)
    async with db.transaction() as session:
        await session.execute(delete(schema_api().PositionProtection))
    # A positive authorization category plus an acknowledgement is not coverage.
    with pytest.raises(PersistenceError):
        await save(
            db,
            protection(
                owner,
                auth,
                state="PROTECTED",
                observed_position_contracts=position,
                required_contracts=position,
                verified_contracts=0,
            ),
            channel,
        )
    with pytest.raises(PersistenceError):
        authorization(
            owner, intent(owner, command(owner)), evidence={"basis": "NATIVE_BRACKET_ACK"}
        )


@pytest.mark.asyncio
async def test_only_explicit_eligible_native_claim_can_be_stored_and_references_are_not_proof(
    execution_db,
):
    db, _, _, _, _, _, projected = await graph(execution_db)
    async with db.transaction() as session:
        loaded = await session.get(type(projected), projected.id)
        loaded.state = "PROTECTED"
        loaded.verified_contracts = 2
        loaded.verified_at = NOW
        loaded.reconciliation_ref, loaded.reconciliation_generation = uuid4(), 1
        loaded.version = 2
    async with db.transaction() as session:
        loaded = await session.get(type(projected), projected.id)
        assert loaded.state == "PROTECTED" and loaded.version == 2
        # This is explicitly stored caller evidence: no reconciler exists here.
        assert all(
            "reconciliation" not in fk.referred_table.name
            for fk in type(projected).__table__.foreign_key_constraints
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_projection_and_intent_uniqueness_and_authorization_version_history(
    execution_db, channel
):
    db, owner, cmd, _, requested, auth, projected = await graph(execution_db)
    for duplicate in (intent(owner, cmd), protection(owner), authorization(owner, requested)):
        with pytest.raises(PersistenceError):
            await save(db, duplicate, channel)
    newer = authorization(
        owner,
        requested,
        authorization_version=2,
        decision="DENIED",
        verified_at=None,
        evidence={"basis": "DENIED"},
    )
    await save(db, newer, channel)
    async with db.transaction() as session:
        assert (await session.get(type(auth), auth.id)).decision == "PAPER_AUTHORIZED"
        assert (await session.get(type(projected), projected.id)).authorization_id == auth.id


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "model,field",
    [
        ("intent", "requested_contracts"),
        ("intent", "policy_version"),
        ("auth", "authorization_version"),
        ("projection", "observed_position_contracts"),
        ("projection", "required_contracts"),
        ("projection", "verified_contracts"),
    ],
)
@pytest.mark.parametrize("value", [True, 1.5, "1", 2**63])
async def test_contract_and_version_counts_never_coerce(execution_db, channel, model, field, value):
    db, owner, _, cmd, *_ = execution_db
    requested = intent(owner, cmd)
    await save(db, requested)
    target = {
        "intent": lambda: intent(owner, cmd, intent_key="second"),
        "auth": lambda: authorization(owner, requested),
        "projection": lambda: protection(owner),
    }[model]()
    setattr(target, field, value)
    with pytest.raises(PersistenceError):
        await save(db, target, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("kind", ["intent", "auth", "projection", "link"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("account_id", uuid4()),
        ("region", "global"),
        ("environment", "prod"),
        ("mode", "BACKTEST"),
        ("account_id", None),
        ("region", None),
        ("environment", None),
        ("mode", None),
        ("product_id", None),
    ],
)
async def test_all_four_account_scope_foreign_keys_and_required_scope_columns(
    execution_db, channel, kind, field, value
):
    db, owner, cmd, row, requested, _, projected = await graph(execution_db)
    target = {
        "intent": lambda: intent(owner, cmd, intent_key="second"),
        "auth": lambda: authorization(owner, requested, authorization_version=2),
        "projection": lambda: protection(owner, product_id=999),
        "link": lambda: link(owner, projected, requested, row),
    }[kind]()
    setattr(target, field, value)
    with pytest.raises(PersistenceError):
        await save(db, target, channel)


@pytest.mark.asyncio
async def test_unreferenced_authorization_delete_is_orm_immutable(execution_db):
    db, owner, _, cmd, *_ = execution_db
    requested = intent(owner, cmd)
    await save(db, requested)
    auth = authorization(owner, requested)
    await save(db, auth)
    with pytest.raises(PersistenceError):
        async with db.transaction() as session:
            await session.delete(await session.get(type(auth), auth.id))


async def refresh_subject(execution_db, kind):
    db, owner, _, cmd, *_ = execution_db
    requested = intent(owner, cmd)
    await save(db, requested)
    if kind.startswith("exit"):
        return db, requested
    auth = authorization(owner, requested)
    await save(db, auth)
    if kind.startswith("auth"):
        return db, auth
    projected = protection(
        owner,
        auth,
        state="PROTECTED",
        verified_contracts=2,
        verified_at=NOW,
        reconciliation_ref=uuid4(),
        reconciliation_generation=1,
    )
    await save(db, projected)
    return db, projected


async def reload_subject(session, loaded, path, fields):
    if path in {"fresh", "deferred_fresh"}:
        model, identity = type(loaded), loaded.id
        session.expunge(loaded)
        if path == "deferred_fresh":
            return (
                await session.scalars(
                    select(model)
                    .where(model.id == identity)
                    .options(load_only(*(getattr(model, field) for field in fields)))
                )
            ).one()
        return await session.get(model, identity)
    if path == "full_refresh":
        await session.refresh(loaded)
    elif path == "partial_refresh":
        await session.refresh(loaded, attribute_names=fields)
    elif path == "partial_refresh_after_expire":
        session.expire(loaded)
        await session.refresh(loaded, attribute_names=fields)
    else:
        session.expire(loaded, fields if path == "expire_partial" else None)
        await session.run_sync(lambda sync: getattr(loaded, fields[0]))
    return loaded


@pytest.mark.asyncio
@pytest.mark.filterwarnings("error::sqlalchemy.exc.SAWarning")
@pytest.mark.parametrize(
    "path",
    [
        "fresh",
        "deferred_fresh",
        "full_refresh",
        "partial_refresh",
        "partial_refresh_after_expire",
        "expire_all",
        "expire_partial",
    ],
)
@pytest.mark.parametrize(
    "kind",
    [
        "projection",
        "exit_hash",
        "exit_schema",
        "exit_policy",
        "auth_hash",
        "auth_basis",
    ],
)
async def test_protection_invariants_reject_corruption_on_every_orm_reload(
    execution_db, path, kind
):
    db, target = await refresh_subject(execution_db, kind)
    if kind == "projection":
        changes = {"verified_contracts": 1}
    elif kind == "exit_hash":
        changes = {"evidence_payload": '{"trigger_basis":"LAST_PRICE"}'}
    elif kind == "exit_schema":
        changes = {"evidence_schema_version": 2}
    elif kind == "auth_hash":
        changes = {"evidence_payload": '{"basis":"DENIED"}'}
    else:
        from hmt_crypto.persistence.execution_evidence import encode_evidence

        payload, digest = encode_evidence(
            "exit_policy" if kind == "exit_policy" else "protection_authorization",
            "STOP_LOSS" if kind == "exit_policy" else "PAPER_AUTHORIZED",
            1,
            {"trigger_basis": "NONE"} if kind == "exit_policy" else {"basis": "DENIED"},
        )
        changes = {"evidence_payload": payload, "evidence_sha256": digest}
    with pytest.raises(PersistenceError):
        async with db.transaction() as session:
            loaded = await session.get(type(target), target.id)
            connection = await session.connection()
            # Simulate stored corruption, including the otherwise checked schema
            # version. Core mutations intentionally bypass only ORM write guards.
            await connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
            try:
                await session.execute(
                    update(target.__table__)
                    .where(target.__table__.c.id == target.id)
                    .values(**changes)
                )
            finally:
                await connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")
            await reload_subject(session, loaded, path, list(changes))


@pytest.mark.asyncio
@pytest.mark.filterwarnings("error::sqlalchemy.exc.SAWarning")
@pytest.mark.parametrize(
    "path",
    [
        "fresh",
        "deferred_fresh",
        "full_refresh",
        "partial_refresh",
        "partial_refresh_after_expire",
        "expire_all",
        "expire_partial",
    ],
)
@pytest.mark.parametrize("kind", ["projection", "exit", "auth"])
async def test_valid_full_partial_and_expired_reload_remain_usable(execution_db, path, kind):
    db, target = await refresh_subject(execution_db, kind)
    fields = ["verified_contracts"] if kind == "projection" else ["evidence_payload"]
    async with db.transaction() as session:
        loaded = await reload_subject(
            session, await session.get(type(target), target.id), path, fields
        )
        if kind == "projection":
            assert await session.run_sync(lambda sync: loaded.verified_contracts) == 2
            loaded.reason, loaded.version = "STALE", 2
        elif kind == "exit":
            assert (
                await session.run_sync(lambda sync: loaded.evidence_payload)
                == '{"trigger_basis":"MARK_PRICE"}'
            )
            loaded.state, loaded.version, loaded.terminal_at = "CANCELLED", 2, NOW
        else:
            assert (
                await session.run_sync(lambda sync: loaded.evidence_payload)
                == '{"basis":"SIMULATION"}'
            )
    async with db.transaction() as session:
        loaded = await session.get(type(target), target.id)
        if kind == "projection":
            assert loaded.reason == "STALE" and loaded.version == 2
        elif kind == "exit":
            assert loaded.state == "CANCELLED" and loaded.version == 2


@pytest.mark.asyncio
@pytest.mark.filterwarnings("error::sqlalchemy.exc.SAWarning")
async def test_partial_refresh_preserves_unrelated_pending_projection_change(execution_db):
    db, target = await refresh_subject(execution_db, "projection")
    async with db.transaction() as session:
        loaded = await session.get(type(target), target.id)
        loaded.reason = "STALE"
        session.expire(loaded, ["required_contracts", "state"])
        with session.no_autoflush:
            await session.refresh(loaded, attribute_names=["verified_contracts"])
            assert loaded.reason == "STALE"
            assert session.is_modified(loaded)
    async with db.transaction() as session:
        assert (await session.get(type(target), target.id)).reason == "STALE"
