"""Built distributions exclude every Phase 3 tests-only support module."""

from __future__ import annotations

import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_phase3_test_support_is_absent_from_wheel_and_sdist(tmp_path: Path) -> None:
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
    for names in (wheel_names, sdist_names):
        assert not any("tests/" in name for name in names)
        assert not any("fake_websocket" in name for name in names)
