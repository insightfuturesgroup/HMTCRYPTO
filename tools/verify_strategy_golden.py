#!/usr/bin/env python3
"""Verify the static HMT Strategy-v2 behavioral fixture without external authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn

SCHEMA_VERSION = "hmt-strategy-v2-golden/v1"
EXPECTED_CASE_IDS = frozenset(
    {
        "buy_engine_standard",
        "grouped_accelerator_first_group",
        "grouped_accelerator_second_group",
        "schedule_override_precedence",
        "execution_override_precedence",
        "stock_target_override",
        "sell_direction",
        "engine_rule_failure_diagnostics",
    }
)
EXPECTED_EXCLUSION_IDS = frozenset(
    {
        "india_vix_regimes",
        "india_session_calendar",
        "india_results_calendar",
        "kite_broker_behavior",
        "india_symbol_normalization",
        "india_lot_semantics",
    }
)
EXPECTED_BOUNDARY = {
    "network": "forbidden",
    "exchange": "forbidden",
    "database": "forbidden",
    "archived_runtime_dependency": "forbidden",
    "fixture_kind": "static_behavioral_contract",
}
HEX_DIGITS = frozenset("0123456789abcdef")
SENSITIVE_NEEDLES = ("api_key", "api-key", "access_token", "signature", "secret")


class FixtureError(ValueError):
    """Raised when a golden fixture violates its offline contract."""


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


def _require_mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{location} must be an object")
    return value


def _require_list(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(f"{location} must be an array")
    return value


def _require_sha256(value: Any, location: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - HEX_DIGITS:
        _fail(f"{location} must be a lowercase SHA-256 hex digest")
    return value


def _ids(rows: Sequence[Any], location: str, key: str) -> frozenset[str]:
    found: list[str] = []
    for index, row_value in enumerate(rows):
        row = _require_mapping(row_value, f"{location}[{index}]")
        item_id = row.get(key)
        if not isinstance(item_id, str) or not item_id:
            _fail(f"{location}[{index}].{key} must be a non-empty string")
        found.append(item_id)
    if len(found) != len(set(found)):
        _fail(f"{location} contains duplicate {key} values")
    return frozenset(found)


def _verify_digest(document: dict[str, Any]) -> None:
    declared = _require_sha256(document.get("content_sha256"), "content_sha256")
    unsigned = {key: value for key, value in document.items() if key != "content_sha256"}
    actual = _digest(unsigned)
    if declared != actual:
        _fail(f"content_sha256 mismatch: declared {declared}, computed {actual}")


def _verify_provenance(document: dict[str, Any]) -> None:
    provenance = _require_mapping(document.get("provenance"), "provenance")
    _require_sha256(provenance.get("archive_sha256"), "provenance.archive_sha256")
    _require_sha256(
        provenance.get("strategy_config_schema_sha256"),
        "provenance.strategy_config_schema_sha256",
    )
    commit = provenance.get("git_commit")
    if not isinstance(commit, str) or len(commit) != 40 or set(commit) - HEX_DIGITS:
        _fail("provenance.git_commit must be a lowercase Git object id")
    if provenance.get("exported_function") != "evaluateStrategyV2":
        _fail("provenance.exported_function must identify evaluateStrategyV2")

    sources = _require_list(provenance.get("source_files"), "provenance.source_files")
    if len(sources) != 4:
        _fail("provenance.source_files must contain the four audited sources")
    paths: set[str] = set()
    for index, source_value in enumerate(sources):
        source = _require_mapping(source_value, f"provenance.source_files[{index}]")
        if set(source) != {"path", "sha256"}:
            _fail(f"provenance.source_files[{index}] has unexpected fields")
        path = source.get("path")
        unsafe_path = (
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or ".." in Path(path).parts
        )
        if unsafe_path:
            _fail(f"provenance.source_files[{index}].path must be archive-relative")
        if path in paths:
            _fail(f"duplicate provenance source path: {path}")
        paths.add(path)
        _require_sha256(source.get("sha256"), f"provenance.source_files[{index}].sha256")

    adjacent = _require_list(
        provenance.get("reviewed_adjacent_files"), "provenance.reviewed_adjacent_files"
    )
    if len(adjacent) != 2:
        _fail("provenance.reviewed_adjacent_files must contain indicator source and test")
    for index, source_value in enumerate(adjacent):
        source = _require_mapping(source_value, f"provenance.reviewed_adjacent_files[{index}]")
        if set(source) != {"path", "sha256"}:
            _fail(f"provenance.reviewed_adjacent_files[{index}] has unexpected fields")
        path = source.get("path")
        if not isinstance(path, str) or path not in {
            "supabase/functions/_shared/indicatorRegistry.ts",
            "src/lib/__tests__/indicatorRegistry.test.ts",
        }:
            _fail(f"provenance.reviewed_adjacent_files[{index}].path is unexpected")
        _require_sha256(source.get("sha256"), f"provenance.reviewed_adjacent_files[{index}].sha256")

    indicator_review = _require_mapping(
        document.get("adjacent_indicator_review"), "adjacent_indicator_review"
    )
    if indicator_review.get("classification") != "reusable_with_changes":
        _fail("adjacent_indicator_review.classification is invalid")
    if indicator_review.get("fixture_status") != "reviewed_and_deferred_from_evaluator_golden":
        _fail("adjacent_indicator_review.fixture_status is invalid")


def _verify_portable_config(document: dict[str, Any]) -> None:
    config = _require_mapping(document.get("portable_config"), "portable_config")
    declared = _require_sha256(document.get("portable_config_sha256"), "portable_config_sha256")
    actual = _digest(config)
    if declared != actual:
        _fail(f"portable_config_sha256 mismatch: declared {declared}, computed {actual}")
    if config.get("schema_version") != 2:
        _fail("portable_config.schema_version must be 2")

    regimes = _require_mapping(config.get("regimes"), "portable_config.regimes")
    enabled = [
        regime
        for regime_value in regimes.values()
        if (regime := _require_mapping(regime_value, "portable_config.regime")).get("enabled")
    ]
    if len(enabled) != 1:
        _fail("portable_config must have exactly one enabled harness regime")
    if enabled[0].get("vix_min") is not None or enabled[0].get("vix_max") is not None:
        _fail("portable_config harness regime must be unbounded")


def _verify_case_coverage(document: dict[str, Any]) -> int:
    case_values = _require_list(document.get("cases"), "cases")
    if _ids(case_values, "cases", "case_id") != EXPECTED_CASE_IDS:
        _fail("cases do not match the reviewed portable golden set")

    cases = {case["case_id"]: case for case in case_values if isinstance(case, dict)}
    required_expected = {
        "passed",
        "status",
        "reason",
        "direction",
        "matched_engine_id",
        "matched_accelerator_id",
        "lots",
        "atr_multiplier",
        "sl_atr_multiplier",
        "target_atr_multiplier",
        "atr_multiplier_source",
        "sl_multiplier_source",
        "target_multiplier_source",
        "stock_target_override_symbol",
        "diagnostics",
    }
    for case_id, case in cases.items():
        _require_mapping(case.get("payload"), f"cases.{case_id}.payload")
        _require_mapping(case.get("options"), f"cases.{case_id}.options")
        expected = _require_mapping(case.get("expected"), f"cases.{case_id}.expected")
        if set(expected) != required_expected:
            _fail(f"cases.{case_id}.expected has an unexpected projection")
        diagnostics = _require_mapping(
            expected.get("diagnostics"), f"cases.{case_id}.expected.diagnostics"
        )
        if set(diagnostics) != {
            "engine_rules",
            "failed_rule",
            "accelerator_condition_mode",
            "accelerator_group_passes",
            "accelerator_evaluations",
            "final_action",
            "trace_statuses",
        }:
            _fail(f"cases.{case_id}.expected.diagnostics has an unexpected projection")

    sources = {
        expected.get(source_key)
        for case in cases.values()
        if isinstance((expected := case.get("expected")), dict)
        for source_key in (
            "atr_multiplier_source",
            "sl_multiplier_source",
            "target_multiplier_source",
        )
    }
    required_sources = {
        "engine",
        "accelerator",
        "schedule_override",
        "broker_override",
        "stock_override",
    }
    if not required_sources <= sources:
        _fail("cases do not cover every reviewed multiplier source")

    grouped = _require_mapping(
        cases["grouped_accelerator_second_group"]["expected"]["diagnostics"],
        "grouped accelerator diagnostics",
    )
    if grouped.get("accelerator_condition_mode") != "or_groups" or grouped.get(
        "accelerator_group_passes"
    ) != {"momentum": False, "quality": True}:
        _fail("grouped accelerator OR/AND characterization is missing")
    failure = _require_mapping(
        cases["engine_rule_failure_diagnostics"]["expected"]["diagnostics"],
        "engine failure diagnostics",
    )
    if not failure.get("failed_rule"):
        _fail("engine failure case must retain the first failed-rule diagnostic")
    return len(case_values)


def _verify_exclusions(document: dict[str, Any]) -> int:
    values = _require_list(document.get("exclusions"), "exclusions")
    if _ids(values, "exclusions", "id") != EXPECTED_EXCLUSION_IDS:
        _fail("exclusions do not match the reviewed India-only boundary")
    valid_classes = {"india_specific_must_replace", "not_needed_for_crypto"}
    for index, value in enumerate(values):
        item = _require_mapping(value, f"exclusions[{index}]")
        if item.get("classification") not in valid_classes:
            _fail(f"exclusions[{index}].classification is invalid")
        for key in ("excluded_behavior", "crypto_replacement"):
            if not isinstance(item.get(key), str) or not item[key]:
                _fail(f"exclusions[{index}].{key} must be a non-empty string")
    return len(values)


def _verify_no_sensitive_material(document: dict[str, Any]) -> None:
    serialized = json.dumps(document, ensure_ascii=False, sort_keys=True).lower()
    for needle in SENSITIVE_NEEDLES:
        if needle in serialized:
            _fail(f"fixture contains forbidden sensitive marker: {needle}")


def verify_fixture(path: Path) -> tuple[int, int]:
    """Validate integrity, provenance, portability, and coverage for one fixture."""

    document = _load(path)
    if document.get("schema_version") != SCHEMA_VERSION:
        _fail(f"schema_version must be {SCHEMA_VERSION}")
    if document.get("authority_boundary") != EXPECTED_BOUNDARY:
        _fail("authority_boundary does not match the offline fixture contract")
    _verify_digest(document)
    _verify_no_sensitive_material(document)
    _verify_provenance(document)
    _verify_portable_config(document)
    return _verify_case_coverage(document), _verify_exclusions(document)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path, help="path to the static golden JSON fixture")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the offline verifier CLI."""

    args = _parser().parse_args(argv)
    try:
        case_count, exclusion_count = verify_fixture(args.fixture)
    except (FixtureError, OSError, json.JSONDecodeError) as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1
    print(f"verified {SCHEMA_VERSION}: {case_count} cases, {exclusion_count} exclusions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
