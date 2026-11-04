"""Portable D9c fixtures; these provide no atomic barrier or dispatch authority."""

from datetime import timedelta
from decimal import Decimal

from execution_test_support import attempt as source_attempt
from execution_test_support import attempt_event as source_event
from execution_test_support import command as source_command
from risk_decision_schema_support import decision, decision_graph, reservation_plan
from risk_reservation_schema_support import reservation_from_decision, reservation_graph
from schema_test_support import NOW, schema_api
from test_coordination_schema import save

from hmt_crypto.persistence.risk_rate_consumption_evidence import (
    derive_dispatch_event_key,
)


async def consumption_parent_graph(db, **graph_changes):
    """Persist the immutable parents while leaving the reservation pending."""
    graph = await reservation_graph(db, **graph_changes)
    owner, command, order, reservation = graph[0], graph[6], graph[7], graph[10]
    await save(db, reservation)
    prepared = source_attempt(
        owner,
        command,
        order,
        prepared_at=NOW + timedelta(seconds=13),
        recorded_at=NOW + timedelta(seconds=14),
    )
    await save(db, prepared)
    event = source_event(
        owner,
        prepared,
        event_key=derive_dispatch_event_key(prepared.id),
        recorded_at=NOW + timedelta(seconds=15),
    )
    await save(db, event)
    return graph, prepared, event


async def divergent_attempt_consumption_graph(db):
    """Build canonical attempt/consumption evidence divergent from valid D9a lineage."""
    graph = await reservation_graph(db)
    owner, command, order, reservation = graph[0], graph[6], graph[7], graph[10]
    await save(db, reservation)
    prepared = source_attempt(
        owner,
        command,
        order,
        prepared_at=NOW + timedelta(seconds=13),
        recorded_at=NOW + timedelta(seconds=14),
        evidence={
            "product_id": 1101,
            "requested_contracts": 3,
            "side": "buy",
            "order_type": "market",
            "reduce_only": False,
        },
    )
    await save(db, prepared)
    event = source_event(
        owner,
        prepared,
        event_key=derive_dispatch_event_key(prepared.id),
        recorded_at=NOW + timedelta(seconds=15),
    )
    await save(db, event)
    consumption = schema_api().RiskRateConsumption.from_parents(
        reservation=reservation,
        attempt=prepared,
        dispatch_event=event,
    )
    return graph, prepared, event, consumption


async def pending_consumption_graph(db, **graph_changes):
    """Persist a deliberately split pending parent plus immutable child fixture."""
    graph, prepared, event = await consumption_parent_graph(db, **graph_changes)
    reservation = graph[10]
    consumption = schema_api().RiskRateConsumption.from_parents(
        reservation=reservation,
        attempt=prepared,
        dispatch_event=event,
    )
    await save(db, consumption)
    return graph, prepared, event, consumption


async def committed_consumption_graph(db):
    """Convert the test-only split fixture to the exact frozen committed shape."""
    graph, prepared, event, consumption = await pending_consumption_graph(db)
    reservation = graph[10]
    async with db.transaction() as session:
        stored = await session.get(type(reservation), reservation.id)
        assert stored is not None
        stored.status = "DISPATCH_COMMITTED"
        stored.version = 2
        stored.pending_rate_units = 0
        stored.updated_at = event.recorded_at
    return graph, prepared, event, consumption


async def committed_modify_consumption_graph(db):
    """Persist one genuine size-and-price MODIFY through the complete ORM graph."""
    graph = await decision_graph(db)
    owner, reconciliation, calculation, state, claim_owner, place_command, target_order = (
        graph[index] for index in (0, 2, 3, 4, 5, 6, 7)
    )
    modify_command = source_command(
        owner,
        request_id="d9c-modify",
        operation_kind="MODIFY_ORDER",
        status="CLAIMED",
        claim_owner=claim_owner.id,
        claim_generation=1,
        claim_expires_at=place_command.claim_expires_at,
        evidence={
            "product_id": target_order.product_id,
            "target_order_id": target_order.id,
            "requested_contracts": 3,
            "limit_price": Decimal("11"),
        },
    )
    await save(db, modify_command)
    approved = decision(
        owner,
        reconciliation,
        calculation,
        state,
        claim_owner,
        modify_command,
        target_order,
        decision_key="d9c-modify",
        reservation_plan_override=reservation_plan(
            approved_contracts=3,
            reserved_open_order_slots=0,
        ),
    )
    await save(db, approved)
    reservation = reservation_from_decision(approved)
    await save(db, reservation)
    prepared = source_attempt(
        owner,
        modify_command,
        target_order,
        prepared_at=NOW + timedelta(seconds=13),
        recorded_at=NOW + timedelta(seconds=14),
    )
    await save(db, prepared)
    event = source_event(
        owner,
        prepared,
        event_key=derive_dispatch_event_key(prepared.id),
        recorded_at=NOW + timedelta(seconds=15),
    )
    await save(db, event)
    consumption = schema_api().RiskRateConsumption.from_parents(
        reservation=reservation,
        attempt=prepared,
        dispatch_event=event,
    )
    await save(db, consumption)
    async with db.transaction() as session:
        stored = await session.get(type(reservation), reservation.id)
        assert stored is not None
        stored.status = "DISPATCH_COMMITTED"
        stored.version = 2
        stored.pending_rate_units = 0
        stored.updated_at = event.recorded_at
    return graph, modify_command, approved, reservation, prepared, event, consumption
