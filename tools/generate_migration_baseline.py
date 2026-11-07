"""Verify the frozen Phase 2 Alembic history without importing live ORM metadata.

The original baseline generation step is deliberately retired. Historical
revisions are immutable; later model changes must be represented by a new
Alembic revision rather than by rewriting this baseline.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "migrations" / "versions" / "phase2_baseline_v1.py"
HARDENING_PATH = ROOT / "migrations" / "versions" / "phase2_hardening_v1.py"
MANIFEST_PATH = ROOT / "migrations" / "schema_manifest.json"


def _load(path: Path, name: str) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError("Frozen migration history cannot be loaded")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _fingerprint(statements: tuple[str, ...]) -> str:
    payload = json.dumps(statements, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _source_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verified() -> bool:
    try:
        manifest: dict[str, Any] = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        baseline = _load(BASELINE_PATH, "verified_phase2_baseline")
        hardening = _load(HARDENING_PATH, "verified_phase2_hardening")
        upgrade = tuple(baseline.UPGRADE_STATEMENTS)
        downgrade = (baseline.EMPTY_SCHEMA_GUARD, *baseline.DOWNGRADE_STATEMENTS)
        return bool(
            baseline.revision == "phase2_baseline_v1"
            and baseline.down_revision is None
            and hardening.revision == "phase2_hardening_v1"
            and hardening.down_revision == baseline.revision
            and manifest["revision"] == baseline.revision
            and manifest["head_revision"] == hardening.revision
            and manifest["upgrade_sha256"] == baseline.BASELINE_SHA256
            and manifest["upgrade_sha256"] == _fingerprint(upgrade)
            and manifest["baseline_downgrade_sha256"] == _fingerprint(downgrade)
            and manifest["baseline_source_sha256"] == _source_fingerprint(BASELINE_PATH)
            and manifest["hardening_source_sha256"] == _source_fingerprint(HARDENING_PATH)
        )
    except Exception:
        return False


def main() -> int:
    """Return success only when every frozen migration fingerprint matches."""

    parser = argparse.ArgumentParser(
        description="verify immutable migration artifacts; rewriting is intentionally unsupported"
    )
    parser.parse_args()
    if not _verified():
        parser.error("frozen migration artifacts do not match their reviewed manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
