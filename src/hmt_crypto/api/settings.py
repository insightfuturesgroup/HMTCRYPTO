"""Typed control-API settings kept separate from exchange credentials."""

from __future__ import annotations

from typing import Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, SecretStr, StrictBool, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.persistence.configuration import DatabaseRuntimeConfig, database_url


def _environment_boolean(value: object) -> object:
    """Normalize only the two documented lowercase environment spellings."""
    if type(value) is str and value in {"true", "false"}:
        return value == "true"
    return value


class ControlRuntimeSettings(BaseSettings):
    """Venue scope and database settings with no Delta credential fields."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        case_sensitive=True,
        hide_input_in_errors=True,
    )

    execution_mode: ExecutionMode = Field(default=ExecutionMode.PAPER, alias="EXECUTION_MODE")
    delta_region: DeltaRegion = Field(default=DeltaRegion.INDIA, alias="DELTA_REGION")
    delta_env: DeltaEnvironment = Field(default=DeltaEnvironment.TESTNET, alias="DELTA_ENV")
    database_url: SecretStr = Field(
        default=SecretStr("sqlite+aiosqlite:///./hmt_crypto.db"), alias="DATABASE_URL"
    )
    database_ca_file: str | None = Field(default=None, alias="DATABASE_CA_FILE")
    database_single_process: StrictBool = Field(default=True, alias="DATABASE_SINGLE_PROCESS")

    @field_validator("database_single_process", mode="before")
    @classmethod
    def _runtime_boolean(cls, value: object) -> object:
        return _environment_boolean(value)

    @model_validator(mode="after")
    def _database_policy(self) -> Self:
        try:
            url = database_url(self.database_url)
        except ValueError:
            raise ConfigurationError() from None
        if url.drivername == "sqlite+aiosqlite" and (
            self.execution_mode is ExecutionMode.LIVE or not self.database_single_process
        ):
            raise ConfigurationError() from None
        return self

    def database_runtime_config(self) -> DatabaseRuntimeConfig:
        """Return the exact secret-minimal projection consumed by `Database`."""
        return DatabaseRuntimeConfig(
            execution_mode=self.execution_mode.value,
            database_url=self.database_url,
            database_single_process=self.database_single_process,
            database_ca_file=self.database_ca_file,
        )


class ControlApiSettings(BaseSettings):
    """Fail-closed single-operator HTTP settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        case_sensitive=True,
        hide_input_in_errors=True,
    )

    operator_password_hash: SecretStr = Field(alias="HMT_API_OPERATOR_PASSWORD_HASH")
    allowed_origin: str = Field(alias="HMT_API_ALLOWED_ORIGIN")
    bind_host: str = Field(default="127.0.0.1", alias="HMT_API_BIND_HOST")
    bind_port: int = Field(default=8000, ge=1, le=65535, alias="HMT_API_BIND_PORT")
    cookie_secure: StrictBool = Field(default=True, alias="HMT_API_COOKIE_SECURE")
    allow_insecure_loopback: StrictBool = Field(
        default=False, alias="HMT_API_ALLOW_INSECURE_LOOPBACK"
    )
    session_idle_seconds: int = Field(
        default=900, ge=60, le=86_400, alias="HMT_API_SESSION_IDLE_SECONDS"
    )
    session_absolute_seconds: int = Field(
        default=28_800, ge=300, le=2_592_000, alias="HMT_API_SESSION_ABSOLUTE_SECONDS"
    )
    max_sessions: int = Field(default=8, ge=1, le=32, alias="HMT_API_MAX_SESSIONS")
    login_attempts_per_five_minutes: int = Field(
        default=10, ge=1, le=100, alias="HMT_API_LOGIN_ATTEMPTS_PER_FIVE_MINUTES"
    )
    max_request_bytes: int = Field(
        default=32_768, ge=1024, le=1_048_576, alias="HMT_API_MAX_REQUEST_BYTES"
    )
    database_control_enabled: StrictBool = Field(
        default=False, alias="HMT_API_DATABASE_CONTROL_ENABLED"
    )
    embed_paper_worker: StrictBool = Field(default=False, alias="HMT_API_EMBED_PAPER_WORKER")
    account_id: UUID | None = Field(default=None, alias="HMT_API_ACCOUNT_ID")
    multi_user_enabled: StrictBool = Field(default=False, alias="HMT_API_MULTI_USER_ENABLED")
    supabase_url: str | None = Field(default=None, alias="HMT_SUPABASE_URL")
    supabase_publishable_key: SecretStr | None = Field(
        default=None, alias="HMT_SUPABASE_PUBLISHABLE_KEY"
    )
    supabase_secret_key: SecretStr | None = Field(default=None, alias="HMT_SUPABASE_SECRET_KEY")
    aws_region: str | None = Field(default=None, alias="HMT_AWS_REGION")
    delta_secret_prefix: str = Field(default="hmt-crypto/delta", alias="HMT_DELTA_SECRET_PREFIX")

    @field_validator(
        "cookie_secure",
        "allow_insecure_loopback",
        "database_control_enabled",
        "embed_paper_worker",
        "multi_user_enabled",
        mode="before",
    )
    @classmethod
    def _api_boolean(cls, value: object) -> object:
        return _environment_boolean(value)

    @field_validator("operator_password_hash")
    @classmethod
    def _argon2id_hash(cls, value: SecretStr) -> SecretStr:
        try:
            raw = value.get_secret_value()
        except BaseException:
            raise ConfigurationError() from None
        if not raw.startswith("$argon2id$") or not 32 <= len(raw) <= 1024:
            raise ConfigurationError() from None
        return value

    @field_validator("bind_host")
    @classmethod
    def _safe_bind_host(cls, value: str) -> str:
        if value not in {"127.0.0.1", "::1", "0.0.0.0"}:
            raise ConfigurationError() from None
        return value

    @model_validator(mode="after")
    def _security_policy(self) -> Self:
        try:
            origin = urlsplit(self.allowed_origin)
        except BaseException:
            raise ConfigurationError() from None
        if (
            not origin.scheme
            or not origin.hostname
            or origin.username is not None
            or origin.password is not None
            or origin.path not in {"", "/"}
            or origin.query
            or origin.fragment
        ):
            raise ConfigurationError() from None
        loopback = origin.hostname in {"127.0.0.1", "localhost", "::1"}
        if origin.scheme != "https":
            if not (
                origin.scheme == "http"
                and loopback
                and self.allow_insecure_loopback
                and not self.cookie_secure
                and self.bind_host in {"127.0.0.1", "::1"}
            ):
                raise ConfigurationError() from None
        if self.bind_host == "0.0.0.0" and (origin.scheme != "https" or not self.cookie_secure):
            raise ConfigurationError() from None
        if self.session_absolute_seconds < self.session_idle_seconds:
            raise ConfigurationError() from None
        if self.database_control_enabled != (self.account_id is not None):
            raise ConfigurationError() from None
        if self.embed_paper_worker and (
            not self.database_control_enabled or self.bind_host not in {"127.0.0.1", "::1"}
        ):
            raise ConfigurationError() from None
        if self.account_id is not None and self.account_id.int == 0:
            raise ConfigurationError() from None
        if self.multi_user_enabled:
            if (
                self.supabase_url is None
                or self.supabase_publishable_key is None
                or self.supabase_secret_key is None
                or self.aws_region is None
            ):
                raise ConfigurationError() from None
            parsed = urlsplit(self.supabase_url)
            if (
                parsed.scheme != "https"
                or parsed.hostname is None
                or not parsed.hostname.endswith(".supabase.co")
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ConfigurationError() from None
            if not self.delta_secret_prefix.startswith("hmt-crypto/"):
                raise ConfigurationError() from None
        return self


__all__ = ["ControlApiSettings", "ControlRuntimeSettings"]
