"""Shared pytest fixtures retain identity without shadowing imported names."""

import ast
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1] / "integration"
NAMES = {"schema_db", "execution_db", "audit_service", "audit_sessions", "database"}
pytestmark = pytest.mark.safety


def test_fixture_import_registration_does_not_shadow_parameters():
    checked = 0
    for path in ROOT.glob("*.py"):
        tree = ast.parse(path.read_text())
        parameters = {
            arg.arg
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            for arg in node.args.args + node.args.kwonlyargs
        }
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom):
                continue
            for alias in node.names:
                if alias.name in NAMES and alias.asname:
                    checked += 1
                    assert alias.asname not in parameters, (path.name, alias.name)
    assert checked >= 40


def test_registered_fixture_aliases_keep_original_identity(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    checked = 0
    for path in ROOT.glob("*.py"):
        tree = ast.parse(path.read_text())
        imports = [
            (node.module, alias.name, alias.asname)
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.name in NAMES and alias.asname
        ]
        if not imports:
            continue
        module = importlib.import_module(path.stem)
        for source, original, imported in imports:
            assert getattr(module, original) is getattr(importlib.import_module(source), original)
            assert getattr(module, imported) is getattr(module, original)
            checked += 1
    assert checked >= 40
