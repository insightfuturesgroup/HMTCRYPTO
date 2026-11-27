"""Ops-6 has a fixed closed-gate, no-build start sequence."""

from pathlib import Path


def test_start_script_has_order_and_no_migration() -> None:
    source = Path("ops/aws/40_start_ui.sh").read_text()
    assert "tools/ec2_release_preflight.py" in source
    assert "disk <= 70" in source
    assert "memory >= 3500000" in source
    assert "--no-deps --no-build redis" in source
    assert "for service in api worker private-worker" in source
    assert "--no-deps --no-build ui" in source
    assert "--no-deps --no-build caddy" in source
    assert "up -d --no-deps --no-build migrate" not in source
    assert "--stop" in source
