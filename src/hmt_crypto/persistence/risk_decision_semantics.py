"""Cross-field and literal comparator semantics for v1 risk decisions."""

import json
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from hmt_crypto.persistence.execution_evidence import validate_stored_evidence
from hmt_crypto.persistence.risk_decision_contracts import (
    _NUMERIC_SHAPES,
    _REDUCE_NA,
    CHECK_INPUT_FIELDS,
    CHECK_KINDS,
    CONTEXT_FIELDS,
    FAIL_REASONS,
    RESERVATION_PLAN_INPUT_FIELDS,
    ReservationPlanV1,
    RiskCheckV1,
    _enum,
    _fail,
    _hash,
    _integer,
    _text,
    _time,
    _uuid,
)


def _context(values: object) -> dict[str, object]:
    if type(values) is not dict or set(values) != CONTEXT_FIELDS:
        _fail()
    result = cast(dict[str, object], values).copy()
    for field in (
        "decision_id",
        "account_id",
        "risk_state_id",
        "command_id",
        "order_id",
        "order_origin_command_id",
        "observed_claim_owner_id",
    ):
        result[field] = _uuid(result[field])
    for field in (
        "leadership_lease_id",
        "recovery_incarnation_id",
        "observed_leadership_owner_id",
        "observed_command_leader_ref",
        "observed_command_recovery_ref",
        "reconciliation_run_id",
        "pnl_snapshot_id",
    ):
        result[field] = _uuid(result[field], optional=True)
    for field in (
        "product_id",
        "order_requested_contracts",
        "policy_version",
        "observed_risk_state_version",
        "observed_command_version",
        "observed_claim_generation",
        "observed_order_version",
    ):
        result[field] = _integer(result[field])
    for field in (
        "command_requested_contracts",
        "observed_command_leader_generation",
        "observed_command_recovery_generation",
        "observed_leadership_generation",
        "observed_leadership_version",
        "reconciliation_run_version",
        "reconciliation_requirements_version",
        "pnl_calculation_version",
        "pnl_reference_asset_id",
        "evaluation_reference_asset_id",
        "evaluation_collateral_asset_id",
    ):
        result[field] = _integer(result[field], optional=True)
    for field in ("observed_halt_revision", "observed_order_transition_sequence"):
        result[field] = _integer(result[field], positive=False)
    for field in ("evaluated_at", "observed_claim_expires_at"):
        result[field] = _time(result[field])
    for field in (
        "observed_leadership_expires_at",
        "reconciliation_observed_at",
    ):
        result[field] = _time(result[field], optional=True)
    result["decision_key"] = _text(result["decision_key"])
    result["region"] = _enum(result["region"], frozenset({"india", "global"}))
    result["environment"] = _enum(result["environment"], frozenset({"testnet", "prod"}))
    result["mode"] = _enum(result["mode"], frozenset({"BACKTEST", "PAPER", "LIVE"}))
    result["command_operation_kind"] = _enum(
        result["command_operation_kind"], frozenset({"PLACE_ORDER", "MODIFY_ORDER"})
    )
    if result["command_evidence_schema_version"] != 1:
        _fail()
    result["command_evidence_payload"] = _text(result["command_evidence_payload"], maximum=2048)
    result["command_evidence_sha256"] = _hash(result["command_evidence_sha256"])
    result["order_requested_side"] = _enum(
        result["order_requested_side"], frozenset({"buy", "sell"})
    )
    if type(result["order_requested_reduce_only"]) is not bool:
        _fail()
    result["outcome"] = _enum(result["outcome"], frozenset({"APPROVED", "DENIED"}))
    result["risk_effect"] = _enum(
        result["risk_effect"], frozenset({"RISK_INCREASE", "REDUCE_ONLY"})
    )
    result["policy_sha256"] = _hash(result["policy_sha256"])
    result["observed_halt_set_state"] = _enum(
        result["observed_halt_set_state"], frozenset({"UNINITIALIZED", "INITIALIZED"})
    )
    result["observed_reconciliation_state"] = _enum(
        result["observed_reconciliation_state"],
        frozenset({"UNKNOWN", "BLOCKED", "READY_CLAIM"}),
    )
    result["observed_accounting_state"] = _enum(
        result["observed_accounting_state"],
        frozenset({"UNKNOWN", "PARTIAL", "COMPLETE_CLAIM"}),
    )
    for field in (
        "evaluation_reference_asset_symbol",
        "evaluation_collateral_asset_symbol",
        "pnl_reference_asset_symbol",
    ):
        if result[field] is not None:
            result[field] = _text(result[field])
    for field in (
        "reconciliation_requirements_sha256",
        "pnl_evidence_sha256",
        "pnl_input_set_sha256",
    ):
        if result[field] is not None:
            result[field] = _hash(result[field])
    if result["reconciliation_completeness"] is not None:
        result["reconciliation_completeness"] = _enum(
            result["reconciliation_completeness"],
            frozenset({"UNKNOWN", "PARTIAL", "COMPLETE"}),
        )
    if result["pnl_completeness"] is not None:
        result["pnl_completeness"] = _enum(
            result["pnl_completeness"], frozenset({"PARTIAL", "COMPLETE"})
        )
    _validate_context_shapes(result)
    _validate_command_intent(result)
    return result


def _all_or_none(values: dict[str, object], fields: tuple[str, ...]) -> bool:
    return all(values[field] is None for field in fields) or all(
        values[field] is not None for field in fields
    )


def _validate_context_shapes(values: dict[str, object]) -> None:
    evaluated = values["evaluated_at"]
    assert isinstance(evaluated, datetime)
    claim_expiry = values["observed_claim_expires_at"]
    if not isinstance(claim_expiry, datetime) or evaluated >= claim_expiry:
        _fail()
    leadership = (
        "leadership_lease_id",
        "recovery_incarnation_id",
        "observed_leadership_owner_id",
        "observed_leadership_generation",
        "observed_leadership_expires_at",
        "observed_leadership_version",
    )
    fences = (
        "observed_command_leader_ref",
        "observed_command_leader_generation",
        "observed_command_recovery_ref",
        "observed_command_recovery_generation",
    )
    if values["mode"] == "LIVE":
        if not all(values[field] is not None for field in (*leadership, *fences)):
            _fail()
        leadership_expiry = values["observed_leadership_expires_at"]
        if not isinstance(leadership_expiry, datetime) or (
            values["observed_leadership_owner_id"] != values["observed_claim_owner_id"]
            or values["observed_command_leader_ref"] != values["leadership_lease_id"]
            or values["observed_command_recovery_ref"] != values["recovery_incarnation_id"]
            or values["observed_command_leader_generation"]
            != values["observed_leadership_generation"]
            or values["observed_command_recovery_generation"]
            != values["observed_leadership_generation"]
            or evaluated >= leadership_expiry
        ):
            _fail()
    elif any(values[field] is not None for field in (*leadership, *fences)):
        _fail()

    reconciliation = (
        "reconciliation_run_id",
        "reconciliation_run_version",
        "reconciliation_requirements_version",
        "reconciliation_requirements_sha256",
        "reconciliation_observed_at",
        "reconciliation_completeness",
    )
    state = values["observed_reconciliation_state"]
    if state == "UNKNOWN":
        if any(values[field] is not None for field in reconciliation):
            _fail()
    elif not all(values[field] is not None for field in reconciliation):
        _fail()
    else:
        reconciliation_time = values["reconciliation_observed_at"]
        if (
            not isinstance(reconciliation_time, datetime)
            or reconciliation_time > evaluated
            or (state == "READY_CLAIM" and values["reconciliation_completeness"] != "COMPLETE")
        ):
            _fail()

    pnl = (
        "pnl_snapshot_id",
        "pnl_completeness",
        "pnl_calculation_version",
        "pnl_reference_asset_id",
        "pnl_reference_asset_symbol",
        "pnl_evidence_sha256",
        "pnl_input_set_sha256",
    )
    accounting = values["observed_accounting_state"]
    if accounting == "UNKNOWN":
        if any(values[field] is not None for field in pnl):
            _fail()
    elif not all(values[field] is not None for field in pnl):
        _fail()
    elif (accounting == "PARTIAL" and values["pnl_completeness"] != "PARTIAL") or (
        accounting == "COMPLETE_CLAIM" and values["pnl_completeness"] != "COMPLETE"
    ):
        _fail()
    reference = ("evaluation_reference_asset_id", "evaluation_reference_asset_symbol")
    collateral = ("evaluation_collateral_asset_id", "evaluation_collateral_asset_symbol")
    if not _all_or_none(values, reference) or not _all_or_none(values, collateral):
        _fail()
    if values["pnl_snapshot_id"] is not None and (
        values["pnl_reference_asset_id"] != values["evaluation_reference_asset_id"]
        or values["pnl_reference_asset_symbol"] != values["evaluation_reference_asset_symbol"]
    ):
        _fail()


def _validate_command_intent(values: dict[str, object]) -> None:
    operation = cast(str, values["command_operation_kind"])
    validate_stored_evidence(
        "intent",
        operation,
        cast(int, values["command_evidence_schema_version"]),
        cast(str, values["command_evidence_payload"]),
        cast(str, values["command_evidence_sha256"]),
    )
    intent = json.loads(str(values["command_evidence_payload"]))
    if intent.get("product_id") != values["product_id"]:
        _fail()
    if values["order_requested_reduce_only"] is True:
        if values["risk_effect"] != "REDUCE_ONLY":
            _fail()
    elif values["risk_effect"] != "RISK_INCREASE":
        _fail()
    if operation == "PLACE_ORDER":
        if (
            values["command_id"] != values["order_origin_command_id"]
            or intent.get("requested_contracts") != values["order_requested_contracts"]
            or intent.get("requested_contracts") != values["command_requested_contracts"]
            or intent.get("side") != values["order_requested_side"]
            or intent.get("reduce_only") != values["order_requested_reduce_only"]
        ):
            _fail()
    else:
        try:
            target = UUID(intent["target_order_id"])
        except Exception:
            _fail()
        if target != values["order_id"]:
            _fail()
        requested = intent.get("requested_contracts")
        if requested != values["command_requested_contracts"]:
            _fail()
        if requested is not None and requested != values["order_requested_contracts"]:
            _fail()
        if requested is None and values["outcome"] == "APPROVED":
            _fail()


def _owned_checks(values: object, context: dict[str, object]) -> tuple[RiskCheckV1, ...]:
    if not isinstance(values, Mapping) or set(values) != set(CHECK_KINDS):
        _fail()
    supplied = cast(Mapping[str, object], values)
    checks: list[RiskCheckV1] = []
    for kind in CHECK_KINDS:
        item = supplied[kind]
        if type(item) is RiskCheckV1:
            check = item
        elif type(item) is dict and set(item) == CHECK_INPUT_FIELDS:
            check = RiskCheckV1.model_validate(dict(item))
        else:
            _fail()
        if check.check_kind != kind:
            _fail()
        _validate_check(check, context)
        checks.append(check)
    _validate_truth_table(tuple(checks), context)
    return tuple(checks)


def _validate_check(check: RiskCheckV1, context: dict[str, object]) -> None:
    outcome = check.outcome
    if check.evidence_observed_at is not None and check.evidence_observed_at > cast(
        datetime, context["evaluated_at"]
    ):
        _fail()
    if (
        (outcome == "PASS" and check.reason_code != "SATISFIED")
        or (outcome == "NOT_APPLICABLE" and check.reason_code != "NOT_APPLICABLE")
        or (
            outcome == "UNKNOWN"
            and check.reason_code
            not in {"MISSING_EVIDENCE", "STALE_EVIDENCE", "AMBIGUOUS_EVIDENCE"}
        )
        or (outcome == "FAIL" and check.reason_code not in FAIL_REASONS[check.check_kind])
    ):
        _fail()
    numeric = _NUMERIC_SHAPES.get(check.check_kind)
    if outcome == "NOT_APPLICABLE" or (
        outcome == "UNKNOWN" and check.reason_code in {"MISSING_EVIDENCE", "AMBIGUOUS_EVIDENCE"}
    ):
        expected = ("NONE", "NONE")
        if outcome == "NOT_APPLICABLE" or check.reason_code == "MISSING_EVIDENCE":
            if check.evidence_observed_at is not None:
                _fail()
        elif check.evidence_observed_at is None:
            _fail()
    else:
        expected = numeric or ("NONE", "NONE")
        if check.evidence_observed_at is None:
            _fail()
    if (check.metric_kind, check.unit) != expected:
        _fail()
    if check.metric_kind == "DECIMAL":
        if (
            check.observed_decimal is None
            or check.limit_decimal is None
            or check.observed_integer is not None
            or check.limit_integer is not None
        ):
            _fail()
        observed_decimal = check.observed_decimal
        limit_decimal = check.limit_decimal
        assert observed_decimal is not None and limit_decimal is not None
        observed: Decimal | int = observed_decimal
        limit: Decimal | int = limit_decimal
    elif check.metric_kind == "INTEGER":
        if (
            check.observed_integer is None
            or check.limit_integer is None
            or check.observed_decimal is not None
            or check.limit_decimal is not None
        ):
            _fail()
        observed_integer = check.observed_integer
        limit_integer = check.limit_integer
        assert observed_integer is not None and limit_integer is not None
        observed = observed_integer
        limit = limit_integer
    else:
        if any(
            value is not None
            for value in (
                check.observed_decimal,
                check.limit_decimal,
                check.observed_integer,
                check.limit_integer,
            )
        ):
            _fail()
        return
    if check.check_kind != "REDUCE_ONLY_NO_REVERSAL" and (observed < 0 or limit < 0):
        _fail()
    if check.check_kind == "MAX_ORDER_RATE" and observed < 1:
        _fail()
    if outcome not in {"PASS", "FAIL"}:
        return
    if check.check_kind == "REDUCE_ONLY_NO_REVERSAL":
        _validate_reduction(check, context)
        return
    if check.check_kind == "PROTECTION":
        passes = observed >= limit
    else:
        passes = observed <= limit
    if outcome == "PASS" and not passes:
        _fail()
    if outcome == "FAIL" and check.reason_code != "POLICY_BLOCKED":
        reconciliation_not_ready = check.check_kind == "FRESHNESS_RECONCILIATION" and (
            context["observed_reconciliation_state"] != "READY_CLAIM"
            or context["reconciliation_completeness"] != "COMPLETE"
        )
        if passes and not reconciliation_not_ready:
            _fail()


def _reduction_passes(check: RiskCheckV1, context: dict[str, object]) -> bool:
    pre = check.limit_integer
    post = check.observed_integer
    contracts = cast(int | None, context["command_requested_contracts"])
    side = cast(str, context["order_requested_side"])
    if pre is None or post is None or contracts is None or pre == 0:
        return False
    signed = contracts if side == "buy" else -contracts
    return post == pre + signed and (post == 0 or (post > 0) == (pre > 0)) and abs(post) < abs(pre)


def _validate_reduction(check: RiskCheckV1, context: dict[str, object]) -> None:
    if context["command_requested_contracts"] is None:
        _fail()
    passes = _reduction_passes(check, context)
    if check.outcome == "PASS" and not passes:
        _fail()
    if check.outcome == "FAIL" and check.reason_code != "POLICY_BLOCKED":
        pre = check.limit_integer
        post = check.observed_integer
        assert pre is not None and post is not None
        reversal = pre != 0 and post != 0 and (pre > 0) != (post > 0)
        if (check.reason_code == "POSITION_REVERSAL") != reversal or passes:
            _fail()


def _validate_truth_table(checks: tuple[RiskCheckV1, ...], context: dict[str, object]) -> None:
    outcomes = {check.check_kind: check.outcome for check in checks}
    allowed_na = (
        frozenset({"REDUCE_ONLY_NO_REVERSAL"})
        if context["risk_effect"] == "RISK_INCREASE"
        else _REDUCE_NA
    )
    if any(
        outcome == "NOT_APPLICABLE" and kind not in allowed_na for kind, outcome in outcomes.items()
    ):
        _fail()
    if context["outcome"] == "APPROVED":
        expected_pass = set(CHECK_KINDS) - set(allowed_na)
        if any(outcomes[kind] != "PASS" for kind in expected_pass):
            _fail()
        if any(outcomes[kind] not in {"PASS", "NOT_APPLICABLE"} for kind in allowed_na):
            _fail()
        if (
            context["risk_effect"] == "RISK_INCREASE"
            and outcomes["REDUCE_ONLY_NO_REVERSAL"] != "NOT_APPLICABLE"
        ):
            _fail()
    elif not any(outcome in {"FAIL", "UNKNOWN"} for outcome in outcomes.values()):
        _fail()


def _owned_plan(value: object, context: dict[str, object]) -> ReservationPlanV1 | None:
    if context["outcome"] == "DENIED":
        if value is not None:
            _fail()
        return None
    if type(value) is ReservationPlanV1:
        plan = value
    elif type(value) is dict and set(value) == RESERVATION_PLAN_INPUT_FIELDS:
        plan = ReservationPlanV1.model_validate(dict(value))
    else:
        _fail()
    evaluated = context["evaluated_at"]
    assert isinstance(evaluated, datetime)
    expected_slots = 1 if context["command_operation_kind"] == "PLACE_ORDER" else 0
    if (
        plan.approved_contracts != context["command_requested_contracts"]
        or plan.approved_side != context["order_requested_side"]
        or plan.reference_asset_id != context["evaluation_reference_asset_id"]
        or plan.reference_asset_symbol != context["evaluation_reference_asset_symbol"]
        or plan.collateral_asset_id != context["evaluation_collateral_asset_id"]
        or plan.collateral_asset_symbol != context["evaluation_collateral_asset_symbol"]
        or plan.reserved_open_order_slots != expected_slots
        or plan.reserved_pending_rate_units != 1
        or not plan.rate_window_start_at <= evaluated < plan.rate_window_end_at
    ):
        _fail()
    amounts = (
        plan.reserved_incremental_gross_exposure,
        plan.reserved_worst_case_loss,
        plan.reserved_collateral,
    )
    if context["risk_effect"] == "RISK_INCREASE":
        if amounts[0] <= 0 or amounts[1] <= 0:
            _fail()
    elif any(amount != 0 for amount in amounts):
        _fail()
    return plan
