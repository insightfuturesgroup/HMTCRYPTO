"""Literal broker-free inputs for Task 2.1d9b reservation evidence."""

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

DECISION_ID = UUID("10000000-0000-4000-8000-000000000001")
ACCOUNT_ID = UUID("20000000-0000-4000-8000-000000000002")
RISK_STATE_ID = UUID("30000000-0000-4000-8000-000000000003")
COMMAND_ID = UUID("40000000-0000-4000-8000-000000000004")
ORDER_ID = UUID("50000000-0000-4000-8000-000000000005")
RESERVATION_ID = UUID("90000000-0000-4000-8000-000000000009")
ATTEMPT_ID = UUID("a0000000-0000-4000-8000-00000000000a")
EVENT_ID = UUID("b0000000-0000-4000-8000-00000000000b")
CORRELATION_ID = UUID("c0000000-0000-4000-8000-00000000000c")

EVALUATED_AT = datetime(2026, 11, 4, 12, 0, 10, tzinfo=UTC)
DECISION_RECORDED_AT = datetime(2026, 11, 4, 12, 0, 11, tzinfo=UTC)
CREATED_AT = datetime(2026, 11, 4, 12, 0, 12, tzinfo=UTC)
ATTEMPT_PREPARED_AT = datetime(2026, 11, 4, 12, 0, 13, tzinfo=UTC)
EVENT_RECORDED_AT = datetime(2026, 11, 4, 12, 0, 14, tzinfo=UTC)
PROOF_OBSERVED_AT = datetime(2026, 11, 4, 12, 0, 20, tzinfo=UTC)
RATE_WINDOW_START_AT = datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC)
RATE_WINDOW_END_AT = datetime(2026, 11, 4, 12, 1, 0, tzinfo=UTC)

PLAN_SHA = "c" * 64
RATE_SHA = "d" * 64
COMMAND_PAYLOAD = (
    '{"order_type":"market","product_id":1101,"reduce_only":false,'
    '"requested_contracts":2,"side":"buy"}'
)
COMMAND_SHA = "7d551ad745bfadd1489333b70885de0c7f60a0447f094adc93219ad1d5c1bf00"
EVENT_PAYLOAD = '{"outcome":"NOT_SENT"}'
EVENT_SHA = "71089fc53dc77b2dec661f88a4c3584b3f2a7b92e0dc5353f42e6c1717884075"
RESERVATION_KEY = "d6fe39a948139a9705ee8e91f13528343db38cf9be4b987b21408080bfea1626"


def reservation_key_values(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "account_id": ACCOUNT_ID,
        "command_id": COMMAND_ID,
        "command_operation_kind": "PLACE_ORDER",
        "decision_evaluated_at": EVALUATED_AT,
        "decision_id": DECISION_ID,
        "decision_outcome": "APPROVED",
        "decision_recorded_at": DECISION_RECORDED_AT,
        "environment": "testnet",
        "mode": "PAPER",
        "order_id": ORDER_ID,
        "product_id": 1101,
        "region": "india",
        "reservation_plan_sha256": PLAN_SHA,
        "risk_effect": "RISK_INCREASE",
        "risk_state_id": RISK_STATE_ID,
    }
    values.update(changes)
    return values


def event(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": EVENT_ID,
        "account_id": ACCOUNT_ID,
        "region": "india",
        "environment": "testnet",
        "mode": "PAPER",
        "attempt_id": ATTEMPT_ID,
        "backend": "SIMULATOR",
        "sequence": 1,
        "event_key": "definite-not-sent-1",
        "event_kind": "DEFINITE_NOT_SENT",
        "observed_at": None,
        "recorded_at": EVENT_RECORDED_AT,
        "ingester_id": None,
        "ingester_generation": None,
        "reconciliation_ref": None,
        "ingester_leader_ref": None,
        "ingester_leader_generation": None,
        "ingester_recovery_ref": None,
        "ingester_recovery_generation": None,
        "evidence_schema_version": 1,
        "evidence_payload": EVENT_PAYLOAD,
        "evidence_sha256": EVENT_SHA,
    }
    values.update(changes)
    return values


def attempt(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": ATTEMPT_ID,
        "account_id": ACCOUNT_ID,
        "region": "india",
        "environment": "testnet",
        "mode": "PAPER",
        "command_id": COMMAND_ID,
        "order_id": ORDER_ID,
        "attempt_number": 1,
        "operation_kind": "PLACE_ORDER",
        "backend": "SIMULATOR",
        "simulator_kind": "PAPER",
        "credential_version": None,
        "correlation_id": CORRELATION_ID,
        "prepared_at": ATTEMPT_PREPARED_AT,
        "recorded_at": ATTEMPT_PREPARED_AT,
        "original_claim_owner": None,
        "original_claim_generation": None,
        "original_claim_expires_at": None,
        "original_leader_ref": None,
        "original_leader_generation": None,
        "original_recovery_ref": None,
        "original_recovery_generation": None,
        "evidence_schema_version": 1,
        "evidence_payload": COMMAND_PAYLOAD,
        "evidence_sha256": COMMAND_SHA,
        "events": [event()],
    }
    values.update(changes)
    return values


def attempt_set_values(**changes: object) -> dict[str, object]:
    values = reservation_key_values() | {
        "reservation_id": RESERVATION_ID,
        "proof_observed_at": PROOF_OBSERVED_AT,
    }
    values.pop("decision_evaluated_at")
    values.pop("decision_outcome")
    values.pop("decision_recorded_at")
    values.pop("reservation_plan_sha256")
    values.pop("risk_effect")
    values.update(changes)
    return values


def current_intent(**changes: object) -> dict[str, object]:
    """Return explicit immutable-decision and current locked intent snapshots."""
    values: dict[str, object] = {
        "decision_command_evidence_schema_version": 1,
        "decision_command_evidence_payload": COMMAND_PAYLOAD,
        "decision_command_evidence_sha256": COMMAND_SHA,
        "current_command_evidence_schema_version": 1,
        "current_command_evidence_payload": COMMAND_PAYLOAD,
        "current_command_evidence_sha256": COMMAND_SHA,
        "decision_approved_contracts": 2,
        "decision_approved_side": "buy",
        "decision_risk_effect": "RISK_INCREASE",
        "decision_order_id": ORDER_ID,
        "decision_order_product_id": 1101,
        "decision_order_origin_command_id": COMMAND_ID,
        "decision_order_requested_contracts": 2,
        "decision_order_requested_side": "buy",
        "decision_order_requested_reduce_only": False,
        "current_order_id": ORDER_ID,
        "current_order_product_id": 1101,
        "current_order_origin_command_id": COMMAND_ID,
        "current_order_requested_contracts": 2,
        "current_order_requested_side": "buy",
        "current_order_requested_order_type": "market",
        "current_order_requested_limit_price": None,
        "current_order_requested_stop_price": None,
        "current_order_requested_reduce_only": False,
    }
    aliases = {
        "command_evidence_schema_version": (
            "decision_command_evidence_schema_version",
            "current_command_evidence_schema_version",
        ),
        "command_evidence_payload": (
            "decision_command_evidence_payload",
            "current_command_evidence_payload",
        ),
        "command_evidence_sha256": (
            "decision_command_evidence_sha256",
            "current_command_evidence_sha256",
        ),
        "order_origin_command_id": (
            "decision_order_origin_command_id",
            "current_order_origin_command_id",
        ),
        "order_product_id": (
            "decision_order_product_id",
            "current_order_product_id",
        ),
        "order_requested_contracts": (
            "decision_order_requested_contracts",
            "current_order_requested_contracts",
        ),
        "order_requested_side": (
            "decision_order_requested_side",
            "current_order_requested_side",
        ),
        "order_requested_order_type": ("current_order_requested_order_type",),
        "order_requested_limit_price": ("current_order_requested_limit_price",),
        "order_requested_stop_price": ("current_order_requested_stop_price",),
        "order_requested_reduce_only": (
            "decision_order_requested_reduce_only",
            "current_order_requested_reduce_only",
        ),
    }
    for field, value in changes.items():
        for target in aliases.get(field, (field,)):
            values[target] = value
    return values


def release_values(**changes: object) -> dict[str, object]:
    values = reservation_key_values() | {
        "reservation_id": RESERVATION_ID,
        "reservation_key": RESERVATION_KEY,
        "reservation_created_at": CREATED_AT,
        "approved_contracts": 2,
        "approved_side": "buy",
        "reference_asset_id": 1,
        "reference_asset_symbol": "USDT",
        "collateral_asset_id": 2,
        "collateral_asset_symbol": "USDT",
        "planned_incremental_gross_exposure": Decimal("100"),
        "planned_worst_case_loss": Decimal("10"),
        "planned_collateral": Decimal("10"),
        "planned_open_order_slots": 1,
        "planned_pending_rate_units": 1,
        "remaining_contracts": 2,
        "remaining_incremental_gross_exposure": Decimal("100"),
        "remaining_worst_case_loss": Decimal("10"),
        "remaining_collateral": Decimal("10"),
        "remaining_open_order_slots": 1,
        "pending_rate_units": 1,
        "rate_policy_version": 1,
        "rate_policy_sha256": RATE_SHA,
        "rate_window_start_at": RATE_WINDOW_START_AT,
        "rate_window_end_at": RATE_WINDOW_END_AT,
        "observed_terminal_command_status": "FAILED",
        "observed_command_version": 4,
        "observed_order_version": 2,
        "proof_observed_at": PROOF_OBSERVED_AT,
    }
    values.update(changes)
    return values


def copied(value: object) -> object:
    """Return caller-owned input copies for mutation tests."""
    return deepcopy(value)
