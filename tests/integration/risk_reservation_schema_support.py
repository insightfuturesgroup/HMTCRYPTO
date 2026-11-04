"""Portable Task 2.1d9b fixtures; never production locking authority."""

from datetime import timedelta
from decimal import Decimal

from risk_decision_schema_support import decision, decision_graph
from schema_test_support import NOW
from sqlalchemy import and_, func, select
from test_coordination_schema import save

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.risk_reservation_evidence import (
    ATTEMPT_EVENT_FIELDS,
    ATTEMPT_FIELDS,
    decode_risk_reservation_release_proof,
    encode_attempt_evidence_set,
    encode_risk_reservation_release_proof,
)


async def reservation_graph(db, **graph_changes):
    """Persist a valid D9a parent graph and return its initial reservation."""
    graph = await decision_graph(db, **graph_changes)
    approved = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    await save(db, approved)
    reservation = reservation_from_decision(approved)
    return (*graph, approved, reservation)


def reservation_from_decision(approved, **changes):
    """Build the owned initial row at a deterministic test-only timestamp."""
    from schema_test_support import schema_api

    values = {"created_at": NOW + timedelta(seconds=12), **changes}
    return schema_api().RiskReservation.from_decision(decision=approved, **values)


def _source_attempt(prepared, source_events):
    values = {field: getattr(prepared, field) for field in ATTEMPT_FIELDS if field != "events"}
    values["events"] = [
        {field: getattr(item, field) for field in ATTEMPT_EVENT_FIELDS}
        for item in source_events
        if item.attempt_id == prepared.id
    ]
    return values


def _current_intent(reservation, command, order):
    return {
        "command_evidence_schema_version": command.evidence_schema_version,
        "command_evidence_payload": command.evidence_payload,
        "command_evidence_sha256": command.evidence_sha256,
        "decision_approved_contracts": reservation.approved_contracts,
        "decision_approved_side": reservation.approved_side,
        "decision_risk_effect": reservation.risk_effect,
        "order_origin_command_id": order.command_id,
        "order_product_id": order.product_id,
        "order_requested_contracts": order.requested_contracts,
        "order_requested_side": order.requested_side,
        "order_requested_order_type": order.requested_order_type,
        "order_requested_limit_price": order.requested_limit_price,
        "order_requested_stop_price": order.requested_stop_price,
        "order_requested_reduce_only": order.requested_reduce_only,
    }


def _encode_test_only_attempt_set(reservation, command, order, attempts, events, proof_observed_at):
    return encode_attempt_evidence_set(
        1,
        {
            "account_id": reservation.account_id,
            "region": reservation.region,
            "environment": reservation.environment,
            "mode": reservation.mode,
            "reservation_id": reservation.id,
            "decision_id": reservation.decision_id,
            "risk_state_id": reservation.risk_state_id,
            "command_id": reservation.command_id,
            "command_operation_kind": reservation.command_operation_kind,
            "order_id": reservation.order_id,
            "product_id": reservation.product_id,
            "proof_observed_at": proof_observed_at,
        },
        [_source_attempt(item, events) for item in attempts],
        current_intent=_current_intent(reservation, command, order),
        observed_command_attempt_count=command.attempt_count,
        locked_attempt_count=len(attempts),
        locked_event_count=len(events),
    )


def apply_test_only_non_authoritative_release(
    reservation,
    command,
    order,
    attempts,
    events,
    *,
    proof_observed_at=NOW + timedelta(seconds=20),
    terminal_status="FAILED",
):
    """Mutate an ORM fixture only; this does not provide locking/CAS authority."""
    attempt_set = _encode_test_only_attempt_set(
        reservation, command, order, attempts, events, proof_observed_at
    )
    proof = encode_risk_reservation_release_proof(
        1,
        {
            "account_id": reservation.account_id,
            "region": reservation.region,
            "environment": reservation.environment,
            "mode": reservation.mode,
            "reservation_id": reservation.id,
            "reservation_key": reservation.reservation_key,
            "decision_id": reservation.decision_id,
            "risk_state_id": reservation.risk_state_id,
            "command_id": reservation.command_id,
            "command_operation_kind": reservation.command_operation_kind,
            "order_id": reservation.order_id,
            "product_id": reservation.product_id,
            "decision_outcome": reservation.decision_outcome,
            "risk_effect": reservation.risk_effect,
            "reservation_plan_sha256": reservation.reservation_plan_sha256,
            "decision_evaluated_at": reservation.decision_evaluated_at,
            "decision_recorded_at": reservation.decision_recorded_at,
            "reservation_created_at": reservation.created_at,
            "approved_contracts": reservation.approved_contracts,
            "approved_side": reservation.approved_side,
            "reference_asset_id": reservation.reference_asset_id,
            "reference_asset_symbol": reservation.reference_asset_symbol,
            "collateral_asset_id": reservation.collateral_asset_id,
            "collateral_asset_symbol": reservation.collateral_asset_symbol,
            "planned_incremental_gross_exposure": reservation.planned_incremental_gross_exposure,
            "planned_worst_case_loss": reservation.planned_worst_case_loss,
            "planned_collateral": reservation.planned_collateral,
            "planned_open_order_slots": reservation.planned_open_order_slots,
            "planned_pending_rate_units": reservation.planned_pending_rate_units,
            "remaining_contracts": reservation.remaining_contracts,
            "remaining_incremental_gross_exposure": (
                reservation.remaining_incremental_gross_exposure
            ),
            "remaining_worst_case_loss": reservation.remaining_worst_case_loss,
            "remaining_collateral": reservation.remaining_collateral,
            "remaining_open_order_slots": reservation.remaining_open_order_slots,
            "pending_rate_units": reservation.pending_rate_units,
            "rate_policy_version": reservation.rate_policy_version,
            "rate_policy_sha256": reservation.rate_policy_sha256,
            "rate_window_start_at": reservation.rate_window_start_at,
            "rate_window_end_at": reservation.rate_window_end_at,
            "observed_terminal_command_status": terminal_status,
            "observed_command_version": command.version + 1,
            "observed_order_version": order.version,
            "proof_observed_at": proof_observed_at,
        },
        attempt_set,
        current_intent=_current_intent(reservation, command, order),
    )
    reservation.remaining_contracts = 0
    reservation.remaining_incremental_gross_exposure = Decimal("0")
    reservation.remaining_worst_case_loss = Decimal("0")
    reservation.remaining_collateral = Decimal("0")
    reservation.remaining_open_order_slots = 0
    reservation.pending_rate_units = 0
    reservation.status = "RELEASED"
    reservation.version = 2
    reservation.updated_at = proof.proof_observed_at
    reservation.release_key = proof.release_key
    reservation.release_reason_code = "DEFINITE_NOT_DISPATCHED"
    reservation.release_proof_version = 1
    reservation.proof_observed_at = proof.proof_observed_at
    reservation.observed_terminal_command_status = proof.observed_terminal_command_status
    reservation.observed_command_version = proof.observed_command_version
    reservation.observed_command_attempt_count = proof.observed_command_attempt_count
    reservation.observed_order_version = proof.observed_order_version
    reservation.order_attempt_event_evidence_count = proof.order_attempt_event_evidence_count
    reservation.order_attempt_evidence_count = proof.order_attempt_evidence_count
    reservation.attempt_evidence_set_sha256 = proof.attempt_evidence_set_sha256
    reservation.release_proof_payload = proof.payload
    reservation.release_proof_sha256 = proof.sha256
    return proof


SCOPE = ("account_id", "region", "environment", "mode")


def _scope_where(model, source):
    return tuple(getattr(model, field) == getattr(source, field) for field in SCOPE)


async def _retrieve_complete_attempts(session, api, reservation, expected_count):
    rows = (
        await session.scalars(
            select(api.OrderAttempt)
            .where(
                api.OrderAttempt.command_id == reservation.command_id,
                *_scope_where(api.OrderAttempt, reservation),
            )
            .order_by(api.OrderAttempt.attempt_number, api.OrderAttempt.id)
            .limit(65)
        )
    ).all()
    if len(rows) != expected_count:
        raise PersistenceValueError()
    return rows


def _attempt_event_join(api):
    return and_(
        api.OrderAttemptEvent.attempt_id == api.OrderAttempt.id,
        api.OrderAttemptEvent.backend == api.OrderAttempt.backend,
        *(
            getattr(api.OrderAttemptEvent, field) == getattr(api.OrderAttempt, field)
            for field in SCOPE
        ),
    )


async def _retrieve_complete_events(session, api, reservation, expected_count):
    rows = (
        await session.scalars(
            select(api.OrderAttemptEvent)
            .join(api.OrderAttempt, _attempt_event_join(api))
            .where(
                api.OrderAttempt.command_id == reservation.command_id,
                *_scope_where(api.OrderAttempt, reservation),
            )
            .order_by(
                api.OrderAttempt.attempt_number,
                api.OrderAttemptEvent.sequence,
                api.OrderAttemptEvent.id,
            )
            .limit(65)
        )
    ).all()
    if len(rows) != expected_count:
        raise PersistenceValueError()
    return rows


async def run_test_only_non_authoritative_atomic_terminalize_and_release(
    session,
    *,
    reservation_id,
    proof_observed_at=NOW + timedelta(seconds=20),
    terminal_status="FAILED",
):
    """Query and mutate one SQLite unit; proves no locks/CAS/concurrency."""
    from schema_test_support import schema_api

    api = schema_api()
    reservation = await session.get(api.RiskReservation, reservation_id)
    if reservation is None:
        raise PersistenceValueError()
    command = await session.get(api.CommandOperation, reservation.command_id)
    order = await session.get(api.Order, reservation.order_id)
    if (
        command is None
        or order is None
        or reservation.status not in {"PENDING_DISPATCH", "RELEASED"}
        or (reservation.status == "PENDING_DISPATCH" and reservation.version != 1)
        or (reservation.status == "RELEASED" and reservation.version != 2)
        or (
            reservation.status == "PENDING_DISPATCH" and command.status not in {"QUEUED", "CLAIMED"}
        )
        or terminal_status not in {"FAILED", "CANCELLED"}
        or tuple(getattr(command, field) for field in SCOPE)
        != tuple(getattr(reservation, field) for field in SCOPE)
        or tuple(getattr(order, field) for field in SCOPE)
        != tuple(getattr(reservation, field) for field in SCOPE)
        or order.product_id != reservation.product_id
        or command.attempt_count > 64
    ):
        raise PersistenceValueError()
    attempt_count = await session.scalar(
        select(func.count())
        .select_from(api.OrderAttempt)
        .where(
            api.OrderAttempt.command_id == reservation.command_id,
            *_scope_where(api.OrderAttempt, reservation),
        )
    )
    if type(attempt_count) is not int or attempt_count > 64:
        raise PersistenceValueError()
    event_count = await session.scalar(
        select(func.count())
        .select_from(api.OrderAttemptEvent)
        .join(api.OrderAttempt, _attempt_event_join(api))
        .where(
            api.OrderAttempt.command_id == reservation.command_id,
            *_scope_where(api.OrderAttempt, reservation),
        )
    )
    if type(event_count) is not int or event_count > 64:
        raise PersistenceValueError()
    attempts = await _retrieve_complete_attempts(session, api, reservation, attempt_count)
    events = await _retrieve_complete_events(session, api, reservation, event_count)
    if reservation.status == "RELEASED":
        if (
            command.status != reservation.observed_terminal_command_status
            or terminal_status != reservation.observed_terminal_command_status
            or command.version != reservation.observed_command_version
            or command.attempt_count != reservation.observed_command_attempt_count
            or order.version != reservation.observed_order_version
            or reservation.proof_observed_at is None
        ):
            raise PersistenceValueError()
        attempt_set = _encode_test_only_attempt_set(
            reservation,
            command,
            order,
            attempts,
            events,
            reservation.proof_observed_at,
        )
        if (
            attempt_set.sha256 != reservation.attempt_evidence_set_sha256
            or attempt_set.attempt_count != reservation.order_attempt_evidence_count
            or attempt_set.event_count != reservation.order_attempt_event_evidence_count
            or attempt_set.observed_command_attempt_count
            != reservation.observed_command_attempt_count
        ):
            raise PersistenceValueError()
        return decode_risk_reservation_release_proof(reservation.release_proof_payload)
    proof = apply_test_only_non_authoritative_release(
        reservation,
        command,
        order,
        attempts,
        events,
        proof_observed_at=proof_observed_at,
        terminal_status=terminal_status,
    )
    command.status = terminal_status
    command.claim_owner = None
    command.claim_expires_at = None
    command.version += 1
    command.completed_at = proof_observed_at
    command.updated_at = proof_observed_at
    session.add_all([command, reservation])
    await session.flush()
    return proof
