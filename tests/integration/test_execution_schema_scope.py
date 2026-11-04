"""Every execution child binds an existing parent in its complete scope."""

from uuid import uuid4

import pytest
from execution_test_support import attempt, attempt_event, command, order, transition, venue_attempt
from execution_test_support import execution_db as execution_db
from schema_test_support import account, credential, product, schema_api, signal, snapshot
from schema_test_support import schema_db as schema_db
from sqlalchemy import delete

from hmt_crypto.persistence.errors import PersistenceError


async def save(db, row):
    async with db.transaction() as session:
        session.add(row)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("account_id", uuid4()),
        ("region", "global"),
        ("environment", "prod"),
        ("mode", "LIVE"),
    ],
)
@pytest.mark.parametrize("kind", ["command", "order", "attempt", "event", "transition"])
async def test_every_account_owned_execution_row_rejects_scope_mismatch(
    execution_db, field, value, kind
):
    db, owner, publication, cmd, row, prepared = execution_db
    factories = {
        "command": lambda: command(owner, request_id="other"),
        "order": lambda: order(owner, cmd, publication, client_order_id="other"),
        "attempt": lambda: attempt(owner, cmd, row, attempt_number=2),
        "event": lambda: attempt_event(owner, prepared),
        "transition": lambda: transition(owner, row),
    }
    candidate = factories[kind]()
    setattr(candidate, field, value)
    with pytest.raises(PersistenceError):
        await save(db, candidate)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,field",
    [
        ("order", "command_id"),
        ("order", "signal_id"),
        ("attempt", "command_id"),
        ("attempt", "order_id"),
        ("event", "attempt_id"),
        ("transition", "order_id"),
        ("transition", "attempt_id"),
        ("transition", "command_id"),
    ],
)
@pytest.mark.parametrize(
    "scope_change",
    [
        {"profile_name": "other"},
        {"region": "global"},
        {"environment": "prod"},
        {"mode": "BACKTEST"},
    ],
)
async def test_children_reject_existing_parent_in_another_scope(
    execution_db, kind, field, scope_change
):
    db, owner, publication, cmd, row, prepared = execution_db
    other = account(**scope_change)
    await save(db, other)
    other_publication = snapshot(region=other.region, environment=other.environment, revision=2)
    await save(db, other_publication)
    await save(
        db,
        schema_api().CatalogueProduct.from_product(
            snapshot_id=other_publication.id, product=product()
        ),
    )
    other_command = command(other)
    await save(db, other_command)
    other_signal = signal(other)
    await save(db, other_signal)
    other_order = order(other, other_command, other_publication, origin="SIMULATOR_INTENT")
    await save(db, other_order)
    other_attempt = attempt(other, other_command, other_order, simulator_kind=other.mode)
    await save(db, other_attempt)
    parents = {
        "command_id": other_command,
        "signal_id": other_signal,
        "order_id": other_order,
        "attempt_id": other_attempt,
    }
    candidates = {
        "order": order(owner, cmd, publication, client_order_id="another"),
        "attempt": attempt(owner, cmd, row, attempt_number=2),
        "event": attempt_event(owner, prepared),
        "transition": transition(owner, row),
    }
    candidate = candidates[kind]
    setattr(candidate, field, parents[field].id)
    if kind == "transition" and field == "attempt_id":
        candidate.attempt_backend = other_attempt.backend
    with pytest.raises(PersistenceError):
        await save(db, candidate)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"product_id": 999},
        {"catalogue_snapshot_id": uuid4()},
        {"region": "global"},
        {"environment": "prod"},
    ],
)
async def test_order_catalogue_membership_and_venue(execution_db, changes):
    db, owner, publication, cmd, *_ = execution_db
    with pytest.raises(PersistenceError):
        await save(db, order(owner, cmd, publication, client_order_id="other", **changes))


@pytest.mark.asyncio
async def test_same_product_id_wrong_venue_snapshot_is_rejected(execution_db):
    db, owner, _, cmd, *_ = execution_db
    foreign = snapshot(region="global")
    await save(db, foreign)
    await save(
        db, schema_api().CatalogueProduct.from_product(snapshot_id=foreign.id, product=product())
    )
    with pytest.raises(PersistenceError):
        await save(db, order(owner, cmd, foreign, client_order_id="other"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope_change",
    [
        {"profile_name": "other"},
        {"region": "global"},
        {"environment": "prod"},
        {"mode": "BACKTEST"},
    ],
)
async def test_identity_reuse_is_scoped_but_duplicates_inside_scope_fail(
    execution_db, scope_change
):
    db, owner, publication, cmd, row, prepared = execution_db
    other = account(**scope_change)
    await save(db, other)
    other_publication = snapshot(region=other.region, environment=other.environment, revision=2)
    await save(db, other_publication)
    await save(
        db,
        schema_api().CatalogueProduct.from_product(
            snapshot_id=other_publication.id, product=product()
        ),
    )
    other_cmd = command(other)
    await save(db, other_cmd)
    other_order = order(other, other_cmd, other_publication, exchange_order_id="exchange-1")
    await save(db, other_order)
    own_exchange = order(
        owner, cmd, publication, client_order_id="client-2", exchange_order_id="exchange-1"
    )
    await save(db, own_exchange)
    other_prepared = attempt(other, other_cmd, other_order, simulator_kind=other.mode)
    await save(db, other_prepared)
    for candidate in (
        command(owner),
        order(owner, cmd, publication),
        order(owner, cmd, publication, client_order_id="client-3", exchange_order_id="exchange-1"),
        attempt(owner, cmd),
    ):
        with pytest.raises(PersistenceError):
            await save(db, candidate)
    for factory, left, right in (
        (attempt_event, prepared, other_prepared),
        (transition, row, other_order),
    ):
        await save(db, factory(owner, left))
        await save(db, factory(other, right))


@pytest.mark.asyncio
async def test_execution_parent_deletion_is_restricted(execution_db):
    db, owner, publication, cmd, row, prepared = execution_db
    await save(db, attempt_event(owner, prepared))
    await save(
        db,
        transition(
            owner, row, attempt_id=prepared.id, attempt_backend=prepared.backend, command_id=cmd.id
        ),
    )
    for parent in (owner, publication, cmd, row, prepared):
        with pytest.raises(PersistenceError):
            async with db.transaction() as session:
                await session.execute(delete(type(parent)).where(type(parent).id == parent.id))


@pytest.mark.asyncio
async def test_live_local_intent_accepts_only_current_scoped_parent_membership(execution_db):
    db, _, publication, *_ = execution_db
    owner = account(mode="LIVE", profile_name="live")
    await save(db, owner)
    cmd = command(owner)
    await save(db, cmd)
    await save(db, order(owner, cmd, publication, origin="LOCAL_INTENT"))
    await save(db, credential(owner))
    await save(db, venue_attempt(owner, cmd))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope_change",
    [
        {"profile_name": "other"},
        {"region": "global"},
        {"environment": "prod"},
        {"mode": "PAPER"},
    ],
)
async def test_venue_attempt_cannot_borrow_an_existing_credential_version(
    execution_db, scope_change
):
    db, *_ = execution_db
    owner = account(mode="LIVE", profile_name="live")
    other = account(**({"mode": "LIVE", "profile_name": "foreign"} | scope_change))
    await save(db, owner)
    await save(db, other)
    cmd = command(owner)
    await save(db, cmd)
    await save(db, credential(other, credential_version=2))
    with pytest.raises(PersistenceError):
        await save(db, venue_attempt(owner, cmd, credential_version=2))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,field",
    [
        ("order", "command_id"),
        ("order", "signal_id"),
        ("attempt", "command_id"),
        ("attempt", "order_id"),
        ("event", "attempt_id"),
        ("transition", "order_id"),
        ("transition", "attempt_id"),
        ("transition", "command_id"),
    ],
)
async def test_execution_children_cannot_reference_missing_parent(execution_db, kind, field):
    db, owner, publication, cmd, row, prepared = execution_db
    candidate = {
        "order": order(owner, cmd, publication, client_order_id="other"),
        "attempt": attempt(owner, cmd, row, attempt_number=2),
        "event": attempt_event(owner, prepared),
        "transition": transition(owner, row),
    }[kind]
    setattr(candidate, field, uuid4())
    if kind == "transition" and field == "attempt_id":
        candidate.attempt_backend = "SIMULATOR"
    with pytest.raises(PersistenceError):
        await save(db, candidate)


@pytest.mark.asyncio
async def test_transition_attempt_backend_cannot_disagree_with_existing_candidate_key(execution_db):
    db, owner, _, _, row, prepared = execution_db
    with pytest.raises(PersistenceError):
        await save(
            db,
            transition(
                owner, row, source="RECONCILIATION", attempt_id=prepared.id, attempt_backend="VENUE"
            ),
        )
