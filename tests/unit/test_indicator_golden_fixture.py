"""Offline checks for the archived HMT indicator golden fixture."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/indicators/hmt_indicators_golden_v1.json"
VERIFIER = ROOT / "tools/verify_indicator_golden.py"

EXPECTED_INDICATORS = {
    "rsi",
    "ema",
    "sma",
    "bollinger_bands",
    "adx",
    "macd",
    "stochastic",
    "atr",
    "heikin_ashi",
}
EXPECTED_FIRST_READY = {
    "rsi": 4,
    "ema": 3,
    "sma": 3,
    "bollinger_bands": 3,
    "adx": 6,
    "macd": 5,
    "stochastic": 3,
    "atr": 4,
    "heikin_ashi": 1,
}
EXPECTED_OUTPUT_LENGTH = {
    "rsi": 7,
    "ema": 8,
    "sma": 8,
    "bollinger_bands": 8,
    "adx": 5,
    "macd": 6,
    "stochastic": 8,
    "atr": 7,
    "heikin_ashi": 10,
}


def _document() -> dict[str, object]:
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


def test_fixture_has_exact_archive_and_oracle_provenance() -> None:
    document = _document()
    provenance = document["provenance"]

    assert document["schema_version"] == "hmt-indicators-golden/v1"
    assert provenance["archive_name"] == "hmt copy.zip"
    assert provenance["archive_sha256"] == (
        "96e6f61d6d49705d4643fadb012afeedec09a9857589819a0ebd660e7cdb8963"
    )
    assert provenance["git_commit"] == "cae59521639108913037dbc99fd6545d8c86ca3a"
    assert provenance["oracle_files"] == [
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
    assert provenance["reviewed_test_files"] == [
        {
            "path": "src/lib/__tests__/indicatorRegistry.test.ts",
            "sha256": "c5ceb9d7b91d9aa2b8e1cfedd5123e614c36ee4677d33a2d65f5ff51e9bf4d2a",
            "coverage": "configuration_only_not_math_oracle",
        }
    ]
    assert provenance["direct_math_test_files"] == []
    assert provenance["oracle_package"] == {"name": "technicalindicators", "version": "3.1.0"}


def test_fixture_covers_every_requested_indicator_without_deferred_results() -> None:
    document = _document()
    indicators = {case["indicator"]: case for case in document["indicators"]}

    assert set(indicators) == EXPECTED_INDICATORS
    assert document["deferred"] == []
    assert len(document["input"]["candles"]) == 10
    assert len({candle["timestamp"] for candle in document["input"]["candles"]}) == 10

    for name, case in indicators.items():
        assert case["first_ready_input_length"] == EXPECTED_FIRST_READY[name]
        assert case["omitted_prefix_count"] == EXPECTED_FIRST_READY[name] - 1
        assert len(case["expected"]) == EXPECTED_OUTPUT_LENGTH[name]
        assert case["tolerance"] == {
            "absolute": "0.000000001",
            "relative": "0.000000001",
        }


def test_fixture_preserves_warmup_missing_component_and_runtime_shape_semantics() -> None:
    document = _document()
    cases = {case["indicator"]: case for case in document["indicators"]}

    assert document["normalization"] == {
        "undefined_object_fields": "json_null",
        "numbers": "unrounded_ieee754",
        "array_warmup": "no_null_padding",
    }

    for name, case in cases.items():
        if name == "heikin_ashi":
            assert case["warmup_probe"] == {
                "input_length": 0,
                "status": "throws",
                "error_name": "TypeError",
            }
            assert case["alignment"] == "one_output_per_input"
        else:
            assert case["warmup_probe"] == {
                "input_length": EXPECTED_FIRST_READY[name] - 1,
                "status": "returns",
                "expected": [],
            }
            assert case["alignment"] == "compressed_valid_outputs_only"

    assert cases["macd"]["expected"][0] == {
        "macd": 1.4833333333333343,
        "signal": None,
        "histogram": None,
    }
    assert cases["stochastic"]["expected"][0]["d"] is None
    assert set(cases["bollinger_bands"]["expected"][0]) == {
        "upper",
        "middle",
        "lower",
        "pb",
    }


def test_offline_verifier_accepts_the_static_fixture() -> None:
    result = _run_verifier(FIXTURE)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "verified hmt-indicators-golden/v1: 9 indicators, 0 deferred"


def test_offline_verifier_rejects_expected_output_tampering(tmp_path: Path) -> None:
    document = _document()
    document["indicators"][0]["expected"][0] = 999
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(document), encoding="utf-8")

    result = _run_verifier(tampered)

    assert result.returncode == 1
    assert "content_sha256 mismatch" in result.stderr
