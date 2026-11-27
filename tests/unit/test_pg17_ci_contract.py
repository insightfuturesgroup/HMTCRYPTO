"""Mandatory database safety coverage belongs in the isolated full-suite job."""

from pathlib import Path
from types import SimpleNamespace

from tools import ci_pytest_policy as policy
from tools.check_schema_manifest_change import manifest_change_is_valid


def test_manifest_requires_migration_source_change():
    assert not manifest_change_is_valid(("migrations/schema_manifest.json",))
    assert manifest_change_is_valid(
        ("migrations/schema_manifest.json", "migrations/versions/new_revision.py")
    )


def test_database_acceptance_is_marked_safety():
    marked = []
    item = SimpleNamespace(
        fixturenames=["postgres_repository_db"], name="probe", add_marker=marked.append
    )
    policy.pytest_collection_modifyitems([item])
    assert len(marked) == 1
    assert marked[0].name == "safety"


def test_pg17_workflow_runs_full_suite_without_filtering():
    workflow = Path(".github/workflows/verify.yml").read_text()
    assert "postgres:17-bookworm" in workflow
    assert "postgres:16-bookworm" not in workflow
    job = workflow.split("  postgres-acceptance:", 1)[1].split("  deployment-shape:", 1)[0]
    assert "docker network create --internal" in job
    assert "python -m pytest -q -rs -p tools.ci_pytest_policy" in job
    assert " -k " not in job
    assert "HMT_CRYPTO_REQUIRE_MIGRATION_POSTGRES=true" in job
    assert "HMT_CRYPTO_REQUIRE_REPOSITORY_POSTGRES=true" in job
    assert "HMT_CRYPTO_REQUIRE_DECIMAL_POSTGRES=true" in job
