"""Literal, broker-free Task 2.1d9a evidence inputs."""

from collections import OrderedDict
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

DECISION_ID = UUID("10000000-0000-4000-8000-000000000001")
ACCOUNT_ID = UUID("20000000-0000-4000-8000-000000000002")
RISK_STATE_ID = UUID("30000000-0000-4000-8000-000000000003")
COMMAND_ID = UUID("40000000-0000-4000-8000-000000000004")
ORDER_ID = UUID("50000000-0000-4000-8000-000000000005")
CLAIM_OWNER_ID = UUID("60000000-0000-4000-8000-000000000006")
RUN_ID = UUID("70000000-0000-4000-8000-000000000007")
PNL_ID = UUID("80000000-0000-4000-8000-000000000008")

OBSERVED_AT = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
EVALUATED_AT = OBSERVED_AT + timedelta(seconds=10)
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64

COMMAND_PAYLOAD = (
    '{"order_type":"market","product_id":1101,"reduce_only":false,'
    '"requested_contracts":2,"side":"buy"}'
)
COMMAND_ENVELOPE = (
    '{"operation":"PLACE_ORDER","payload":' + COMMAND_PAYLOAD + ',"schema_version":1}'
)
COMMAND_HASH = sha256(COMMAND_ENVELOPE.encode("ascii")).hexdigest()

CHECK_KINDS = (
    "PRODUCT_CATALOGUE",
    "MARKET_TRADABILITY",
    "MAX_POSITION",
    "TOTAL_EXPOSURE",
    "MAX_LEVERAGE",
    "MAX_LOSS_PER_TRADE",
    "MAX_DAILY_LOSS",
    "MAX_OPEN_ORDERS",
    "MAX_ORDER_RATE",
    "PRICE_COLLAR",
    "DUPLICATE_ORDER",
    "COLLATERAL_MARGIN",
    "KILL_HALT",
    "FRESHNESS_RECONCILIATION",
    "PROTECTION",
    "REDUCE_ONLY_NO_REVERSAL",
)

NUMERIC_PASS = {
    "MAX_POSITION": ("INTEGER", "CONTRACTS", None, None, 2, 10),
    "TOTAL_EXPOSURE": (
        "DECIMAL",
        "REFERENCE_ASSET",
        Decimal("100"),
        Decimal("1000"),
        None,
        None,
    ),
    "MAX_LEVERAGE": (
        "DECIMAL",
        "LEVERAGE",
        Decimal("2"),
        Decimal("5"),
        None,
        None,
    ),
    "MAX_LOSS_PER_TRADE": (
        "DECIMAL",
        "REFERENCE_ASSET",
        Decimal("10"),
        Decimal("50"),
        None,
        None,
    ),
    "MAX_DAILY_LOSS": (
        "DECIMAL",
        "REFERENCE_ASSET",
        Decimal("20"),
        Decimal("100"),
        None,
        None,
    ),
    "MAX_OPEN_ORDERS": ("INTEGER", "ORDERS", None, None, 1, 10),
    "MAX_ORDER_RATE": ("INTEGER", "REQUESTS", None, None, 1, 20),
    "PRICE_COLLAR": (
        "DECIMAL",
        "BASIS_POINTS",
        Decimal("50"),
        Decimal("100"),
        None,
        None,
    ),
    "COLLATERAL_MARGIN": (
        "DECIMAL",
        "COLLATERAL_ASSET",
        Decimal("10"),
        Decimal("100"),
        None,
        None,
    ),
    "FRESHNESS_RECONCILIATION": ("INTEGER", "SECONDS", None, None, 5, 30),
    "PROTECTION": ("INTEGER", "CONTRACTS", None, None, 2, 2),
}


def checks() -> OrderedDict[str, dict[str, object]]:
    """Return a complete approved risk-increase check map in caller order."""
    result: OrderedDict[str, dict[str, object]] = OrderedDict()
    for index, kind in enumerate(CHECK_KINDS, start=1):
        if kind == "REDUCE_ONLY_NO_REVERSAL":
            result[kind] = {
                "check_kind": kind,
                "outcome": "NOT_APPLICABLE",
                "reason_code": "NOT_APPLICABLE",
                "metric_kind": "NONE",
                "observed_decimal": None,
                "limit_decimal": None,
                "observed_integer": None,
                "limit_integer": None,
                "unit": "NONE",
                "evidence_observed_at": None,
                "input_set_sha256": f"{index:064x}",
            }
            continue
        metric = NUMERIC_PASS.get(kind)
        if metric is None:
            metric = ("NONE", "NONE", None, None, None, None)
        result[kind] = {
            "check_kind": kind,
            "outcome": "PASS",
            "reason_code": "SATISFIED",
            "metric_kind": metric[0],
            "unit": metric[1],
            "observed_decimal": metric[2],
            "limit_decimal": metric[3],
            "observed_integer": metric[4],
            "limit_integer": metric[5],
            "evidence_observed_at": OBSERVED_AT,
            "input_set_sha256": f"{index:064x}",
        }
    return result


def plan(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "approved_contracts": 2,
        "approved_side": "buy",
        "reference_asset_id": 1,
        "reference_asset_symbol": "USDT",
        "collateral_asset_id": 2,
        "collateral_asset_symbol": "USDT",
        "reserved_incremental_gross_exposure": Decimal("100"),
        "reserved_worst_case_loss": Decimal("10"),
        "reserved_collateral": Decimal("10"),
        "reserved_open_order_slots": 1,
        "reserved_pending_rate_units": 1,
        "rate_policy_version": 1,
        "rate_policy_sha256": HASH_D,
        "rate_window_start_at": OBSERVED_AT,
        "rate_window_end_at": EVALUATED_AT + timedelta(seconds=50),
    }
    values.update(changes)
    return values


def context(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "decision_id": DECISION_ID,
        "account_id": ACCOUNT_ID,
        "region": "india",
        "environment": "testnet",
        "mode": "PAPER",
        "decision_key": "decision-1",
        "risk_state_id": RISK_STATE_ID,
        "command_id": COMMAND_ID,
        "command_operation_kind": "PLACE_ORDER",
        "command_evidence_schema_version": 1,
        "command_evidence_payload": COMMAND_PAYLOAD,
        "command_evidence_sha256": COMMAND_HASH,
        "command_requested_contracts": 2,
        "order_id": ORDER_ID,
        "product_id": 1101,
        "order_origin_command_id": COMMAND_ID,
        "order_requested_contracts": 2,
        "order_requested_side": "buy",
        "order_requested_reduce_only": False,
        "outcome": "APPROVED",
        "risk_effect": "RISK_INCREASE",
        "policy_version": 1,
        "policy_sha256": HASH_A,
        "evaluation_reference_asset_id": 1,
        "evaluation_reference_asset_symbol": "USDT",
        "evaluation_collateral_asset_id": 2,
        "evaluation_collateral_asset_symbol": "USDT",
        "observed_risk_state_version": 2,
        "observed_halt_set_state": "INITIALIZED",
        "observed_halt_revision": 0,
        "observed_reconciliation_state": "READY_CLAIM",
        "observed_accounting_state": "COMPLETE_CLAIM",
        "observed_command_version": 3,
        "observed_claim_owner_id": CLAIM_OWNER_ID,
        "observed_claim_generation": 1,
        "observed_claim_expires_at": EVALUATED_AT + timedelta(seconds=10),
        "observed_order_version": 2,
        "observed_order_transition_sequence": 1,
        "observed_command_leader_ref": None,
        "observed_command_leader_generation": None,
        "observed_command_recovery_ref": None,
        "observed_command_recovery_generation": None,
        "leadership_lease_id": None,
        "recovery_incarnation_id": None,
        "observed_leadership_owner_id": None,
        "observed_leadership_generation": None,
        "observed_leadership_expires_at": None,
        "observed_leadership_version": None,
        "reconciliation_run_id": RUN_ID,
        "reconciliation_run_version": 2,
        "reconciliation_requirements_version": 1,
        "reconciliation_requirements_sha256": HASH_B,
        "reconciliation_observed_at": OBSERVED_AT,
        "reconciliation_completeness": "COMPLETE",
        "pnl_snapshot_id": PNL_ID,
        "pnl_completeness": "COMPLETE",
        "pnl_calculation_version": 2,
        "pnl_reference_asset_id": 1,
        "pnl_reference_asset_symbol": "USDT",
        "pnl_evidence_sha256": HASH_C,
        "pnl_input_set_sha256": HASH_B,
        "evaluated_at": EVALUATED_AT,
    }
    values.update(changes)
    return values


def owned(value: object) -> object:
    return deepcopy(value)
