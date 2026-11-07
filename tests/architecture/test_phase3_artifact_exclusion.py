"""Built distributions preserve tests but exclude tests-only authority."""

from __future__ import annotations

import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _contains_path(names: set[str], relative: str) -> bool:
    return any(name == relative or name.endswith(f"/{relative}") for name in names)


def test_phase3_artifacts_keep_tests_non_executable_and_exclude_authority(
    tmp_path: Path,
) -> None:
    subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(tmp_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(tmp_path.glob("*.whl"))
    sdist = next(tmp_path.glob("*.tar.gz"))
    with zipfile.ZipFile(wheel) as archive:
        wheel_names = set(archive.namelist())
    with tarfile.open(sdist, "r:gz") as archive:
        sdist_names = set(archive.getnames())
    assert not any(name == "tests" or name.startswith("tests/") for name in wheel_names)
    assert not any("fake_websocket" in name for name in wheel_names)
    assert not any("/.superpowers/" in f"/{name}/" for name in wheel_names)

    assert _contains_path(sdist_names, "tests/unit/test_package.py")
    assert _contains_path(sdist_names, "tests/integration/support/fake_websocket.py")
    assert not _contains_path(
        sdist_names,
        "tests/integration/support/task210_worker_clock_issuer.py",
    )
    assert not _contains_path(
        sdist_names,
        "tests/integration/support/task27_private_credentials_issuer.py",
    )
    assert not any("/.superpowers/" in f"/{name}/" for name in sdist_names)
