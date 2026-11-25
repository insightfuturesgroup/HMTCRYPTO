"""Explicit CI exceptions and mandatory execution of safety tests.

Load with pytest -p tools.ci_pytest_policy. Assertions remain untouched.
"""

import json
from pathlib import Path

import pytest

EXPECTED = json.loads(Path(__file__).with_name("ci_expected_failures.json").read_text())
seen: set[str] = set()
violations: list[str] = []


def pytest_configure(config):
    seen.clear()
    violations.clear()
    config.addinivalue_line("markers", "safety: must execute and must never skip")


def pytest_deselected(items):
    violations.extend(
        f"safety test deselected: {item.nodeid}"
        for item in items
        if item.get_closest_marker("safety")
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    report = (yield).get_result()
    if report.skipped and item.get_closest_marker("safety"):
        violations.append(f"safety test skipped: {item.nodeid} ({report.when})")
    if item.nodeid not in EXPECTED:
        return
    if report.when == "call":
        seen.add(item.nodeid)
        # Only an assertion failure may match this documented exception.
        if report.failed and call.excinfo and call.excinfo.errisinstance(AssertionError):
            report.outcome = "skipped"
            report.wasxfail = EXPECTED[item.nodeid]
        else:
            violations.append(f"expected scoped-FK failure absent: {item.nodeid}")


def pytest_sessionfinish(session, exitstatus):
    violations.extend(f"expected test not executed: {nodeid}" for nodeid in EXPECTED.keys() - seen)
    if violations:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter):
    for violation in violations:
        terminalreporter.write_line(violation, red=True)
