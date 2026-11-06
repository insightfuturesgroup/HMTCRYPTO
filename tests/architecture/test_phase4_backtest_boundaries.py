"""Executable size, authority, and import-safety boundaries for Phase 4 replay."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src/hmt_crypto/backtest"
TESTS = (
    ROOT / "tests/unit/test_backtest_engine.py",
    ROOT / "tests/unit/test_backtest_accounting_limits.py",
    ROOT / "tests/unit/test_backtest_models.py",
    ROOT / "tests/architecture/test_phase4_backtest_boundaries.py",
)
FORBIDDEN_IMPORTS = (
    "hmt_crypto.exchange",
    "hmt_crypto.persistence",
    "httpx",
    "sqlalchemy",
    "websockets",
)


def test_backtest_modules_and_tests_respect_review_ceiling() -> None:
    for path in PACKAGE.glob("*.py"):
        count = len(path.read_text().splitlines())
        assert count <= 600, (path.relative_to(ROOT), count)
    for path in TESTS:
        count = len(path.read_text().splitlines())
        assert count <= 800, (path.relative_to(ROOT), count)


def test_backtest_package_has_no_network_database_or_exchange_import_authority() -> None:
    imported: set[str] = set()
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)
    assert not any(
        name == forbidden or name.startswith(f"{forbidden}.")
        for name in imported
        for forbidden in FORBIDDEN_IMPORTS
    ), imported


def test_backtest_import_has_no_ambient_io_or_task_side_effects() -> None:
    script = r"""import asyncio
import builtins
import importlib
import os
import pathlib
import socket
import sys
import threading

sys.path.insert(0, "src")
importlib.import_module("hmt_crypto.backtest")
for name in tuple(sys.modules):
    if name == "hmt_crypto.backtest" or name.startswith("hmt_crypto.backtest."):
        del sys.modules[name]

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
importlib.import_module("hmt_crypto.backtest")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
