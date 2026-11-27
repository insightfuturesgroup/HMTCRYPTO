"""The first-UI runbook preserves order and explicit blockers."""

from pathlib import Path


def test_first_ui_order_and_negative_requirements() -> None:
    source = Path("docs/FIRST_UI_CHECK.md").read_text()
    positions = [source.index(f"## {number}.") for number in range(1, 7)]
    assert positions == sorted(positions)
    assert "unauthenticated webhook request must\nbe rejected" in source
    assert "gate-open state" in source
    assert "ops/aws/40_start_ui.sh --stop" in source


def test_go_live_blockers_include_patch_and_digest_followup() -> None:
    source = Path("docs/GO_LIVE_CHECKLIST.md").read_text()
    for identifier in ("P1-01", "P1-02", "P1-03", "P1-04", "P1-06"):
        assert identifier in source
    assert "Patch 1c" in source
    assert "test_execution_metadata_tables_and_compiles_postgres" in source
    assert "CI-built ARM64 images" in source
