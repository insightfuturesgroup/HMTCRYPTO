"""Shared exact immutable agreement between reservations and their D9a decisions."""

from hmt_crypto.persistence.errors import PersistenceValueError

RESERVATION_DECISION_COLUMNS = {
    "decision_id": "id",
    "account_id": "account_id",
    "region": "region",
    "environment": "environment",
    "mode": "mode",
    "risk_state_id": "risk_state_id",
    "command_id": "command_id",
    "command_operation_kind": "command_operation_kind",
    "order_id": "order_id",
    "product_id": "product_id",
    "decision_outcome": "outcome",
    "risk_effect": "risk_effect",
    "reservation_plan_sha256": "reservation_plan_sha256",
    "decision_evaluated_at": "evaluated_at",
    "decision_recorded_at": "recorded_at",
    "approved_contracts": "approved_contracts",
    "approved_side": "approved_side",
    "reference_asset_id": "reference_asset_id",
    "reference_asset_symbol": "reference_asset_symbol",
    "collateral_asset_id": "collateral_asset_id",
    "collateral_asset_symbol": "collateral_asset_symbol",
    "planned_incremental_gross_exposure": "reserved_incremental_gross_exposure",
    "planned_worst_case_loss": "reserved_worst_case_loss",
    "planned_collateral": "reserved_collateral",
    "planned_open_order_slots": "reserved_open_order_slots",
    "planned_pending_rate_units": "reserved_pending_rate_units",
    "rate_policy_version": "rate_policy_version",
    "rate_policy_sha256": "rate_policy_sha256",
    "rate_window_start_at": "rate_window_start_at",
    "rate_window_end_at": "rate_window_end_at",
}


def validate_reservation_decision_agreement(
    values: dict[str, object], decision: dict[str, object]
) -> None:
    """Require every immutable D9a copy, including the approved plan, to agree."""
    for child, parent in RESERVATION_DECISION_COLUMNS.items():
        left, right = values[child], decision[parent]
        if not (type(left) is type(right) and left == right):
            raise PersistenceValueError()
