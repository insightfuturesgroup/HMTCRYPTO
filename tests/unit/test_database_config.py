"""Database configuration must never disclose DSNs or weaken execution gates."""

import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from hmt_crypto.config import Settings


@pytest.fixture(autouse=True)
def clean_database_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in os.environ:
        if name.startswith(("DATABASE_", "DELTA_")) or name == "EXECUTION_MODE":
            monkeypatch.delenv(name)


def test_database_defaults_are_secret_and_lazy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None)
    assert isinstance(settings.database_url, SecretStr)
    assert settings.database_url.get_secret_value() == "sqlite+aiosqlite:///./hmt_crypto.db"
    assert settings.database_migration_url is None
    assert settings.database_ca_file is None
    assert settings.database_single_process is True
    assert list(tmp_path.iterdir()) == []


def test_runtime_ca_file_is_loaded_without_exposing_database_secret(tmp_path: Path):
    ca_file = tmp_path / "supabase-ca.crt"
    ca_file.write_text("fixture")
    settings = Settings(_env_file=None, DATABASE_CA_FILE=str(ca_file))
    assert settings.database_ca_file == str(ca_file)


def test_database_urls_are_redacted_everywhere():
    url = "postgresql+asyncpg://example:database-password@invalid.example/app"
    direct = "postgresql+asyncpg://owner:migration-password@invalid.example/app"
    settings = Settings(_env_file=None, DATABASE_URL=url, DATABASE_MIGRATION_URL=direct)
    assert settings.database_url.get_secret_value() == url
    assert settings.database_migration_url.get_secret_value() == direct
    rendered = repr(settings) + str(settings.model_dump()) + settings.model_dump_json()
    rendered += str(settings.safe_diagnostics())
    for sensitive in [url, direct, "database-password", "migration-password"]:
        assert sensitive not in rendered
    with pytest.raises(ValidationError):
        settings.database_url = SecretStr("sqlite+aiosqlite:///changed.db")


@pytest.mark.parametrize("field", ["DATABASE_URL", "DATABASE_MIGRATION_URL"])
@pytest.mark.parametrize(
    "value",
    [
        "private-invalid-dsn",
        "postgresql://user:private-invalid-dsn@invalid.example/db",
        "mysql+asyncmy://user:private-invalid-dsn@invalid.example/db",
        "sqlite+aiosqlite://private-invalid-dsn/db",
        "sqlite+aiosqlite:///db?private-invalid-dsn=yes",
        "sqlite+aiosqlite:///:memory:",
        "postgresql+asyncpg://user:private-invalid-dsn@invalid.example:bad/db",
    ],
)
def test_invalid_database_urls_produce_only_redacted_validation_errors(field: str, value: str):
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, **{field: value})
    rendered = str(error.value) + repr(error.value.errors()) + error.value.json()
    assert value not in rendered
    assert "private-invalid-dsn" not in rendered


@pytest.mark.parametrize("source", ["environment", "dotenv", "init"])
def test_misspelled_database_settings_fail_closed(source, monkeypatch, tmp_path):
    with pytest.raises(ValidationError) as error:
        if source == "environment":
            monkeypatch.setenv("DATABASE_SINGLE_PROCES", "private-database-setting")
            Settings(_env_file=None)
        elif source == "dotenv":
            path = tmp_path / ".env"
            path.write_text("DATABASE_SINGLE_PROCES=private-database-setting\n")
            Settings(_env_file=path)
        else:
            Settings(_env_file=None, DATABASE_SINGLE_PROCES="private-database-setting")
    assert "private-database-setting" not in str(error.value) + error.value.json()


@pytest.mark.parametrize("source", ["init", "environment", "dotenv"])
@pytest.mark.parametrize("value", ["true", "false", "True", "1", 1, True, False])
def test_single_process_flag_retains_strict_boolean_rules(
    source: str, value: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    valid = type(value) is bool if source == "init" else value in ("true", "false")

    def load() -> Settings:
        if source == "init":
            return Settings(_env_file=None, DATABASE_SINGLE_PROCESS=value)
        if source == "environment":
            monkeypatch.setenv("DATABASE_SINGLE_PROCESS", str(value))
            return Settings(_env_file=None)
        path = tmp_path / ".env"
        path.write_text(f"DATABASE_SINGLE_PROCESS={value}\n")
        return Settings(_env_file=path)

    if valid:
        assert load().database_single_process is (value is True or value == "true")
    else:
        with pytest.raises(ValidationError):
            load()
