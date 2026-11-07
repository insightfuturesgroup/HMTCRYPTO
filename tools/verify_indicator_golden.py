#!/usr/bin/env python3
"""Verify the static HMT indicator fixture without HMT, Node, or external authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, NoReturn

SCHEMA_VERSION = "hmt-indicators-golden/v1"
EXPECTED_BOUNDARY = {
    "network": "forbidden",
    "exchange": "forbidden",
    "database": "forbidden",
    "archived_runtime_dependency": "forbidden",
    "fixture_kind": "static_behavioral_contract",
}
EXPECTED_ORACLE_FILES = [
    {
        "path": "src/lib/indicators.ts",
        "sha256": "c28375671109b6f416c8e50f04e63f78ead3d0afb42b0cacd308a30c0bf37698",
        "role": "hmt_wrapper_and_heikin_ashi_implementation",
    },
    {
        "path": "node_modules/technicalindicators/package.json",
        "sha256": "d0fa9b6e8b5fcf21801ab82a3098764e8185bea9fba3f8e61bafb9abb5863d61",
        "role": "vendored_oracle_manifest",
    },
    {
        "path": "node_modules/technicalindicators/dist/index.js",
        "sha256": "83890f53cc855c07e173e6ff27235e952e66d444dafa48b740fab8970092b059",
        "role": "vendored_oracle_runtime",
    },
]
EXPECTED_TEST_FILES = [
    {
        "path": "src/lib/__tests__/indicatorRegistry.test.ts",
        "sha256": "c5ceb9d7b91d9aa2b8e1cfedd5123e614c36ee4677d33a2d65f5ff51e9bf4d2a",
        "coverage": "configuration_only_not_math_oracle",
    }
]
CASE_SPECS = {
    "rsi": {
        "export": "calculateRSI",
        "parameters": {"period": 3},
        "inputs": ["close"],
        "outputs": ["value"],
        "first": 4,
        "length": 7,
    },
    "ema": {
        "export": "calculateEMA",
        "parameters": {"period": 3},
        "inputs": ["close"],
        "outputs": ["value"],
        "first": 3,
        "length": 8,
    },
    "sma": {
        "export": "calculateSMA",
        "parameters": {"period": 3},
        "inputs": ["close"],
        "outputs": ["value"],
        "first": 3,
        "length": 8,
    },
    "bollinger_bands": {
        "export": "calculateBollingerBands",
        "parameters": {"period": 3, "std_dev": 2},
        "inputs": ["close"],
        "outputs": ["upper", "middle", "lower", "pb"],
        "first": 3,
        "length": 8,
    },
    "adx": {
        "export": "calculateADX",
        "parameters": {"period": 3},
        "inputs": ["high", "low", "close"],
        "outputs": ["adx", "pdi", "mdi"],
        "first": 6,
        "length": 5,
    },
    "macd": {
        "export": "calculateMACD",
        "parameters": {"fast_period": 3, "slow_period": 5, "signal_period": 2},
        "inputs": ["close"],
        "outputs": ["macd", "signal", "histogram"],
        "first": 5,
        "length": 6,
    },
    "stochastic": {
        "export": "calculateStochastic",
        "parameters": {"period": 3, "signal_period": 2},
        "inputs": ["high", "low", "close"],
        "outputs": ["k", "d"],
        "first": 3,
        "length": 8,
    },
    "atr": {
        "export": "calculateATR",
        "parameters": {"period": 3},
        "inputs": ["high", "low", "close"],
        "outputs": ["value"],
        "first": 4,
        "length": 7,
    },
    "heikin_ashi": {
        "export": "calculateHeikinAshi",
        "parameters": {},
        "inputs": ["open", "high", "low", "close"],
        "outputs": ["open", "high", "low", "close"],
        "first": 1,
        "length": 10,
    },
}
HEX_DIGITS = frozenset("0123456789abcdef")
SENSITIVE_NEEDLES = ("api_key", "api-key", "access_token", "signature", "secret")
BASE_CASE_KEYS = {
    "indicator",
    "hmt_export",
    "input_fields",
    "parameters",
    "output_fields",
    "first_ready_input_length",
    "omitted_prefix_count",
    "alignment",
    "component_semantics",
    "warmup_probe",
    "tolerance",
    "expected",
}


class FixtureError(ValueError):
    """Raised when an indicator fixture violates its offline contract."""


def _fail(message: str) -> NoReturn:
    raise FixtureError(message)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    _fail(f"non-finite JSON number: {value}")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(value, dict):
        _fail("fixture root must be an object")
    _assert_finite(value, "fixture")
    return value


def _assert_finite(value: Any, location: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        _fail(f"{location} contains a non-finite number")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_finite(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_finite(item, f"{location}[{index}]")


def _mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{location} must be an object")
    return value


def _list(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(f"{location} must be an array")
    return value


def _sha256(value: Any, location: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - HEX_DIGITS:
        _fail(f"{location} must be a lowercase SHA-256 hex digest")
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _verify_digest(document: dict[str, Any]) -> None:
    declared = _sha256(document.get("content_sha256"), "content_sha256")
    unsigned = {key: value for key, value in document.items() if key != "content_sha256"}
    actual = _digest(unsigned)
    if declared != actual:
        _fail(f"content_sha256 mismatch: declared {declared}, computed {actual}")


def _verify_provenance(document: dict[str, Any]) -> None:
    provenance = _mapping(document.get("provenance"), "provenance")
    if provenance.get("archive_name") != "hmt copy.zip":
        _fail("provenance.archive_name is unexpected")
    _sha256(provenance.get("archive_sha256"), "provenance.archive_sha256")
    commit = provenance.get("git_commit")
    if not isinstance(commit, str) or len(commit) != 40 or set(commit) - HEX_DIGITS:
        _fail("provenance.git_commit must be a lowercase Git object id")
    if provenance.get("oracle_kind") != "archived_hmt_wrapper_plus_vendored_package":
        _fail("provenance.oracle_kind is unexpected")
    if provenance.get("oracle_package") != {
        "name": "technicalindicators",
        "version": "3.1.0",
    }:
        _fail("provenance.oracle_package is unexpected")
    if provenance.get("oracle_files") != EXPECTED_ORACLE_FILES:
        _fail("provenance.oracle_files do not match the archived oracle")
    if provenance.get("reviewed_test_files") != EXPECTED_TEST_FILES:
        _fail("provenance.reviewed_test_files do not match the archive")
    if provenance.get("direct_math_test_files") != []:
        _fail("direct_math_test_files must remain empty because none were archived")
    if provenance.get("direct_math_test_status") != "absent_from_archive":
        _fail("direct_math_test_status must record the archived coverage gap")


def _verify_input(document: dict[str, Any]) -> int:
    input_value = _mapping(document.get("input"), "input")
    if set(input_value) != {"candles"}:
        _fail("input must contain only candles")
    candles = _list(input_value.get("candles"), "input.candles")
    if len(candles) != 10:
        _fail("input.candles must contain the ten reviewed candles")
    timestamps: set[str] = set()
    for index, candle_value in enumerate(candles):
        candle = _mapping(candle_value, f"input.candles[{index}]")
        if set(candle) != {"timestamp", "open", "high", "low", "close", "volume"}:
            _fail(f"input.candles[{index}] has an unexpected shape")
        timestamp = candle.get("timestamp")
        if not isinstance(timestamp, str) or not timestamp:
            _fail(f"input.candles[{index}].timestamp must be a string")
        if timestamp in timestamps:
            _fail(f"input.candles[{index}].timestamp is duplicated")
        timestamps.add(timestamp)
        for field in ("open", "high", "low", "close", "volume"):
            value = candle.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                _fail(f"input.candles[{index}].{field} must be numeric")
        if candle["high"] < max(candle["open"], candle["close"]):
            _fail(f"input.candles[{index}].high is below its body")
        if candle["low"] > min(candle["open"], candle["close"]):
            _fail(f"input.candles[{index}].low is above its body")
    declared = _sha256(document.get("input_sha256"), "input_sha256")
    actual = _digest(candles)
    if declared != actual:
        _fail(f"input_sha256 mismatch: declared {declared}, computed {actual}")
    return len(candles)


def _verify_tolerance(value: Any, location: str) -> None:
    tolerance = _mapping(value, location)
    if set(tolerance) != {"absolute", "relative"}:
        _fail(f"{location} has an unexpected shape")
    for field in ("absolute", "relative"):
        raw = tolerance.get(field)
        try:
            parsed = Decimal(raw) if isinstance(raw, str) else Decimal("NaN")
        except InvalidOperation:
            parsed = Decimal("NaN")
        if not parsed.is_finite() or parsed <= 0 or parsed > Decimal("0.000001"):
            _fail(f"{location}.{field} must be a small positive decimal string")


def _verify_expected_rows(case: dict[str, Any], spec: dict[str, Any], location: str) -> None:
    rows = _list(case.get("expected"), f"{location}.expected")
    if len(rows) != spec["length"]:
        _fail(f"{location}.expected has the wrong output length")
    output_fields = spec["outputs"]
    if output_fields == ["value"]:
        for index, value in enumerate(rows):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                _fail(f"{location}.expected[{index}] must be numeric")
        return
    for index, row_value in enumerate(rows):
        row = _mapping(row_value, f"{location}.expected[{index}]")
        if set(row) != set(output_fields):
            _fail(f"{location}.expected[{index}] has an unexpected shape")
        for field, value in row.items():
            null_allowed = (case["indicator"], index, field) in {
                ("macd", 0, "signal"),
                ("macd", 0, "histogram"),
                ("stochastic", 0, "d"),
            }
            if value is None and null_allowed:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                _fail(f"{location}.expected[{index}].{field} must be numeric")


def _verify_cases(document: dict[str, Any], candle_count: int) -> int:
    values = _list(document.get("indicators"), "indicators")
    cases: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(values):
        case = _mapping(value, f"indicators[{index}]")
        name = case.get("indicator")
        if not isinstance(name, str) or name not in CASE_SPECS:
            _fail(f"indicators[{index}].indicator is unexpected")
        if name in cases:
            _fail(f"duplicate indicator: {name}")
        cases[name] = case
    if set(cases) != set(CASE_SPECS):
        _fail("indicators do not match the nine requested oracles")

    for name, case in cases.items():
        spec = CASE_SPECS[name]
        expected_keys = BASE_CASE_KEYS | (
            {"observed_empty_error"} if name == "heikin_ashi" else set()
        )
        if set(case) != expected_keys:
            _fail(f"indicators.{name} has an unexpected projection")
        for actual_key, spec_key in (
            ("hmt_export", "export"),
            ("parameters", "parameters"),
            ("input_fields", "inputs"),
            ("output_fields", "outputs"),
            ("first_ready_input_length", "first"),
        ):
            if case.get(actual_key) != spec[spec_key]:
                _fail(f"indicators.{name}.{actual_key} is unexpected")
        first_ready = spec["first"]
        if case.get("omitted_prefix_count") != first_ready - 1:
            _fail(f"indicators.{name}.omitted_prefix_count is unexpected")
        expected_alignment = (
            "one_output_per_input" if name == "heikin_ashi" else "compressed_valid_outputs_only"
        )
        if case.get("alignment") != expected_alignment:
            _fail(f"indicators.{name}.alignment is unexpected")
        if spec["length"] != candle_count - (first_ready - 1):
            _fail(f"indicators.{name} has inconsistent warmup arithmetic")
        probe = _mapping(case.get("warmup_probe"), f"indicators.{name}.warmup_probe")
        if name == "heikin_ashi":
            if probe != {"input_length": 0, "status": "throws", "error_name": "TypeError"}:
                _fail("heikin_ashi warmup probe must retain its archived empty-input error")
            if case.get("observed_empty_error") != "TypeError":
                _fail("heikin_ashi observed_empty_error is unexpected")
        elif probe != {"input_length": first_ready - 1, "status": "returns", "expected": []}:
            _fail(f"indicators.{name}.warmup_probe is unexpected")
        if not isinstance(case.get("component_semantics"), str) or not case["component_semantics"]:
            _fail(f"indicators.{name}.component_semantics must be non-empty")
        _verify_tolerance(case.get("tolerance"), f"indicators.{name}.tolerance")
        _verify_expected_rows(case, spec, f"indicators.{name}")
    return len(cases)


def _verify_document_contract(document: dict[str, Any]) -> None:
    if document.get("normalization") != {
        "undefined_object_fields": "json_null",
        "numbers": "unrounded_ieee754",
        "array_warmup": "no_null_padding",
    }:
        _fail("normalization does not match the reviewed JSON projection")
    if document.get("deferred") != []:
        _fail("deferred must be empty because every requested archived oracle was executable")
    exclusions = _list(document.get("scope_exclusions"), "scope_exclusions")
    if {item.get("export") for item in exclusions if isinstance(item, dict)} != {
        "calculateAllIndicators",
        "getIndicator",
    }:
        _fail("scope_exclusions do not match the reviewed direct-export boundary")
    serialized = json.dumps(document, ensure_ascii=False, sort_keys=True).lower()
    for needle in SENSITIVE_NEEDLES:
        if needle in serialized:
            _fail(f"fixture contains forbidden sensitive marker: {needle}")


def verify_fixture(path: Path) -> tuple[int, int]:
    """Validate integrity, provenance, output shapes, warmups, and tolerances."""

    document = _load(path)
    if document.get("schema_version") != SCHEMA_VERSION:
        _fail(f"schema_version must be {SCHEMA_VERSION}")
    if document.get("authority_boundary") != EXPECTED_BOUNDARY:
        _fail("authority_boundary does not match the offline fixture contract")
    _verify_digest(document)
    _verify_document_contract(document)
    _verify_provenance(document)
    candle_count = _verify_input(document)
    indicator_count = _verify_cases(document, candle_count)
    return indicator_count, len(document["deferred"])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path, help="path to the static indicator fixture")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the offline verifier CLI."""

    args = _parser().parse_args(argv)
    try:
        indicator_count, deferred_count = verify_fixture(args.fixture)
    except (FixtureError, OSError, json.JSONDecodeError) as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1
    print(f"verified {SCHEMA_VERSION}: {indicator_count} indicators, {deferred_count} deferred")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
