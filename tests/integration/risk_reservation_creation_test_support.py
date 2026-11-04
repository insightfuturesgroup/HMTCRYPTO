"""Portable d9b creation validator; never production locking authority.

This deliberately test-only helper exercises the reviewed single-process
algorithm against SQLite.  It cannot prove lock order, database wall time,
phantom prevention, contention safety, or any other production authority.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from schema_test_support import schema_api
from sqlalchemy import select

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.risk_reservation_capacity import (
    _from_scaled,
    _scaled,
    calculate_capacity,
)

SCOPE = ("account_id", "region", "environment", "mode")
REPLAY_IDENTITY = (
    *SCOPE,
    "reservation_key",
    "decision_id",
    "risk_state_id",
    "command_id",
    "command_operation_kind",
    "order_id",
    "product_id",
    "decision_outcome",
    "risk_effect",
    "reservation_plan_sha256",
    "decision_evaluated_at",
    "decision_recorded_at",
    "approved_contracts",
    "approved_side",
    "reference_asset_id",
    "reference_asset_symbol",
    "collateral_asset_id",
    "collateral_asset_symbol",
    "planned_incremental_gross_exposure",
    "planned_worst_case_loss",
    "planned_collateral",
    "planned_open_order_slots",
    "planned_pending_rate_units",
    "rate_policy_version",
    "rate_policy_sha256",
    "rate_window_start_at",
    "rate_window_end_at",
    "created_at",
)


@dataclass(frozen=True)
class LockedCapacityBase:
    """Explicit already-locked source values for one portable test snapshot."""

    product_contracts: int
    account_gross_exposure: Decimal
    account_daily_loss: Decimal
    account_collateral: Decimal
    account_open_order_slots: int
    account_rate_units: int


def baseline_locked_capacity() -> LockedCapacityBase:
    """Match the deterministic d9a fixture before its proposed reservation."""
    return LockedCapacityBase(
        product_contracts=0,
        account_gross_exposure=Decimal("0"),
        account_daily_loss=Decimal("10"),
        account_collateral=Decimal("0"),
        account_open_order_slots=0,
        account_rate_units=0,
    )


def _fail() -> None:
    raise PersistenceValueError()


def _scope_predicates(model, source):
    return tuple(getattr(model, field) == getattr(source, field) for field in SCOPE)


def _same(source, target, fields) -> bool:
    return all(getattr(source, field) == getattr(target, field) for field in fields)


def _validate_mutable_parents(decision, state, command, order, leadership, created_at):
    state_fields = (
        ("version", "observed_risk_state_version"),
        ("halt_set_state", "observed_halt_set_state"),
        ("halt_revision", "observed_halt_revision"),
        ("reconciliation_state", "observed_reconciliation_state"),
        ("accounting_state", "observed_accounting_state"),
        ("reconciliation_run_id", "reconciliation_run_id"),
        ("reconciliation_run_version", "reconciliation_run_version"),
        ("reconciliation_observed_at", "reconciliation_observed_at"),
        ("pnl_snapshot_id", "pnl_snapshot_id"),
        ("accounting_calculation_version", "pnl_calculation_version"),
        ("accounting_input_set_sha256", "pnl_input_set_sha256"),
        ("reference_asset_id", "pnl_reference_asset_id"),
        ("reference_asset_symbol", "pnl_reference_asset_symbol"),
    )
    command_fields = (
        ("version", "observed_command_version"),
        ("claim_owner", "observed_claim_owner_id"),
        ("claim_generation", "observed_claim_generation"),
        ("claim_expires_at", "observed_claim_expires_at"),
        ("leader_ref", "observed_command_leader_ref"),
        ("leader_generation", "observed_command_leader_generation"),
        ("recovery_ref", "observed_command_recovery_ref"),
        ("recovery_generation", "observed_command_recovery_generation"),
        ("operation_kind", "command_operation_kind"),
        ("evidence_schema_version", "command_evidence_schema_version"),
        ("evidence_payload", "command_evidence_payload"),
        ("evidence_sha256", "command_evidence_sha256"),
    )
    order_fields = (
        ("version", "observed_order_version"),
        ("last_transition_sequence", "observed_order_transition_sequence"),
        ("command_id", "order_origin_command_id"),
        ("product_id", "product_id"),
        ("requested_contracts", "order_requested_contracts"),
        ("requested_side", "order_requested_side"),
        ("requested_reduce_only", "order_requested_reduce_only"),
    )
    if (
        state is None
        or command is None
        or order is None
        or decision.outcome != "APPROVED"
        or command.status != "CLAIMED"
        or command.id != decision.command_id
        or order.id != decision.order_id
        or any(getattr(state, left) != getattr(decision, right) for left, right in state_fields)
        or any(getattr(command, left) != getattr(decision, right) for left, right in command_fields)
        or any(getattr(order, left) != getattr(decision, right) for left, right in order_fields)
        or command.claim_expires_at is None
        or created_at >= command.claim_expires_at
    ):
        _fail()
    if decision.mode == "LIVE":
        leadership_fields = (
            ("id", "leadership_lease_id"),
            ("recovery_incarnation_id", "recovery_incarnation_id"),
            ("owner_id", "observed_leadership_owner_id"),
            ("generation", "observed_leadership_generation"),
            ("expires_at", "observed_leadership_expires_at"),
            ("version", "observed_leadership_version"),
        )
        if (
            leadership is None
            or leadership.state != "HELD"
            or any(
                getattr(leadership, left) != getattr(decision, right)
                for left, right in leadership_fields
            )
            or leadership.expires_at is None
            or created_at >= leadership.expires_at
        ):
            _fail()
    elif leadership is not None or decision.leadership_lease_id is not None:
        _fail()


def _capacity_plan(reservation):
    return {
        "account_id": reservation.account_id,
        "region": reservation.region,
        "environment": reservation.environment,
        "mode": reservation.mode,
        "product_id": reservation.product_id,
        "contracts": reservation.approved_contracts,
        "incremental_gross_exposure": reservation.planned_incremental_gross_exposure,
        "worst_case_loss": reservation.planned_worst_case_loss,
        "collateral": reservation.planned_collateral,
        "open_order_slots": reservation.planned_open_order_slots,
        "pending_rate_units": reservation.planned_pending_rate_units,
        "rate_policy_version": reservation.rate_policy_version,
        "rate_policy_sha256": reservation.rate_policy_sha256,
        "rate_window_start_at": reservation.rate_window_start_at,
        "rate_window_end_at": reservation.rate_window_end_at,
    }


def _capacity_reservation(reservation):
    return {
        "account_id": reservation.account_id,
        "region": reservation.region,
        "environment": reservation.environment,
        "mode": reservation.mode,
        "product_id": reservation.product_id,
        "status": reservation.status,
        "remaining_contracts": reservation.remaining_contracts,
        "remaining_incremental_gross_exposure": (reservation.remaining_incremental_gross_exposure),
        "remaining_worst_case_loss": reservation.remaining_worst_case_loss,
        "remaining_collateral": reservation.remaining_collateral,
        "remaining_open_order_slots": reservation.remaining_open_order_slots,
        "pending_rate_units": reservation.pending_rate_units,
        "rate_policy_version": reservation.rate_policy_version,
        "rate_policy_sha256": reservation.rate_policy_sha256,
        "rate_window_start_at": reservation.rate_window_start_at,
        "rate_window_end_at": reservation.rate_window_end_at,
    }


def _add_exact(left: Decimal, right: Decimal) -> Decimal:
    return _from_scaled(_scaled(left) + _scaled(right))


def _require_capacity_within_decision(decision, candidate, totals, base) -> None:
    if type(base) is not LockedCapacityBase:
        _fail()
    payload = json.loads(decision.evidence_payload)
    checks = {item["check_kind"]: item for item in payload["check_results"]}
    comparisons = (
        (
            "MAX_POSITION",
            base.product_contracts + totals.contracts,
            base.product_contracts + totals.contracts,
            "limit_integer",
            int,
        ),
        (
            "TOTAL_EXPOSURE",
            _add_exact(base.account_gross_exposure, totals.incremental_gross_exposure),
            _add_exact(base.account_gross_exposure, totals.incremental_gross_exposure),
            "limit_decimal",
            Decimal,
        ),
        (
            "MAX_LOSS_PER_TRADE",
            candidate.planned_worst_case_loss,
            candidate.planned_worst_case_loss,
            "limit_decimal",
            Decimal,
        ),
        (
            "MAX_DAILY_LOSS",
            _add_exact(base.account_daily_loss, totals.worst_case_loss),
            _add_exact(base.account_daily_loss, totals.worst_case_loss),
            "limit_decimal",
            Decimal,
        ),
        (
            "COLLATERAL_MARGIN",
            candidate.planned_collateral,
            _add_exact(base.account_collateral, totals.collateral),
            "limit_decimal",
            Decimal,
        ),
        (
            "MAX_OPEN_ORDERS",
            base.account_open_order_slots + totals.open_order_slots,
            base.account_open_order_slots + totals.open_order_slots,
            "limit_integer",
            int,
        ),
        (
            "MAX_ORDER_RATE",
            base.account_rate_units + totals.rate_units,
            base.account_rate_units + totals.rate_units,
            "limit_integer",
            int,
        ),
    )
    for kind, observed_value, limited_value, limit_field, converter in comparisons:
        check = checks[kind]
        observed = check[limit_field.replace("limit", "observed")]
        limit = check[limit_field]
        if (
            check["outcome"] != "PASS"
            or observed is None
            or limit is None
            or observed_value != converter(observed)
            or limited_value > converter(limit)
        ):
            _fail()


async def run_test_only_non_authoritative_create_reservation(
    session,
    *,
    decision_id,
    created_at: datetime,
    locked_base: LockedCapacityBase,
):
    """Validate/insert once in SQLite; proves no locks, DB time, or concurrency."""
    api = schema_api()
    decision = await session.get(api.RiskDecision, decision_id)
    if decision is None:
        _fail()
    existing = await session.scalar(
        select(api.RiskReservation).where(
            api.RiskReservation.decision_id == decision.id,
            *_scope_predicates(api.RiskReservation, decision),
        )
    )
    if existing is not None:
        expected = api.RiskReservation.from_decision(
            decision=decision, created_at=existing.created_at
        )
        if not _same(existing, expected, REPLAY_IDENTITY):
            _fail()
        return existing, True

    state = await session.get(api.RiskState, decision.risk_state_id)
    command = await session.get(api.CommandOperation, decision.command_id)
    order = await session.get(api.Order, decision.order_id)
    leadership = (
        await session.get(api.LeadershipLease, decision.leadership_lease_id)
        if decision.leadership_lease_id is not None
        else None
    )
    _validate_mutable_parents(decision, state, command, order, leadership, created_at)
    if command.attempt_count != 0 or not (
        decision.rate_window_start_at
        <= decision.evaluated_at
        <= decision.recorded_at
        <= created_at
        < decision.rate_window_end_at
    ):
        _fail()
    attempts = (
        await session.scalars(
            select(api.OrderAttempt).where(
                api.OrderAttempt.command_id == decision.command_id,
                *_scope_predicates(api.OrderAttempt, decision),
            )
        )
    ).all()
    if attempts:
        _fail()

    candidate = api.RiskReservation.from_decision(decision=decision, created_at=created_at)
    active = (
        await session.scalars(
            select(api.RiskReservation).where(
                *_scope_predicates(api.RiskReservation, decision),
                api.RiskReservation.status != "RELEASED",
            )
        )
    ).all()
    totals = calculate_capacity(
        _capacity_plan(candidate),
        [_capacity_reservation(item) for item in active],
        [],
    )
    _require_capacity_within_decision(decision, candidate, totals, locked_base)
    session.add(candidate)
    await session.flush()
    return candidate, False
