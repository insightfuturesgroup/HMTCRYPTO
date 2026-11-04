"""Task 2.1d9a canonical risk decisions are strict historical evidence only."""

import importlib
import json
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError
from risk_decision_test_support import (
    CHECK_KINDS,
    DECISION_ID,
    EVALUATED_AT,
    HASH_A,
    OBSERVED_AT,
    checks,
    context,
    plan,
)
from sqlalchemy.dialects import sqlite

from hmt_crypto.persistence.errors import PersistenceValueError


def evidence():
    return importlib.import_module("hmt_crypto.persistence.risk_decision_evidence")


_DEFAULT_PLAN = object()


def encode(*, values=None, results=None, reservation=_DEFAULT_PLAN):
    return evidence().encode_risk_decision_evidence(
        1,
        context() if values is None else values,
        checks() if results is None else results,
        plan() if reservation is _DEFAULT_PLAN else reservation,
    )


def denied(target="MAX_POSITION", *, reason="POLICY_BLOCKED"):
    results = checks()
    item = dict(results[target])
    item["outcome"] = "FAIL"
    item["reason_code"] = reason
    if reason == "LIMIT_EXCEEDED":
        if item["metric_kind"] == "INTEGER":
            item["observed_integer"], item["limit_integer"] = 11, 10
        else:
            item["observed_decimal"], item["limit_decimal"] = Decimal("11"), Decimal("10")
    results[target] = item
    return context(outcome="DENIED"), results


def reduce_only_case():
    payload = (
        '{"order_type":"market","product_id":1101,"reduce_only":true,'
        '"requested_contracts":2,"side":"buy"}'
    )
    envelope = '{"operation":"PLACE_ORDER","payload":' + payload + ',"schema_version":1}'
    values = context(
        command_evidence_payload=payload,
        command_evidence_sha256=sha256(envelope.encode("ascii")).hexdigest(),
        order_requested_reduce_only=True,
        risk_effect="REDUCE_ONLY",
    )
    results = checks()
    for kind in (
        "MAX_POSITION",
        "TOTAL_EXPOSURE",
        "MAX_LEVERAGE",
        "MAX_LOSS_PER_TRADE",
        "MAX_DAILY_LOSS",
        "PROTECTION",
    ):
        results[kind] = {
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
            "input_set_sha256": results[kind]["input_set_sha256"],
        }
    collateral = dict(results["COLLATERAL_MARGIN"])
    collateral["observed_decimal"] = Decimal("0")
    results["COLLATERAL_MARGIN"] = collateral
    results["REDUCE_ONLY_NO_REVERSAL"] = {
        "check_kind": "REDUCE_ONLY_NO_REVERSAL",
        "outcome": "PASS",
        "reason_code": "SATISFIED",
        "metric_kind": "INTEGER",
        "observed_decimal": None,
        "limit_decimal": None,
        "observed_integer": -3,
        "limit_integer": -5,
        "unit": "CONTRACTS",
        "evidence_observed_at": OBSERVED_AT,
        "input_set_sha256": results["REDUCE_ONLY_NO_REVERSAL"]["input_set_sha256"],
    }
    reservation = plan(
        reserved_incremental_gross_exposure=Decimal("0"),
        reserved_worst_case_loss=Decimal("0"),
        reserved_collateral=Decimal("0"),
    )
    return values, results, reservation


def test_exact_v1_check_order_and_closed_fail_reason_map():
    module = evidence()
    assert module.CHECK_KINDS == CHECK_KINDS
    assert module.FAIL_REASONS == {
        "PRODUCT_CATALOGUE": frozenset(
            {"UNSUPPORTED_PRODUCT", "PRODUCT_DISABLED", "POLICY_BLOCKED"}
        ),
        "MARKET_TRADABILITY": frozenset({"MARKET_NOT_TRADABLE", "POLICY_BLOCKED"}),
        "MAX_POSITION": frozenset({"LIMIT_EXCEEDED", "POLICY_BLOCKED"}),
        "TOTAL_EXPOSURE": frozenset({"LIMIT_EXCEEDED", "POLICY_BLOCKED"}),
        "MAX_LEVERAGE": frozenset({"LIMIT_EXCEEDED", "POLICY_BLOCKED"}),
        "MAX_LOSS_PER_TRADE": frozenset({"LIMIT_EXCEEDED", "POLICY_BLOCKED"}),
        "MAX_DAILY_LOSS": frozenset({"LIMIT_EXCEEDED", "POLICY_BLOCKED"}),
        "MAX_OPEN_ORDERS": frozenset({"LIMIT_EXCEEDED", "POLICY_BLOCKED"}),
        "MAX_ORDER_RATE": frozenset({"LIMIT_EXCEEDED", "POLICY_BLOCKED"}),
        "PRICE_COLLAR": frozenset({"PRICE_OUTSIDE_COLLAR", "POLICY_BLOCKED"}),
        "DUPLICATE_ORDER": frozenset({"DUPLICATE_ORDER", "POLICY_BLOCKED"}),
        "COLLATERAL_MARGIN": frozenset({"INSUFFICIENT_COLLATERAL", "POLICY_BLOCKED"}),
        "KILL_HALT": frozenset({"HALT_ACTIVE", "POLICY_BLOCKED"}),
        "FRESHNESS_RECONCILIATION": frozenset({"RECONCILIATION_NOT_READY", "POLICY_BLOCKED"}),
        "PROTECTION": frozenset({"PROTECTION_INADEQUATE", "POLICY_BLOCKED"}),
        "REDUCE_ONLY_NO_REVERSAL": frozenset(
            {"POSITION_REVERSAL", "LIMIT_EXCEEDED", "POLICY_BLOCKED"}
        ),
    }


def test_payload_has_exact_top_level_and_nested_property_sets():
    encoded = encode()
    raw = json.loads(encoded.payload)
    assert set(raw) == evidence().DECISION_PAYLOAD_FIELDS
    assert [item["check_kind"] for item in raw["check_results"]] == list(CHECK_KINDS)
    assert all(set(item) == evidence().CHECK_EVIDENCE_FIELDS for item in raw["check_results"])
    assert set(raw["reservation_plan"]) == evidence().RESERVATION_PLAN_STORED_FIELDS
    assert type(raw["command_evidence_payload"]) is str
    assert raw["command_evidence_payload"].startswith("{")
    assert encoded.check_set_sha256 == raw["check_set_sha256"]
    assert encoded.reservation_plan_sha256 == raw["reservation_plan"]["reservation_plan_sha256"]


def test_full_literal_v1_payload_and_hash_vector():
    fixture = json.loads((Path(__file__).parents[1] / "fixtures/risk_decision_v1.json").read_text())
    expected_payload = json.dumps(
        fixture["payload"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    encoded = encode()
    assert encoded.payload == expected_payload
    assert encoded.evidence_sha256 == fixture["expected_evidence_sha256"]
    assert encoded.check_set_sha256 == fixture["expected_check_set_sha256"]
    assert encoded.reservation_plan_sha256 == fixture["expected_reservation_plan_sha256"]


def test_caller_mapping_order_is_normalized_but_stored_reordering_is_rejected():
    supplied = checks()
    reversed_results = dict(reversed(tuple(supplied.items())))
    baseline = encode(results=supplied)
    assert encode(results=reversed_results) == baseline
    raw = json.loads(baseline.payload)
    raw["check_results"].reverse()
    corrupted = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    with pytest.raises(PersistenceValueError):
        evidence().decode_risk_decision_evidence(corrupted)


@pytest.mark.parametrize("kind", CHECK_KINDS)
def test_missing_or_extra_check_is_rejected(kind):
    missing = checks()
    missing.pop(kind)
    with pytest.raises(PersistenceValueError):
        encode(results=missing)
    extra = checks()
    extra["EXTRA"] = dict(next(iter(extra.values()))) | {"check_kind": "EXTRA"}
    with pytest.raises(PersistenceValueError):
        encode(results=extra)


@pytest.mark.parametrize("kind", CHECK_KINDS[:-1])
def test_risk_increase_approval_requires_first_fifteen_pass(kind):
    results = checks()
    results[kind] = {
        "check_kind": kind,
        "outcome": "UNKNOWN",
        "reason_code": "MISSING_EVIDENCE",
        "metric_kind": "NONE",
        "observed_decimal": None,
        "limit_decimal": None,
        "observed_integer": None,
        "limit_integer": None,
        "unit": "NONE",
        "evidence_observed_at": None,
        "input_set_sha256": results[kind]["input_set_sha256"],
    }
    with pytest.raises(PersistenceValueError):
        encode(results=results)


def test_approved_reduce_only_exact_na_allowlist_and_zero_reserves():
    values, results, reservation = reduce_only_case()
    assert encode(values=values, results=results, reservation=reservation)
    for kind in (
        "PRODUCT_CATALOGUE",
        "MARKET_TRADABILITY",
        "MAX_OPEN_ORDERS",
        "MAX_ORDER_RATE",
        "PRICE_COLLAR",
        "DUPLICATE_ORDER",
        "COLLATERAL_MARGIN",
        "KILL_HALT",
        "FRESHNESS_RECONCILIATION",
        "REDUCE_ONLY_NO_REVERSAL",
    ):
        changed = deepcopy(results)
        changed[kind] = {
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
            "input_set_sha256": results[kind]["input_set_sha256"],
        }
        with pytest.raises(PersistenceValueError):
            encode(values=values, results=changed, reservation=reservation)


def test_denied_requires_negative_result_and_all_null_plan():
    values, results = denied()
    encoded = encode(values=values, results=results, reservation=None)
    assert encoded.reservation_plan_sha256 is None
    assert set(json.loads(encoded.payload)["reservation_plan"].values()) == {None}
    with pytest.raises(PersistenceValueError):
        encode(values=context(outcome="DENIED"), reservation=None)
    with pytest.raises(PersistenceValueError):
        encode(values=values, results=results, reservation=plan())


@pytest.mark.parametrize(
    "kind,reason,observed,limit",
    [
        ("MAX_POSITION", "LIMIT_EXCEEDED", 11, 10),
        ("TOTAL_EXPOSURE", "LIMIT_EXCEEDED", Decimal("11"), Decimal("10")),
        ("MAX_LEVERAGE", "LIMIT_EXCEEDED", Decimal("6"), Decimal("5")),
        ("MAX_LOSS_PER_TRADE", "LIMIT_EXCEEDED", Decimal("51"), Decimal("50")),
        ("MAX_DAILY_LOSS", "LIMIT_EXCEEDED", Decimal("101"), Decimal("100")),
        ("MAX_OPEN_ORDERS", "LIMIT_EXCEEDED", 11, 10),
        ("MAX_ORDER_RATE", "LIMIT_EXCEEDED", 21, 20),
        ("PRICE_COLLAR", "PRICE_OUTSIDE_COLLAR", Decimal("101"), Decimal("100")),
        ("COLLATERAL_MARGIN", "INSUFFICIENT_COLLATERAL", Decimal("101"), Decimal("100")),
        ("FRESHNESS_RECONCILIATION", "RECONCILIATION_NOT_READY", 31, 30),
        ("PROTECTION", "PROTECTION_INADEQUATE", 1, 2),
    ],
)
def test_numeric_specific_fail_reasons_require_exact_failing_comparator(
    kind, reason, observed, limit
):
    values, results = denied(kind, reason=reason)
    item = dict(results[kind])
    if item["metric_kind"] == "DECIMAL":
        item["observed_decimal"], item["limit_decimal"] = observed, limit
    else:
        item["observed_integer"], item["limit_integer"] = observed, limit
    results[kind] = item
    assert encode(values=values, results=results, reservation=None)
    passing = dict(item)
    if item["metric_kind"] == "DECIMAL":
        passing["observed_decimal"] = limit
    elif kind == "PROTECTION":
        passing["observed_integer"] = limit
    else:
        passing["observed_integer"] = limit
    results[kind] = passing
    with pytest.raises(PersistenceValueError):
        encode(values=values, results=results, reservation=None)


def test_policy_blocked_can_deny_even_when_numeric_comparator_passes():
    values, results = denied("MAX_POSITION", reason="POLICY_BLOCKED")
    assert results["MAX_POSITION"]["observed_integer"] <= results["MAX_POSITION"]["limit_integer"]
    assert encode(values=values, results=results, reservation=None)


@pytest.mark.parametrize(
    "kind,reason",
    [
        ("PRODUCT_CATALOGUE", "UNSUPPORTED_PRODUCT"),
        ("PRODUCT_CATALOGUE", "PRODUCT_DISABLED"),
        ("MARKET_TRADABILITY", "MARKET_NOT_TRADABLE"),
        ("DUPLICATE_ORDER", "DUPLICATE_ORDER"),
        ("KILL_HALT", "HALT_ACTIVE"),
    ],
)
def test_categorical_specific_failure_reasons(kind, reason):
    values, results = denied(kind, reason=reason)
    assert encode(values=values, results=results, reservation=None)


@pytest.mark.parametrize("kind", CHECK_KINDS)
def test_wrong_specific_fail_reason_is_rejected_for_every_check(kind):
    values, results = denied(kind)
    results[kind] = dict(results[kind]) | {"reason_code": "UNSUPPORTED_PRODUCT"}
    if kind == "PRODUCT_CATALOGUE":
        results[kind]["reason_code"] = "HALT_ACTIVE"
    with pytest.raises(PersistenceValueError):
        encode(values=values, results=results, reservation=None)


@pytest.mark.parametrize(
    "reason,time,metric,valid",
    [
        ("MISSING_EVIDENCE", None, False, True),
        ("MISSING_EVIDENCE", OBSERVED_AT, False, False),
        ("AMBIGUOUS_EVIDENCE", OBSERVED_AT, False, True),
        ("AMBIGUOUS_EVIDENCE", None, False, False),
        ("STALE_EVIDENCE", OBSERVED_AT, True, True),
        ("STALE_EVIDENCE", None, True, False),
        ("STALE_EVIDENCE", OBSERVED_AT, False, False),
    ],
)
def test_unknown_numeric_shapes_are_literal(reason, time, metric, valid):
    results = checks()
    original = results["MAX_POSITION"]
    results["MAX_POSITION"] = {
        "check_kind": "MAX_POSITION",
        "outcome": "UNKNOWN",
        "reason_code": reason,
        "metric_kind": "INTEGER" if metric else "NONE",
        "observed_decimal": None,
        "limit_decimal": None,
        "observed_integer": 2 if metric else None,
        "limit_integer": 10 if metric else None,
        "unit": "CONTRACTS" if metric else "NONE",
        "evidence_observed_at": time,
        "input_set_sha256": original["input_set_sha256"],
    }

    def call():
        return encode(values=context(outcome="DENIED"), results=results, reservation=None)

    if valid:
        assert call()
    else:
        with pytest.raises(PersistenceValueError):
            call()


@pytest.mark.parametrize(
    "field,value",
    [
        ("observed_decimal", 1.5),
        ("observed_decimal", Decimal("NaN")),
        ("observed_decimal", Decimal("1e20")),
        ("observed_decimal", Decimal("1e-19")),
        ("observed_integer", True),
        ("observed_integer", 2**63),
        ("input_set_sha256", "A" * 64),
        ("input_set_sha256", "a" * 63),
    ],
)
def test_check_numeric_hash_boundaries_are_strict_and_secret_safe(field, value):
    values, results = denied("MAX_POSITION")
    target = "TOTAL_EXPOSURE" if "decimal" in field else "MAX_POSITION"
    item = dict(results[target])
    item[field] = value
    results[target] = item
    with pytest.raises(PersistenceValueError) as error:
        encode(values=values, results=results, reservation=None)
    assert "SECRET" not in str(error.value) + repr(error.value)


def test_evidence_times_cannot_follow_decision_time():
    results = checks()
    results["PRODUCT_CATALOGUE"] = dict(results["PRODUCT_CATALOGUE"])
    results["PRODUCT_CATALOGUE"]["evidence_observed_at"] = EVALUATED_AT + timedelta(microseconds=1)
    with pytest.raises(PersistenceValueError):
        encode(results=results)


@pytest.mark.parametrize(
    "missing_pair",
    [
        {"evaluation_reference_asset_id": None},
        {"evaluation_reference_asset_symbol": None},
        {"evaluation_collateral_asset_id": None},
        {"evaluation_collateral_asset_symbol": None},
    ],
)
def test_evaluation_asset_pairs_are_all_or_none_and_required_by_numeric_units(missing_pair):
    with pytest.raises(PersistenceValueError):
        encode(values=context(**missing_pair))


def test_denied_can_omit_asset_pair_only_when_no_evidence_or_parent_requires_it():
    results = checks()
    for kind in ("TOTAL_EXPOSURE", "MAX_LOSS_PER_TRADE", "MAX_DAILY_LOSS"):
        results[kind] = {
            "check_kind": kind,
            "outcome": "UNKNOWN",
            "reason_code": "MISSING_EVIDENCE",
            "metric_kind": "NONE",
            "observed_decimal": None,
            "limit_decimal": None,
            "observed_integer": None,
            "limit_integer": None,
            "unit": "NONE",
            "evidence_observed_at": None,
            "input_set_sha256": results[kind]["input_set_sha256"],
        }
    values = context(
        outcome="DENIED",
        evaluation_reference_asset_id=None,
        evaluation_reference_asset_symbol=None,
        observed_accounting_state="UNKNOWN",
        pnl_snapshot_id=None,
        pnl_completeness=None,
        pnl_calculation_version=None,
        pnl_reference_asset_id=None,
        pnl_reference_asset_symbol=None,
        pnl_evidence_sha256=None,
        pnl_input_set_sha256=None,
    )
    assert encode(values=values, results=results, reservation=None)
    with pytest.raises(PersistenceValueError):
        encode(values=values | {"pnl_snapshot_id": UUID(int=9)}, results=results, reservation=None)


def test_plan_and_pnl_assets_must_equal_evaluation_assets():
    with pytest.raises(PersistenceValueError):
        encode(reservation=plan(reference_asset_symbol="BTC"))
    with pytest.raises(PersistenceValueError):
        encode(values=context(pnl_reference_asset_symbol="BTC"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("approved_contracts", 0),
        ("approved_contracts", True),
        ("approved_side", "hold"),
        ("reference_asset_id", 0),
        ("reference_asset_symbol", ""),
        ("reference_asset_symbol", "x" * 129),
        ("reserved_collateral", Decimal("-0.000000000000000001")),
        ("reserved_collateral", 1.0),
        ("reserved_pending_rate_units", 0),
        ("rate_policy_sha256", "A" * 64),
        ("rate_window_end_at", OBSERVED_AT),
    ],
)
def test_plan_boundaries_are_strict(field, value):
    with pytest.raises(PersistenceValueError):
        encode(reservation=plan(**{field: value}))


def test_operation_shapes_and_effect_amounts_are_exact():
    with pytest.raises(PersistenceValueError):
        encode(values=context(command_requested_contracts=3))
    with pytest.raises(PersistenceValueError):
        encode(values=context(order_requested_side="sell"))
    with pytest.raises(PersistenceValueError):
        encode(values=context(order_requested_reduce_only=True))
    with pytest.raises(PersistenceValueError):
        encode(reservation=plan(reserved_open_order_slots=0))
    values, results, reservation = reduce_only_case()
    for amount in (
        "reserved_incremental_gross_exposure",
        "reserved_worst_case_loss",
        "reserved_collateral",
    ):
        with pytest.raises(PersistenceValueError):
            encode(
                values=values,
                results=results,
                reservation=reservation | {amount: Decimal("0.000000000000000001")},
            )


@pytest.mark.parametrize(
    "pre,post,side,contracts,valid",
    [
        (-5, -3, "buy", 2, True),
        (-5, 0, "buy", 5, True),
        (5, 3, "sell", 2, True),
        (5, 0, "sell", 5, True),
        (-5, 1, "buy", 6, False),
        (5, -1, "sell", 6, False),
        (-5, -5, "buy", 0, False),
        (0, 0, "buy", 1, False),
        (-5, -3, "sell", 2, False),
    ],
)
def test_reduce_only_no_reversal_arithmetic(pre, post, side, contracts, valid):
    values, results, reservation = reduce_only_case()
    values = values | {
        "order_requested_side": side,
        "command_requested_contracts": contracts,
        "order_requested_contracts": contracts,
        "command_evidence_payload": values["command_evidence_payload"]
        .replace('"requested_contracts":2', f'"requested_contracts":{contracts}')
        .replace('"side":"buy"', f'"side":"{side}"'),
    }
    envelope = (
        '{"operation":"PLACE_ORDER","payload":'
        + values["command_evidence_payload"]
        + ',"schema_version":1}'
    )
    values["command_evidence_sha256"] = sha256(envelope.encode("ascii")).hexdigest()
    results = deepcopy(results)
    results["REDUCE_ONLY_NO_REVERSAL"]["observed_integer"] = post
    results["REDUCE_ONLY_NO_REVERSAL"]["limit_integer"] = pre
    reservation = reservation | {"approved_side": side, "approved_contracts": contracts}

    def call():
        return encode(values=values, results=results, reservation=reservation)

    if valid:
        assert call()
    else:
        with pytest.raises(PersistenceValueError):
            call()


def test_price_only_modify_can_only_be_denied():
    modify_payload = (
        '{"limit_price":"11","product_id":1101,'
        '"target_order_id":"50000000-0000-4000-8000-000000000005"}'
    )
    envelope = '{"operation":"MODIFY_ORDER","payload":' + modify_payload + ',"schema_version":1}'
    values, results = denied("DUPLICATE_ORDER", reason="DUPLICATE_ORDER")
    values = values | {
        "command_id": UUID("90000000-0000-4000-8000-000000000009"),
        "command_operation_kind": "MODIFY_ORDER",
        "command_evidence_payload": modify_payload,
        "command_evidence_sha256": sha256(envelope.encode("ascii")).hexdigest(),
        "command_requested_contracts": None,
    }
    assert encode(values=values, results=results, reservation=None)
    with pytest.raises(PersistenceValueError):
        encode(values=values | {"outcome": "APPROVED"}, results=checks(), reservation=plan())
    wrong_target = values | {"order_id": UUID("50000000-0000-4000-8000-000000000099")}
    with pytest.raises(PersistenceValueError):
        encode(values=wrong_target, results=results, reservation=None)


def test_mode_leadership_and_command_fence_bundles_are_exact():
    with pytest.raises(PersistenceValueError):
        encode(values=context(observed_command_leader_ref=UUID(int=9)))
    live = context(
        mode="LIVE",
        leadership_lease_id=UUID(int=11),
        recovery_incarnation_id=UUID(int=12),
        observed_leadership_owner_id=context()["observed_claim_owner_id"],
        observed_leadership_generation=3,
        observed_leadership_expires_at=EVALUATED_AT + timedelta(seconds=20),
        observed_leadership_version=4,
        observed_command_leader_ref=UUID(int=11),
        observed_command_leader_generation=3,
        observed_command_recovery_ref=UUID(int=12),
        observed_command_recovery_generation=3,
    )
    assert encode(values=live)
    with pytest.raises(PersistenceValueError):
        encode(values=live | {"observed_command_recovery_generation": 4})


def test_reconciliation_and_pnl_bundle_shapes_are_exact():
    with pytest.raises(PersistenceValueError):
        encode(values=context(reconciliation_requirements_version=None))
    with pytest.raises(PersistenceValueError):
        encode(
            values=context(
                observed_reconciliation_state="READY_CLAIM", reconciliation_completeness="PARTIAL"
            )
        )
    with pytest.raises(PersistenceValueError):
        encode(values=context(pnl_input_set_sha256=None))
    with pytest.raises(PersistenceValueError):
        encode(values=context(observed_accounting_state="PARTIAL", pnl_completeness="COMPLETE"))


def test_payload_models_are_strict_frozen_extra_forbidden_and_owned():
    module = evidence()
    item = module.RiskCheckV1.model_validate(next(iter(checks().values())))
    with pytest.raises(ValidationError):
        item.outcome = "FAIL"
    with pytest.raises(PersistenceValueError):
        module.RiskCheckV1.model_validate(
            next(iter(checks().values())) | {"api_secret": "SECRET-RISK-MARKER"}
        )
    reservation = module.ReservationPlanV1.model_validate(plan())
    with pytest.raises(ValidationError):
        reservation.approved_contracts = 9
    supplied = checks()
    encoded = encode(results=supplied)
    supplied["MAX_POSITION"]["observed_integer"] = 999
    assert (
        evidence().validate_risk_decision_evidence(1, encoded.payload, encoded.evidence_sha256)
        is None
    )


def test_derived_hash_payload_or_version_overrides_are_not_input_fields():
    for field, value in (
        ("check_set_version", 1),
        ("check_set_sha256", HASH_A),
        ("evidence_payload", "{}"),
        ("evidence_sha256", HASH_A),
        ("reservation_plan_sha256", HASH_A),
        ("api_secret", "SECRET-RISK-MARKER"),
    ):
        with pytest.raises(PersistenceValueError) as error:
            encode(values=context(**{field: value}))
        assert "SECRET-RISK-MARKER" not in str(error.value) + repr(error.value)


def test_stored_payload_and_each_internal_digest_are_validated():
    encoded = encode()
    assert (
        evidence().validate_risk_decision_evidence(1, encoded.payload, encoded.evidence_sha256)
        is None
    )
    raw = json.loads(encoded.payload)
    mutations = []
    changed_item = deepcopy(raw)
    changed_item["check_results"][0]["evidence_sha256"] = "f" * 64
    mutations.append(changed_item)
    changed_set = deepcopy(raw)
    changed_set["check_set_sha256"] = "f" * 64
    mutations.append(changed_set)
    changed_plan = deepcopy(raw)
    changed_plan["reservation_plan"]["reservation_plan_sha256"] = "f" * 64
    mutations.append(changed_plan)
    changed_context = deepcopy(raw)
    changed_context["decision_key"] = "changed"
    for changed in mutations:
        payload = json.dumps(changed, sort_keys=True, separators=(",", ":"))
        with pytest.raises(PersistenceValueError):
            evidence().decode_risk_decision_evidence(payload)
    changed_context_payload = json.dumps(changed_context, sort_keys=True, separators=(",", ":"))
    changed_encoding = evidence().decode_risk_decision_evidence(changed_context_payload)
    assert changed_encoding.evidence_sha256 != encoded.evidence_sha256
    with pytest.raises(PersistenceValueError):
        evidence().validate_risk_decision_evidence(
            1, changed_context_payload, encoded.evidence_sha256
        )
    for digest in (None, True, "A" * 64, "a" * 63, "f" * 64):
        with pytest.raises(PersistenceValueError):
            evidence().validate_risk_decision_evidence(1, encoded.payload, digest)


def _mutated_json_value(value):
    if value is None:
        return "mutated"
    if type(value) is bool:
        return not value
    if type(value) is int:
        return value + 1
    if type(value) is str:
        return value + "x"
    if type(value) is list:
        return list(reversed(value))
    if type(value) is dict:
        return value | {"unexpected": None}
    raise AssertionError(type(value))


def _assert_payload_mutation_changes_or_rejects(baseline, changed):
    payload = json.dumps(changed, sort_keys=True, separators=(",", ":"))
    try:
        mutated = evidence().decode_risk_decision_evidence(payload)
    except PersistenceValueError:
        return
    assert mutated.evidence_sha256 != baseline.evidence_sha256


def test_every_decision_check_and_plan_property_mutation_changes_or_rejects_digest():
    baseline = encode()
    raw = json.loads(baseline.payload)
    for field in evidence().DECISION_PAYLOAD_FIELDS - {
        "check_results",
        "reservation_plan",
    }:
        changed = deepcopy(raw)
        changed[field] = _mutated_json_value(changed[field])
        _assert_payload_mutation_changes_or_rejects(baseline, changed)
    for field in evidence().CHECK_EVIDENCE_FIELDS:
        changed = deepcopy(raw)
        changed["check_results"][0][field] = _mutated_json_value(changed["check_results"][0][field])
        _assert_payload_mutation_changes_or_rejects(baseline, changed)
    for field in evidence().RESERVATION_PLAN_STORED_FIELDS:
        changed = deepcopy(raw)
        changed["reservation_plan"][field] = _mutated_json_value(changed["reservation_plan"][field])
        _assert_payload_mutation_changes_or_rejects(baseline, changed)


def test_literal_hash_envelope_discriminators_are_domain_separated():
    encoded = encode()
    payload = json.loads(encoded.payload)
    envelopes = (
        {
            "kind": "risk_check_evidence",
            "payload": {
                "decision_id": payload["decision_id"],
                **{
                    key: value
                    for key, value in payload["check_results"][0].items()
                    if key != "evidence_sha256"
                },
            },
            "schema_version": 1,
        },
        {
            "kind": "risk_check_set",
            "payload": {
                "check_set_version": 1,
                "decision_id": payload["decision_id"],
                "results": [
                    {
                        "check_kind": item["check_kind"],
                        "evidence_sha256": item["evidence_sha256"],
                        "outcome": item["outcome"],
                    }
                    for item in payload["check_results"]
                ],
            },
            "schema_version": 1,
        },
        {
            "kind": "risk_reservation_plan",
            "payload": {
                "decision_id": payload["decision_id"],
                **{
                    key: value
                    for key, value in payload["reservation_plan"].items()
                    if key != "reservation_plan_sha256"
                },
            },
            "schema_version": 1,
        },
        {"kind": "risk_decision", "payload": payload, "schema_version": 1},
    )
    for envelope in envelopes:
        baseline = sha256(
            json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("ascii")
        ).hexdigest()
        for field in ("kind", "schema_version"):
            changed = deepcopy(envelope)
            changed[field] = _mutated_json_value(changed[field])
            assert (
                sha256(
                    json.dumps(changed, sort_keys=True, separators=(",", ":")).encode("ascii")
                ).hexdigest()
                != baseline
            )


@pytest.mark.parametrize(
    "changes",
    [
        {"decision_id": str(DECISION_ID)},
        {"product_id": True},
        {"product_id": 0},
        {"policy_version": True},
        {"policy_version": 2**63},
        {"decision_key": ""},
        {"decision_key": "x\x00y"},
        {"evaluation_reference_asset_symbol": "x" * 129},
        {"evaluated_at": EVALUATED_AT.replace(tzinfo=None)},
        {"observed_claim_expires_at": EVALUATED_AT},
        {"command_evidence_schema_version": True},
    ],
)
def test_decision_context_uuid_bigint_text_utc_and_version_boundaries_are_strict(changes):
    with pytest.raises(PersistenceValueError):
        encode(values=context(**changes))


def test_each_reduce_only_na_allowlisted_check_may_independently_pass():
    values, results, reservation = reduce_only_case()
    for kind in (
        "MAX_POSITION",
        "TOTAL_EXPOSURE",
        "MAX_LEVERAGE",
        "MAX_LOSS_PER_TRADE",
        "MAX_DAILY_LOSS",
        "PROTECTION",
    ):
        changed = deepcopy(results)
        changed[kind] = deepcopy(checks()[kind])
        if kind == "MAX_LOSS_PER_TRADE":
            changed[kind]["observed_decimal"] = Decimal("0")
        assert encode(values=values, results=changed, reservation=reservation)


def test_denied_decisions_retain_the_effect_specific_na_allowlist():
    values, results, reservation = reduce_only_case()
    values = values | {"outcome": "DENIED"}
    results["DUPLICATE_ORDER"] = {
        **results["DUPLICATE_ORDER"],
        "outcome": "FAIL",
        "reason_code": "DUPLICATE_ORDER",
    }
    assert encode(values=values, results=results, reservation=None)
    for mandatory in set(CHECK_KINDS) - {
        "MAX_POSITION",
        "TOTAL_EXPOSURE",
        "MAX_LEVERAGE",
        "MAX_LOSS_PER_TRADE",
        "MAX_DAILY_LOSS",
        "PROTECTION",
    }:
        changed = deepcopy(results)
        changed[mandatory] = {
            **changed[mandatory],
            "outcome": "NOT_APPLICABLE",
            "reason_code": "NOT_APPLICABLE",
            "metric_kind": "NONE",
            "unit": "NONE",
            "observed_decimal": None,
            "limit_decimal": None,
            "observed_integer": None,
            "limit_integer": None,
            "evidence_observed_at": None,
        }
        with pytest.raises(PersistenceValueError):
            encode(values=values, results=changed, reservation=None)


def test_size_changing_modify_can_be_approved_with_zero_new_order_slot():
    modify_command = UUID("90000000-0000-4000-8000-000000000009")
    payload = (
        '{"product_id":1101,"requested_contracts":2,'
        '"target_order_id":"50000000-0000-4000-8000-000000000005"}'
    )
    envelope = '{"operation":"MODIFY_ORDER","payload":' + payload + ',"schema_version":1}'
    values = context(
        command_id=modify_command,
        command_operation_kind="MODIFY_ORDER",
        command_evidence_payload=payload,
        command_evidence_sha256=sha256(envelope.encode("ascii")).hexdigest(),
    )
    encoded = encode(values=values, reservation=plan(reserved_open_order_slots=0))
    assert json.loads(encoded.payload)["reservation_plan"]["reserved_open_order_slots"] == 0


def test_payload_adapter_checks_bind_and_result_without_sibling_columns():
    adapter = evidence().RiskDecisionEvidencePayload()
    payload = encode().payload
    dialect = sqlite.dialect()
    assert adapter.process_bind_param(payload, dialect) == payload
    assert adapter.process_result_value(payload, dialect) == payload
    with pytest.raises(PersistenceValueError):
        adapter.process_bind_param("{}", dialect)
    with pytest.raises(PersistenceValueError):
        adapter.process_result_value("{}", dialect)


def test_evidence_payload_limit_admits_maximum_documented_values_and_rejects_excess():
    maximum = context(
        decision_key="x" * 128,
        evaluation_reference_asset_symbol="r" * 128,
        evaluation_collateral_asset_symbol="c" * 128,
        pnl_reference_asset_symbol="r" * 128,
    )
    reservation = plan(
        reference_asset_symbol="r" * 128,
        collateral_asset_symbol="c" * 128,
        reserved_incremental_gross_exposure=Decimal("99999999999999999999.999999999999999999"),
    )
    encoded = encode(values=maximum, reservation=reservation)
    assert len(encoded.payload.encode("ascii")) <= 16_384
    with pytest.raises(PersistenceValueError):
        encode(values=maximum | {"decision_key": "x" * 129}, reservation=reservation)
