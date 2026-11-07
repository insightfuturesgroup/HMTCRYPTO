"""Owner-only migration configuration must fail closed without leaking secrets."""

from __future__ import annotations

import os
import ssl
from types import FrameType, ModuleType, TracebackType

import pytest
from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg

from hmt_crypto.persistence import migration_configuration
from hmt_crypto.persistence.migration_configuration import (
    MigrationConfigurationError,
    comparable_database_target,
    load_migration_url,
    offline_migration_url,
)


@pytest.fixture(autouse=True)
def clean_database_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.upper() in {
            "DATABASE_MIGRATION_CA_FILE",
            "DATABASE_MIGRATION_URL",
            "DATABASE_URL",
        }:
            monkeypatch.delenv(name)


def _set_distinct_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://hmt_runtime:runtime-secret@db.example/hmt_crypto",
    )


def _traceback_retains_secret(error: BaseException, secret: str) -> bool:
    def retains(value: object, *, depth: int, seen_values: set[int]) -> bool:
        if id(value) in seen_values or depth > 8:
            return False
        seen_values.add(id(value))
        if type(value) is str:
            return secret in value
        if getattr(value, "password", None) == secret:
            return True
        if value is None or type(value) in (bool, bytes, float, int, type):
            return False
        if isinstance(value, (FrameType, ModuleType, TracebackType)) or callable(value):
            return False
        if isinstance(value, dict):
            return any(
                retains(item, depth=depth + 1, seen_values=seen_values)
                for pair in value.items()
                for item in pair
            )
        if isinstance(value, (list, tuple, set, frozenset)):
            return any(retains(item, depth=depth + 1, seen_values=seen_values) for item in value)
        attributes = getattr(value, "__dict__", None)
        return type(attributes) is dict and any(
            retains(item, depth=depth + 1, seen_values=seen_values) for item in attributes.values()
        )

    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
        if any(
            retains(value, depth=0, seen_values=set())
            for value in (*current.args, *vars(current).values())
        ):
            return True
        traceback = current.__traceback__
        while traceback is not None:
            module = traceback.tb_frame.f_globals.get("__name__")
            if module == "hmt_crypto.persistence.migration_configuration":
                if any(
                    retains(value, depth=0, seen_values=set())
                    for value in traceback.tb_frame.f_locals.values()
                ):
                    return True
            traceback = traceback.tb_next
    return False


class _FatalProbe(BaseException):
    pass


def test_runtime_identity_is_required_before_owner_credentials_are_admitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        "postgresql+asyncpg://owner:migration-secret@db.example/hmt_crypto",
    )
    with pytest.raises(MigrationConfigurationError, match="not safely configured"):
        load_migration_url()


def test_configuration_failures_do_not_retain_the_dsn_in_exception_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "migration-traceback-secret-canary"
    _set_distinct_runtime(monkeypatch)
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        f"postgresql+asyncpg://owner:{secret}@db.example/hmt_crypto?ssl=disable",
    )
    with pytest.raises(MigrationConfigurationError) as captured:
        load_migration_url()
    assert not _traceback_retains_secret(captured.value, secret)


def test_base_exception_during_url_parsing_is_sanitized_recursively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "migration-parse-interrupt-secret-canary"
    _set_distinct_runtime(monkeypatch)
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        f"postgresql+asyncpg://owner:{secret}@db.example/hmt_crypto",
    )

    def interrupt(_raw: str) -> object:
        raise _FatalProbe

    monkeypatch.setattr(migration_configuration, "make_url", interrupt)
    with pytest.raises(MigrationConfigurationError) as captured:
        load_migration_url()
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert not _traceback_retains_secret(captured.value, secret)


def test_tls_setup_failures_do_not_retain_the_structured_dsn_in_exception_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "migration-tls-traceback-secret-canary"
    _set_distinct_runtime(monkeypatch)
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        f"postgresql+asyncpg://owner:{secret}@db.example/hmt_crypto",
    )
    monkeypatch.setenv("DATABASE_MIGRATION_CA_FILE", "/missing/ca.pem")
    url = load_migration_url()
    with pytest.raises(MigrationConfigurationError) as captured:
        migration_configuration.migration_connect_args(url)
    assert not _traceback_retains_secret(captured.value, secret)


def test_base_exception_during_tls_setup_is_sanitized_recursively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "migration-tls-interrupt-secret-canary"
    _set_distinct_runtime(monkeypatch)
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        f"postgresql+asyncpg://owner:{secret}@db.example/hmt_crypto",
    )
    url = load_migration_url()

    def interrupt(*_args: object, **_kwargs: object) -> object:
        raise _FatalProbe

    monkeypatch.setattr(migration_configuration.ssl, "create_default_context", interrupt)
    with pytest.raises(MigrationConfigurationError) as captured:
        migration_configuration.migration_connect_args(url)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert not _traceback_retains_secret(captured.value, secret)


@pytest.mark.parametrize(
    "query",
    (
        "ssl=disable",
        "ssl=require",
        "ssl=verify-full",
        "sslmode=verify-full&sslrootcert=/tmp/ca.pem",
    ),
)
def test_driver_query_parameters_cannot_bypass_verified_connect_args(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    _set_distinct_runtime(monkeypatch)
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        f"postgresql+asyncpg://owner:migration-secret@db.example/hmt_crypto?{query}",
    )
    with pytest.raises(MigrationConfigurationError, match="not safely configured"):
        load_migration_url()


def test_online_connect_args_always_verify_certificate_and_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_distinct_runtime(monkeypatch)
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        "postgresql+asyncpg://owner:migration-secret@db.example/hmt_crypto",
    )
    builder = getattr(migration_configuration, "migration_connect_args", None)
    assert callable(builder), "verified asyncpg connect-argument builder is missing"
    arguments = builder(load_migration_url())
    assert set(arguments) == {"server_settings", "ssl"}
    assert arguments["server_settings"] == {"timezone": "UTC"}
    tls = arguments["ssl"]
    assert isinstance(tls, ssl.SSLContext)
    assert tls.verify_mode is ssl.CERT_REQUIRED
    assert tls.check_hostname is True


def test_missing_custom_ca_file_fails_without_exposing_its_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_distinct_runtime(monkeypatch)
    path = "/missing/ca/path-with-secret-canary.pem"
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        "postgresql+asyncpg://owner:migration-secret@db.example/hmt_crypto",
    )
    monkeypatch.setenv("DATABASE_MIGRATION_CA_FILE", path)
    with pytest.raises(MigrationConfigurationError) as error:
        migration_configuration.migration_connect_args(load_migration_url())
    assert path not in str(error.value) + repr(error.value)


@pytest.mark.parametrize(
    "runtime",
    (
        "postgresql+asyncpg://owner:runtime-secret@session.example/hmt_crypto",
        "postgresql+asyncpg://owner.projectref:runtime-secret@session.example/hmt_crypto",
    ),
)
def test_owner_role_cannot_be_reused_through_an_alternate_runtime_host(
    monkeypatch: pytest.MonkeyPatch,
    runtime: str,
) -> None:
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        "postgresql+asyncpg://owner:migration-secret@db.example/hmt_crypto",
    )
    monkeypatch.setenv("DATABASE_URL", runtime)
    with pytest.raises(MigrationConfigurationError):
        load_migration_url()


def test_runtime_and_migration_database_names_must_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        "postgresql+asyncpg://owner:migration-secret@db.example/hmt_crypto",
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://hmt_runtime:runtime-secret@db.example/other_database",
    )
    with pytest.raises(MigrationConfigurationError):
        load_migration_url()


def test_unrelated_runtime_host_is_an_ambiguous_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        "postgresql+asyncpg://owner:migration-secret@migration.example/hmt_crypto",
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://hmt_runtime:runtime-secret@unrelated.example/hmt_crypto",
    )
    with pytest.raises(MigrationConfigurationError):
        load_migration_url()


def test_matching_supabase_direct_and_session_pooler_target_is_admitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        "postgresql+asyncpg://postgres:migration-secret@db.projectref.supabase.co/postgres",
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://hmt_runtime.projectref:runtime-secret@"
        "aws-0-ap-south-1.pooler.supabase.com:5432/postgres",
    )
    assert load_migration_url().host == "db.projectref.supabase.co"


@pytest.mark.parametrize(
    "override",
    (
        "user=owner",
        "host=other.example",
        "database=other_database",
        "port=9999",
        "password=other_password",
    ),
)
def test_runtime_identity_overrides_are_rejected_using_asyncpg_semantics(
    monkeypatch: pytest.MonkeyPatch,
    override: str,
) -> None:
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        "postgresql+asyncpg://owner:migration-secret@db.example/hmt_crypto",
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://hmt_runtime:runtime-secret@db.example/hmt_crypto?" + override,
    )
    with pytest.raises(MigrationConfigurationError):
        load_migration_url()


def test_runtime_identity_is_compared_to_driver_resolved_connect_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        "postgresql+asyncpg://owner:migration-secret@db.example/hmt_crypto",
    )
    _set_distinct_runtime(monkeypatch)
    original = PGDialect_asyncpg.create_connect_args

    def redirect(self: PGDialect_asyncpg, url: object) -> tuple[list[object], dict[str, object]]:
        positional, arguments = original(self, url)  # type: ignore[arg-type]
        return positional, arguments | {"host": "driver-resolved.example"}

    monkeypatch.setattr(PGDialect_asyncpg, "create_connect_args", redirect)
    with pytest.raises(MigrationConfigurationError):
        load_migration_url()


def test_base_exception_during_target_resolution_is_sanitized_recursively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "target-resolution-interrupt-secret-canary"
    _set_distinct_runtime(monkeypatch)
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        f"postgresql+asyncpg://owner:{secret}@db.example/hmt_crypto",
    )
    url = load_migration_url()

    def interrupt(*_args: object, **_kwargs: object) -> object:
        raise _FatalProbe

    monkeypatch.setattr(PGDialect_asyncpg, "create_connect_args", interrupt)
    with pytest.raises(MigrationConfigurationError) as captured:
        comparable_database_target(url)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert not _traceback_retains_secret(captured.value, secret)


def test_non_identity_runtime_driver_option_does_not_obscure_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        "postgresql+asyncpg://owner:migration-secret@db.example/hmt_crypto",
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://hmt_runtime:runtime-secret@db.example/hmt_crypto?ssl=require",
    )
    assert load_migration_url().username == "owner"


def test_migration_url_is_loaded_only_from_the_exact_environment_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "database_migration_url",
        "postgresql+asyncpg://owner:lowercase-secret@db.example/hmt_crypto",
    )
    with pytest.raises(MigrationConfigurationError, match="not safely configured") as error:
        load_migration_url()
    assert "lowercase-secret" not in str(error.value)


def test_duplicate_case_variant_is_ambiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        "postgresql+asyncpg://owner:exact-secret@db.example/hmt_crypto",
    )
    monkeypatch.setenv(
        "database_migration_url",
        "postgresql+asyncpg://owner:lower-secret@db.example/hmt_crypto",
    )
    with pytest.raises(MigrationConfigurationError):
        load_migration_url()


@pytest.mark.parametrize(
    "value",
    [
        "sqlite+aiosqlite:///migration.db",
        "postgresql://owner:secret@db.example/hmt_crypto",
        "postgresql+psycopg://owner:secret@db.example/hmt_crypto",
        "postgresql+asyncpg:///hmt_crypto",
        "postgresql+asyncpg://db.example/hmt_crypto",
        "postgresql+asyncpg://owner@db.example/hmt_crypto",
        "postgresql+asyncpg://owner:secret@db.example",
        "postgresql+asyncpg://owner:secret@db.example:6543/hmt_crypto",
        "postgresql+asyncpg://owner:secret@aws-0-region.pooler.supabase.com/hmt_crypto",
        "postgresql+asyncpg://owner:secret@db.example/hmt_crypto?pgbouncer=true",
        "postgresql+asyncpg://owner:secret@db.example/hmt_crypto?statement_cache_size=0",
        "postgresql+asyncpg://owner:secret@db.example/hmt_crypto?host=other.example",
        "postgresql+asyncpg://owner:secret@db.example/hmt_crypto?application_name=alembic",
    ],
)
def test_unsafe_migration_targets_are_rejected_without_echoing_the_url(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DATABASE_MIGRATION_URL", value)
    with pytest.raises(MigrationConfigurationError) as error:
        load_migration_url()
    rendered = str(error.value) + repr(error.value)
    assert value not in rendered
    assert "secret" not in rendered


def test_migration_credentials_must_differ_from_runtime_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        "postgresql+asyncpg://owner:migration-secret@DB.EXAMPLE:5432/hmt_crypto",
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://owner:runtime-secret@db.example/hmt_crypto",
    )
    with pytest.raises(MigrationConfigurationError, match="not safely configured") as error:
        load_migration_url()
    assert "migration-secret" not in str(error.value)
    assert "runtime-secret" not in str(error.value)


def test_runtime_equivalence_check_is_case_insensitive_for_environment_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        "postgresql+asyncpg://owner:migration-secret@db.example/hmt_crypto",
    )
    monkeypatch.setenv(
        "database_url",
        "postgresql+asyncpg://owner:runtime-secret@DB.EXAMPLE:5432/hmt_crypto",
    )
    with pytest.raises(MigrationConfigurationError):
        load_migration_url()


def test_distinct_owner_role_on_same_database_is_admitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = "postgresql+asyncpg://owner:migration-secret@db.example:5432/hmt_crypto"
    monkeypatch.setenv("DATABASE_MIGRATION_URL", migration)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://hmt_runtime:runtime-secret@DB.EXAMPLE/hmt_crypto",
    )
    parsed = load_migration_url()
    assert parsed.drivername == "postgresql+asyncpg"
    assert parsed.username == "owner"
    assert parsed.password == "migration-secret"
    assert parsed.query == {}


def test_offline_conversion_uses_sync_driver_without_retaining_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        "postgresql+asyncpg://owner:p%40ss%2Bword@db.example:5432/hmt_crypto",
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://hmt_runtime:runtime-secret@db.example:5432/hmt_crypto",
    )
    asynchronous = load_migration_url()
    synchronous = offline_migration_url(asynchronous)
    assert synchronous.drivername == "postgresql"
    assert synchronous.username == asynchronous.username
    assert synchronous.password is None
    assert synchronous.host == asynchronous.host
    assert synchronous.database == asynchronous.database
    assert synchronous.query == {}


def test_base_exception_during_offline_conversion_is_sanitized_recursively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "migration-offline-interrupt-secret-canary"
    _set_distinct_runtime(monkeypatch)
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        f"postgresql+asyncpg://owner:{secret}@db.example/hmt_crypto",
    )
    url = load_migration_url()

    def interrupt(_url: object) -> None:
        raise _FatalProbe

    monkeypatch.setattr(migration_configuration, "_validate_owner_url", interrupt)
    with pytest.raises(MigrationConfigurationError) as captured:
        offline_migration_url(url)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert not _traceback_retains_secret(captured.value, secret)
