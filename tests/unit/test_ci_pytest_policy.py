"""The CI exception policy must fail closed without changing assertions."""

from types import SimpleNamespace

import pytest

from tools import ci_pytest_policy as policy


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
