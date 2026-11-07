"""Architecture limits for the pure kill-switch workflow."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULES = (
    "src/hmt_crypto/oms/_kill_switch_types.py",
    "src/hmt_crypto/oms/_kill_switch_values.py",
    "src/hmt_crypto/oms/kill_switch.py",
)
FORBIDDEN_PREFIXES = (
    "hmt_crypto.exchange",
    "hmt_crypto.persistence",
    "sqlalchemy",
    "httpx",
    "websockets",
)


def test_kill_switch_modules_remain_small() -> None:
    for relative in MODULES:
        lines = (ROOT / relative).read_text(encoding="utf-8").splitlines()
        assert len(lines) <= 500, relative


def test_kill_switch_state_machine_remains_layer_neutral() -> None:
    for relative in MODULES:
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.append(node.module)
        assert all(not name.startswith(FORBIDDEN_PREFIXES) for name in imported), relative
