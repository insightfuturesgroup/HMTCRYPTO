"""Built-artifact and production-graph boundaries for tests-only authority."""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
import tarfile
import textwrap
import tomllib
import zipfile
from collections.abc import Iterable
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_ISSUER = "tests/integration/support/task210_worker_clock_issuer.py"


def _contains_path(names: Iterable[str], relative: str) -> bool:
    return any(name == relative or name.endswith(f"/{relative}") for name in names)


def _built_artifact_names(tmp_path: Path) -> tuple[set[str], set[str]]:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--outdir",
            str(tmp_path),
        ],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    wheel = next(tmp_path.glob("*.whl"))
    sdist = next(tmp_path.glob("*.tar.gz"))
    with zipfile.ZipFile(wheel) as archive:
        wheel_names = set(archive.namelist())
    with tarfile.open(sdist, "r:gz") as archive:
        sdist_names = set(archive.getnames())
    return wheel_names, sdist_names


def test_task210_issuer_has_the_exact_cumulative_artifact_boundary(tmp_path: Path) -> None:
    wheel_names, sdist_names = _built_artifact_names(tmp_path)

    assert not any(name == "tests" or name.startswith("tests/") for name in wheel_names)
    assert not _contains_path(wheel_names, _ISSUER)
    assert not _contains_path(sdist_names, _ISSUER)
    assert _contains_path(sdist_names, "tests/unit/test_package.py")
    assert _contains_path(sdist_names, "tests/unit/test_private_worker_ownership.py")
    assert not any("/.superpowers/" in f"/{name}/" for name in sdist_names)


def test_sdist_exclusions_name_only_the_current_exact_authority_path() -> None:
    document = tomllib.loads((_ROOT / "pyproject.toml").read_text())
    exclusions = document["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"]

    assert exclusions == [f"/{_ISSUER}", "/.superpowers"]


def test_production_graph_never_imports_tests_support() -> None:
    imported_tests: list[tuple[Path, str]] = []
    for path in sorted((_ROOT / "src/hmt_crypto").rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_tests.extend(
                    (path, alias.name)
                    for alias in node.names
                    if alias.name == "tests" or alias.name.startswith("tests.")
                )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "tests" or module.startswith("tests."):
                    imported_tests.append((path, module))
    assert imported_tests == []


def test_worker_authorities_are_not_package_root_exports() -> None:
    package = importlib.import_module("hmt_crypto")
    worker = importlib.import_module("hmt_crypto.worker")

    assert worker.__all__ == []
    for name in (
        "Task210SinglePrivateWorkerProof",
        "Task210PrivateWorkerCurrentnessPort",
        "Task210TrustedDispatchClock",
        "Task210TrustedClockCurrentnessPort",
    ):
        assert not hasattr(package, name)
        assert not hasattr(worker, name)


def test_task210_source_tree_issuer_import_is_inert() -> None:
    script = textwrap.dedent(
        """
        import importlib
        import sys

        sys.path.insert(0, ".")
        sys.path.insert(0, "src")
        importlib.import_module("tests.integration.support.task210_worker_clock_issuer")
        clock = importlib.import_module("hmt_crypto.worker.clock")
        leadership = importlib.import_module("hmt_crypto.worker.leadership")
        assert clock._CLOCK_STATES == {}
        assert clock._CURRENTNESS_STATES == {}
        assert leadership._PROOF_STATES == {}
        assert leadership._CURRENTNESS_STATES == {}
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
