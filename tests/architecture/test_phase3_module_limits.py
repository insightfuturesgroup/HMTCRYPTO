"""Executable line ceilings and manifest for all current Phase 3 foundation paths."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION = {
    "src/hmt_crypto/exchange/delta/endpoints.py",
    "src/hmt_crypto/exchange/delta/websocket_contracts.py",
    "src/hmt_crypto/exchange/delta/websocket_codec.py",
    "src/hmt_crypto/exchange/delta/websocket_subscriptions.py",
    "src/hmt_crypto/exchange/delta/websocket_transport.py",
}
TEST_SUPPORT = {
    "tests/unit/test_websocket_endpoint_policy.py",
    "tests/unit/test_websocket_contracts.py",
    "tests/unit/test_websocket_codec.py",
    "tests/unit/test_websocket_subscriptions.py",
    "tests/unit/test_websocket_subscription_registry.py",
    "tests/unit/test_websocket_transport.py",
    "tests/integration/support/fake_websocket.py",
    "tests/architecture/test_phase3_module_limits.py",
    "tests/architecture/test_phase3_artifact_exclusion.py",
}


def test_phase3_manifest_covers_every_current_foundation_path() -> None:
    discovered = {
        path.relative_to(ROOT).as_posix()
        for base in (ROOT / "src", ROOT / "tests")
        for path in base.rglob("*websocket*.py")
    }
    discovered.add("src/hmt_crypto/exchange/delta/endpoints.py")
    discovered.update(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests/architecture").glob("test_phase3_*.py")
    )
    assert discovered == PRODUCTION | TEST_SUPPORT


def test_phase3_files_respect_reviewed_physical_line_ceilings() -> None:
    for relative in PRODUCTION:
        count = len((ROOT / relative).read_text().splitlines())
        assert count <= 500, (relative, count, "target")
        assert count <= 600, (relative, count, "hard")
    for relative in TEST_SUPPORT:
        count = len((ROOT / relative).read_text().splitlines())
        assert count <= 800, (relative, count)


def test_phase3_runtime_imports_have_no_eager_external_side_effects() -> None:
    script = r"""import asyncio
import builtins
import importlib
import os
import pathlib
import socket
import sys
import threading

sys.path.insert(0, "src")
targets = (
    "hmt_crypto.exchange.delta.websocket_contracts",
    "hmt_crypto.exchange.delta.websocket_subscriptions",
    "hmt_crypto.exchange.delta.websocket_codec",
    "hmt_crypto.exchange.delta.websocket_transport",
)
for target in targets:
    importlib.import_module(target)
for target in targets:
    del sys.modules[target]

def forbidden(*args, **kwargs):
    raise AssertionError("eager external side effect")

socket.socket = forbidden
asyncio.create_task = forbidden
threading.Thread.start = forbidden
builtins.open = forbidden
pathlib.Path.open = forbidden
pathlib.Path.read_text = forbidden
pathlib.Path.read_bytes = forbidden
os.getenv = forbidden
os._Environ.__getitem__ = forbidden

for target in targets:
    importlib.import_module(target)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
