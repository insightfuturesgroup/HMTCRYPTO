"""Disabled-by-default orchestration for one durable Delta Live submission."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from hmt_crypto.api.live_order_http import (
    DeltaIndiaLiveOrderHttpAdapter,
    _issue_live_dispatch_permit,
)
from hmt_crypto.api.live_order_transport import (
    LiveOrderResponseOutcome,
    classify_live_order_response,
)
from hmt_crypto.persistence.db import Database
from hmt_crypto.worker.live_submission_queue import (
    claim_next_live_submission,
    mark_live_submission_sending,
    prepare_claimed_live_submission,
    record_live_submission_outcome,
)


class LiveExecutionSettings(BaseSettings):
    """Independent activation evidence for the otherwise unreachable dispatcher."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        frozen=True,
        case_sensitive=True,
        hide_input_in_errors=True,
    )

    enabled: bool = Field(default=False, alias="HMT_LIVE_ORDER_DISPATCH_ENABLED")
    qualification_sha256: str | None = Field(
        default=None, alias="HMT_LIVE_ORDER_QUALIFICATION_SHA256"
    )

    @model_validator(mode="after")
    def _require_explicit_qualification(self) -> Self:
        digest = self.qualification_sha256
        if self.enabled and (
            type(digest) is not str
            or len(digest) != 64
            or digest == "0" * 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("live dispatch requires qualification evidence")
        return self


@dataclass(frozen=True, slots=True)
class LiveExecutionResult:
    kind: str
    submission_id: UUID | None


async def execute_one_live_submission(
    database: Database,
    *,
    settings: LiveExecutionSettings,
    account_id: UUID,
    owner_id: UUID,
    external_user_id: str,
    adapter: DeltaIndiaLiveOrderHttpAdapter,
) -> LiveExecutionResult:
    """Execute at most one claimed submission, with no transport retry path."""
    if (
        type(settings) is not LiveExecutionSettings
        or not settings.enabled
        or type(account_id) is not UUID
        or account_id.int == 0
        or type(owner_id) is not UUID
        or owner_id.int == 0
        or type(external_user_id) is not str
        or not 1 <= len(external_user_id) <= 128
    ):
        raise ValueError("live execution is not explicitly qualified")

    claim = await claim_next_live_submission(database, account_id=account_id, owner_id=owner_id)
    if claim is None:
        return LiveExecutionResult("IDLE", None)

    await prepare_claimed_live_submission(database, claim)
    sending = await mark_live_submission_sending(database, claim)
    permit = _issue_live_dispatch_permit(
        submission_id=claim.submission_id,
        body_sha256=sending.prepared.body_sha256,
        sending_evidence_sha256=sending.sending_evidence_sha256,
        expires_at=sending.permit_expires_at,
    )
    try:
        response = await adapter.send_once(sending.prepared, permit)
        outcome = classify_live_order_response(
            prepared=sending.prepared,
            status_code=response.status_code,
            body=response.body,
            external_user_id=external_user_id,
        )
    except Exception:
        outcome = LiveOrderResponseOutcome("UNCERTAIN", None)

    await record_live_submission_outcome(database, claim, outcome)
    return LiveExecutionResult(outcome.kind, claim.submission_id)


__all__ = ["LiveExecutionResult", "LiveExecutionSettings", "execute_one_live_submission"]
