"""Fail-closed broker-independent Strategy-v2 evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from hmt_crypto.strategy.evaluator_models import (
    AcceleratorDiagnostic,
    FinalAction,
    GroupDiagnostic,
    RuleDiagnostic,
    StrategyDiagnostics,
    StrategyEvaluation,
)
from hmt_crypto.strategy.evaluator_values import (
    JsonValue,
    StrategyInputError,
    _display_number,
    _finite_number,
    _invalid,
    _mapping,
    _positive_integer,
    _positive_number,
    _scalar_text,
    _sequence,
    _snapshot,
    _text,
    _utc_timestamp,
    _validate_portable_config,
)

_OPTION_KEYS = frozenset(
    {
        "brokerOverrides",
        "eventCalendarLookupStatus",
        "eventCalendarMatches",
        "executionOverrides",
        "market",
        "now",
        "symbol",
        "tradeType",
    }
)
_ACTION_KEYS = frozenset({"lots", "atr_multiplier", "sl_atr_multiplier", "target_atr_multiplier"})


@dataclass(frozen=True, slots=True)
class _RuleSet:
    passed: bool
    failed_rule: str | None
    diagnostics: tuple[RuleDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class _AcceleratorResult:
    passed: bool
    condition_mode: str
    groups: tuple[GroupDiagnostic, ...]


def _payload_value(payload: dict[str, JsonValue], field: str) -> JsonValue:
    return payload.get(field)


def _evaluate_rule(rule_value: JsonValue, payload: dict[str, JsonValue]) -> RuleDiagnostic:
    rule = _mapping(rule_value)
    field = _text(rule.get("field"))
    operator = _text(rule.get("operator"))
    raw = _payload_value(payload, field)
    if raw is None:
        return RuleDiagnostic(
            rule=f"{field} {operator}", passed=False, reason=f'field "{field}" is missing'
        )

    if operator in {"gte", "lte", "lt", "between"}:
        if type(raw) not in (int, float):
            return RuleDiagnostic(
                rule=f"{field} {operator}",
                passed=False,
                reason=f'field "{field}" is not numeric',
            )
        actual = cast("int | float", raw)
        actual_text = _display_number(actual)
        if operator == "gte":
            threshold = _finite_number(rule.get("min"))
            passed = actual >= threshold
            comparison = ">=" if passed else "<"
            reason = f"{field}={actual_text} {comparison} {_display_number(threshold)}"
        elif operator == "lte":
            threshold = _finite_number(rule.get("max"))
            passed = actual <= threshold
            comparison = "<=" if passed else ">"
            reason = f"{field}={actual_text} {comparison} {_display_number(threshold)}"
        elif operator == "lt":
            threshold = _finite_number(rule.get("max"))
            passed = actual < threshold
            comparison = "<" if passed else ">="
            reason = f"{field}={actual_text} {comparison} {_display_number(threshold)}"
        else:
            low = _finite_number(rule.get("min"))
            high = _finite_number(rule.get("max"))
            if low > high:
                raise _invalid()
            passed = low <= actual <= high
            relation = "in" if passed else "not in"
            reason = (
                f"{field}={actual_text} {relation} "
                f"[{_display_number(low)}, {_display_number(high)}]"
            )
    elif operator == "eq":
        expected = rule.get("value")
        if type(raw) not in (str, int, float, bool) or type(expected) not in (
            str,
            int,
            float,
            bool,
        ):
            raise _invalid()
        actual_text = _scalar_text(raw)
        expected_text = _scalar_text(expected)
        passed = actual_text == expected_text
        relation = "==" if passed else "!="
        reason = f'{field}="{actual_text}" {relation} "{expected_text}"'
    elif operator == "in":
        values = _sequence(rule.get("values"))
        if type(raw) not in (str, int, float, bool):
            raise _invalid()
        rendered = []
        for item in values:
            if type(item) not in (str, int, float, bool):
                raise _invalid()
            rendered.append(_scalar_text(item))
        actual_text = _scalar_text(raw)
        passed = actual_text in rendered
        relation = "in" if passed else "not in"
        reason = f'{field}="{actual_text}" {relation} [{", ".join(rendered)}]'
    else:
        raise _invalid()
    return RuleDiagnostic(rule=f"{field} {operator}", passed=passed, reason=reason)


def _evaluate_rule_set(rules_value: JsonValue, payload: dict[str, JsonValue]) -> _RuleSet:
    rules = _sequence(rules_value)
    diagnostics = tuple(_evaluate_rule(rule, payload) for rule in rules)
    failed_rule = next((rule.reason for rule in diagnostics if not rule.passed), None)
    return _RuleSet(
        passed=bool(diagnostics) and failed_rule is None,
        failed_rule=failed_rule,
        diagnostics=diagnostics,
    )


def _condition_groups(accelerator: dict[str, JsonValue]) -> list[tuple[str, JsonValue]]:
    raw_groups = accelerator.get("condition_groups")
    if type(raw_groups) is list and raw_groups:
        groups: list[tuple[str, JsonValue]] = []
        for raw_group in raw_groups:
            group = _mapping(raw_group)
            groups.append((_text(group.get("group_id")), group.get("conditions")))
        return groups
    return [("legacy", accelerator.get("conditions"))]


def _evaluate_accelerator(
    accelerator: dict[str, JsonValue], payload: dict[str, JsonValue]
) -> _AcceleratorResult:
    groups = _condition_groups(accelerator)
    diagnostics = []
    for group_id, rules in groups:
        result = _evaluate_rule_set(rules, payload)
        diagnostics.append(
            GroupDiagnostic(
                group_id=group_id,
                passed=result.passed,
                failed_rule=result.failed_rule,
                rule_passes=tuple(rule.passed for rule in result.diagnostics),
            )
        )
    frozen = tuple(diagnostics)
    return _AcceleratorResult(
        passed=any(group.passed for group in frozen),
        condition_mode="or_groups" if len(frozen) > 1 else "and",
        groups=frozen,
    )


def _portable_slot(config: dict[str, JsonValue], direction: str) -> dict[str, JsonValue]:
    regimes = _mapping(config.get("regimes"))
    candidates = []
    for value in regimes.values():
        regime = _mapping(value)
        if regime.get("enabled") is True:
            if regime.get("vix_min") is not None or regime.get("vix_max") is not None:
                raise _invalid()
            candidates.append(regime)
    if len(candidates) != 1:
        raise _invalid()
    schedule_name = "buy_schedule" if direction == "BUY" else "sell_schedule"
    schedule = _mapping(candidates[0].get(schedule_name))
    slots = [_mapping(value) for value in schedule.values() if value is not None]
    if len(slots) != 1:
        raise _invalid()
    if slots[0].get("start_time") != "00:00" or slots[0].get("end_time") != "23:59":
        raise _invalid()
    return slots[0]


def _validate_portable_boundary(
    config: dict[str, JsonValue], options: dict[str, JsonValue]
) -> None:
    market = options.get("market")
    if market not in ("delta_india", "delta_global"):
        raise _invalid()
    if options.get("eventCalendarLookupStatus") != "success":
        raise _invalid()
    if _sequence(options.get("eventCalendarMatches")):
        raise _invalid()

    guardrails = _mapping(config.get("guardrails"))
    if guardrails.get("one_active_trade_per_stock") is not False:
        raise _invalid()
    event_calendar = _mapping(guardrails.get("event_calendar"))
    if event_calendar.get("enabled") is not False:
        raise _invalid()
    universe = _mapping(guardrails.get("stock_universe"))
    if universe.get("mode") != "all":
        raise _invalid()
    if _sequence(universe.get("whitelist")) or _sequence(universe.get("blocklist")):
        raise _invalid()


def _resolve_direction(payload: dict[str, JsonValue], options: dict[str, JsonValue]) -> str:
    payload_direction = payload.get("trade_type")
    option_direction = options.get("tradeType")
    if option_direction is not None and payload_direction is not None:
        if option_direction != payload_direction:
            raise _invalid()
    raw = option_direction if option_direction is not None else payload_direction
    if raw not in ("BUY", "SELL"):
        raise _invalid()
    return cast("str", raw)


def _engine_action(config: dict[str, JsonValue], engine: dict[str, JsonValue]) -> FinalAction:
    standard = _mapping(config.get("standard"))
    lots_value = engine.get("default_lots", standard.get("lots"))
    return FinalAction(
        lots=_positive_integer(lots_value),
        atr_multiplier=_positive_number(engine.get("atr_multiplier")),
        sl_atr_multiplier=_positive_number(engine.get("sl_atr_multiplier")),
        target_atr_multiplier=_positive_number(
            engine.get("target_atr_multiplier", standard.get("target_atr_multiplier"))
        ),
    )


def _merge_action(base: FinalAction, override_value: JsonValue) -> FinalAction:
    override = _mapping(override_value)
    if not set(override).issubset(_ACTION_KEYS):
        raise _invalid()
    return FinalAction(
        lots=_positive_integer(override.get("lots", base.lots)),
        atr_multiplier=_positive_number(override.get("atr_multiplier", base.atr_multiplier)),
        sl_atr_multiplier=_positive_number(
            override.get("sl_atr_multiplier", base.sl_atr_multiplier)
        ),
        target_atr_multiplier=_positive_number(
            override.get("target_atr_multiplier", base.target_atr_multiplier)
        ),
    )


def _stock_override(
    config: dict[str, JsonValue], options: dict[str, JsonValue]
) -> tuple[str, int | float] | None:
    raw_symbol = options.get("symbol")
    if raw_symbol is None:
        return None
    symbol = _text(raw_symbol).strip().upper()
    if not symbol:
        raise _invalid()
    standard = _mapping(config.get("standard"))
    for raw_override in _sequence(standard.get("stock_target_overrides", [])):
        override = _mapping(raw_override)
        configured_symbol = _text(override.get("symbol")).strip().upper()
        if configured_symbol == symbol:
            return symbol, _positive_number(override.get("target_atr_multiplier"))
    return None


def _rejected(
    *, direction: str, failures: tuple[tuple[str, str, _RuleSet], ...]
) -> StrategyEvaluation:
    if not failures:
        raise _invalid()
    failed_rule = failures[0][2].failed_rule
    reason = "No engine passed rules: " + "; ".join(
        f"{engine_id} ({engine_name}): {rules.failed_rule}"
        for engine_id, engine_name, rules in failures
    )
    diagnostics = StrategyDiagnostics(
        engine_rules=tuple(rule for _, _, rules in failures for rule in rules.diagnostics),
        failed_rule=failed_rule,
        accelerator_condition_mode=None,
        accelerator_group_passes=(),
        accelerator_evaluations=(),
        final_action=None,
        trace_statuses=tuple(
            [(f"engine_{engine_id}", "failed") for engine_id, _, _ in failures]
            + [("engine_selection", "failed")]
        ),
    )
    return StrategyEvaluation(
        passed=False,
        status="rejected",
        reason=reason,
        direction=direction,
        matched_engine_id=None,
        matched_accelerator_id=None,
        lots=0,
        atr_multiplier=None,
        sl_atr_multiplier=None,
        target_atr_multiplier=0,
        atr_multiplier_source=None,
        sl_multiplier_source=None,
        target_multiplier_source=None,
        stock_target_override_symbol=None,
        diagnostics=diagnostics,
    )


def evaluate_strategy_v2(config: object, payload: object, options: object) -> StrategyEvaluation:
    """Evaluate portable Strategy-v2 rules without granting execution authority."""

    frozen_config = _mapping(_snapshot(config))
    frozen_payload = _mapping(_snapshot(payload))
    frozen_options = _mapping(_snapshot(options))
    _validate_portable_config(frozen_config)
    if not set(frozen_options).issubset(_OPTION_KEYS):
        raise _invalid()
    required_options = {
        "market",
        "now",
        "symbol",
        "eventCalendarMatches",
        "eventCalendarLookupStatus",
        "tradeType",
    }
    if not required_options.issubset(frozen_options):
        raise _invalid()
    _utc_timestamp(frozen_options.get("now"))
    option_symbol = _text(frozen_options.get("symbol")).strip().upper()
    if not option_symbol:
        raise _invalid()
    payload_symbol = frozen_payload.get("symbol")
    if payload_symbol is not None and _text(payload_symbol).strip().upper() != option_symbol:
        raise _invalid()
    if "executionOverrides" in frozen_options and "brokerOverrides" in frozen_options:
        raise _invalid()
    _validate_portable_boundary(frozen_config, frozen_options)

    direction = _resolve_direction(frozen_payload, frozen_options)
    slot = _portable_slot(frozen_config, direction)
    schedule_entries = _sequence(slot.get("engines"))
    engines = _mapping(frozen_config.get("engines"))

    selected_id: str | None = None
    selected_engine: dict[str, JsonValue] | None = None
    selected_rules: _RuleSet | None = None
    engine_failures: list[tuple[str, str, _RuleSet]] = []
    for raw_entry in schedule_entries:
        entry = _mapping(raw_entry)
        engine_id = _text(entry.get("engine_id"))
        engine = _mapping(engines.get(engine_id))
        if engine.get("direction") != direction:
            continue
        rules = _evaluate_rule_set(engine.get("rules"), frozen_payload)
        if rules.passed:
            selected_id, selected_engine, selected_rules = engine_id, engine, rules
            break
        engine_failures.append((engine_id, _text(engine.get("name")), rules))

    if selected_id is None or selected_engine is None or selected_rules is None:
        return _rejected(
            direction=direction,
            failures=tuple(engine_failures),
        )
    if selected_engine.get("mode") != "live":
        raise _invalid()

    selected_entry = next(
        _mapping(raw_entry)
        for raw_entry in schedule_entries
        if _mapping(raw_entry).get("engine_id") == selected_id
    )
    allowed_ids = tuple(_text(value) for value in _sequence(selected_entry.get("accelerator_ids")))
    accelerators = _mapping(frozen_config.get("accelerators"))
    candidates = []
    for accelerator_id in allowed_ids:
        accelerator = _mapping(accelerators.get(accelerator_id))
        if accelerator.get("enabled") is not True or accelerator.get("mode") != "live":
            continue
        priority = _positive_integer(accelerator.get("priority"))
        candidates.append((priority, accelerator_id, accelerator))
    candidates.sort(key=lambda candidate: candidate[0])

    evaluations: list[AcceleratorDiagnostic] = []
    matched_accelerator_id: str | None = None
    matched_accelerator: dict[str, JsonValue] | None = None
    matched_accelerator_result: _AcceleratorResult | None = None
    trace_statuses: list[tuple[str, str]] = [
        *((f"engine_{engine_id}", "failed") for engine_id, _, _ in engine_failures),
        (f"engine_{selected_id}", "success"),
        ("engine_selection", "success"),
        ("engine_mode", "success"),
    ]
    for _, accelerator_id, accelerator in candidates:
        evaluated = _evaluate_accelerator(accelerator, frozen_payload)
        evaluations.append(
            AcceleratorDiagnostic(
                accelerator_id=accelerator_id,
                status="success" if evaluated.passed else "failed",
                condition_mode=evaluated.condition_mode,
                groups=evaluated.groups,
            )
        )
        trace_statuses.append(
            (f"acc_{accelerator_id}", "success" if evaluated.passed else "failed")
        )
        if evaluated.passed:
            matched_accelerator_id = accelerator_id
            matched_accelerator = accelerator
            matched_accelerator_result = evaluated
            trace_statuses.append((f"acc_{accelerator_id}_action", "success"))
            break
    if not any(evaluation.status == "success" for evaluation in evaluations):
        trace_statuses.append(("accelerator_selection", "skipped"))

    action = _engine_action(frozen_config, selected_engine)
    atr_source = "engine"
    sl_source = "engine"
    target_source = "engine"
    if matched_accelerator is not None:
        action = _merge_action(action, matched_accelerator.get("action"))
        atr_source = "accelerator"
        sl_source = "accelerator"
        target_source = "accelerator"
        schedule_overrides = _mapping(selected_entry.get("accelerator_overrides", {}))
        schedule_override = schedule_overrides.get(cast("str", matched_accelerator_id))
        if schedule_override is not None:
            override = _mapping(schedule_override)
            action = _merge_action(action, override)
            if "atr_multiplier" in override:
                atr_source = "schedule_override"
            if "sl_atr_multiplier" in override:
                sl_source = "schedule_override"
            if "target_atr_multiplier" in override:
                target_source = "schedule_override"
    stock_override = _stock_override(frozen_config, frozen_options)
    if stock_override is None:
        trace_statuses.append(("stock_target_override", "skipped"))
        stock_symbol = None
    else:
        stock_symbol, target_multiplier = stock_override
        action = FinalAction(
            lots=action.lots,
            atr_multiplier=action.atr_multiplier,
            sl_atr_multiplier=action.sl_atr_multiplier,
            target_atr_multiplier=target_multiplier,
        )
        trace_statuses.append(("stock_target_override", "success"))
        target_source = "stock_override"

    raw_execution_override = frozen_options.get("executionOverrides")
    raw_broker_override = frozen_options.get("brokerOverrides")
    final_override = (
        raw_execution_override if raw_execution_override is not None else raw_broker_override
    )
    if final_override is not None:
        override = _mapping(final_override)
        if not set(override).issubset(_ACTION_KEYS):
            raise _invalid()
        action = _merge_action(action, override)
        if "atr_multiplier" in override:
            atr_source = "broker_override"
        if "sl_atr_multiplier" in override:
            sl_source = "broker_override"
        if "target_atr_multiplier" in override:
            target_source = "broker_override"
        trace_statuses.append(("broker_action_override", "success"))
    trace_statuses.append(("final_action", "success"))

    diagnostics = StrategyDiagnostics(
        engine_rules=selected_rules.diagnostics,
        failed_rule=None,
        accelerator_condition_mode=(
            None
            if matched_accelerator_result is None
            else matched_accelerator_result.condition_mode
        ),
        accelerator_group_passes=(
            ()
            if matched_accelerator_result is None
            else tuple(
                (group.group_id, group.passed) for group in matched_accelerator_result.groups
            )
        ),
        accelerator_evaluations=tuple(evaluations),
        final_action=action,
        trace_statuses=tuple(trace_statuses),
    )
    return StrategyEvaluation(
        passed=True,
        status="passed",
        reason="Passed — eligible for live execution",
        direction=direction,
        matched_engine_id=selected_id,
        matched_accelerator_id=matched_accelerator_id,
        lots=action.lots,
        atr_multiplier=action.atr_multiplier,
        sl_atr_multiplier=action.sl_atr_multiplier,
        target_atr_multiplier=action.target_atr_multiplier,
        atr_multiplier_source=atr_source,
        sl_multiplier_source=sl_source,
        target_multiplier_source=target_source,
        stock_target_override_symbol=stock_symbol,
        diagnostics=diagnostics,
    )


__all__ = ["StrategyInputError", "evaluate_strategy_v2"]
