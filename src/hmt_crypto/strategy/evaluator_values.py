"""Bounded exact-built-in values for pure strategy evaluation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TypeAlias, cast

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

_MAX_DEPTH = 20
_MAX_NODES = 100_000
_MAX_CONTAINER_ITEMS = 10_000
_MAX_STRING_LENGTH = 10_000
_MAX_SAFE_INTEGER = 2**53 - 1
_UTC_INSTANT = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z"
)


class StrategyInputError(ValueError):
    """The portable strategy input is malformed or outside safe bounds."""

    def __init__(self) -> None:
        super().__init__("portable strategy input is invalid")


@dataclass(slots=True)
class _Budget:
    remaining: int = _MAX_NODES


def _invalid() -> StrategyInputError:
    return StrategyInputError()


def _snapshot(value: object) -> JsonValue:
    try:
        return _copy_json(value, depth=0, budget=_Budget(), active=set())
    except (RuntimeError, RecursionError):
        raise _invalid() from None


def _copy_json(value: object, *, depth: int, budget: _Budget, active: set[int]) -> JsonValue:
    budget.remaining -= 1
    if budget.remaining < 0 or depth > _MAX_DEPTH:
        raise _invalid()
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if abs(value) > _MAX_SAFE_INTEGER:
            raise _invalid()
        return value
    if type(value) is float:
        if not math.isfinite(value) or abs(value) > _MAX_SAFE_INTEGER:
            raise _invalid()
        return value
    if type(value) is str:
        if len(value) > _MAX_STRING_LENGTH:
            raise _invalid()
        return value
    if type(value) not in (dict, list):
        raise _invalid()

    identity = id(value)
    if identity in active:
        raise _invalid()
    active.add(identity)
    try:
        if type(value) is list:
            sequence = cast("list[object]", value)
            if len(sequence) > _MAX_CONTAINER_ITEMS:
                raise _invalid()
            return [
                _copy_json(item, depth=depth + 1, budget=budget, active=active) for item in sequence
            ]
        mapping = cast("dict[object, object]", value)
        if len(mapping) > _MAX_CONTAINER_ITEMS:
            raise _invalid()
        copied: dict[str, JsonValue] = {}
        for key, item in mapping.items():
            if type(key) is not str or not key or len(key) > _MAX_STRING_LENGTH:
                raise _invalid()
            copied[key] = _copy_json(item, depth=depth + 1, budget=budget, active=active)
        return copied
    finally:
        active.remove(identity)


def _mapping(value: JsonValue) -> dict[str, JsonValue]:
    if type(value) is not dict:
        raise _invalid()
    return value


def _sequence(value: JsonValue) -> list[JsonValue]:
    if type(value) is not list:
        raise _invalid()
    return value


def _text(value: JsonValue) -> str:
    if type(value) is not str or not value:
        raise _invalid()
    return value


def _positive_integer(value: JsonValue) -> int:
    if type(value) is not int or value <= 0:
        raise _invalid()
    return value


def _positive_number(value: JsonValue) -> int | float:
    number = _finite_number(value)
    if number <= 0:
        raise _invalid()
    return number


def _finite_number(value: JsonValue) -> int | float:
    if type(value) not in (int, float):
        raise _invalid()
    return cast("int | float", value)


def _display_number(value: int | float) -> str:
    if type(value) is int:
        return str(value)
    floating = cast("float", value)
    if floating == 0:
        return "0"
    if floating.is_integer():
        return str(int(floating))
    rendered = repr(floating).lower()
    mantissa, marker, exponent_text = rendered.partition("e")
    if not marker:
        return rendered
    exponent = int(exponent_text)
    if abs(floating) >= 1e-6:
        sign = ""
        unsigned = mantissa
        if unsigned.startswith("-"):
            sign, unsigned = "-", unsigned[1:]
        whole, dot, fraction = unsigned.partition(".")
        digits = whole + (fraction if dot else "")
        decimal_index = len(whole) + exponent
        if decimal_index <= 0:
            return f"{sign}0.{('0' * -decimal_index)}{digits}"
        if decimal_index >= len(digits):
            return f"{sign}{digits}{'0' * (decimal_index - len(digits))}"
        return f"{sign}{digits[:decimal_index]}.{digits[decimal_index:]}"
    normalized_exponent = f"+{exponent}" if exponent >= 0 else str(exponent)
    return f"{mantissa}e{normalized_exponent}"


def _utc_timestamp(value: JsonValue) -> str:
    text = _text(value)
    if _UTC_INSTANT.fullmatch(text) is None:
        raise _invalid()
    try:
        datetime.fromisoformat(f"{text[:-1]}+00:00")
    except ValueError:
        raise _invalid() from None
    return text


def _scalar_text(value: JsonValue) -> str:
    """Render one scalar like the archived JavaScript ``String`` operation."""

    if type(value) is str:
        return value
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) in (int, float):
        return _display_number(cast("int | float", value))
    raise _invalid()


def _keys(
    value: dict[str, JsonValue],
    *,
    exact: frozenset[str] | None = None,
    allowed: frozenset[str] | None = None,
    required: frozenset[str] = frozenset(),
) -> None:
    present = frozenset(value)
    if exact is not None and present != exact:
        raise _invalid()
    if allowed is not None and not present.issubset(allowed):
        raise _invalid()
    if not required.issubset(present):
        raise _invalid()


def _validate_rule(value: JsonValue) -> None:
    rule = _mapping(value)
    operator = _text(rule.get("operator"))
    _text(rule.get("field"))
    shapes = {
        "gte": frozenset({"field", "operator", "min"}),
        "lte": frozenset({"field", "operator", "max"}),
        "lt": frozenset({"field", "operator", "max"}),
        "between": frozenset({"field", "operator", "min", "max"}),
        "eq": frozenset({"field", "operator", "value"}),
        "in": frozenset({"field", "operator", "values"}),
    }
    shape = shapes.get(operator)
    if shape is None:
        raise _invalid()
    _keys(rule, exact=shape)
    if operator in {"gte", "between"}:
        _finite_number(rule.get("min"))
    if operator in {"lte", "lt", "between"}:
        _finite_number(rule.get("max"))
    if operator == "between" and _finite_number(rule["min"]) > _finite_number(rule["max"]):
        raise _invalid()
    if operator == "eq":
        _scalar_text(rule.get("value"))
    if operator == "in":
        for item in _sequence(rule.get("values")):
            _scalar_text(item)


def _validate_action(value: JsonValue, *, complete: bool = False) -> None:
    action = _mapping(value)
    action_keys = frozenset(
        {"lots", "atr_multiplier", "sl_atr_multiplier", "target_atr_multiplier"}
    )
    _keys(
        action,
        exact=action_keys if complete else None,
        allowed=None if complete else action_keys,
    )
    if not action:
        raise _invalid()
    if "lots" in action:
        _positive_integer(action["lots"])
    for field in ("atr_multiplier", "sl_atr_multiplier", "target_atr_multiplier"):
        if field in action:
            _positive_number(action[field])


def _validate_schedule(
    value: JsonValue,
    *,
    direction: str,
    engines: dict[str, JsonValue],
) -> set[str]:
    schedule = _mapping(value)
    if set(schedule) != {"1"}:
        raise _invalid()
    slot = _mapping(schedule["1"])
    _keys(slot, exact=frozenset({"start_time", "end_time", "engines"}))
    if slot["start_time"] != "00:00" or slot["end_time"] != "23:59":
        raise _invalid()
    entries = _sequence(slot["engines"])
    if not entries:
        raise _invalid()
    referenced: set[str] = set()
    seen_engines: set[str] = set()
    for raw_entry in entries:
        entry = _mapping(raw_entry)
        _keys(
            entry,
            allowed=frozenset({"engine_id", "accelerator_ids", "accelerator_overrides"}),
            required=frozenset({"engine_id", "accelerator_ids"}),
        )
        engine_id = _text(entry["engine_id"])
        engine = _mapping(engines.get(engine_id))
        if engine_id in seen_engines or engine.get("direction") != direction:
            raise _invalid()
        seen_engines.add(engine_id)
        accelerator_ids = tuple(_text(item) for item in _sequence(entry["accelerator_ids"]))
        if len(accelerator_ids) != len(set(accelerator_ids)):
            raise _invalid()
        overrides = _mapping(entry.get("accelerator_overrides", {}))
        if not set(overrides).issubset(accelerator_ids):
            raise _invalid()
        for override in overrides.values():
            _validate_action(override)
        referenced.update(accelerator_ids)
    return referenced


def _validate_portable_config(config: dict[str, JsonValue]) -> None:
    """Validate the complete frozen portable schema before selecting one route."""

    _keys(
        config,
        exact=frozenset(
            {
                "schema_version",
                "algo_name",
                "algo_version",
                "description",
                "engines",
                "regimes",
                "accelerators",
                "standard",
                "guardrails",
            }
        ),
    )
    if config["schema_version"] != 2:
        raise _invalid()
    for field in ("algo_name", "algo_version", "description"):
        _text(config[field])

    engines = _mapping(config["engines"])
    if not engines:
        raise _invalid()
    engine_keys = frozenset(
        {
            "engine_id",
            "name",
            "direction",
            "mode",
            "default_lots",
            "atr_multiplier",
            "sl_atr_multiplier",
            "target_atr_multiplier",
            "rules",
        }
    )
    for engine_id, raw_engine in engines.items():
        engine = _mapping(raw_engine)
        _keys(engine, exact=engine_keys)
        if engine["engine_id"] != engine_id or engine["direction"] not in {"BUY", "SELL"}:
            raise _invalid()
        _text(engine["name"])
        if engine["mode"] != "live":
            raise _invalid()
        _validate_action(
            {
                "lots": engine["default_lots"],
                "atr_multiplier": engine["atr_multiplier"],
                "sl_atr_multiplier": engine["sl_atr_multiplier"],
                "target_atr_multiplier": engine["target_atr_multiplier"],
            },
            complete=True,
        )
        rules = _sequence(engine["rules"])
        if not rules:
            raise _invalid()
        for rule in rules:
            _validate_rule(rule)

    regimes = _mapping(config["regimes"])
    if len(regimes) != 1:
        raise _invalid()
    referenced_accelerators: set[str] = set()
    regime_keys = frozenset(
        {"regime_id", "name", "enabled", "vix_min", "vix_max", "buy_schedule", "sell_schedule"}
    )
    for regime_id, raw_regime in regimes.items():
        regime = _mapping(raw_regime)
        _keys(regime, exact=regime_keys)
        if (
            regime["regime_id"] != regime_id
            or regime["enabled"] is not True
            or regime["vix_min"] is not None
            or regime["vix_max"] is not None
        ):
            raise _invalid()
        _text(regime["name"])
        referenced_accelerators.update(
            _validate_schedule(regime["buy_schedule"], direction="BUY", engines=engines)
        )
        referenced_accelerators.update(
            _validate_schedule(regime["sell_schedule"], direction="SELL", engines=engines)
        )

    accelerators = _mapping(config["accelerators"])
    if set(accelerators) != referenced_accelerators:
        raise _invalid()
    priorities: set[int] = set()
    accelerator_keys = frozenset(
        {
            "accelerator_id",
            "name",
            "priority",
            "mode",
            "enabled",
            "conditions",
            "condition_groups",
            "action",
        }
    )
    for accelerator_id, raw_accelerator in accelerators.items():
        accelerator = _mapping(raw_accelerator)
        _keys(
            accelerator,
            allowed=accelerator_keys,
            required=accelerator_keys - {"condition_groups"},
        )
        priority = _positive_integer(accelerator["priority"])
        if (
            accelerator["accelerator_id"] != accelerator_id
            or accelerator["enabled"] is not True
            or accelerator["mode"] != "live"
            or priority in priorities
        ):
            raise _invalid()
        priorities.add(priority)
        _text(accelerator["name"])
        conditions = _sequence(accelerator["conditions"])
        groups = _sequence(accelerator.get("condition_groups", []))
        if groups:
            if conditions:
                raise _invalid()
            group_ids: set[str] = set()
            for raw_group in groups:
                group = _mapping(raw_group)
                _keys(group, exact=frozenset({"group_id", "conditions"}))
                group_id = _text(group["group_id"])
                if group_id in group_ids:
                    raise _invalid()
                group_ids.add(group_id)
                group_rules = _sequence(group["conditions"])
                if not group_rules:
                    raise _invalid()
                for rule in group_rules:
                    _validate_rule(rule)
        else:
            if not conditions:
                raise _invalid()
            for rule in conditions:
                _validate_rule(rule)
        _validate_action(accelerator["action"], complete=True)

    standard = _mapping(config["standard"])
    _keys(
        standard,
        exact=frozenset({"lots", "target_atr_multiplier", "stock_target_overrides"}),
    )
    _positive_integer(standard["lots"])
    _positive_number(standard["target_atr_multiplier"])
    symbols: set[str] = set()
    for raw_override in _sequence(standard["stock_target_overrides"]):
        override = _mapping(raw_override)
        _keys(override, exact=frozenset({"symbol", "target_atr_multiplier"}))
        symbol = _text(override["symbol"]).strip().upper()
        if not symbol or symbol in symbols:
            raise _invalid()
        symbols.add(symbol)
        _positive_number(override["target_atr_multiplier"])

    guardrails = _mapping(config["guardrails"])
    _keys(
        guardrails,
        exact=frozenset({"one_active_trade_per_stock", "stock_universe", "event_calendar"}),
    )
    universe = _mapping(guardrails["stock_universe"])
    _keys(universe, exact=frozenset({"mode", "whitelist", "blocklist"}))
    calendar = _mapping(guardrails["event_calendar"])
    _keys(
        calendar,
        exact=frozenset({"enabled", "event_types", "days_before", "days_after", "action"}),
    )
    if (
        guardrails["one_active_trade_per_stock"] is not False
        or universe["mode"] != "all"
        or _sequence(universe["whitelist"])
        or _sequence(universe["blocklist"])
        or calendar["enabled"] is not False
        or _sequence(calendar["event_types"])
        or type(calendar["days_before"]) is not int
        or calendar["days_before"] != 0
        or type(calendar["days_after"]) is not int
        or calendar["days_after"] != 0
        or calendar["action"] != "block"
    ):
        raise _invalid()


__all__ = ["JsonValue", "StrategyInputError"]
