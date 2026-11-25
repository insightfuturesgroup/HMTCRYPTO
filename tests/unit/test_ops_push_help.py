"""Read-only operator push checks, exercised with offline Git fakes."""

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.safety
ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize("scenario,expected", [("synced", 0), ("ahead", 1), ("missing", 1)])
def test_push_verification_is_read_only(tmp_path, scenario, expected):
    binary = tmp_path / "git"
    binary.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'case "$1" in\n'
        '  rev-parse) printf "%s\\n" "$FAKE_ROOT" ;;\n'
        '  branch) printf "codex/audit-fixes\\n" ;;\n'
        '  status) printf "## codex/audit-fixes...origin/codex/audit-fixes\\n" ;;\n'
        '  show-ref) test "$SCENARIO" != missing ;;\n'
        '  log) if [ "$SCENARIO" = ahead ]; then printf "abc1234 offline fixture\\n"; fi ;;\n'
        '  *) printf "Unexpected Git mutation or network command\\n" >&2; exit 99 ;;\n'
        "esac\n"
    )
    binary.chmod(0o700)
    result = subprocess.run(
        ["bash", str(ROOT / "ops/mac/00_verify_push.sh")],
        env={"PATH": f"{tmp_path}:/usr/bin:/bin", "FAKE_ROOT": str(tmp_path), "SCENARIO": scenario},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == expected
    assert "Unexpected Git" not in result.stderr
    assert ("PASS" in result.stdout) is (scenario == "synced")


def test_local_ops_configuration_is_ignored_without_reading_it():
    assert "/ops/local.env" in (ROOT / ".gitignore").read_text().splitlines()
