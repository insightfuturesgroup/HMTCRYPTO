"""B4 static private-read admission boundary tests."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]


def test_real_sealed_admission_scenarios_are_isolated_from_registry_owner() -> None:
    script = textwrap.dedent(
        """
        import sys
        sys.path.insert(0, ".")
        sys.path.insert(0, "src")
        from tests.unit.private_read_admission_test_support import run
        run()
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_admission_modules_are_dependency_light_and_non_authorizing() -> None:
    paths = (
        _ROOT / "src/hmt_crypto/exchange/delta/private_admission.py",
        _ROOT / "src/hmt_crypto/exchange/delta/private_admission_policy.py",
    )
    forbidden_import_roots = {
        "httpx",
        "requests",
        "socket",
        "sqlalchemy",
        "subprocess",
        "time",
    }
    forbidden_names = {
        "sign_at",
        "_sign_owned_private_read_at",
        "acquire",
        "acquire_mutation_once",
        "observe_nonblocking",
        "send",
    }
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=os.fspath(path))
        imports = {
            node.names[0].name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
        }
        imports.update(
            (node.module or "").split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        called = {
            node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
        }
        assert imports.isdisjoint(forbidden_import_roots)
        assert called.isdisjoint(forbidden_names)


def test_production_package_does_not_export_private_admission_authority() -> None:
    script = textwrap.dedent(
        """
        import sys
        sys.path.insert(0, "src")
        import hmt_crypto.exchange.delta as delta
        assert "PrivateReadAdmission" not in vars(delta)
        assert "PrivateReadAdmission" not in delta.__all__
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
