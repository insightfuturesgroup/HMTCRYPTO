from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr

from hmt_crypto.api.settings import ControlApiSettings, ControlRuntimeSettings


def _example(name: str) -> dict[str, str]:
    path = Path(__file__).resolve().parents[2] / "deploy" / name
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip("'")
    return values


def test_compose_control_api_profile_passes_the_real_security_policy() -> None:
    """The checked-in Compose profile must start behind its HTTPS edge."""
    values = _example("api.env.example")
    settings = ControlApiSettings(
        HMT_API_OPERATOR_PASSWORD_HASH=SecretStr(values["HMT_API_OPERATOR_PASSWORD_HASH"]),
        HMT_API_ALLOWED_ORIGIN=values["HMT_API_ALLOWED_ORIGIN"],
        HMT_API_BIND_HOST="0.0.0.0",
        HMT_API_COOKIE_SECURE=values["HMT_API_COOKIE_SECURE"] == "true",
        HMT_API_ALLOW_INSECURE_LOOPBACK=values["HMT_API_ALLOW_INSECURE_LOOPBACK"] == "true",
    )

    assert settings.allowed_origin.startswith("https://")
    assert settings.cookie_secure is True


def test_runtime_images_drop_root_before_entrypoint() -> None:
    """Both API and static-dashboard runtime stages must execute without root."""
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")

    python_runtime = dockerfile.split("FROM python:3.13.10-slim-bookworm AS python-runtime", 1)[1]
    api_prefix = python_runtime.split("FROM python-runtime AS api", 1)[0]
    assert "USER 10001:10001" in api_prefix

    ui_runtime = dockerfile.split("FROM nginx:1.28.0-alpine AS ui", 1)[1]
    assert "USER 101:101" in ui_runtime


def test_non_root_nginx_owns_its_ephemeral_runtime_mounts() -> None:
    root = Path(__file__).resolve().parents[2]
    for name in ("docker-compose.yml", "docker-compose.ec2.yml"):
        compose = (root / name).read_text(encoding="utf-8")
        ui = compose.split("  ui:\n", 1)[1].split("\n  caddy:\n", 1)[0]
        assert "/var/cache/nginx:size=16m,noexec,nosuid,nodev,uid=101,gid=101,mode=0750" in ui
        assert "/var/run:size=1m,noexec,nosuid,nodev,uid=101,gid=101,mode=0750" in ui


def test_ec2_migration_mounts_the_pinned_supabase_ca() -> None:
    root = Path(__file__).resolve().parents[2]
    compose = (root / "docker-compose.ec2.yml").read_text(encoding="utf-8")
    migration = _example("ec2/migration.env.example")

    assert "/etc/hmt/supabase-prod-ca.crt:/run/secrets/supabase-prod-ca.crt:ro" in compose
    assert migration["DATABASE_MIGRATION_CA_FILE"] == "/run/secrets/supabase-prod-ca.crt"
    migrate_service = compose.split("  migrate:\n", 1)[1].split("\n  api:\n", 1)[0]
    assert "network_mode: host" in migrate_service
    assert "networks:" not in migrate_service


def test_ec2_runtime_services_mount_and_use_the_pinned_supabase_ca() -> None:
    root = Path(__file__).resolve().parents[2]
    compose = (root / "docker-compose.ec2.yml").read_text(encoding="utf-8")
    api = _example("ec2/api.env.example")
    worker = _example("ec2/worker.env.example")

    # Migration, API, public worker, and private worker all use the pinned CA mount.
    assert compose.count("/etc/hmt/supabase-prod-ca.crt:/run/secrets/supabase-prod-ca.crt:ro") == 4
    assert api["DATABASE_CA_FILE"] == "/run/secrets/supabase-prod-ca.crt"
    assert worker["DATABASE_CA_FILE"] == "/run/secrets/supabase-prod-ca.crt"


def test_api_compose_service_receives_no_exchange_credentials() -> None:
    """The browser/control process must not inherit the worker secret environment."""
    root = Path(__file__).resolve().parents[2]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    api = compose.split("  api:\n", 1)[1].split("\n  migrate:\n", 1)[0]

    assert "env_file: .env" not in api
    assert "env_file: deploy/api.env" in api
    assert "DELTA_INDIA" not in api
    assert "DELTA_GLOBAL" not in api


def test_control_runtime_projection_never_requires_delta_credentials() -> None:
    """A LIVE-shaped read/control API loads scope and DB only, never a signer slot."""
    runtime = ControlRuntimeSettings(
        _env_file=None,
        EXECUTION_MODE="LIVE",
        DELTA_REGION="india",
        DELTA_ENV="prod",
        DATABASE_URL=SecretStr("postgresql+asyncpg://runtime:secret@db.example/hmt"),
        DATABASE_SINGLE_PROCESS=False,
    )

    assert runtime.execution_mode.value == "LIVE"
    assert runtime.delta_region.value == "india"
    assert runtime.delta_env.value == "prod"
    assert "api_key" not in type(runtime).model_fields
    assert "api_secret" not in type(runtime).model_fields
