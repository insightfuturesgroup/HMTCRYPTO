"""Typed, fail-closed configuration; Phase 1 never authorizes order writes."""

from enum import StrEnum
from hashlib import sha256
from typing import Any, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class ExecutionMode(StrEnum):
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE = "LIVE"


class DeltaRegion(StrEnum):
    INDIA = "india"
    GLOBAL = "global"


class DeltaEnvironment(StrEnum):
    TESTNET = "testnet"
    PROD = "prod"


class _DeltaEnvSource(EnvSettingsSource):
    """Reject misspelled/generic Delta variables while ignoring unrelated OS settings."""

    def __call__(self) -> dict[str, Any]:
        values = super().__call__()
        known = {
            str(field.validation_alias).lower() for field in self.settings_cls.model_fields.values()
        }
        for name, value in self.env_vars.items():
            if name.lower().startswith("delta_") and name.lower() not in known:
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
        hide_input_in_errors=True,
    )

    execution_mode: ExecutionMode = Field(default=ExecutionMode.PAPER, alias="EXECUTION_MODE")
    delta_region: DeltaRegion = Field(default=DeltaRegion.INDIA, alias="DELTA_REGION")
    delta_env: DeltaEnvironment = Field(default=DeltaEnvironment.TESTNET, alias="DELTA_ENV")
    delta_live_trading_enabled: bool = Field(default=False, alias="DELTA_LIVE_TRADING_ENABLED")
    delta_live_confirmation: SecretStr | None = Field(default=None, alias="DELTA_LIVE_CONFIRMATION")
    delta_india_testnet_api_key: SecretStr | None = Field(
        default=None, alias="DELTA_INDIA_TESTNET_API_KEY"
    )
    delta_india_testnet_api_secret: SecretStr | None = Field(
        default=None, alias="DELTA_INDIA_TESTNET_API_SECRET"
    )
    delta_india_prod_api_key: SecretStr | None = Field(
        default=None, alias="DELTA_INDIA_PROD_API_KEY"
    )
    delta_india_prod_api_secret: SecretStr | None = Field(
        default=None, alias="DELTA_INDIA_PROD_API_SECRET"
    )
    delta_global_testnet_api_key: SecretStr | None = Field(
        default=None, alias="DELTA_GLOBAL_TESTNET_API_KEY"
    )
    delta_global_testnet_api_secret: SecretStr | None = Field(
        default=None, alias="DELTA_GLOBAL_TESTNET_API_SECRET"
    )
    delta_global_prod_api_key: SecretStr | None = Field(
        default=None, alias="DELTA_GLOBAL_PROD_API_KEY"
    )
    delta_global_prod_api_secret: SecretStr | None = Field(
        default=None, alias="DELTA_GLOBAL_PROD_API_SECRET"
    )
    production_writes_forbidden: bool = Field(default=True, alias="PRODUCTION_WRITES_FORBIDDEN")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, _DeltaEnvSource(settings_cls), dotenv_settings, file_secret_settings)

    @property
    def rest_base_url(self) -> str:
        return {
            (DeltaRegion.INDIA, DeltaEnvironment.PROD): "https://api.india.delta.exchange",
            (DeltaRegion.INDIA, DeltaEnvironment.TESTNET): "https://cdn-ind.testnet.deltaex.org",
            (DeltaRegion.GLOBAL, DeltaEnvironment.PROD): "https://api.delta.exchange",
            (DeltaRegion.GLOBAL, DeltaEnvironment.TESTNET): "https://testnet-api.delta.exchange",
        }[self.delta_region, self.delta_env]

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
    def live_config_ready(self) -> bool:
        # The validator guarantees every LIVE instance meets all configuration gates.
        return self.execution_mode is ExecutionMode.LIVE

    @property
    def order_write_capable(self) -> bool:
        return False

    @model_validator(mode="after")
    def validate_selected_credentials_and_live_gates(self) -> Self:
        key, secret = self.selected_api_key, self.selected_api_secret
        if (key is None) != (secret is None):
            raise ValueError("Selected credential slot requires a complete API key/secret pair")
        if any(
            value is not None and not value.get_secret_value().strip() for value in (key, secret)
        ):
            raise ValueError("Selected credentials must not be empty or whitespace")
        if self.execution_mode is ExecutionMode.LIVE:
            if not self.delta_live_trading_enabled:
                raise ValueError("LIVE requires DELTA_LIVE_TRADING_ENABLED=true")
            if (
                self.delta_live_confirmation is None
                or self.delta_live_confirmation.get_secret_value()
                != "I_UNDERSTAND_THIS_CAN_PLACE_REAL_ORDERS"
            ):
                raise ValueError("LIVE requires the exact explicit confirmation")
            if key is None or secret is None:
                raise ValueError("LIVE requires credentials in the selected slot")
        return self

    def safe_diagnostics(self) -> dict[str, str | None]:
        """Expose no credential material; hash only the API key, never the secret."""
        key = self.selected_api_key
        fingerprint = (
            sha256(key.get_secret_value().encode("utf-8")).hexdigest()[:12] if key else None
        )
        return {
            "credential_slot": self.selected_credential_slot,
            "api_key_fingerprint": fingerprint,
        }
