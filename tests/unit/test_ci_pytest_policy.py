"""The CI exception policy must fail closed without changing assertions."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import ci_pytest_policy as policy


def test_expected_failures_contains_only_the_remaining_patch_1c_contract():
    expected = json.loads(Path("tools/ci_expected_failures.json").read_text())
    assert expected == {
        "tests/integration/test_execution_schema_metadata.py::"
        "test_execution_metadata_tables_and_compiles_postgres": (
            "Patch 1c: account foreign keys lack region/environment/mode columns; "
            "remove before go-live."
        )
    }
    assert (
        "tests/integration/test_identity_catalogue_schema.py::"
        "test_postgres_ddl_has_private_scoped_named_restrict_constraints" not in expected
    )


@pytest.mark.parametrize(
    "outcome,expected_bad", [("failed", False), ("passed", True), ("skipped", True)]
)
def test_expected_outcome_policy(monkeypatch, outcome, expected_bad):
    monkeypatch.setattr(policy, "EXPECTED", {"probe": "scope missing"})
    monkeypatch.setattr(policy, "seen", set())
    monkeypatch.setattr(policy, "violations", [])
    report = SimpleNamespace(
        when="call", failed=outcome == "failed", skipped=outcome == "skipped", outcome=outcome
    )
    item = SimpleNamespace(nodeid="probe", get_closest_marker=lambda name: None)
    call = SimpleNamespace(excinfo=SimpleNamespace(errisinstance=lambda kind: True))
    hook = policy.pytest_runtest_makereport(item, call)
    next(hook)
    with pytest.raises(StopIteration):
        hook.send(SimpleNamespace(get_result=lambda: report))
    assert bool(policy.violations) is expected_bad
    assert policy.seen == {"probe"}


def test_safety_deselection_fails_session(monkeypatch):
    monkeypatch.setattr(policy, "EXPECTED", {})
    monkeypatch.setattr(policy, "violations", [])
    policy.pytest_deselected(
        [SimpleNamespace(nodeid="probe", get_closest_marker=lambda name: True)]
    )
    session = SimpleNamespace(exitstatus=0)
    policy.pytest_sessionfinish(session, 0)
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
