"""Cleanup paths must satisfy name analysis without weakening error redaction."""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.safety
ROOT = Path(__file__).parents[2]
FILES = (
    "src/hmt_crypto/domain/_private_fill_contracts.py",
    "src/hmt_crypto/market_data/public_stream_control.py",
    "src/hmt_crypto/market_data/stream_control_owner.py",
)


def test_cleanup_paths_have_no_undefined_names():
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "F821", *FILES],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, "Cleanup-path F821 regression; run the targeted lint check"
