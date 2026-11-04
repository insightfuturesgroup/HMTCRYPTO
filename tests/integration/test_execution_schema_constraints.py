"""Reject inconsistent queue, provenance and evidence rows at storage boundaries."""

from datetime import timedelta
from uuid import uuid4

import pytest
from execution_test_support import (
    attempt,
    attempt_event,
    command,
    order,
    transition,
    venue_attempt,
)
from execution_test_support import execution_db as execution_db
from schema_test_support import NOW, account, credential, schema_api, scope
from schema_test_support import schema_db as schema_db
from sqlalchemy import insert, select

from hmt_crypto.persistence.errors import PersistenceError


async def persist(database, row):
    async with database.transaction() as session:
        session.add(row)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"status": "bad"},
        {"status": "COMPLETED"},
        {"completed_at": NOW},
        {"status": "CLAIMED"},
        {"claim_owner": uuid4()},
        {"claim_generation": -1},
        {"attempt_count": -1},
        {"version": 0},
        {"updated_at": NOW - timedelta(seconds=1)},
        {"available_at": NOW - timedelta(seconds=1)},
        {"request_id": "x\x00tail"},
        {"created_at": NOW.replace(tzinfo=None)},
        {"attempt_count": True},
        {"completed_at": NOW - timedelta(seconds=1), "status": "FAILED"},
        {"claim_owner": uuid4(), "claim_expires_at": NOW, "claim_generation": 1},
        {"recovery_ref": uuid4()},
        {"leader_generation": 1},
    ],
)
async def test_command_consistency_checks(execution_db, changes):
    db, owner, *_ = execution_db
    row = command(owner, **({"request_id": str(uuid4())} | changes))
    with pytest.raises(PersistenceError):
        await persist(db, row)


@pytest.mark.asyncio
async def test_claimed_and_terminal_queue_receipts_do_not_terminalize_order(execution_db):
    db, owner, _, _, row, _ = execution_db
    claimed = command(
        owner,
        request_id="claimed",
        status="CLAIMED",
        claim_owner=uuid4(),
        claim_generation=1,
        claim_expires_at=NOW + timedelta(minutes=1),
    )
    completed = command(owner, request_id="done", status="COMPLETED", completed_at=NOW)
    await persist(db, claimed)
    await persist(db, completed)
    async with db.transaction() as session:
        assert (await session.get(schema_api().Order, row.id)).state == "PREPARED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"requested_contracts": 0},
        {"requested_contracts": True},
        {"requested_contracts": 1.5},
        {"requested_contracts": "2"},
        {"requested_contracts": 2**63},
        {"client_order_id": "x" * 33},
        {"client_order_id": "unsafe/path"},
        {"client_order_id": "x\x00tail"},
        {"command_id": None},
        {"catalogue_snapshot_id": None},
        {"requested_side": None},
        {"origin": "LOCAL_INTENT"},
        {"origin": "ADOPTED"},
        {"state": "FILLED"},
        {"terminal_at": NOW},
        {"last_transition_sequence": -1},
        {"observed_side": "new-enum"},
        {"version": 0},
    ],
)
async def test_local_order_contracts_and_terminality(execution_db, changes):
    db, owner, publication, cmd, *_ = execution_db
    with pytest.raises(PersistenceError):
        await persist(
            db, order(owner, cmd, publication, **({"client_order_id": str(uuid4())[:32]} | changes))
        )


@pytest.mark.asyncio
async def test_adopted_venue_order_preserves_unknown_without_fabricating_intent(execution_db):
    db, _, _, _, _, _ = execution_db
    owner = account(mode="LIVE", profile_name="live")
    await persist(db, owner)
    api = schema_api()
    row = api.Order(
        id=uuid4(),
        **scope(owner),
        origin="ADOPTED",
        exchange_order_id="venue-7",
        product_id=999,
        state="UNKNOWN",
        observed_side="UNKNOWN",
        version=1,
        last_transition_sequence=0,
        created_at=NOW,
        updated_at=NOW,
    )
    await persist(db, row)
    async with db.transaction() as session:
        observed = await session.get(api.Order, row.id)
        assert observed.command_id is observed.catalogue_snapshot_id is None
        assert observed.requested_contracts is observed.client_order_id is None
        assert observed.observed_order_type is None
    for changes in (
        {"exchange_order_id": None},
        {"requested_contracts": 2},
        {"client_order_id": "invented"},
        {"command_id": uuid4()},
    ):
        values = {c.name: getattr(row, c.name) for c in api.Order.__table__.columns}
        values.update(id=uuid4(), exchange_order_id="venue-8")
        values.update(changes)
        with pytest.raises(PersistenceError):
            async with db.transaction() as session:
                await session.execute(insert(api.Order).values(**values))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"command_id": None},
        {"attempt_number": 0},
        {"attempt_number": True},
        {"backend": "VENUE"},
        {"credential_version": 1},
        {"simulator_kind": None},
        {"simulator_kind": "BACKTEST"},
        {"original_leader_ref": uuid4()},
        {"recorded_at": NOW - timedelta(seconds=1)},
        {"original_claim_generation": 0},
        {"original_recovery_generation": 1},
    ],
)
async def test_attempt_requires_command_and_consistent_backend_evidence(execution_db, changes):
    db, owner, _, cmd, row, _ = execution_db
    with pytest.raises(PersistenceError):
        await persist(db, attempt(owner, cmd, row, **({"attempt_number": 2} | changes)))


@pytest.mark.asyncio
async def test_attempt_identity_is_command_scoped_and_order_independent(execution_db):
    db, owner, _, _, row, _ = execution_db
    modify = command(
        owner,
        request_id="modify",
        operation_kind="MODIFY_ORDER",
        evidence={"product_id": 1101, "target_order_id": row.id, "requested_contracts": 3},
    )
    await persist(db, modify)
    await persist(db, attempt(owner, modify, row, operation_kind="MODIFY_ORDER"))
    account_command = command(
        owner,
        request_id="account",
        operation_kind="SET_MARGIN_MODE",
        evidence={"margin_mode": "isolated"},
    )
    await persist(db, account_command)
    await persist(db, attempt(owner, account_command, operation_kind="SET_MARGIN_MODE"))
    with pytest.raises(PersistenceError):
        await persist(db, attempt(owner, modify, operation_kind="MODIFY_ORDER"))


@pytest.mark.asyncio
async def test_venue_attempt_requires_real_scoped_credential_and_original_fence(execution_db):
    db, _, _, _, _, _ = execution_db
    owner = account(mode="LIVE", profile_name="venue")
    await persist(db, owner)
    cmd = command(owner)
    await persist(db, cmd)
    with pytest.raises(PersistenceError):
        await persist(db, venue_attempt(owner, cmd))
    await persist(db, credential(owner))
    await persist(db, venue_attempt(owner, cmd))
    for missing in (
        "original_claim_owner",
        "original_claim_generation",
        "original_claim_expires_at",
        "original_leader_ref",
        "original_leader_generation",
        "credential_version",
    ):
        with pytest.raises(PersistenceError):
            await persist(db, venue_attempt(owner, cmd, attempt_number=2, **{missing: None}))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["event", "transition"])
async def test_evidence_sequence_identity_immutability_and_rollback(execution_db, kind):
    db, owner, _, _, row, prepared = execution_db
    factory, parent = (attempt_event, prepared) if kind == "event" else (transition, row)
    evidence = factory(owner, parent)
    await persist(db, evidence)
    for changes in (
        {"sequence": 0},
        {"sequence": 1, "event_key": "second"},
        {"sequence": 2},
        {"sequence": True},
    ):
        with pytest.raises(PersistenceError):
            await persist(db, factory(owner, parent, **changes))
    # Sequence gaps and same-state observations deliberately remain admissible.
    await persist(db, factory(owner, parent, sequence=3, event_key="third"))
    with pytest.raises(PersistenceError):
        async with db.transaction() as session:
            loaded = await session.get(type(evidence), evidence.id)
            loaded.sequence = 9
    with pytest.raises(PersistenceError):
        async with db.transaction() as session:
            loaded = await session.get(type(evidence), evidence.id)
            await session.delete(loaded)
    with pytest.raises(PersistenceError):
        async with db.transaction() as session:
            session.add(factory(owner, parent, sequence=4, event_key="fourth"))
            await session.flush()
            session.add(factory(owner, parent, sequence=0, event_key="bad"))
    async with db.transaction() as session:
        assert (await session.scalars(select(type(evidence).sequence))).all() == [1, 3]


@pytest.mark.asyncio
async def test_simulator_events_cannot_claim_venue_response_and_resolution_has_own_fence(
    execution_db,
):
    db, owner, _, _, _, prepared = execution_db
    with pytest.raises(PersistenceError):
        await persist(db, attempt_event(owner, prepared, event_kind="RESPONSE_ACCEPTED"))
    await persist(db, attempt_event(owner, prepared, event_kind="SIMULATED_ACCEPTED"))
    with pytest.raises(PersistenceError):
        await persist(
            db,
            attempt_event(
                owner, prepared, sequence=2, event_key="resolution", event_kind="RECONCILED"
            ),
        )
    await persist(
        db,
        attempt_event(
            owner,
            prepared,
            sequence=2,
            event_key="resolution",
            event_kind="RECONCILED",
            ingester_id=uuid4(),
            ingester_generation=7,
            reconciliation_ref=uuid4(),
        ),
    )


@pytest.mark.asyncio
async def test_paper_transition_cannot_attribute_venue_response_to_simulator_attempt(execution_db):
    db, owner, _, _, row, prepared = execution_db
    with pytest.raises(PersistenceError):
        await persist(
            db,
            transition(owner, row, event_kind="RESPONSE", source="VENUE", attempt_id=prepared.id),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"source": "VENUE"},
        {"attempt_backend": "VENUE"},
        {"attempt_id": None, "attempt_backend": None},
        {"attempt_backend": None},
        {"attempt_id": None},
        {"source": "LOCAL"},
        {"source": "RECONCILIATION"},
    ],
)
async def test_response_transition_rejects_invalid_provenance_even_through_core(
    execution_db, changes
):
    db, owner, _, _, row, prepared = execution_db
    candidate = transition(
        owner,
        row,
        event_kind="RESPONSE",
        source="SIMULATOR",
        attempt_id=prepared.id,
        attempt_backend="SIMULATOR",
    )
    values = {c.name: getattr(candidate, c.name) for c in candidate.__table__.columns}
    values.update(changes)
    with pytest.raises(PersistenceError):
        async with db.transaction() as session:
            await session.execute(insert(candidate.__table__).values(**values))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["PAPER", "BACKTEST", "LIVE"])
async def test_response_transition_backend_and_neutral_observations_round_trip(execution_db, mode):
    db, _, publication, *_ = execution_db
    owner = account(mode=mode, profile_name="provenance")
    await persist(db, owner)
    cmd = command(owner)
    await persist(db, cmd)
    row = order(
        owner, cmd, publication, origin="LOCAL_INTENT" if mode == "LIVE" else "SIMULATOR_INTENT"
    )
    await persist(db, row)
    if mode == "LIVE":
        await persist(db, credential(owner))
        prepared = venue_attempt(owner, cmd, row)
    else:
        prepared = attempt(owner, cmd, row, simulator_kind=mode)
    await persist(db, prepared)
    response = transition(
        owner,
        row,
        event_kind="RESPONSE",
        source=prepared.backend,
        attempt_id=prepared.id,
        attempt_backend=prepared.backend,
    )
    await persist(db, response)
    for sequence, source in enumerate(("LOCAL", "RECONCILIATION"), start=2):
        await persist(
            db,
            transition(
                owner,
                row,
                event_kind="OBSERVATION",
                source=source,
                sequence=sequence,
                event_key=f"neutral-{sequence}",
            ),
        )
    # Reconciliation may cite a real attempt, or remain independent of an attempt.
    await persist(
        db,
        transition(
            owner,
            row,
            event_kind="RECONCILIATION",
            source="RECONCILIATION",
            sequence=4,
            event_key="linked-recon",
            attempt_id=prepared.id,
            attempt_backend=prepared.backend,
        ),
    )
    async with db.transaction() as session:
        loaded = await session.get(type(response), response.id)
        assert loaded.attempt_backend == prepared.backend
    wrong_source = "SIMULATOR" if mode == "LIVE" else "VENUE"
    with pytest.raises(PersistenceError):
        await persist(
            db,
            transition(
                owner,
                row,
                event_kind="RESPONSE",
                source=wrong_source,
                sequence=5,
                event_key="wrong-source",
                attempt_id=prepared.id,
                attempt_backend=prepared.backend,
            ),
        )
