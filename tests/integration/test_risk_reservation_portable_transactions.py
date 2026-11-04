"""Single-process d9b algorithms; these tests prove no locking authority."""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
import risk_reservation_creation_test_support as creation_support
import risk_reservation_schema_support as release_support
from execution_test_support import attempt as source_attempt
from execution_test_support import attempt_event as source_event
from execution_test_support import command as source_command
from execution_test_support import order as source_order
from risk_decision_schema_support import (
    check_results,
    decision,
    decision_graph,
    reservation_plan,
)
from risk_reservation_creation_test_support import (
    baseline_locked_capacity,
    run_test_only_non_authoritative_create_reservation,
)
from risk_reservation_schema_support import (
    reservation_graph,
    run_test_only_non_authoritative_atomic_terminalize_and_release,
)
from schema_test_support import NOW, product, schema_api
from schema_test_support import schema_db as schema_db
from sqlalchemy import func, select
from test_coordination_schema import save

from hmt_crypto.persistence.errors import PersistenceValueError


async def _reservation_count(schema_db) -> int:
    async with schema_db.transaction() as session:
        value = await session.scalar(select(func.count()).select_from(schema_api().RiskReservation))
    assert value is not None
    return value


async def _create(schema_db, decision_id, *, created_at=NOW + timedelta(seconds=12)):
    async with schema_db.transaction() as session:
        return await run_test_only_non_authoritative_create_reservation(
            session,
            decision_id=decision_id,
            created_at=created_at,
            locked_base=baseline_locked_capacity(),
        )


async def _decision_with_plan(schema_db, **plan_changes):
    graph = await decision_graph(schema_db)
    approved = decision(
        *[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)],
        reservation_plan_override=reservation_plan(**plan_changes),
    )
    await save(schema_db, approved)
    return graph, approved


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["nonzero_counter", "counter_zero_prior_attempt"])
async def test_test_only_creation_rejects_each_pre_attempt_violation(schema_db, case):
    graph = await reservation_graph(schema_db)
    command = graph[6]
    if case == "nonzero_counter":
        async with schema_db.transaction() as session:
            stored = await session.get(type(command), command.id)
            assert stored is not None
            stored.attempt_count = 1
    else:
        prepared = source_attempt(
            graph[0],
            command,
            graph[7],
            prepared_at=NOW + timedelta(seconds=11),
            recorded_at=NOW + timedelta(seconds=11),
        )
        async with schema_db.transaction() as session:
            session.add(prepared)
    with pytest.raises(PersistenceValueError):
        await _create(schema_db, graph[9].id)
    assert await _reservation_count(schema_db) == 0


@pytest.mark.asyncio
async def test_test_only_creation_rejects_stale_d9a_mutable_evidence(schema_db):
    graph = await reservation_graph(schema_db)
    async with schema_db.transaction() as session:
        command = await session.get(type(graph[6]), graph[6].id)
        assert command is not None
        command.version = 2
        command.updated_at = NOW + timedelta(seconds=1)
    with pytest.raises(PersistenceValueError):
        await _create(schema_db, graph[9].id)
    assert await _reservation_count(schema_db) == 0


@pytest.mark.asyncio
async def test_test_only_creation_rejects_expired_claim_while_rate_window_is_live(
    schema_db,
):
    _, approved = await _decision_with_plan(
        schema_db, rate_window_end_at=NOW + timedelta(minutes=5)
    )
    with pytest.raises(PersistenceValueError):
        await _create(
            schema_db,
            approved.id,
            created_at=NOW + timedelta(minutes=3),
        )
    assert await _reservation_count(schema_db) == 0


@pytest.mark.asyncio
async def test_test_only_creation_rejects_expired_live_leadership_independently(
    schema_db,
):
    graph = list(
        await decision_graph(schema_db, mode="LIVE", profile_name="portable-live-creation")
    )
    async with schema_db.transaction() as session:
        command = await session.get(type(graph[6]), graph[6].id)
        assert command is not None
        command.claim_expires_at = NOW + timedelta(minutes=5)
        command.updated_at = NOW + timedelta(seconds=1)
        graph[6] = command
    leadership = graph[8]
    assert leadership is not None
    approved = decision(
        *[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)],
        reservation_plan_override=reservation_plan(rate_window_end_at=NOW + timedelta(minutes=5)),
        observed_command_leader_ref=leadership.id,
        observed_command_leader_generation=leadership.generation,
        observed_command_recovery_ref=graph[1].id,
        observed_command_recovery_generation=leadership.generation,
        leadership_lease_id=leadership.id,
        recovery_incarnation_id=graph[1].id,
        observed_leadership_owner_id=graph[5].id,
        observed_leadership_generation=leadership.generation,
        observed_leadership_expires_at=leadership.expires_at,
        observed_leadership_version=leadership.version,
    )
    await save(schema_db, approved)
    with pytest.raises(PersistenceValueError):
        await _create(
            schema_db,
            approved.id,
            created_at=NOW + timedelta(minutes=3),
        )
    assert await _reservation_count(schema_db) == 0


@pytest.mark.asyncio
async def test_test_only_creation_rejects_expired_rate_window_with_live_claim(schema_db):
    graph = await reservation_graph(schema_db)
    with pytest.raises(PersistenceValueError):
        await _create(
            schema_db,
            graph[9].id,
            created_at=NOW + timedelta(minutes=2),
        )
    assert await _reservation_count(schema_db) == 0


@pytest.mark.asyncio
async def test_test_only_creation_rechecks_changed_capacity_against_decision_limits(
    schema_db,
):
    graph = await reservation_graph(schema_db)
    async with schema_db.transaction() as session:
        session.add(graph[10])

    owner, state, claim_owner = graph[0], graph[4], graph[5]
    command = source_command(
        owner,
        request_id="request-capacity-2",
        status="CLAIMED",
        claim_owner=claim_owner.id,
        claim_generation=1,
        claim_expires_at=NOW + timedelta(minutes=3),
    )
    await save(schema_db, command)
    order = source_order(
        owner,
        command,
        SimpleNamespace(id=graph[7].catalogue_snapshot_id),
        client_order_id="capacity-2",
    )
    await save(schema_db, order)
    checks = check_results()
    checks["TOTAL_EXPOSURE"]["limit_decimal"] = Decimal("100")
    approved = decision(
        owner,
        graph[2],
        graph[3],
        state,
        claim_owner,
        command,
        order,
        decision_key="decision-capacity-2",
        check_results_override=checks,
    )
    await save(schema_db, approved)

    with pytest.raises(PersistenceValueError):
        await _create(schema_db, approved.id)
    assert await _reservation_count(schema_db) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("account_observed_exposure,accepted", [("200", True), ("100", False)])
async def test_test_only_creation_isolates_product_position_but_aggregates_account_capacity(
    schema_db, account_observed_exposure, accepted
):
    graph = await reservation_graph(schema_db)
    async with schema_db.transaction() as session:
        session.add_all(
            [
                graph[10],
                schema_api().CatalogueProduct.from_product(
                    snapshot_id=graph[7].catalogue_snapshot_id,
                    product=product(1),
                ),
            ]
        )
    owner, state, claim_owner = graph[0], graph[4], graph[5]
    command = source_command(
        owner,
        request_id="request-cross-product",
        status="CLAIMED",
        claim_owner=claim_owner.id,
        claim_generation=1,
        claim_expires_at=NOW + timedelta(minutes=3),
        evidence={
            "product_id": 1102,
            "requested_contracts": 2,
            "side": "buy",
            "order_type": "market",
            "reduce_only": False,
        },
    )
    await save(schema_db, command)
    order = source_order(
        owner,
        command,
        SimpleNamespace(id=graph[7].catalogue_snapshot_id),
        client_order_id="cross-product",
        product_id=1102,
    )
    await save(schema_db, order)
    checks = check_results()
    checks["MAX_POSITION"].update(observed_integer=2, limit_integer=2)
    checks["TOTAL_EXPOSURE"]["observed_decimal"] = Decimal(account_observed_exposure)
    checks["MAX_DAILY_LOSS"]["observed_decimal"] = Decimal("30")
    checks["MAX_OPEN_ORDERS"]["observed_integer"] = 2
    checks["MAX_ORDER_RATE"]["observed_integer"] = 2
    approved = decision(
        owner,
        graph[2],
        graph[3],
        state,
        claim_owner,
        command,
        order,
        decision_key="decision-cross-product",
        check_results_override=checks,
    )
    await save(schema_db, approved)
    if accepted:
        created, replay = await _create(schema_db, approved.id)
        assert not replay and created.product_id == 1102
        assert await _reservation_count(schema_db) == 2
    else:
        with pytest.raises(PersistenceValueError):
            await _create(schema_db, approved.id)
        assert await _reservation_count(schema_db) == 1


@pytest.mark.asyncio
async def test_test_only_creation_returns_exact_replay_after_later_attempt_without_resum(
    schema_db, monkeypatch
):
    graph = await reservation_graph(schema_db)
    created, was_replay = await _create(schema_db, graph[9].id)
    assert not was_replay

    prepared = source_attempt(
        graph[0],
        graph[6],
        graph[7],
        prepared_at=NOW + timedelta(seconds=13),
        recorded_at=NOW + timedelta(seconds=13),
    )
    async with schema_db.transaction() as session:
        command = await session.get(type(graph[6]), graph[6].id)
        assert command is not None
        command.attempt_count = 1
        session.add(prepared)

    def _capacity_must_not_run(*args, **kwargs):
        raise AssertionError("an exact replay must not be re-summed")

    monkeypatch.setattr(creation_support, "calculate_capacity", _capacity_must_not_run)
    replay, was_replay = await _create(
        schema_db, graph[9].id, created_at=NOW + timedelta(seconds=14)
    )
    assert was_replay
    assert replay.id == created.id
    assert replay.reservation_key == created.reservation_key
    assert await _reservation_count(schema_db) == 1


def _row_values(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.c}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "command_count_over_limit",
        "incomplete",
        "unsafe_extra_persisted",
        "byte_cap",
    ],
)
async def test_test_only_release_evidence_failure_leaves_command_and_capacity_unchanged(
    schema_db, case, monkeypatch
):
    graph = await reservation_graph(schema_db)
    reservation = graph[10]
    async with schema_db.transaction() as session:
        session.add(reservation)
    prepared = source_attempt(
        graph[0],
        graph[6],
        graph[7],
        prepared_at=NOW + timedelta(seconds=13),
        recorded_at=NOW + timedelta(seconds=13),
    )
    safe_event = source_event(
        graph[0],
        prepared,
        event_kind="DEFINITE_NOT_SENT",
        recorded_at=NOW + timedelta(seconds=14),
        evidence={"outcome": "NOT_SENT"},
    )
    events = [safe_event]
    if case == "unsafe_extra_persisted":
        events.append(
            source_event(
                graph[0],
                prepared,
                id=uuid4(),
                sequence=2,
                event_key="unsafe-extra-persisted",
                event_kind="DISPATCH_STARTED",
                recorded_at=NOW + timedelta(seconds=15),
                evidence={"outcome": "UNKNOWN"},
            )
        )
    async with schema_db.transaction() as session:
        session.add_all([prepared, *events])
        if case == "command_count_over_limit":
            command = await session.get(type(graph[6]), graph[6].id)
            assert command is not None
            command.attempt_count = 65

    if case == "incomplete":

        async def truncate_retrieval(*args, **kwargs):
            return []

        monkeypatch.setattr(release_support, "_retrieve_complete_attempts", truncate_retrieval)
    elif case == "byte_cap":

        def reject_oversized_canonical(*args, **kwargs):
            from hmt_crypto.persistence.risk_reservation_evidence import (
                guard_attempt_set_canonical_bytes,
            )

            return guard_attempt_set_canonical_bytes(b"x" * 524_289)

        monkeypatch.setattr(
            release_support,
            "encode_attempt_evidence_set",
            reject_oversized_canonical,
        )

    async with schema_db.transaction() as session:
        command = await session.get(type(graph[6]), graph[6].id)
        stored = await session.get(type(reservation), reservation.id)
        assert command is not None and stored is not None
        before_command = _row_values(command)
        before_reservation = _row_values(stored)

    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await run_test_only_non_authoritative_atomic_terminalize_and_release(
                session,
                reservation_id=reservation.id,
            )

    async with schema_db.transaction() as session:
        command = await session.get(type(graph[6]), graph[6].id)
        stored = await session.get(type(reservation), reservation.id)
        assert command is not None and stored is not None
        assert _row_values(command) == before_command
        assert _row_values(stored) == before_reservation


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["attempts", "events"])
async def test_test_only_release_queries_stored_rows_and_rejects_each_65_boundary(
    schema_db, boundary
):
    graph = await reservation_graph(schema_db)
    reservation = graph[10]
    async with schema_db.transaction() as session:
        session.add(reservation)
    attempts = []
    events = []
    attempt_total = 65 if boundary == "attempts" else 1
    for index in range(attempt_total):
        attempt_id = uuid4()
        prepared = source_attempt(
            graph[0],
            graph[6],
            graph[7],
            id=attempt_id,
            attempt_number=index + 1,
            correlation_id=uuid4(),
            prepared_at=NOW + timedelta(seconds=13),
            recorded_at=NOW + timedelta(seconds=13),
        )
        attempts.append(prepared)
        if boundary == "attempts":
            continue
        for sequence in range(1, 66):
            event_kind = "DEFINITE_NOT_SENT" if sequence == 1 else "DISPATCH_STARTED"
            outcome = "NOT_SENT" if sequence == 1 else "UNKNOWN"
            events.append(
                source_event(
                    graph[0],
                    prepared,
                    id=uuid4(),
                    sequence=sequence,
                    event_key=f"stored-event-{sequence}",
                    event_kind=event_kind,
                    recorded_at=NOW + timedelta(seconds=14),
                    evidence={"outcome": outcome},
                )
            )
    async with schema_db.transaction() as session:
        session.add_all([*attempts, *events])
        command = await session.get(type(graph[6]), graph[6].id)
        assert command is not None
        command.attempt_count = 64 if boundary == "attempts" else 1
    async with schema_db.transaction() as session:
        command = await session.get(type(graph[6]), graph[6].id)
        stored = await session.get(type(reservation), reservation.id)
        assert command is not None and stored is not None
        before_command = _row_values(command)
        before_reservation = _row_values(stored)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await run_test_only_non_authoritative_atomic_terminalize_and_release(
                session,
                reservation_id=reservation.id,
            )
    async with schema_db.transaction() as session:
        command = await session.get(type(graph[6]), graph[6].id)
        stored = await session.get(type(reservation), reservation.id)
        assert command is not None and stored is not None
        assert _row_values(command) == before_command
        assert _row_values(stored) == before_reservation


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["attempts", "events"])
async def test_hard_bounded_retrieval_rejects_locked_count_64_when_row_65_appears(
    schema_db, boundary
):
    graph = await reservation_graph(schema_db)
    reservation = graph[10]
    attempts = []
    events = []
    attempt_total = 65 if boundary == "attempts" else 1
    for index in range(attempt_total):
        attempt_id = uuid4()
        prepared = source_attempt(
            graph[0],
            graph[6],
            graph[7],
            id=attempt_id,
            attempt_number=index + 1,
            correlation_id=uuid4(),
            prepared_at=NOW + timedelta(seconds=13),
            recorded_at=NOW + timedelta(seconds=13),
        )
        attempts.append(prepared)
        if boundary == "events":
            for sequence in range(1, 66):
                events.append(
                    source_event(
                        graph[0],
                        prepared,
                        id=uuid4(),
                        sequence=sequence,
                        event_key=f"retrieval-disagreement-{sequence}",
                        event_kind="DEFINITE_NOT_SENT",
                        recorded_at=NOW + timedelta(seconds=14),
                        evidence={"outcome": "NOT_SENT"},
                    )
                )
    async with schema_db.transaction() as session:
        session.add_all([reservation, *attempts, *events])
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            stored = await session.get(type(reservation), reservation.id)
            assert stored is not None
            if boundary == "attempts":
                await release_support._retrieve_complete_attempts(session, schema_api(), stored, 64)
            else:
                await release_support._retrieve_complete_events(session, schema_api(), stored, 64)


@pytest.mark.asyncio
async def test_test_only_release_loads_exact_current_command_and_order_parents(schema_db):
    graph = await reservation_graph(schema_db)
    async with schema_db.transaction() as session:
        session.add(graph[10])
    prepared = source_attempt(
        graph[0],
        graph[6],
        graph[7],
        prepared_at=NOW + timedelta(seconds=13),
        recorded_at=NOW + timedelta(seconds=13),
    )
    observed = source_event(
        graph[0],
        prepared,
        event_kind="DEFINITE_NOT_SENT",
        recorded_at=NOW + timedelta(seconds=14),
        evidence={"outcome": "NOT_SENT"},
    )
    alternate_command = source_command(
        graph[0],
        request_id="wrong-parent-command",
        status="CLAIMED",
        claim_owner=graph[5].id,
        claim_generation=1,
        claim_expires_at=NOW + timedelta(minutes=3),
    )
    await save(schema_db, alternate_command)
    alternate_order = source_order(
        graph[0],
        alternate_command,
        SimpleNamespace(id=graph[7].catalogue_snapshot_id),
        client_order_id="wrong-parent-order",
    )
    await save(schema_db, alternate_order)
    async with schema_db.transaction() as session:
        session.add_all([prepared, observed])
        current_order = await session.get(type(graph[7]), graph[7].id)
        assert current_order is not None
        current_order.version = 2
        current_order.updated_at = NOW + timedelta(seconds=1)
    async with schema_db.transaction() as session:
        proof = await run_test_only_non_authoritative_atomic_terminalize_and_release(
            session, reservation_id=graph[10].id
        )
    assert proof.observed_order_version == 2
    async with schema_db.transaction() as session:
        original = await session.get(type(graph[6]), graph[6].id)
        alternate = await session.get(type(alternate_command), alternate_command.id)
        reservation = await session.get(type(graph[10]), graph[10].id)
        assert original is not None and alternate is not None and reservation is not None
        assert original.status == "FAILED"
        assert alternate.status == "CLAIMED"
        assert reservation.order_id == graph[7].id != alternate_order.id
