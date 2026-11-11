"""Lease-fenced recovery of one possible prior Delta Live write."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from hmt_crypto.api.live_order_recovery_http import (
    DeltaIndiaLiveOrderRecoveryAdapter,
    LiveOrderRecoveryUnavailable,
)
from hmt_crypto.persistence.db import Database
from hmt_crypto.worker.live_submission_queue import (
    claim_live_submission_recovery,
    record_uncertain_recovery_lookup,
)


@dataclass(frozen=True, slots=True)
class LiveRecoveryResult:
    kind: str
    submission_id: UUID | None


async def recover_one_live_submission(
    database: Database,
    *,
    account_id: UUID,
    owner_id: UUID,
    external_user_id: str,
    adapter: DeltaIndiaLiveOrderRecoveryAdapter,
) -> LiveRecoveryResult:
    """Perform one read-only lookup; absence never proves rejection."""
    if (
        type(account_id) is not UUID
        or account_id.int == 0
        or type(owner_id) is not UUID
        or owner_id.int == 0
        or type(external_user_id) is not str
        or not external_user_id.isdigit()
        or external_user_id.startswith("0")
        or len(external_user_id) > 19
    ):
        raise ValueError("invalid live recovery scope")
    claim = await claim_live_submission_recovery(database, account_id=account_id, owner_id=owner_id)
    if claim is None:
        return LiveRecoveryResult("IDLE", None)
    try:
        observation = await adapter.lookup(
            client_order_id=claim.client_order_id,
            expected_user_id=external_user_id,
        )
    except LiveOrderRecoveryUnavailable:
        return LiveRecoveryResult("UNAVAILABLE", claim.submission_id)
    await record_uncertain_recovery_lookup(database, claim, observation)
    return LiveRecoveryResult(observation.result, claim.submission_id)


__all__ = ["LiveRecoveryResult", "recover_one_live_submission"]
