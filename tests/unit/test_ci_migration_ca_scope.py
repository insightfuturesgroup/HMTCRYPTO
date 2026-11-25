"""Keep migration-only TLS configuration out of ordinary suite settings."""

import importlib
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from hmt_crypto.config import Settings

pytestmark = pytest.mark.safety
ROOT = Path(__file__).parents[2]
ACCEPTANCE_CA = "HMT_CRYPTO_MIGRATION_ACCEPTANCE_CA_FILE"
MIGRATION_CA = "DATABASE_MIGRATION_CA_FILE"


def test_workflow_does_not_export_migration_ca_to_the_whole_suite():
    workflow = (ROOT / ".github/workflows/verify.yml").read_text()
    assert "-e DATABASE_MIGRATION_CA_FILE=" not in workflow
    assert "-e HMT_CRYPTO_MIGRATION_ACCEPTANCE_CA_FILE=" in workflow


def test_fake_ca_is_exposed_only_inside_the_migration_fixture(monkeypatch, tmp_path):
    monkeypatch.delenv(MIGRATION_CA, raising=False)
    monkeypatch.setenv(ACCEPTANCE_CA, "/tmp/offline-migration-ca.crt")
    # Settings must not open or validate this fake CA path.
    database_url = f"sqlite+aiosqlite:///{tmp_path}/offline.db"
    Settings(_env_file=None, DATABASE_URL=database_url)
    assert MIGRATION_CA not in os.environ
    monkeypatch.syspath_prepend(str(ROOT / "tests/integration"))
    module = importlib.import_module("test_migration_postgresql_acceptance")
    with monkeypatch.context() as scoped:
        module.migration_acceptance_ca.__wrapped__(scoped)
        assert os.environ[MIGRATION_CA] == "/tmp/offline-migration-ca.crt"
    assert MIGRATION_CA not in os.environ
    Settings(_env_file=None, DATABASE_URL=database_url)


def test_settings_still_rejects_globally_injected_migration_ca(monkeypatch, tmp_path):
    monkeypatch.setenv(MIGRATION_CA, "/tmp/offline-migration-ca.crt")
    with pytest.raises(ValidationError) as captured:
        Settings(_env_file=None, DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path}/offline.db")
    assert any(
        error["loc"] == (MIGRATION_CA,) and error["type"] == "extra_forbidden"
        for error in captured.value.errors(include_input=False)
    )


def test_test_image_copies_required_files_and_excludes_operator_configuration():
    dockerfile = (ROOT / "Dockerfile").read_text().split(" AS test\n", 1)[1]
    dockerfile = dockerfile.split("\nFROM ", 1)[0]
    for statement in ("COPY docs ./docs", "COPY ops ./ops", "COPY .gitignore ./.gitignore"):
        assert statement in dockerfile
    assert "ops/local.env" in (ROOT / ".dockerignore").read_text().splitlines()


@pytest.mark.parametrize(
    "relative_path",
    ["docs/MUMBAI_BOOTSTRAP.md", "ops/mac/00_verify_push.sh", ".gitignore"],
)
def test_required_image_files_exist_instead_of_silently_skipping(relative_path):
    assert (ROOT / relative_path).is_file()
