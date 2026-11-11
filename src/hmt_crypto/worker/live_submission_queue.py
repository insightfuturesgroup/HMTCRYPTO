"""Atomic lease-fenced claims for the disabled Live submission transport."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import desc, select

from hmt_crypto.api.live_submission import SubmissionFence, submission_event_sha256
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.models import (
    Account,
    AccountLiveControl,
    AccountRiskPolicy,
    LiveOrderIntent,
    LiveOrderPreview,
    LiveOrderSubmission,
    LiveOrderSubmissionEvent,
    PlatformLiveControl,
    PrivateReconciliationSnapshot,
    PrivateWorkerLease,
)


@dataclass(frozen=True, slots=True)
class ClaimedLiveSubmission:
    submission_id: UUID
    account_id: UUID
    intent_id: UUID
    preview_id: UUID
    client_order_id: str
    request_sha256: str
    fence: SubmissionFence
    claim_until: datetime


async def claim_next_live_submission(
    database: Database,
    *,
    account_id: UUID,
    owner_id: UUID,
    claim_seconds: int = 30,
) -> ClaimedLiveSubmission | None:
    """Claim one admitted submission only while every Live gate remains valid."""
    if (
        type(account_id) is not UUID
        or type(owner_id) is not UUID
        or account_id.int == 0
        or owner_id.int == 0
        or type(claim_seconds) is not int
        or not 5 <= claim_seconds <= 120
    ):
        raise ValueError("invalid live submission claim policy")
    now = datetime.now(UTC)
    async with database.transaction() as session:
        lease = await session.scalar(
            select(PrivateWorkerLease)
            .where(PrivateWorkerLease.account_id == account_id)
            .with_for_update()
        )
        account = await session.get(Account, account_id)
        control = await session.scalar(
            select(AccountLiveControl).where(AccountLiveControl.account_id == account_id)
        )
        global_control = await session.get(PlatformLiveControl, "global")
        risk = await session.scalar(
            select(AccountRiskPolicy).where(AccountRiskPolicy.account_id == account_id)
        )
        reconciliation = await session.scalar(
            select(PrivateReconciliationSnapshot)
            .where(PrivateReconciliationSnapshot.account_id == account_id)
            .order_by(desc(PrivateReconciliationSnapshot.observed_at))
            .limit(1)
        )
        if (
            lease is None
            or lease.owner_id != owner_id
            or lease.lease_until <= now
            or account is None
            or account.mode != "LIVE"
            or account.environment != "prod"
            or account.current_credential_version != lease.credential_version
            or account.identity_verified_at is None
            or control is None
            or control.credential_state != "verified"
            or control.reconciliation_state != "ready"
            or control.reconciled_at is None
            or control.reconciled_at < now - timedelta(seconds=60)
            or risk is None
            or not risk.enabled
            or reconciliation is None
            or reconciliation.outcome != "ready"
            or reconciliation.observed_at < now - timedelta(seconds=60)
            or reconciliation.worker_owner_id != lease.owner_id
            or reconciliation.worker_generation != lease.generation
            or reconciliation.credential_version != lease.credential_version
        ):
            return None
        submission = await session.scalar(
            select(LiveOrderSubmission)
            .where(
                LiveOrderSubmission.account_id == account_id,
                LiveOrderSubmission.status == "PENDING",
            )
            .order_by(LiveOrderSubmission.created_at, LiveOrderSubmission.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if submission is None:
            return None
        intent = await session.get(LiveOrderIntent, submission.intent_id)
        preview = None if intent is None else await session.get(LiveOrderPreview, intent.preview_id)
        if (
            intent is None
            or preview is None
            or preview.account_id != account_id
            or intent.created_at < now - timedelta(seconds=15)
            or preview.risk_policy_version != risk.version
            or (preview.reduce_only and not control.exits_enabled)
            or (
                not preview.reduce_only
                and (
                    not control.entries_enabled
                    or global_control is None
                    or not global_control.entries_enabled
                )
            )
        ):
            return None
        fence = SubmissionFence(owner_id, lease.generation, lease.credential_version)
        claim_until = min(lease.lease_until, now + timedelta(seconds=claim_seconds))
        submission.status = "CLAIMED"
        submission.worker_owner_id = fence.owner_id
        submission.worker_generation = fence.generation
        submission.credential_version = fence.credential_version
        submission.claim_until = claim_until
        submission.updated_at = now
        evidence = submission_event_sha256(
            submission_id=submission.id,
            sequence=1,
            from_status="PENDING",
            to_status="CLAIMED",
            fence=fence,
            occurred_at=now,
        )
        session.add(
            LiveOrderSubmissionEvent(
                id=uuid4(),
                submission_id=submission.id,
                account_id=account_id,
                sequence=1,
                from_status="PENDING",
                to_status="CLAIMED",
                worker_owner_id=fence.owner_id,
                worker_generation=fence.generation,
                credential_version=fence.credential_version,
                evidence_sha256=evidence,
                occurred_at=now,
            )
        )
        return ClaimedLiveSubmission(
            submission_id=submission.id,
            account_id=account_id,
            intent_id=intent.id,
            preview_id=preview.id,
            client_order_id=submission.client_order_id,
            request_sha256=submission.request_sha256,
            fence=fence,
            claim_until=claim_until,
        )


__all__ = ["ClaimedLiveSubmission", "claim_next_live_submission"]
