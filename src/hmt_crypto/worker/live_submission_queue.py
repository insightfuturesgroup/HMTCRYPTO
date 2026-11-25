"""Atomic lease-fenced claims for the disabled Live submission transport."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from prometheus_client import Counter
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from hmt_crypto.api.live_order_recovery import RecoveryObservation
from hmt_crypto.api.live_order_transport import (
    LiveOrderResponseOutcome,
    PreparedLiveOrder,
    prepare_live_order,
)
from hmt_crypto.api.live_submission import (
    SubmissionFence,
    recovery_check_sha256,
    require_transition,
    submission_event_sha256,
)
from hmt_crypto.logging import get_logger
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.models import (
    Account,
    AccountLiveControl,
    AccountRiskPolicy,
    LiveOrderIntent,
    LiveOrderPreview,
    LiveOrderRecoveryCheck,
    LiveOrderSubmission,
    LiveOrderSubmissionEvent,
    PlatformLiveControl,
    PrivateReconciliationSnapshot,
    PrivateWorkerLease,
)
from hmt_crypto.risk_operations import alert_uncertainty
from hmt_crypto.risk_policy import policy_rejection, record_rejection
from hmt_crypto.worker.scope import normalize_worker_uuid

RECOVERY_MISSING = Counter(
    "hmt_live_recovery_missing_evidence_total",
    "Recovery remains unresolved because required fill evidence is missing",
    ("reason",),
)


def _missing_recovery_evidence(reason: str) -> None:
    # Only fixed internal reason names reach this boundary; never log payloads.
    RECOVERY_MISSING.labels(reason=reason).inc()
    get_logger("worker.live_recovery").warning(
        "live_recovery_evidence_missing", reason=reason, outcome="UNCERTAIN"
    )


def _database_uuid(value: object) -> UUID:
    return normalize_worker_uuid(value, message="invalid database Live submission UUID")


def _database_fence(owner_id: object, generation: int, credential_version: int) -> SubmissionFence:
    return SubmissionFence(
        _database_uuid(owner_id),
        generation,
        credential_version,
    )


async def _next_submission_sequence(session: AsyncSession, submission_id: UUID) -> int:
    current = await session.scalar(
        select(func.max(LiveOrderSubmissionEvent.sequence)).where(
            LiveOrderSubmissionEvent.submission_id == submission_id
        )
    )
    return 1 if current is None else int(current) + 1


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


@dataclass(frozen=True, slots=True)
class SendingLiveSubmission:
    prepared: PreparedLiveOrder
    sending_evidence_sha256: str
    permit_expires_at: datetime


async def has_live_submission_recovery_debt(database: Database, *, account_id: UUID) -> bool:
    """Block new venue writes while an earlier possible write is unresolved."""
    if type(account_id) is not UUID or account_id.int == 0:
        raise ValueError("invalid live recovery-debt scope")
    async with database.transaction() as session:
        submission_id = await session.scalar(
            select(LiveOrderSubmission.id)
            .join(LiveOrderIntent, LiveOrderIntent.id == LiveOrderSubmission.intent_id)
            .where(
                LiveOrderSubmission.account_id == account_id,
                LiveOrderSubmission.status.in_(("SENDING", "UNCERTAIN")),
            )
            .limit(1)
        )
        return submission_id is not None


async def claim_live_submission_recovery(
    database: Database, *, account_id: UUID, owner_id: UUID
) -> ClaimedLiveSubmission | None:
    """Claim the oldest possible prior write under the current private-worker lease."""
    if (
        type(account_id) is not UUID
        or account_id.int == 0
        or type(owner_id) is not UUID
        or owner_id.int == 0
    ):
        raise ValueError("invalid live recovery claim scope")
    now = datetime.now(UTC)
    async with database.transaction() as session:
        lease = await session.scalar(
            select(PrivateWorkerLease)
            .where(PrivateWorkerLease.account_id == account_id)
            .with_for_update()
        )
        account = await session.get(Account, account_id)
        if (
            lease is None
            or lease.owner_id != owner_id
            or lease.lease_until <= now
            or account is None
            or account.current_credential_version != lease.credential_version
        ):
            return None
        submission = await session.scalar(
            select(LiveOrderSubmission)
            .join(LiveOrderIntent, LiveOrderIntent.id == LiveOrderSubmission.intent_id)
            .where(
                LiveOrderSubmission.account_id == account_id,
                LiveOrderSubmission.status.in_(("SENDING", "UNCERTAIN")),
            )
            .order_by(LiveOrderSubmission.created_at, LiveOrderSubmission.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if submission is None:
            return None
        intent = await session.get(LiveOrderIntent, submission.intent_id)
        preview = None if intent is None else await session.get(LiveOrderPreview, intent.preview_id)
        if intent is None or preview is None:
            raise ValueError("live recovery evidence is unavailable")
        active = _database_fence(lease.owner_id, lease.generation, lease.credential_version)
        if submission.status == "SENDING":
            submission.status = "UNCERTAIN"
            submission.last_error_code = "worker_recovery_required"
            submission.updated_at = now
            sequence = await _next_submission_sequence(session, _database_uuid(submission.id))
            evidence = submission_event_sha256(
                submission_id=_database_uuid(submission.id),
                sequence=sequence,
                from_status="SENDING",
                to_status="UNCERTAIN",
                fence=active,
                occurred_at=now,
            )
            session.add(
                LiveOrderSubmissionEvent(
                    id=uuid4(),
                    submission_id=submission.id,
                    account_id=submission.account_id,
                    sequence=sequence,
                    from_status="SENDING",
                    to_status="UNCERTAIN",
                    worker_owner_id=active.owner_id,
                    worker_generation=active.generation,
                    credential_version=active.credential_version,
                    evidence_sha256=evidence,
                    occurred_at=now,
                )
            )
        return ClaimedLiveSubmission(
            submission_id=_database_uuid(submission.id),
            account_id=submission.account_id,
            intent_id=intent.id,
            preview_id=preview.id,
            client_order_id=submission.client_order_id,
            request_sha256=submission.request_sha256,
            fence=active,
            claim_until=lease.lease_until,
        )


def _fill_recovery_observation(
    fills_payload: str | None,
    *,
    product_id: int,
    side: str,
    contracts: int,
    sent_at: datetime,
) -> RecoveryObservation | None:
    """Accept only one uniquely matching fully-filled Delta order."""
    if fills_payload is None:
        _missing_recovery_evidence("fills_payload")
        return None
    try:
        rows = json.loads(fills_payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(rows, list):
        return None
    grouped: dict[int, int] = {}
    earliest = sent_at - timedelta(seconds=2)
    for row in rows:
        if not isinstance(row, dict) or row.get("product_id") != product_id:
            continue
        if row.get("side") != side:
            continue
        order_id, size, created_at = row.get("order_id"), row.get("size"), row.get("created_at")
        if order_id is None:
            _missing_recovery_evidence("order_id")
            return None
        if size is None:
            _missing_recovery_evidence("size")
            return None
        if (
            not isinstance(order_id, int | str)
            or isinstance(order_id, bool)
            or not isinstance(size, int | str)
            or isinstance(size, bool)
        ):
            return None
        try:
            order_id = int(order_id)
            size = int(size)
            observed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if observed.tzinfo is None or observed.astimezone(UTC) < earliest:
            continue
        if order_id > 0 and size > 0:
            grouped[order_id] = grouped.get(order_id, 0) + size
    matches = [order_id for order_id, filled in grouped.items() if filled == contracts]
    if len(matches) != 1:
        return None
    return RecoveryObservation("FOUND", matches[0], "closed", contracts, 0, None)


async def recover_from_reconciliation(
    database: Database, claim: ClaimedLiveSubmission
) -> RecoveryObservation | None:
    """Resolve an uncertain market order from unique recent fill evidence."""
    async with database.transaction() as session:
        submission = await session.get(LiveOrderSubmission, claim.submission_id)
        if submission is not None and submission.status == "UNCERTAIN":
            alert_uncertainty(
                submission.sent_at,
                now=datetime.now(UTC),
                logger=get_logger("worker.live_recovery"),
                context={
                    "account_id": str(claim.account_id),
                    "submission_id": str(claim.submission_id),
                },
            )
        intent = (
            None if submission is None else await session.get(LiveOrderIntent, submission.intent_id)
        )
        preview = None if intent is None else await session.get(LiveOrderPreview, intent.preview_id)
        snapshot = await session.scalar(
            select(PrivateReconciliationSnapshot)
            .where(PrivateReconciliationSnapshot.account_id == claim.account_id)
            .order_by(desc(PrivateReconciliationSnapshot.observed_at))
            .limit(1)
        )
        if (
            submission is None
            or submission.status != "UNCERTAIN"
            or submission.sent_at is None
            or preview is None
            or preview.order_type != "market"
            or snapshot is None
            or snapshot.outcome != "ready"
            or snapshot.observed_at < submission.sent_at
        ):
            return None
        return _fill_recovery_observation(
            snapshot.fills_payload,
            product_id=preview.product_id,
            side=preview.side,
            contracts=preview.contracts,
            sent_at=submission.sent_at,
        )


async def claim_next_live_submission(
    database: Database,
    *,
    account_id: UUID,
    owner_id: UUID,
    claim_seconds: int = 30,
    entries_allowed: bool = True,
) -> ClaimedLiveSubmission | None:
    """Claim one admitted submission only while every Live gate remains valid."""
    if (
        type(account_id) is not UUID
        or type(owner_id) is not UUID
        or account_id.int == 0
        or owner_id.int == 0
        or type(claim_seconds) is not int
        or not 5 <= claim_seconds <= 120
        or type(entries_allowed) is not bool
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
            select(AccountRiskPolicy)
            .where(AccountRiskPolicy.account_id == account_id)
            .with_for_update()
        )
        reconciliation = await session.scalar(
            select(PrivateReconciliationSnapshot)
            .options(
                load_only(
                    PrivateReconciliationSnapshot.id,
                    PrivateReconciliationSnapshot.observed_at,
                    PrivateReconciliationSnapshot.credential_version,
                    PrivateReconciliationSnapshot.outcome,
                    PrivateReconciliationSnapshot.worker_owner_id,
                    PrivateReconciliationSnapshot.worker_generation,
                )
            )
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
            or reconciliation is None
            or reconciliation.outcome != "ready"
            or reconciliation.observed_at < now - timedelta(seconds=60)
            or reconciliation.worker_owner_id != lease.owner_id
            or reconciliation.worker_generation != lease.generation
            or reconciliation.credential_version != lease.credential_version
        ):
            return None
        expired = await session.scalar(
            select(LiveOrderSubmission)
            .where(
                LiveOrderSubmission.account_id == account_id,
                LiveOrderSubmission.status.in_(("CLAIMED", "PREPARED")),
                LiveOrderSubmission.claim_until <= now,
                LiveOrderSubmission.sent_at.is_(None),
            )
            .order_by(LiveOrderSubmission.created_at, LiveOrderSubmission.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if expired is not None:
            if (
                expired.worker_owner_id is None
                or expired.worker_generation is None
                or expired.credential_version is None
            ):
                raise ValueError("expired pre-dispatch submission has no fence")
            expired_status = expired.status
            expired_fence = _database_fence(
                expired.worker_owner_id,
                expired.worker_generation,
                expired.credential_version,
            )
            sequence = await _next_submission_sequence(session, _database_uuid(expired.id))
            evidence = submission_event_sha256(
                submission_id=_database_uuid(expired.id),
                sequence=sequence,
                from_status=expired_status,
                to_status="PENDING",
                fence=expired_fence,
                occurred_at=now,
            )
            session.add(
                LiveOrderSubmissionEvent(
                    id=uuid4(),
                    submission_id=expired.id,
                    account_id=expired.account_id,
                    sequence=sequence,
                    from_status=expired_status,
                    to_status="PENDING",
                    worker_owner_id=expired_fence.owner_id,
                    worker_generation=expired_fence.generation,
                    credential_version=expired_fence.credential_version,
                    evidence_sha256=evidence,
                    occurred_at=now,
                )
            )
            expired.status = "PENDING"
            expired.worker_owner_id = None
            expired.worker_generation = None
            expired.credential_version = None
            expired.claim_until = None
            expired.attempt_number = 0
            expired.prepared_request_payload = None
            expired.prepared_request_sha256 = None
            expired.last_error_code = "predispatch_claim_expired"
            expired.updated_at = now

        statement = (
            select(LiveOrderSubmission)
            .join(
                LiveOrderIntent,
                LiveOrderIntent.id == LiveOrderSubmission.intent_id,
            )
            .join(
                LiveOrderPreview,
                LiveOrderPreview.id == LiveOrderIntent.preview_id,
            )
            .where(
                LiveOrderSubmission.account_id == account_id,
                LiveOrderSubmission.status == "PENDING",
            )
            .order_by(
                desc(LiveOrderPreview.reduce_only),
                LiveOrderSubmission.created_at,
                LiveOrderSubmission.id,
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if not entries_allowed:
            statement = statement.where(LiveOrderPreview.reduce_only.is_(True))
        submission = await session.scalar(statement)
        if submission is None:
            return None
        intent = await session.get(LiveOrderIntent, submission.intent_id)
        preview = None if intent is None else await session.get(LiveOrderPreview, intent.preview_id)
        if preview is not None:
            risk_reason = policy_rejection(
                risk, stage="submission_claim", reduce_only=preview.reduce_only
            )
            if risk_reason:
                submission.last_error_code = risk_reason
                submission.updated_at = now
                return None
            if (
                not preview.reduce_only
                and risk is not None
                and preview.risk_policy_version != risk.version
            ):
                record_rejection("submission_claim", "risk_policy_changed")
                submission.last_error_code = "risk_policy_changed"
                submission.updated_at = now
                return None
        if (
            intent is None
            or preview is None
            or preview.account_id != account_id
            or (preview.reduce_only and not control.exits_enabled)
            or (
                not preview.reduce_only
                and (
                    risk is None
                    or preview.risk_policy_version != risk.version
                    or preview.notional > risk.max_order_notional
                    or not control.entries_enabled
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
        sequence = await _next_submission_sequence(session, _database_uuid(submission.id))
        evidence = submission_event_sha256(
            submission_id=_database_uuid(submission.id),
            sequence=sequence,
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
                sequence=sequence,
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
            submission_id=_database_uuid(submission.id),
            account_id=account_id,
            intent_id=intent.id,
            preview_id=preview.id,
            client_order_id=submission.client_order_id,
            request_sha256=submission.request_sha256,
            fence=fence,
            claim_until=claim_until,
        )


async def prepare_claimed_live_submission(
    database: Database, claim: ClaimedLiveSubmission
) -> PreparedLiveOrder:
    """Persist canonical wire evidence after revalidating the exact worker fence."""
    if type(claim) is not ClaimedLiveSubmission:
        raise ValueError("invalid live submission claim")
    now = datetime.now(UTC)
    async with database.transaction() as session:
        submission = await session.scalar(
            select(LiveOrderSubmission)
            .where(LiveOrderSubmission.id == claim.submission_id)
            .with_for_update()
        )
        lease = await session.scalar(
            select(PrivateWorkerLease)
            .where(PrivateWorkerLease.account_id == claim.account_id)
            .with_for_update()
        )
        if (
            submission is None
            or lease is None
            or submission.status != "CLAIMED"
            or submission.account_id != claim.account_id
            or submission.intent_id != claim.intent_id
            or submission.client_order_id != claim.client_order_id
            or submission.request_sha256 != claim.request_sha256
            or submission.worker_owner_id is None
            or submission.worker_generation is None
            or submission.credential_version is None
            or submission.claim_until is None
            or submission.claim_until <= now
            or lease.lease_until <= now
        ):
            raise ValueError("live submission claim is stale")
        stored_fence = _database_fence(
            submission.worker_owner_id,
            submission.worker_generation,
            submission.credential_version,
        )
        active_fence = _database_fence(
            lease.owner_id,
            lease.generation,
            lease.credential_version,
        )
        require_transition(
            current="CLAIMED",
            target="PREPARED",
            stored_fence=stored_fence,
            actor_fence=active_fence,
        )
        intent = await session.get(LiveOrderIntent, submission.intent_id)
        preview = None if intent is None else await session.get(LiveOrderPreview, intent.preview_id)
        if intent is None or preview is None or preview.id != claim.preview_id:
            raise ValueError("live submission evidence is unavailable")
        prepared = prepare_live_order(
            product_id=preview.product_id,
            contracts=preview.contracts,
            side=preview.side,
            order_type=preview.order_type,
            limit_price=preview.limit_price,
            reduce_only=preview.reduce_only,
            client_order_id=submission.client_order_id,
        )
        submission.status = "PREPARED"
        submission.prepared_request_payload = prepared.body.decode("ascii")
        submission.prepared_request_sha256 = prepared.body_sha256
        submission.attempt_number = 1
        submission.updated_at = now
        sequence = await _next_submission_sequence(session, _database_uuid(submission.id))
        event_hash = submission_event_sha256(
            submission_id=_database_uuid(submission.id),
            sequence=sequence,
            from_status="CLAIMED",
            to_status="PREPARED",
            fence=stored_fence,
            occurred_at=now,
        )
        session.add(
            LiveOrderSubmissionEvent(
                id=uuid4(),
                submission_id=submission.id,
                account_id=submission.account_id,
                sequence=sequence,
                from_status="CLAIMED",
                to_status="PREPARED",
                worker_owner_id=stored_fence.owner_id,
                worker_generation=stored_fence.generation,
                credential_version=stored_fence.credential_version,
                evidence_sha256=event_hash,
                occurred_at=now,
            )
        )
        return prepared


async def mark_live_submission_sending(
    database: Database, claim: ClaimedLiveSubmission
) -> SendingLiveSubmission:
    """Persist possible venue entry before a transport may send any bytes."""
    if type(claim) is not ClaimedLiveSubmission:
        raise ValueError("invalid live submission claim")
    now = datetime.now(UTC)
    async with database.transaction() as session:
        submission = await session.scalar(
            select(LiveOrderSubmission)
            .where(LiveOrderSubmission.id == claim.submission_id)
            .with_for_update()
        )
        lease = await session.scalar(
            select(PrivateWorkerLease)
            .where(PrivateWorkerLease.account_id == claim.account_id)
            .with_for_update()
        )
        if (
            submission is None
            or lease is None
            or submission.status != "PREPARED"
            or submission.prepared_request_payload is None
            or submission.prepared_request_sha256 is None
            or submission.claim_until is None
            or submission.claim_until <= now
            or lease.lease_until <= now
            or submission.worker_owner_id is None
            or submission.worker_generation is None
            or submission.credential_version is None
        ):
            raise ValueError("prepared live submission is stale")
        stored_fence = _database_fence(
            submission.worker_owner_id,
            submission.worker_generation,
            submission.credential_version,
        )
        active_fence = _database_fence(lease.owner_id, lease.generation, lease.credential_version)
        require_transition(
            current="PREPARED",
            target="SENDING",
            stored_fence=stored_fence,
            actor_fence=active_fence,
        )
        intent = await session.get(LiveOrderIntent, submission.intent_id)
        preview = None if intent is None else await session.get(LiveOrderPreview, intent.preview_id)
        if intent is None or preview is None:
            raise ValueError("live submission evidence is unavailable")
        prepared = prepare_live_order(
            product_id=preview.product_id,
            contracts=preview.contracts,
            side=preview.side,
            order_type=preview.order_type,
            limit_price=preview.limit_price,
            reduce_only=preview.reduce_only,
            client_order_id=submission.client_order_id,
        )
        if (
            prepared.body_sha256 != submission.prepared_request_sha256
            or prepared.body.decode("ascii") != submission.prepared_request_payload
        ):
            raise ValueError("prepared live submission evidence changed")
        submission.status = "SENDING"
        submission.sent_at = now
        submission.updated_at = now
        sequence = await _next_submission_sequence(session, _database_uuid(submission.id))
        event_hash = submission_event_sha256(
            submission_id=_database_uuid(submission.id),
            sequence=sequence,
            from_status="PREPARED",
            to_status="SENDING",
            fence=stored_fence,
            occurred_at=now,
        )
        session.add(
            LiveOrderSubmissionEvent(
                id=uuid4(),
                submission_id=submission.id,
                account_id=submission.account_id,
                sequence=sequence,
                from_status="PREPARED",
                to_status="SENDING",
                worker_owner_id=stored_fence.owner_id,
                worker_generation=stored_fence.generation,
                credential_version=stored_fence.credential_version,
                evidence_sha256=event_hash,
                occurred_at=now,
            )
        )
        permit_expires_at = min(submission.claim_until, lease.lease_until)
        return SendingLiveSubmission(prepared, event_hash, permit_expires_at)


async def record_live_submission_outcome(
    database: Database,
    claim: ClaimedLiveSubmission,
    outcome: LiveOrderResponseOutcome,
) -> None:
    """Persist a classified response without ever retrying an uncertain write."""
    if (
        type(claim) is not ClaimedLiveSubmission
        or type(outcome) is not LiveOrderResponseOutcome
        or outcome.kind not in {"ACCEPTED", "REJECTED", "UNCERTAIN"}
        or (
            outcome.kind == "ACCEPTED"
            and (type(outcome.exchange_order_id) is not int or outcome.exchange_order_id < 1)
        )
        or (outcome.kind != "ACCEPTED" and outcome.exchange_order_id is not None)
    ):
        raise ValueError("invalid live submission outcome")
    now = datetime.now(UTC)
    async with database.transaction() as session:
        submission = await session.scalar(
            select(LiveOrderSubmission)
            .where(LiveOrderSubmission.id == claim.submission_id)
            .with_for_update()
        )
        if (
            submission is None
            or submission.status != "SENDING"
            or submission.worker_owner_id is None
            or submission.worker_generation is None
            or submission.credential_version is None
        ):
            raise ValueError("sending live submission is unavailable")
        fence = _database_fence(
            submission.worker_owner_id,
            submission.worker_generation,
            submission.credential_version,
        )
        require_transition(
            current="SENDING", target=outcome.kind, stored_fence=fence, actor_fence=claim.fence
        )
        submission.status = outcome.kind
        submission.exchange_order_id = outcome.exchange_order_id
        submission.last_error_code = "response_uncertain" if outcome.kind == "UNCERTAIN" else None
        submission.resolved_at = None if outcome.kind == "UNCERTAIN" else now
        submission.updated_at = now
        sequence = await _next_submission_sequence(session, _database_uuid(submission.id))
        event_hash = submission_event_sha256(
            submission_id=_database_uuid(submission.id),
            sequence=sequence,
            from_status="SENDING",
            to_status=outcome.kind,
            fence=fence,
            occurred_at=now,
        )
        session.add(
            LiveOrderSubmissionEvent(
                id=uuid4(),
                submission_id=submission.id,
                account_id=submission.account_id,
                sequence=sequence,
                from_status="SENDING",
                to_status=outcome.kind,
                worker_owner_id=fence.owner_id,
                worker_generation=fence.generation,
                credential_version=fence.credential_version,
                evidence_sha256=event_hash,
                occurred_at=now,
            )
        )


async def record_uncertain_recovery_lookup(
    database: Database,
    claim: ClaimedLiveSubmission,
    observation: RecoveryObservation,
) -> None:
    """Record one lookup; only a positive match can resolve uncertainty."""
    if type(claim) is not ClaimedLiveSubmission or type(observation) is not RecoveryObservation:
        raise ValueError("invalid live recovery observation")
    now = datetime.now(UTC)
    async with database.transaction() as session:
        submission = await session.scalar(
            select(LiveOrderSubmission)
            .where(LiveOrderSubmission.id == claim.submission_id)
            .with_for_update()
        )
        lease = await session.scalar(
            select(PrivateWorkerLease)
            .where(PrivateWorkerLease.account_id == claim.account_id)
            .with_for_update()
        )
        if (
            submission is None
            or lease is None
            or submission.status != "UNCERTAIN"
            or submission.worker_owner_id is None
            or submission.worker_generation is None
            or submission.credential_version is None
            or submission.client_order_id != claim.client_order_id
        ):
            raise ValueError("uncertain live submission is unavailable")
        active = _database_fence(lease.owner_id, lease.generation, lease.credential_version)
        if active != claim.fence or lease.lease_until <= now:
            raise ValueError("uncertain recovery fence is stale")
        prior = await session.scalar(
            select(func.max(LiveOrderRecoveryCheck.attempt)).where(
                LiveOrderRecoveryCheck.submission_id == submission.id
            )
        )
        attempt = 1 if prior is None else int(prior) + 1
        evidence = recovery_check_sha256(
            submission_id=_database_uuid(submission.id),
            attempt=attempt,
            result=observation.result,
            client_order_id=submission.client_order_id,
            exchange_order_id=observation.exchange_order_id,
            fence=active,
            occurred_at=now,
        )
        session.add(
            LiveOrderRecoveryCheck(
                id=uuid4(),
                submission_id=submission.id,
                account_id=submission.account_id,
                attempt=attempt,
                result=observation.result,
                client_order_id=submission.client_order_id,
                exchange_order_id=observation.exchange_order_id,
                worker_owner_id=active.owner_id,
                worker_generation=active.generation,
                credential_version=active.credential_version,
                evidence_sha256=evidence,
                occurred_at=now,
            )
        )
        if observation.result != "FOUND":
            return
        submission.status = "ACCEPTED"
        submission.exchange_order_id = observation.exchange_order_id
        submission.exchange_order_state = observation.order_state
        submission.exchange_order_size = observation.size
        submission.exchange_unfilled_size = observation.unfilled_size
        submission.exchange_cancellation_reason = observation.cancellation_reason
        submission.last_error_code = None
        submission.resolved_at = now
        submission.updated_at = now
        sequence = await _next_submission_sequence(session, _database_uuid(submission.id))
        event_hash = submission_event_sha256(
            submission_id=_database_uuid(submission.id),
            sequence=sequence,
            from_status="UNCERTAIN",
            to_status="ACCEPTED",
            fence=active,
            occurred_at=now,
        )
        session.add(
            LiveOrderSubmissionEvent(
                id=uuid4(),
                submission_id=submission.id,
                account_id=submission.account_id,
                sequence=sequence,
                from_status="UNCERTAIN",
                to_status="ACCEPTED",
                worker_owner_id=active.owner_id,
                worker_generation=active.generation,
                credential_version=active.credential_version,
                evidence_sha256=event_hash,
                occurred_at=now,
            )
        )


__all__ = [
    "ClaimedLiveSubmission",
    "SendingLiveSubmission",
    "claim_live_submission_recovery",
    "claim_next_live_submission",
    "has_live_submission_recovery_debt",
    "mark_live_submission_sending",
    "prepare_claimed_live_submission",
    "record_live_submission_outcome",
    "record_uncertain_recovery_lookup",
    "recover_from_reconciliation",
]
