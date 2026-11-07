"""Review boundaries for the pure operator-session security primitive."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION = {
    "src/hmt_crypto/api/_operator_session_values.py",
    "src/hmt_crypto/api/operator_sessions.py",
}


def test_operator_session_manifest_covers_the_session_primitive() -> None:
    discovered = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "src/hmt_crypto/api").glob("*operator_session*.py")
    }
    assert discovered == PRODUCTION


def test_operator_session_modules_stay_below_the_reviewed_line_ceiling() -> None:
    for relative in PRODUCTION:
        count = len((ROOT / relative).read_text(encoding="utf-8").splitlines())
        assert count <= 500, (relative, count)


def test_operator_session_modules_remain_pure_and_layer_neutral() -> None:
    forbidden = {
        "fastapi",
        "httpx",
        "requests",
        "socket",
        "sqlalchemy",
        "hmt_crypto.exchange",
        "hmt_crypto.persistence",
    }
    for relative in PRODUCTION:
        path = ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert not any(
            imported == boundary or imported.startswith(f"{boundary}.")
            for imported in imports
            for boundary in forbidden
        )


def test_operator_session_imports_have_no_eager_external_side_effects() -> None:
    script = r"""import builtins
import importlib
import os
import pathlib
import socket
import sys

sys.path.insert(0, "src")
targets = (
    "hmt_crypto.api._operator_session_values",
    "hmt_crypto.api.operator_sessions",
)
for target in targets:
    importlib.import_module(target)
for target in targets:
    del sys.modules[target]

def forbidden(*args, **kwargs):
    raise AssertionError("eager external side effect")

socket.socket = forbidden
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
