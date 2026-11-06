"""Isolated HTTP contract test for the sealed private-read owner graph."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

_ROOT = Path(__file__).resolve().parents[2]


def _run_isolated(entrypoint: str) -> subprocess.CompletedProcess[str]:
    script = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, ".")
        sys.path.insert(0, "src")
        from tests.unit.private_read_runtime_test_support import {entrypoint}
        {entrypoint}()
        print("ok")
        """
    )
    return subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_mock_transport_private_read_contract_runs_with_real_sealed_authorities() -> None:
    """Catch signed-read transport regressions without leaking a process-global owner."""
    result = _run_isolated("run")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_ip_whitelist_response_terminally_invalidates_only_the_credential() -> None:
    """Catch a documented IP auth rejection being reused or blamed on clock drift."""
    result = _run_isolated("run_unauthorized")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_refresh_authority_rejects_replayed_trusted_clock_observation() -> None:
    """Catch a retry or post-response phase silently reusing a stale instant."""
    result = _run_isolated("run_stale_clock_rejection")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
