"""Offline contract checks for the archived HMT Strategy-v2 golden fixture."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/strategy/hmt_strategy_v2_golden_v1.json"
VERIFIER = ROOT / "tools/verify_strategy_golden.py"

EXPECTED_CASE_IDS = {
    "buy_engine_standard",
    "grouped_accelerator_first_group",
    "grouped_accelerator_second_group",
    "schedule_override_precedence",
    "execution_override_precedence",
    "stock_target_override",
    "sell_direction",
    "engine_rule_failure_diagnostics",
}
EXPECTED_EXCLUSIONS = {
    "india_vix_regimes",
    "india_session_calendar",
    "india_results_calendar",
    "kite_broker_behavior",
    "india_symbol_normalization",
    "india_lot_semantics",
}


def _fixture_document() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _run_verifier(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFIER), str(path)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_fixture_declares_exact_portable_cases_and_exclusions() -> None:
    document = _fixture_document()

    assert document["schema_version"] == "hmt-strategy-v2-golden/v1"
    assert {case["case_id"] for case in document["cases"]} == EXPECTED_CASE_IDS
    assert {item["id"] for item in document["exclusions"]} == EXPECTED_EXCLUSIONS
    assert document["authority_boundary"] == {
        "network": "forbidden",
        "exchange": "forbidden",
        "database": "forbidden",
        "archived_runtime_dependency": "forbidden",
        "fixture_kind": "static_behavioral_contract",
    }


def test_fixture_binds_archived_source_provenance_without_secrets() -> None:
    document = _fixture_document()
    provenance = document["provenance"]

    assert provenance["archive_name"] == "hmt copy.zip"
    assert provenance["git_commit"] == "cae59521639108913037dbc99fd6545d8c86ca3a"
    assert provenance["exported_function"] == "evaluateStrategyV2"
    assert provenance["source_files"] == [
        {
            "path": "supabase/functions/_shared/strategyEvaluatorV2.ts",
            "sha256": "9e4c2f83ae48261a7f5032e25ccbefd7b1ae8a46d3e545b6d3151ed811d648d2",
        },
        {
            "path": "supabase/functions/_shared/strategyConfigTypes.ts",
            "sha256": "0242587622b9784518ed9900fb0b87aa282bd169a0f4b5f81c9663872cad8e8e",
        },
        {
            "path": "src/lib/strategyTemplates/algo615.ts",
            "sha256": "adb69aed8a98e3bbf26207f86495c49b7daa4e6298291377d056b59eb1c5862c",
        },
        {
            "path": "src/lib/__tests__/strategyEvaluatorV2.test.ts",
            "sha256": "2d85bdfccfe25bc7ff628aca77de0565094841e6b12648568d0ab86de2c900ac",
        },
    ]
    assert provenance["reviewed_adjacent_files"] == [
        {
            "path": "supabase/functions/_shared/indicatorRegistry.ts",
            "sha256": "68bf0050688adb229d32b8d99a767715f6d61330fe906873db2822c6c70a7363",
        },
        {
            "path": "src/lib/__tests__/indicatorRegistry.test.ts",
            "sha256": "c5ceb9d7b91d9aa2b8e1cfedd5123e614c36ee4677d33a2d65f5ff51e9bf4d2a",
        },
    ]
    assert document["adjacent_indicator_review"]["classification"] == "reusable_with_changes"
    assert document["adjacent_indicator_review"]["fixture_status"] == (
        "reviewed_and_deferred_from_evaluator_golden"
    )
    serialized = json.dumps(document, sort_keys=True).lower()
    for sensitive_name in ("api_key", "api-key", "secret", "signature", "access_token"):
        assert sensitive_name not in serialized


def test_fixture_covers_rules_groups_direction_multipliers_and_diagnostics() -> None:
    document = _fixture_document()
    cases = {case["case_id"]: case for case in document["cases"]}

    assert cases["buy_engine_standard"]["expected"]["direction"] == "BUY"
    assert cases["sell_direction"]["expected"]["direction"] == "SELL"
    assert (
        cases["grouped_accelerator_first_group"]["expected"]["diagnostics"][
            "accelerator_condition_mode"
        ]
        == "or_groups"
    )
    assert cases["grouped_accelerator_second_group"]["expected"]["diagnostics"][
        "accelerator_group_passes"
    ] == {"momentum": False, "quality": True}
    assert cases["engine_rule_failure_diagnostics"]["expected"]["diagnostics"]["failed_rule"]

    source_cases = {
        case_id: (
            case["expected"].get("atr_multiplier_source"),
            case["expected"].get("sl_multiplier_source"),
            case["expected"].get("target_multiplier_source"),
        )
        for case_id, case in cases.items()
    }
    assert source_cases["buy_engine_standard"] == ("engine", "engine", "engine")
    assert source_cases["grouped_accelerator_first_group"] == (
        "accelerator",
        "accelerator",
        "accelerator",
    )
    assert source_cases["schedule_override_precedence"] == (
        "schedule_override",
        "schedule_override",
        "schedule_override",
    )
    assert source_cases["execution_override_precedence"] == (
        "broker_override",
        "broker_override",
        "broker_override",
    )
    assert source_cases["stock_target_override"][2] == "stock_override"


def test_offline_verifier_accepts_the_committed_fixture() -> None:
    result = _run_verifier(FIXTURE)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "verified hmt-strategy-v2-golden/v1: 8 cases, 6 exclusions"


def test_offline_verifier_rejects_content_tampering(tmp_path: Path) -> None:
    document = _fixture_document()
    document["cases"][0]["expected"]["lots"] = 99
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(document), encoding="utf-8")

    result = _run_verifier(tampered)

    assert result.returncode == 1
    assert "content_sha256 mismatch" in result.stderr
