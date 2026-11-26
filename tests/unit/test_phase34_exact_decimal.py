"""Phase34 exact-decimal migration contract."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations/versions/phase34_exact_decimal_v1.py"


def _load():
    spec = importlib.util.spec_from_file_location("phase34_exact_decimal", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_phase34_protects_exact_inventory_and_keeps_ratios_out() -> None:
    migration = _load()
    columns = {(table, column) for table, column, _old_type in migration.EXACT_COLUMNS}
    assert len(columns) == 19
    assert ("account_risk_policies", "usd_inr_rate") in columns
    assert not any("multiplier" in column or column.endswith("_pct") for _, column in columns)


def test_phase34_validates_before_acceptance_and_has_a_typed_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load()
    upgrade: list[str] = []
    monkeypatch.setattr(migration.op, "execute", upgrade.append)
    migration.upgrade()
    rendered = "\n".join(upgrade)
    assert rendered.count(" TYPE NUMERIC USING ") == 19
    assert rendered.count(" NOT VALID") == 57
    assert rendered.count(" VALIDATE CONSTRAINT ") == 57
    assert "'NaN', 'Infinity', '-Infinity'" in rendered
    assert 'trunc("usd_inr_rate", 18)' in rendered

    downgrade: list[str] = []
    monkeypatch.setattr(migration.op, "execute", downgrade.append)
    migration.downgrade()
    reverted = "\n".join(downgrade)
    assert reverted.count(" TYPE NUMERIC(38, 18) ") == 18
    assert reverted.count(" TYPE NUMERIC(18, 6) ") == 1
    assert reverted.count(" DROP CONSTRAINT ") == 57
