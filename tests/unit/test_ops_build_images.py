"""Ops-4b is a serial, pinned, secret-free host build contract."""

from pathlib import Path

SCRIPT = Path("ops/aws/20_build_images.sh")


def test_build_script_is_pinned_serial_and_cleans_temporary_swap() -> None:
    source = SCRIPT.read_text()
    assert "https://github.com/insightfuturesgroup/HMTCRYPTO.git" in source
    assert "expected_script_sha" in source
    assert "root_use <= 70" in source
    assert "CPUCreditBalance" not in source
    assert "cloudwatch" not in source.lower()
    assert "root disk use is above 70 percent" in source
    assert "fallocate -l 4G" in source
    assert "COMPOSE_PARALLEL_LIMIT=1" in source
    assert 'git -c safe.directory="$REPO_DIR"' in source
    assert "sudo env HMT_TLS_EMAIL=build-only@example.invalid docker compose" in source
    assert "for service in api worker private-worker ui" in source
    assert "docker builder prune --force" in source
    assert "trap cleanup_swap" in source
    assert "env_file" not in source


def test_build_runbook_has_safe_stall_and_deferred_digest_path() -> None:
    source = Path("docs/OPS_4B_BUILD_IMAGES.md").read_text()
    assert "RUN ON:" in source
    assert "FOLDER:" in source
    assert "PROMPTS:" in source
    assert "EXPECTED OUTPUT:" in source
    assert "FAILURE FORMAT:" in source
    assert "Do not\nloop" in source
    assert "post-UI" in source
