"""Runtime parity and fail-closed tests for the portable Strategy-v2 evaluator."""

from __future__ import annotations

import importlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/strategy/hmt_strategy_v2_golden_v1.json"


def _document() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _runtime() -> Any:
    try:
        return importlib.import_module("hmt_crypto.strategy.evaluator")
    except ModuleNotFoundError:
        pytest.fail("portable Strategy-v2 runtime is not implemented")


def test_standard_buy_matches_the_archived_golden_contract() -> None:
    """Dropping engine defaults would change the standard BUY action."""

    document = _document()
    case = next(case for case in document["cases"] if case["case_id"] == "buy_engine_standard")

    result = _runtime().evaluate_strategy_v2(
        document["portable_config"], case["payload"], case["options"]
    )

    assert result.to_dict() == case["expected"]


def test_first_passing_accelerator_group_selects_the_accelerator_action() -> None:
    """Ignoring OR-group success would incorrectly leave the engine action selected."""

    document = _document()
    case = next(
        case for case in document["cases"] if case["case_id"] == "grouped_accelerator_first_group"
    )

    result = _runtime().evaluate_strategy_v2(
        document["portable_config"], case["payload"], case["options"]
    )

    assert result.to_dict() == case["expected"]


def test_second_passing_accelerator_group_is_sufficient() -> None:
    """Treating accelerator groups as ANDed would reject a valid quality group."""

    document = _document()
    case = next(
        case for case in document["cases"] if case["case_id"] == "grouped_accelerator_second_group"
    )

    result = _runtime().evaluate_strategy_v2(
        document["portable_config"], case["payload"], case["options"]
    )

    assert result.to_dict() == case["expected"]


def test_schedule_action_override_wins_over_accelerator_defaults() -> None:
    """Skipping the route override would emit stale accelerator sizing."""

    document = _document()
    case = next(
        case for case in document["cases"] if case["case_id"] == "schedule_override_precedence"
    )

    result = _runtime().evaluate_strategy_v2(
        document["portable_config"], case["payload"], case["options"]
    )

    assert result.to_dict() == case["expected"]


def test_execution_override_is_the_final_action_precedence_layer() -> None:
    """Applying route values last would silently ignore a caller execution override."""

    document = _document()
    case = next(
        case for case in document["cases"] if case["case_id"] == "execution_override_precedence"
    )

    result = _runtime().evaluate_strategy_v2(
        document["portable_config"], case["payload"], case["options"]
    )

    assert result.to_dict() == case["expected"]


def test_symbol_target_override_changes_only_the_target_multiplier() -> None:
    """Applying the symbol override to every field would corrupt sizing and protection."""

    document = _document()
    case = next(case for case in document["cases"] if case["case_id"] == "stock_target_override")

    result = _runtime().evaluate_strategy_v2(
        document["portable_config"], case["payload"], case["options"]
    )

    assert result.to_dict() == case["expected"]


def test_sell_direction_selects_the_sell_engine() -> None:
    """Always selecting the BUY route would reverse a SELL signal's semantics."""

    document = _document()
    case = next(case for case in document["cases"] if case["case_id"] == "sell_direction")

    result = _runtime().evaluate_strategy_v2(
        document["portable_config"], case["payload"], case["options"]
    )

    assert result.to_dict() == case["expected"]


def test_engine_rejection_keeps_all_rule_diagnostics() -> None:
    """Short-circuiting the first failed rule would hide the second failure from operators."""

    document = _document()
    case = next(
        case for case in document["cases"] if case["case_id"] == "engine_rule_failure_diagnostics"
    )

    result = _runtime().evaluate_strategy_v2(
        document["portable_config"], case["payload"], case["options"]
    )

    assert result.to_dict() == case["expected"]


def test_all_failing_engines_remain_visible_in_rejection_diagnostics() -> None:
    """A later engine failure must not disappear behind the first failed candidate."""

    document = _document()
    case = next(
        case for case in document["cases"] if case["case_id"] == "engine_rule_failure_diagnostics"
    )
    backup = json.loads(json.dumps(document["portable_config"]["engines"]["BUY_CORE"]))
    backup["engine_id"] = "BUY_BACKUP"
    backup["name"] = "Portable buy backup"
    document["portable_config"]["engines"]["BUY_BACKUP"] = backup
    slot = document["portable_config"]["regimes"]["PORTABLE_UNBOUNDED"]["buy_schedule"]["1"]
    slot["engines"].append({"engine_id": "BUY_BACKUP", "accelerator_ids": []})

    result = _runtime().evaluate_strategy_v2(
        document["portable_config"], case["payload"], case["options"]
    )

    assert result.passed is False
    assert result.reason == (
        "No engine passed rules: BUY_CORE (Portable buy core): volume_ratio=0.25 < 0.5; "
        "BUY_BACKUP (Portable buy backup): volume_ratio=0.25 < 0.5"
    )
    assert len(result.diagnostics.engine_rules) == 4
    assert result.diagnostics.trace_statuses == (
        ("engine_BUY_CORE", "failed"),
        ("engine_BUY_BACKUP", "failed"),
        ("engine_selection", "failed"),
    )


def test_unknown_execution_option_fails_closed() -> None:
    """Silently accepting a new option could create an unreviewed execution policy."""

    document = _document()
    case = document["cases"][0]
    case["options"]["allow_unreviewed_behavior"] = True

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


def test_india_event_calendar_guardrail_is_not_silently_ported() -> None:
    """Enabling the equity-results guardrail would reintroduce an excluded India policy."""

    document = _document()
    case = document["cases"][0]
    document["portable_config"]["guardrails"]["event_calendar"]["enabled"] = True

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


def test_india_market_hours_are_rejected_in_the_portable_core() -> None:
    """Honoring an NSE-style opening time would make a 24x7 strategy unexpectedly dormant."""

    document = _document()
    case = document["cases"][0]
    slot = document["portable_config"]["regimes"]["PORTABLE_UNBOUNDED"]["buy_schedule"]["1"]
    slot["start_time"] = "09:15"

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


def test_numeric_rule_thresholds_may_be_zero() -> None:
    """Treating every numeric field as a positive multiplier would reject valid thresholds."""

    document = _document()
    case = document["cases"][0]
    document["portable_config"]["engines"]["BUY_CORE"]["rules"][0]["min"] = 0
    case["payload"]["volume_ratio"] = 0

    result = _runtime().evaluate_strategy_v2(
        document["portable_config"], case["payload"], case["options"]
    )

    assert result.passed is True
    assert result.diagnostics.engine_rules[0].reason == "volume_ratio=0 >= 0"


@pytest.mark.parametrize(
    ("rule", "value", "passed"),
    [
        ({"operator": "gte", "min": 1}, 1, True),
        ({"operator": "lte", "max": 1}, 1, True),
        ({"operator": "lt", "max": 1}, 1, False),
        ({"operator": "between", "min": 1, "max": 2}, 1, True),
        ({"operator": "between", "min": 1, "max": 2}, 2, True),
        ({"operator": "between", "min": 1, "max": 2}, 2.1, False),
        ({"operator": "in", "values": ["A", "B"]}, "B", True),
    ],
)
def test_rule_operator_boundaries_are_explicit(
    rule: dict[str, object], value: object, passed: bool
) -> None:
    """Inclusive, exclusive, range, and membership boundaries must not drift."""

    document = _document()
    case = document["cases"][0]
    configured = document["portable_config"]["engines"]["BUY_CORE"]["rules"][0]
    configured.clear()
    configured.update({"field": "portable_value", **rule})
    case["payload"]["portable_value"] = value

    result = _runtime().evaluate_strategy_v2(
        document["portable_config"], case["payload"], case["options"]
    )

    assert result.diagnostics.engine_rules[0].passed is passed


@pytest.mark.parametrize(
    "mutation",
    [
        lambda config: config["engines"]["BUY_CORE"].__setitem__("rules", []),
        lambda config: config["accelerators"]["GROUPED"]["condition_groups"][0].__setitem__(
            "conditions", []
        ),
        lambda config: config["accelerators"]["OVERRIDDEN"].__setitem__("conditions", []),
    ],
)
def test_empty_rule_sets_and_condition_groups_fail_closed(mutation: Any) -> None:
    """An empty AND group cannot become a vacuous pass or an unexplained rejection."""

    document = _document()
    case = document["cases"][0]
    mutation(document["portable_config"])

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


def test_unreviewed_accelerator_action_field_fails_closed() -> None:
    """An execution-shaped field must not sneak through a pure strategy action."""

    document = _document()
    case = next(
        case for case in document["cases"] if case["case_id"] == "grouped_accelerator_first_group"
    )
    document["portable_config"]["accelerators"]["GROUPED"]["action"]["reduce_only"] = True

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


class _CallbackDict(dict[str, object]):
    called = False

    def items(self):  # type: ignore[no-untyped-def]
        type(self).called = True
        raise AssertionError("caller callback must not run")


class _CallbackList(list[object]):
    called = False

    def __iter__(self):  # type: ignore[no-untyped-def]
        type(self).called = True
        raise AssertionError("caller callback must not run")


@pytest.mark.parametrize("nonfinite", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_payload_numbers_fail_closed(nonfinite: float) -> None:
    """A non-finite signal must never reach comparison or sizing logic."""

    document = _document()
    case = document["cases"][0]
    case["payload"]["volume_ratio"] = nonfinite

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


def test_mapping_subclass_is_rejected_without_running_its_callbacks() -> None:
    """A hostile mapping subclass must not execute code during snapshotting."""

    document = _document()
    case = document["cases"][0]
    hostile = _CallbackDict(document["portable_config"])
    _CallbackDict.called = False

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(hostile, case["payload"], case["options"])

    assert _CallbackDict.called is False


def test_list_subclass_is_rejected_without_running_its_callbacks() -> None:
    """A hostile sequence subclass must not execute code during snapshotting."""

    document = _document()
    case = document["cases"][0]
    case["options"]["eventCalendarMatches"] = _CallbackList()
    _CallbackList.called = False

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )

    assert _CallbackList.called is False


def test_cyclic_input_fails_closed() -> None:
    """A cyclic caller graph must not recurse indefinitely or produce partial output."""

    document = _document()
    case = document["cases"][0]
    case["payload"]["cycle"] = case["payload"]

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


def test_oversized_string_fails_before_strategy_evaluation() -> None:
    """Unbounded caller strings would make diagnostics an allocation vector."""

    document = _document()
    case = document["cases"][0]
    case["options"]["symbol"] = "X" * 10_001

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


def test_payload_and_option_direction_must_agree() -> None:
    """Conflicting direction sources must not select an arbitrary side."""

    document = _document()
    case = document["cases"][0]
    case["options"]["tradeType"] = "SELL"

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


def test_bounded_vix_regime_is_rejected() -> None:
    """Accepting a VIX range would revive the excluded India regime policy."""

    document = _document()
    case = document["cases"][0]
    document["portable_config"]["regimes"]["PORTABLE_UNBOUNDED"]["vix_min"] = 10

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


def test_nonempty_equity_event_matches_are_rejected() -> None:
    """Event payloads must not imply that the excluded results calendar is active."""

    document = _document()
    case = document["cases"][0]
    case["options"]["eventCalendarMatches"] = [{"symbol": "ETHUSD"}]

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


def test_result_is_immutable_and_serialization_is_detached() -> None:
    """Mutating a serialized diagnostic must not rewrite a prior decision."""

    document = _document()
    case = document["cases"][0]
    result = _runtime().evaluate_strategy_v2(
        document["portable_config"], case["payload"], case["options"]
    )

    with pytest.raises(FrozenInstanceError):
        result.status = "rejected"
    detached = result.to_dict()
    detached["status"] = "rejected"

    assert result.status == "passed"
    assert result.to_dict() == case["expected"]


def test_evaluation_is_deterministic_nonmutating_and_clock_independent() -> None:
    """Wall-clock or input mutation would make replayed signals non-reproducible."""

    document = _document()
    case = document["cases"][0]
    config_before = json.dumps(document["portable_config"], sort_keys=True)
    payload_before = json.dumps(case["payload"], sort_keys=True)
    first = _runtime().evaluate_strategy_v2(
        document["portable_config"], case["payload"], case["options"]
    )
    case["options"]["now"] = "2099-12-31T23:59:59.999Z"
    second = _runtime().evaluate_strategy_v2(
        document["portable_config"], case["payload"], case["options"]
    )

    assert first == second
    assert json.dumps(document["portable_config"], sort_keys=True) == config_before
    assert json.dumps(case["payload"], sort_keys=True) == payload_before


def test_exchange_neutral_execution_override_alias_preserves_golden_behavior() -> None:
    """Renaming the compatibility hook must not change final precedence."""

    document = _document()
    case = next(
        case for case in document["cases"] if case["case_id"] == "execution_override_precedence"
    )
    case["options"]["executionOverrides"] = case["options"].pop("brokerOverrides")

    result = _runtime().evaluate_strategy_v2(
        document["portable_config"], case["payload"], case["options"]
    )

    assert result.to_dict() == case["expected"]


def test_package_level_evaluator_api_executes_the_golden_contract() -> None:
    """Dropping the public export would leave callers coupled to a private module path."""

    document = _document()
    case = document["cases"][0]
    strategy = importlib.import_module("hmt_crypto.strategy")

    result = strategy.evaluate_strategy_v2(
        document["portable_config"], case["payload"], case["options"]
    )

    assert result.to_dict() == case["expected"]


def test_both_execution_override_aliases_are_rejected_even_when_one_is_null() -> None:
    """Alias presence must not let one caller-controlled precedence path hide another."""

    document = _document()
    case = document["cases"][0]
    case["options"]["brokerOverrides"] = None
    case["options"]["executionOverrides"] = {"lots": 9}

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda config: config["engines"]["SELL_CORE"].__setitem__("unreviewed", True),
        lambda config: config["engines"]["BUY_CORE"]["rules"][0].__setitem__("max", 100),
        lambda config: config.pop("algo_version"),
    ],
)
def test_unreviewed_or_unversioned_configuration_fails_closed(mutation: Any) -> None:
    """Ignored configuration, including on an unselected side, is unsafe semantic drift."""

    document = _document()
    case = document["cases"][0]
    mutation(document["portable_config"])

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


def test_only_the_frozen_24x7_compatibility_route_key_is_admitted() -> None:
    """Changing the archived weekday key must not revive day-of-week routing implicitly."""

    document = _document()
    case = document["cases"][0]
    schedule = document["portable_config"]["regimes"]["PORTABLE_UNBOUNDED"]["buy_schedule"]
    schedule["7"] = schedule.pop("1")

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


@pytest.mark.parametrize("field", ["atr_multiplier", "sl_atr_multiplier"])
def test_unsafe_magnitude_action_numbers_fail_closed(field: str) -> None:
    """Finite IEEE values beyond exact JSON-safe magnitude must not become sizing output."""

    document = _document()
    case = document["cases"][0]
    document["portable_config"]["engines"]["BUY_CORE"][field] = 1e308

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


def test_duplicate_group_ids_and_priorities_fail_closed() -> None:
    """Ambiguous diagnostic identities or accelerator ordering cannot be silently collapsed."""

    document = _document()
    case = document["cases"][0]
    groups = document["portable_config"]["accelerators"]["GROUPED"]["condition_groups"]
    groups[1]["group_id"] = groups[0]["group_id"]

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )

    document = _document()
    case = document["cases"][0]
    document["portable_config"]["accelerators"]["OVERRIDDEN"]["priority"] = 1
    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


def test_scalar_rule_text_matches_the_archived_javascript_semantics() -> None:
    """Python's ``str(True)`` must not diverge from the archived ``String(true)`` oracle."""

    document = _document()
    case = document["cases"][0]
    rule = document["portable_config"]["engines"]["BUY_CORE"]["rules"][0]
    rule.clear()
    rule.update({"field": "portable_flag", "operator": "eq", "value": "true"})
    case["payload"]["portable_flag"] = True

    result = _runtime().evaluate_strategy_v2(
        document["portable_config"], case["payload"], case["options"]
    )

    assert result.passed is True
    assert result.diagnostics.engine_rules[0].reason == ('portable_flag="true" == "true"')


@pytest.mark.parametrize(
    ("configured", "payload_value", "passed", "reason"),
    [
        ("0.000001", 1e-6, True, 'portable_number="0.000001" == "0.000001"'),
        (0.3, 0.30000000000000004, False, 'portable_number="0.30000000000000004" != "0.3"'),
    ],
)
def test_scalar_number_text_uses_javascript_shortest_round_trip_semantics(
    configured: object, payload_value: float, passed: bool, reason: str
) -> None:
    """Rounded Python formatting must not merge or split archived JavaScript values."""

    document = _document()
    case = document["cases"][0]
    rule = document["portable_config"]["engines"]["BUY_CORE"]["rules"][0]
    rule.clear()
    rule.update({"field": "portable_number", "operator": "eq", "value": configured})
    case["payload"]["portable_number"] = payload_value

    result = _runtime().evaluate_strategy_v2(
        document["portable_config"], case["payload"], case["options"]
    )

    assert result.passed is passed
    assert result.diagnostics.engine_rules[0].reason == reason


@pytest.mark.parametrize(
    "now",
    ["not-an-instant", "2026-07-13T05:00:00+05:30", "2026-02-30T05:00:00Z"],
)
def test_compatibility_clock_input_must_be_an_explicit_utc_instant(now: str) -> None:
    """The ignored compatibility clock must not admit local-time or malformed semantics."""

    document = _document()
    case = document["cases"][0]
    case["options"]["now"] = now

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


def test_conflicting_payload_and_option_symbols_fail_closed() -> None:
    """Two disagreeing symbol authorities must not choose an arbitrary target override."""

    document = _document()
    case = document["cases"][0]
    case["payload"]["symbol"] = "BTCUSD"

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


def test_direction_confused_schedule_entry_fails_closed() -> None:
    """A SELL engine cannot be silently skipped inside the frozen BUY route."""

    document = _document()
    case = document["cases"][0]
    slot = document["portable_config"]["regimes"]["PORTABLE_UNBOUNDED"]["buy_schedule"]["1"]
    slot["engines"][0]["engine_id"] = "SELL_CORE"

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


def test_ignored_legacy_conditions_cannot_hide_behind_or_groups() -> None:
    """Only one reviewed accelerator condition representation may carry semantics."""

    document = _document()
    case = document["cases"][0]
    accelerator = document["portable_config"]["accelerators"]["GROUPED"]
    accelerator["conditions"] = [{"field": "unreviewed", "operator": "eq", "value": "hidden"}]

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


def test_invalid_between_bounds_fail_before_route_selection() -> None:
    """Malformed rules on the unselected side remain part of the reviewed config."""

    document = _document()
    case = document["cases"][0]
    rule = document["portable_config"]["engines"]["SELL_CORE"]["rules"][0]
    rule.clear()
    rule.update({"field": "volume_ratio", "operator": "between", "min": 2, "max": 1})

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


def test_disabled_calendar_compatibility_fields_are_frozen() -> None:
    """Ignored India-calendar values must not become an unreviewed semantic channel."""

    document = _document()
    case = document["cases"][0]
    document["portable_config"]["guardrails"]["event_calendar"]["days_before"] = 1

    with pytest.raises(_runtime().StrategyInputError):
        _runtime().evaluate_strategy_v2(
            document["portable_config"], case["payload"], case["options"]
        )


@pytest.mark.parametrize("value", [True, "1"])
def test_numeric_rules_do_not_coerce_boolean_or_text_payloads(value: object) -> None:
    """Python bool-as-int and archived Number coercion are excluded from the pure boundary."""

    document = _document()
    case = document["cases"][0]
    case["payload"]["volume_ratio"] = value

    result = _runtime().evaluate_strategy_v2(
        document["portable_config"], case["payload"], case["options"]
    )

    assert result.passed is False
    assert result.diagnostics.engine_rules[0].reason == 'field "volume_ratio" is not numeric'


def test_shared_input_aliases_are_snapshotted_without_false_cycle_detection() -> None:
    """Acyclic shared data is copied safely while true back-edges remain rejected."""

    document = _document()
    case = document["cases"][0]
    shared = {"value": 1}
    case["payload"]["shared_a"] = shared
    case["payload"]["shared_b"] = shared

    result = _runtime().evaluate_strategy_v2(
        document["portable_config"], case["payload"], case["options"]
    )
    shared["value"] = 2

    assert result.to_dict() == case["expected"]
