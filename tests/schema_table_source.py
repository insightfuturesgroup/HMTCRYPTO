"""Independent table discovery from the reviewed Alembic upgrade chain."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "migrations" / "versions"
BASELINE = VERSIONS / "phase2_baseline_v1.py"


def _upgrade_function(tree: ast.Module) -> ast.FunctionDef:
    functions = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
    ]
    assert len(functions) == 1
    return functions[0]


def migration_chain_tables() -> frozenset[str]:
    """Discover table names without importing application model metadata."""

    baseline_source = BASELINE.read_text(encoding="utf-8")
    tables = set(re.findall(r"CREATE TABLE hmt_crypto\.([a-z0-9_]+)", baseline_source))
    assert tables

    for path in sorted(VERSIONS.glob("*.py")):
        if path == BASELINE:
            continue
        upgrade = _upgrade_function(ast.parse(path.read_text(encoding="utf-8"), path.name))
        for node in ast.walk(upgrade):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not isinstance(node.func.value, ast.Name) or node.func.value.id != "op":
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            name = node.args[0].value
            if not isinstance(name, str):
                continue
            if node.func.attr == "create_table":
                tables.add(name)
            elif node.func.attr == "drop_table":
                tables.discard(name)
    return frozenset(tables)
