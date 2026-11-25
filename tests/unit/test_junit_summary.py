"""JUnit summaries classify failures while withholding diagnostic values."""

import importlib.util
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

pytestmark = pytest.mark.safety


def test_junit_grouping_redacts_environment_values_and_parameter_values():
    path = Path(__file__).parents[2] / "ops/ci/summarize_junit.py"
    spec = importlib.util.spec_from_file_location("junit_summary", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = ET.Element("testsuite")
    for _ in range(3):
        case = ET.SubElement(root, "testcase")
        ET.SubElement(
            case,
            "error",
            message=(
                "failed on setup with ValidationError: 1 validation error for Settings\n"
                "DATABASE_X=offline-canary"
            ),
        )
    case = ET.SubElement(root, "testcase")
    ET.SubElement(case, "failure", message="AssertionError: offline-canary")
    case = ET.SubElement(root, "testcase", classname="tests.demo", name="test_fake[offline-canary]")
    ET.SubElement(case, "skipped", message="testnet opt-in offline-canary")
    summary = module.summarize(root)
    assert summary["counts"] == {"error": 3, "failure": 1, "skipped": 1}
    assert summary["error_phases"] == {"setup": 3}
    assert summary["top_groups"][0]["count"] == 3
    assert "offline-canary" not in json.dumps(summary)
    assert all(len(group["first_message_line"]) <= 160 for group in summary["top_groups"])
