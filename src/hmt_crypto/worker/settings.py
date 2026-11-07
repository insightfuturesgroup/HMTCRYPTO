"""Typed settings for the credential-free PAPER/BACKTEST worker process."""

from __future__ import annotations

from typing import Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PaperWorkerSettings(BaseSettings):
    """Process identity and bounded scheduling settings for the offline worker."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        case_sensitive=True,
        hide_input_in_errors=True,
    )

    account_id: UUID = Field(alias="HMT_WORKER_ACCOUNT_ID")
    worker_id: UUID = Field(default_factory=uuid4, alias="HMT_WORKER_ID")
    poll_interval_milliseconds: int = Field(
        default=500,
        ge=50,
        le=10_000,
        alias="HMT_WORKER_POLL_INTERVAL_MILLISECONDS",
    )
    failure_backoff_milliseconds: int = Field(
        default=2_000,
        ge=100,
        le=60_000,
        alias="HMT_WORKER_FAILURE_BACKOFF_MILLISECONDS",
    )

    @model_validator(mode="after")
    def _nonzero_identities(self) -> Self:
        if self.account_id.int == 0 or self.worker_id.int == 0:
            raise ValueError("worker identities must be nonzero")
        return self


__all__ = ["PaperWorkerSettings"]
