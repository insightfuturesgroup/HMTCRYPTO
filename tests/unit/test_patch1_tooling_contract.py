"""Patch 1 tooling and offline-verification contracts."""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _manifest_change_is_valid(paths: tuple[str, ...]) -> bool:
    path = ROOT / "tools" / "check_schema_manifest_change.py"
    spec = importlib.util.spec_from_file_location("check_schema_manifest_change", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.manifest_change_is_valid(paths)


def test_python_toolchain_is_pinned_to_the_supported_interpreter() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["requires-python"] == ">=3.13,<3.14"
    assert project["tool"]["ruff"]["target-version"] == "py313"
    assert project["tool"]["mypy"]["python_version"] == "3.13"
    assert any(
        requirement.startswith("hatchling")
        for requirement in project["project"]["optional-dependencies"]["dev"]
    )

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert dockerfile.count("FROM python:3.13.10-slim-bookworm") >= 2


def test_verify_workflow_runs_patch_branch_and_offline_test_containers() -> None:
    workflow_path = ROOT / ".github" / "workflows" / "verify.yml"
    source = workflow_path.read_text(encoding="utf-8")

    assert "pull_request:" in source
    assert "branches: [main, codex/audit-fixes]" in source
    assert 'python-version: "3.13.10"' in source
    assert "--network none" in source
    assert "check_schema_manifest_change.py" in source
    assert "DATABASE_MIGRATION_CA_FILE=/tmp/hmt-acceptance-postgres.crt" in source
    assert "ssl=on" in source
    assert "\n  deploy:" not in source


def test_schema_manifest_guard_is_present() -> None:
    guard = ROOT / "tools" / "check_schema_manifest_change.py"
    assert guard.is_file()


def test_schema_manifest_guard_requires_a_corresponding_migration_change() -> None:
    assert _manifest_change_is_valid(("README.md",))
    assert not _manifest_change_is_valid(("migrations/schema_manifest.json",))
    assert _manifest_change_is_valid(
        (
            "migrations/schema_manifest.json",
            "migrations/versions/phase99_example.py",
        )
    )


def test_schema_manifest_guard_recognizes_a_new_branch_push_base() -> None:
    path = ROOT / "tools" / "check_schema_manifest_change.py"
    spec = importlib.util.spec_from_file_location("check_schema_manifest_change", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.normalized_base("0" * 40) == "HEAD^"
    assert module.normalized_base("abc123") == "abc123"
