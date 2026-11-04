"""Real database execution fixtures; no transport or private credentials."""

import json
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest_asyncio
from schema_test_support import NOW, account, credential, product, schema_api, scope, snapshot
from schema_test_support import schema_db as schema_db


def command(owner, **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        request_id="request-1",
        operation_kind="PLACE_ORDER",
        correlation_id=uuid4(),
        status="QUEUED",
        available_at=NOW,
        attempt_count=0,
        claim_generation=0,
        version=1,
        created_at=NOW,
        updated_at=NOW,
        evidence={
            "product_id": 1101,
            "requested_contracts": 2,
            "side": "buy",
            "order_type": "market",
            "reduce_only": False,
        },
    )
    values.update(changes)
    return schema_api().CommandOperation.from_evidence(**values)


def order(owner, cmd, publication, **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        command_id=cmd.id,
        origin="SIMULATOR_INTENT",
        client_order_id="client-1",
        catalogue_snapshot_id=publication.id,
        product_id=1101,
        requested_contracts=2,
        requested_side="buy",
        requested_order_type="limit",
        requested_limit_price=None,
        requested_reduce_only=False,
        state="PREPARED",
        version=1,
        last_transition_sequence=0,
        created_at=NOW,
        updated_at=NOW,
    )
    values.update(changes)
    return schema_api().Order(**values)


def attempt(owner, cmd, row=None, **changes):
    evidence = json.loads(cmd.evidence_payload)
    for field in ("limit_price", "leverage", "margin_delta"):
        if field in evidence:
            evidence[field] = Decimal(evidence[field])
    if "target_order_id" in evidence:
        evidence["target_order_id"] = UUID(evidence["target_order_id"])
    values = dict(
        id=uuid4(),
        **scope(owner),
        command_id=cmd.id,
        order_id=row.id if row else None,
        attempt_number=1,
        operation_kind=cmd.operation_kind,
        backend="SIMULATOR",
        simulator_kind="PAPER",
        correlation_id=uuid4(),
        prepared_at=NOW,
        recorded_at=NOW,
        evidence=evidence,
    )
    values.update(changes)
    return schema_api().OrderAttempt.from_evidence(**values)


def attempt_event(owner, prepared, **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        attempt_id=prepared.id,
        backend=prepared.backend,
        sequence=1,
        event_key="event-1",
        event_kind="DISPATCH_STARTED",
        recorded_at=NOW,
        evidence={"outcome": "UNKNOWN"},
    )
    values.update(changes)
    return schema_api().OrderAttemptEvent.from_evidence(**values)


def transition(owner, row, **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        order_id=row.id,
        sequence=1,
        event_kind="OBSERVATION",
        to_state="UNKNOWN",
        event_key="transition-1",
        source="SIMULATOR",
        recorded_at=NOW,
        evidence={"reason": "OBSERVED"},
    )
    values.update(changes)
    return schema_api().OrderTransition.from_evidence(**values)


@pytest_asyncio.fixture
async def execution_db(schema_db):
    api = schema_api()
    owner, publication = account(), snapshot()
    async with schema_db.transaction() as session:
        session.add_all([owner, publication])
    async with schema_db.transaction() as session:
        session.add_all(
            [
                credential(owner),
                api.CatalogueProduct.from_product(snapshot_id=publication.id, product=product()),
            ]
        )
    cmd = command(owner)
    async with schema_db.transaction() as session:
        session.add(cmd)
    row = order(owner, cmd, publication)
    async with schema_db.transaction() as session:
        session.add(row)
    prepared = attempt(owner, cmd, row)
    async with schema_db.transaction() as session:
        session.add(prepared)
    return schema_db, owner, publication, cmd, row, prepared


def venue_attempt(owner, cmd, row=None, **changes):
    values = dict(
        backend="VENUE",
        simulator_kind=None,
        credential_version=1,
        original_claim_owner=uuid4(),
        original_claim_generation=1,
        original_claim_expires_at=NOW + timedelta(minutes=1),
        original_leader_ref=uuid4(),
        original_leader_generation=1,
    )
    values.update(changes)
    return attempt(owner, cmd, row, **values)
