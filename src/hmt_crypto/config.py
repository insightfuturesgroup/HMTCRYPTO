"""Typed, fail-closed configuration; Phase 1 never authorizes order writes."""

from enum import StrEnum
from hashlib import sha256
from typing import Any, Self, cast
from urllib.parse import urlsplit

from pydantic import (
    Field,
    ModelWrapValidatorHandler,
    SecretStr,
    StrictBool,
    ValidationError,
    model_validator,
)
from pydantic.fields import FieldInfo
from pydantic_core import InitErrorDetails
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from hmt_crypto._private_enums import _capture_foreign_exact_enum
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta.endpoints import get_delta_endpoints
from hmt_crypto.persistence.configuration import database_url

_STRICT_BOOLEAN_ALIASES = frozenset(
    {
        "DELTA_ALLOW_DEVELOPMENT_ENDPOINT_OVERRIDE",
        "DELTA_LIVE_TRADING_ENABLED",
        "PRODUCTION_WRITES_FORBIDDEN",
        "DATABASE_SINGLE_PROCESS",
    }
)


def _normalize_source_booleans(values: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(values)
    for alias in _STRICT_BOOLEAN_ALIASES:
        value = normalized.get(alias)
        if type(value) is str and value in {"true", "false"}:
            normalized[alias] = value == "true"
    return normalized


class ExecutionMode(StrEnum):
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE = "LIVE"


_capture_foreign_exact_enum(
    ExecutionMode,
    (("BACKTEST", "BACKTEST"), ("PAPER", "PAPER"), ("LIVE", "LIVE")),
)


class DeltaRegion(StrEnum):
    INDIA = "india"
    GLOBAL = "global"


_capture_foreign_exact_enum(DeltaRegion, (("INDIA", "india"), ("GLOBAL", "global")))


class DeltaEnvironment(StrEnum):
    TESTNET = "testnet"
    PROD = "prod"


_capture_foreign_exact_enum(DeltaEnvironment, (("TESTNET", "testnet"), ("PROD", "prod")))


class _DeltaEnvSource(EnvSettingsSource):
    """Reject unknown Delta/database variables while ignoring unrelated OS settings."""

    def __call__(self) -> dict[str, Any]:
        values = super().__call__()
        known = {str(field.validation_alias) for field in self.settings_cls.model_fields.values()}
        for name, value in self.env_vars.items():
            folded = name.upper()
            if name not in known and (
                folded in known or folded.startswith(("DELTA_", "DATABASE_"))
            ):
                values[name] = SecretStr(value) if value is not None else None
        return _normalize_source_booleans(values)


class _NormalizedSettingsSource(PydanticBaseSettingsSource):
    """Normalize documented text booleans for dotenv without weakening init values."""

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        wrapped: PydanticBaseSettingsSource,
    ) -> None:
        super().__init__(settings_cls)
        self._wrapped = wrapped

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self._wrapped.get_field_value(field, field_name)

    def __call__(self) -> dict[str, Any]:
        raw = self._wrapped()
        values = _normalize_source_booleans(
            {
                name: value
                for name, value in raw.items()
                if not name.startswith(("HMT_API_", "HMT_ALERT_", "HMT_WORKER_"))
            }
        )
        raw_values = getattr(self._wrapped, "env_vars", None)
        if type(raw_values) is dict:
            known = {
                str(field.validation_alias) for field in self.settings_cls.model_fields.values()
            }
            for name, value in raw_values.items():
                folded = name.upper()
                if name not in known and (
                    folded in known or folded.startswith(("DELTA_", "DATABASE_"))
                ):
                    values[name] = SecretStr(value) if value is not None else None
        return values


class Settings(BaseSettings):
    """Load explicit exchange slots without falling back to generic credentials.

    ``live_config_ready`` describes configuration only. Runtime clock, catalogue,
    reconciliation and transport policy are not implemented in this phase, so
    ``order_write_capable`` is always false, including on testnet.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        case_sensitive=True,
        hide_input_in_errors=True,
    )

    execution_mode: ExecutionMode = Field(default=ExecutionMode.PAPER, alias="EXECUTION_MODE")
    delta_region: DeltaRegion = Field(default=DeltaRegion.INDIA, alias="DELTA_REGION")
    delta_env: DeltaEnvironment = Field(default=DeltaEnvironment.TESTNET, alias="DELTA_ENV")
    delta_allow_development_endpoint_override: StrictBool = Field(
        default=False, alias="DELTA_ALLOW_DEVELOPMENT_ENDPOINT_OVERRIDE"
    )
    delta_development_rest_base_url: str | None = Field(
        default=None, alias="DELTA_DEVELOPMENT_REST_BASE_URL"
    )
    delta_live_trading_enabled: StrictBool = Field(
        default=False, alias="DELTA_LIVE_TRADING_ENABLED"
    )
    delta_live_confirmation: SecretStr | None = Field(default=None, alias="DELTA_LIVE_CONFIRMATION")
    delta_india_testnet_api_key: SecretStr | None = Field(
        default=None, alias="DELTA_INDIA_TESTNET_API_KEY"
    )
    delta_india_testnet_api_secret: SecretStr | None = Field(
        default=None, alias="DELTA_INDIA_TESTNET_API_SECRET"
    )
    delta_india_testnet_credential_file: SecretStr | None = Field(
        default=None, alias="DELTA_INDIA_TESTNET_CREDENTIAL_FILE"
    )
    delta_india_prod_api_key: SecretStr | None = Field(
        default=None, alias="DELTA_INDIA_PROD_API_KEY"
    )
    delta_india_prod_api_secret: SecretStr | None = Field(
        default=None, alias="DELTA_INDIA_PROD_API_SECRET"
    )
    delta_india_prod_credential_file: SecretStr | None = Field(
        default=None, alias="DELTA_INDIA_PROD_CREDENTIAL_FILE"
    )
    delta_global_testnet_api_key: SecretStr | None = Field(
        default=None, alias="DELTA_GLOBAL_TESTNET_API_KEY"
    )
    delta_global_testnet_api_secret: SecretStr | None = Field(
        default=None, alias="DELTA_GLOBAL_TESTNET_API_SECRET"
    )
    delta_global_testnet_credential_file: SecretStr | None = Field(
        default=None, alias="DELTA_GLOBAL_TESTNET_CREDENTIAL_FILE"
    )
    delta_global_prod_api_key: SecretStr | None = Field(
        default=None, alias="DELTA_GLOBAL_PROD_API_KEY"
    )
    delta_global_prod_api_secret: SecretStr | None = Field(
        default=None, alias="DELTA_GLOBAL_PROD_API_SECRET"
    )
    delta_global_prod_credential_file: SecretStr | None = Field(
        default=None, alias="DELTA_GLOBAL_PROD_CREDENTIAL_FILE"
    )
    production_writes_forbidden: StrictBool = Field(
        default=True, alias="PRODUCTION_WRITES_FORBIDDEN"
    )
    database_url: SecretStr = Field(
        default=SecretStr("sqlite+aiosqlite:///./hmt_crypto.db"), alias="DATABASE_URL"
    )
    database_ca_file: str | None = Field(default=None, alias="DATABASE_CA_FILE")
    database_migration_url: SecretStr | None = Field(default=None, alias="DATABASE_MIGRATION_URL")
    database_single_process: StrictBool = Field(default=True, alias="DATABASE_SINGLE_PROCESS")

    def __init__(self, **values: Any) -> None:
        """Reject ambiguous init names before the settings source drops one."""
        ambiguous = False
        for field_name, field in type(self).model_fields.items():
            alias = field.validation_alias
            if (
                type(alias) is str
                and alias != field_name
                and field_name in values
                and alias in values
            ):
                ambiguous = True
                break
        if ambiguous:
            values.clear()
            details = cast(
                list[InitErrorDetails],
                [
                    {
                        "type": "value_error",
                        "loc": (),
                        "input": None,
                        "ctx": {
                            "error": ValueError(
                                "Settings field and alias must not both be supplied"
                            )
                        },
                    }
                ],
            )
            raise ValidationError.from_exception_data(
                type(self).__name__, details, hide_input=True
            ) from None
        try:
            super().__init__(**values)
        finally:
            values.clear()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            _DeltaEnvSource(settings_cls),
            _NormalizedSettingsSource(settings_cls, dotenv_settings),
            file_secret_settings,
        )

    @property
    def rest_base_url(self) -> str:
        return get_delta_endpoints(
            self.delta_region,
            self.delta_env,
            development_rest_base_url=self.delta_development_rest_base_url,
            allow_development_override=self.delta_allow_development_endpoint_override,
            live_requested=self.execution_mode is ExecutionMode.LIVE,
        ).rest_base_url

    @property
    def selected_credential_slot(self) -> str:
        return f"DELTA_{self.delta_region.value.upper()}_{self.delta_env.value.upper()}"

    @property
    def selected_api_key(self) -> SecretStr | None:
        value: SecretStr | None = getattr(self, f"{self.selected_credential_slot.lower()}_api_key")
        return value

    @property
    def selected_api_secret(self) -> SecretStr | None:
        value: SecretStr | None = getattr(
            self, f"{self.selected_credential_slot.lower()}_api_secret"
        )
        return value

    @property
    def selected_credential_file(self) -> SecretStr | None:
        """Return only the selected slot's optional credential-file path."""
        value: SecretStr | None = getattr(
            self, f"{self.selected_credential_slot.lower()}_credential_file"
        )
        return value

    @property
    def live_config_ready(self) -> bool:
        # The validator guarantees every LIVE instance meets all configuration gates.
        return self.execution_mode is ExecutionMode.LIVE

    @property
    def order_write_capable(self) -> bool:
        return False

    @model_validator(mode="after")
    def validate_selected_credentials_and_live_gates(self) -> Self:
        database_url(self.database_url)
        if self.database_migration_url is not None:
            database_url(self.database_migration_url)
        key, secret = self.selected_api_key, self.selected_api_secret
        credential_file = self.selected_credential_file
        if (key is None) != (secret is None):
            raise ValueError("Selected credential slot requires a complete API key/secret pair")
        if any(
            value is not None and not value.get_secret_value().strip() for value in (key, secret)
        ):
            raise ValueError("Selected credentials must not be empty or whitespace")
        if credential_file is not None and not credential_file.get_secret_value().strip():
            raise ValueError("Selected credential-file path must not be empty or whitespace")
        if credential_file is not None and key is not None:
            raise ValueError(
                "Selected inline credentials and credential file are mutually exclusive"
            )
        if self.execution_mode is ExecutionMode.LIVE:
            if not self.delta_live_trading_enabled:
                raise ValueError("LIVE requires DELTA_LIVE_TRADING_ENABLED=true")
            if (
                self.delta_live_confirmation is None
                or self.delta_live_confirmation.get_secret_value()
                != "I_UNDERSTAND_THIS_CAN_PLACE_REAL_ORDERS"
            ):
                raise ValueError("LIVE requires the exact explicit confirmation")
            if (key is None or secret is None) and credential_file is None:
                raise ValueError("LIVE requires credentials in the selected slot")
        override = self.delta_development_rest_base_url
        allow_override = self.delta_allow_development_endpoint_override
        if (override is None) != (not allow_override):
            raise ValueError(
                "Development endpoint override requires both URL and explicit enable flag"
            )
        if override is not None:
            credential_values = (
                self.delta_india_testnet_api_key,
                self.delta_india_testnet_api_secret,
                self.delta_india_testnet_credential_file,
                self.delta_india_prod_api_key,
                self.delta_india_prod_api_secret,
                self.delta_india_prod_credential_file,
                self.delta_global_testnet_api_key,
                self.delta_global_testnet_api_secret,
                self.delta_global_testnet_credential_file,
                self.delta_global_prod_api_key,
                self.delta_global_prod_api_secret,
                self.delta_global_prod_credential_file,
            )
            if any(value is not None for value in credential_values):
                raise ValueError("Development endpoint override forbids real credentials")
            if self.execution_mode is ExecutionMode.LIVE:
                raise ValueError("Development endpoint override makes LIVE impossible")
            if self.delta_env is not DeltaEnvironment.TESTNET:
                raise ValueError("Development endpoint override is testnet-only")
            try:
                get_delta_endpoints(
                    self.delta_region,
                    self.delta_env,
                    development_rest_base_url=override,
                    allow_development_override=allow_override,
                    live_requested=False,
                )
            except ConfigurationError as error:
                raise ValueError("Development endpoint override is invalid") from error
        return self

    @model_validator(mode="before")
    @classmethod
    def reject_alias_and_field_name_duplicates(cls, values: Any) -> Any:
        """Reject ambiguous constructor payloads before Pydantic chooses a winner."""
        if type(values) is dict:
            for field_name, field in cls.model_fields.items():
                alias = field.validation_alias
                if type(alias) is str and alias != field_name:
                    if field_name in values and alias in values:
                        raise ValueError("Settings field and alias must not both be supplied")
        return values

    @model_validator(mode="wrap")
    @classmethod
    def redact_validation_error_inputs(
        cls, values: Any, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        """Keep raw source values out of every structured validation error.

        Pydantic's hide_input_in_errors only protects the human-readable error.
        Remove input payloads before rebuilding the exception so errors()/json()
        cannot expose credentials, including from rejected or mistyped fields.
        """
        try:
            return handler(values)
        except ValidationError as error:
            details = cast(list[InitErrorDetails], error.errors(include_input=False))
            raise ValidationError.from_exception_data(
                cls.__name__, details, hide_input=True
            ) from None

    def safe_diagnostics(self) -> dict[str, str | None]:
        """Expose no credential material; hash only the API key, never the secret."""
        key = self.selected_api_key
        fingerprint = (
            sha256(key.get_secret_value().encode("utf-8")).hexdigest()[:12] if key else None
        )
        diagnostics = {
            "credential_slot": self.selected_credential_slot,
            "api_key_fingerprint": fingerprint,
        }
        if self.delta_development_rest_base_url is not None:
            endpoints = get_delta_endpoints(
                self.delta_region,
                self.delta_env,
                development_rest_base_url=self.delta_development_rest_base_url,
                allow_development_override=True,
                live_requested=False,
            )
            if endpoints.is_development_override:
                diagnostics["development_rest_host"] = urlsplit(endpoints.rest_base_url).hostname
        return diagnostics
