"""Migration tooling must validate configuration without leaking credentials."""

import ssl

import pytest
from sqlalchemy.engine import make_url

from hmt_crypto.api.settings import ControlRuntimeSettings
from hmt_crypto.persistence.migration_configuration import (
    MigrationConfigurationError,
    load_migration_url,
)
from tools.migrate_mumbai import build_environment, failure_diagnostic

pytestmark = pytest.mark.safety


def test_fake_environment_satisfies_runtime_loader_and_owner_guard(monkeypatch, tmp_path):
    ca = tmp_path / "ca.crt"
    ca.write_text("offline fixture; no TLS connection is attempted")
    values = build_environment(
        ca, "aws-0-ap-south-1.pooler.supabase.com", "owner@/%fixture", "runtime@/%fixture"
    )
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    settings = ControlRuntimeSettings(_env_file=None)
    runtime = settings.database_runtime_config()
    assert runtime.database_single_process is False
    assert runtime.database_ca_file == str(ca)
    assert runtime.execution_mode == "LIVE"
    assert make_url(values["DATABASE_URL"]).password == "runtime@/%fixture"
    assert load_migration_url().password == "owner@/%fixture"


@pytest.mark.parametrize(
    "error,category",
    [
        (ConnectionRefusedError("password=do-not-print"), "connection"),
        (ssl.SSLCertVerificationError("postgres://do-not-print"), "TLS"),
        (MigrationConfigurationError(), "guard"),
        (
            RuntimeError(
                "Database migration failed without exposing server diagnostics (SQLSTATE 28P01)"
            ),
            "authentication",
        ),
        (RuntimeError("password=do-not-print"), "redacted"),
    ],
)
def test_failure_diagnostic_never_renders_exception_values(error, category):
    result = failure_diagnostic(error)
    assert type(error).__name__ in result
    assert category in result
    assert "do-not-print" not in result
    assert "postgres://" not in result


def test_document_script_matches_executable_tool():
    from pathlib import Path

    root = Path(__file__).parents[2]
    embedded = (
        (root / "docs/MUMBAI_BOOTSTRAP.md")
        .read_text()
        .split("```python\n", 1)[1]
        .split("\n```", 1)[0]
    )
    assert embedded == (root / "tools/migrate_mumbai.py").read_text().rstrip()
