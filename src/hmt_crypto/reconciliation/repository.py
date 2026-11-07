"""Transactional checkpoint and local-projection repository."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hmt_crypto.domain.account import AccountRef
from hmt_crypto.persistence.attempt_models import OrderAttempt
from hmt_crypto.persistence.execution_event_models import OrderAttemptEvent
from hmt_crypto.persistence.identity_models import Account, CredentialVersion
from hmt_crypto.persistence.order_models import Order
from hmt_crypto.persistence.reconciliation_models import ReconciliationRun
from hmt_crypto.persistence.reconciliation_resource_models import ReconciliationResource
from hmt_crypto.persistence.snapshot_models import PositionSnapshot
from hmt_crypto.reconciliation.checkpoint_models import AccountReconciliationCheckpoint
from hmt_crypto.reconciliation.contracts import (
    LocalAccountProjection,
    LocalOrderTruth,
    ReconciliationDrift,
    ReconciliationDriftKind,
    ReconciliationReceipt,
    ReconciliationTrigger,
    UncertainOrderOutcome,
    UncertainResolution,
)

_TERMINAL_ORDER_STATES = frozenset({"FILLED", "CANCELLED", "REJECTED", "EXPIRED"})


class ReconciliationEvidenceLimitError(ValueError):
    """A complete normalized bundle cannot fit the reviewed durable bound."""


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("UTC timestamp required")
    return value.astimezone(UTC)


def _scope(model: Any, account: AccountRef) -> tuple[Any, ...]:
    return (
        model.account_id == account.account_id,
        model.region == account.region.value,
        model.environment == account.environment.value,
        model.mode == account.mode.value,
    )


def _account_scope(account: AccountRef) -> tuple[Any, ...]:
    return (
        Account.id == account.account_id,
        Account.region == account.region.value,
        Account.environment == account.environment.value,
        Account.mode == account.mode.value,
    )


async def _current_binding(session: AsyncSession, account: AccountRef) -> bool:
    owner = await session.scalar(select(Account).where(*_account_scope(account)).with_for_update())
    if (
        owner is None
        or owner.external_user_id != account.external_user_id
        or owner.identity_verified_at is None
        or owner.current_credential_version != account.credential_version
    ):
        return False
    credential = await session.scalar(
        select(CredentialVersion).where(
            *_scope(CredentialVersion, account),
            CredentialVersion.credential_version == account.credential_version,
        )
    )
    return (
        credential is not None and credential.observed_external_user_id == account.external_user_id
    )


def request_sha256(
    account: AccountRef,
    request_id: UUID,
    trigger: ReconciliationTrigger,
    product_ids: tuple[int, ...],
) -> str:
    payload = json.dumps(
        {
            "account_id": str(account.account_id),
            "credential_version": account.credential_version,
            "environment": account.environment.value,
            "mode": account.mode.value,
            "product_ids": list(product_ids),
            "region": account.region.value,
            "request_id": str(request_id),
            "trigger": trigger.value,
            "version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def external_user_sha256(account: AccountRef) -> str:
    return hashlib.sha256(account.external_user_id.encode("ascii")).hexdigest()


def local_projection_sha256(projection: LocalAccountProjection) -> str:
    """Hash one immutable local comparison input without granting it authority."""
    payload = json.dumps(
        {
            "open_orders": [
                {
                    "client_order_id": item.client_order_id,
                    "exchange_order_id": item.exchange_order_id,
                    "local_order_id": str(item.local_order_id),
                    "product_id": item.product_id,
                    "uncertain_attempt_ids": [
                        str(attempt_id) for attempt_id in item.uncertain_attempt_ids
                    ],
                }
                for item in projection.open_orders
            ],
            "signed_positions": [list(item) for item in projection.signed_positions],
            "unbound_uncertain_attempt_ids": [
                str(item) for item in projection.unbound_uncertain_attempt_ids
            ],
            "version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def canonical_evidence(value: dict[str, object]) -> tuple[str, str]:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    encoded = payload.encode("ascii")
    if not 2 <= len(encoded) <= 16_777_216:
        raise ReconciliationEvidenceLimitError(
            "reconciliation evidence is outside the durable bound"
        )
    return payload, hashlib.sha256(encoded).hexdigest()


def _receipt(row: AccountReconciliationCheckpoint, *, replayed: bool) -> ReconciliationReceipt:
    trigger = ReconciliationTrigger(row.trigger)
    if row.state == "FAILED":
        return ReconciliationReceipt(
            request_id=row.id,
            trigger=trigger,
            ready=False,
            complete=False,
            evidence_sha256=None,
            observed_at=None,
            drifts=(),
            uncertain=(),
            replayed=replayed,
            failure_code=row.failure_code,
        )
    if row.state != "FINISHED" or row.evidence_payload is None or row.evidence_sha256 is None:
        raise RuntimeError("reconciliation checkpoint is not terminal")
    raw = json.loads(row.evidence_payload)
    if type(raw) is not dict:
        raise RuntimeError("reconciliation evidence is invalid")
    drifts = tuple(
        ReconciliationDrift(
            kind=ReconciliationDriftKind(item["kind"]),
            subject_sha256=item["subject_sha256"],
            product_id=item["product_id"],
            external_exposure=item["external_exposure"],
        )
        for item in raw.get("drifts", [])
    )
    uncertain = tuple(
        UncertainOrderOutcome(
            attempt_id=UUID(item["attempt_id"]),
            local_order_id=(
                UUID(item["local_order_id"]) if item["local_order_id"] is not None else None
            ),
            resolution=UncertainResolution(item["resolution"]),
            evidence_sha256=item["evidence_sha256"],
        )
        for item in raw.get("uncertain", [])
    )
    observed_at = datetime.fromisoformat(raw["observed_at"])
    return ReconciliationReceipt(
        request_id=row.id,
        trigger=trigger,
        ready=row.readiness == "READY",
        complete=True,
        evidence_sha256=row.evidence_sha256,
        observed_at=_utc(observed_at),
        drifts=drifts,
        uncertain=uncertain,
        replayed=replayed,
    )


class ReconciliationCheckpointRepository:
    """Own idempotent request admission, terminalization, and local comparison input."""

    async def begin(
        self,
        session: AsyncSession,
        account: AccountRef,
        *,
        request_id: UUID,
        trigger: ReconciliationTrigger,
        product_ids: tuple[int, ...],
        now: datetime,
    ) -> ReconciliationReceipt | None:
        """Insert a blocked pending barrier or replay its terminal result."""
        now = _utc(now)
        digest = request_sha256(account, request_id, trigger, product_ids)
        if not await _current_binding(session, account):
            raise ValueError("reconciliation account binding is not current")
        row = await session.get(AccountReconciliationCheckpoint, request_id)
        if row is not None:
            if (
                row.account_id != account.account_id
                or row.region != account.region.value
                or row.environment != account.environment.value
                or row.mode != account.mode.value
                or row.credential_version != account.credential_version
                or row.external_user_sha256 != external_user_sha256(account)
                or row.trigger != trigger.value
                or row.request_sha256 != digest
            ):
                raise ValueError("reconciliation request identity conflicts")
            return None if row.state == "PENDING" else _receipt(row, replayed=True)
        session.add(
            AccountReconciliationCheckpoint(
                id=request_id,
                account_id=account.account_id,
                region=account.region.value,
                environment=account.environment.value,
                mode=account.mode.value,
                trigger=trigger.value,
                credential_version=account.credential_version,
                external_user_sha256=external_user_sha256(account),
                request_sha256=digest,
                state="PENDING",
                readiness="BLOCKED",
                evidence_payload=None,
                evidence_sha256=None,
                drift_count=0,
                external_exposure=False,
                failure_code=None,
                created_at=now,
                updated_at=now,
                finished_at=None,
                version=1,
            )
        )
        await session.flush()
        return None

    async def finish(
        self,
        session: AsyncSession,
        account: AccountRef,
        *,
        request_id: UUID,
        evidence: dict[str, object],
        drifts: tuple[ReconciliationDrift, ...],
        uncertain: tuple[UncertainOrderOutcome, ...],
        expected_local_projection_sha256: str,
        observed_at: datetime,
        recorded_at: datetime,
    ) -> ReconciliationReceipt:
        """Atomically publish a complete bundle; exchange truth always remains in evidence."""
        observed = _utc(observed_at)
        recorded = _utc(recorded_at)
        if observed > recorded:
            raise ValueError("reconciliation evidence is from the future")
        binding_is_current = await _current_binding(session, account)
        row = await session.scalar(
            select(AccountReconciliationCheckpoint)
            .where(
                AccountReconciliationCheckpoint.id == request_id,
                *_scope(AccountReconciliationCheckpoint, account),
            )
            .with_for_update()
        )
        if row is None:
            raise ValueError("reconciliation checkpoint is missing")
        if row.state != "PENDING":
            return _receipt(row, replayed=True)
        if not binding_is_current:
            row.state = "FAILED"
            row.failure_code = "INVALID_RESPONSE"
            row.updated_at = recorded
            row.finished_at = recorded
            row.version += 1
            await session.flush()
            return _receipt(row, replayed=False)
        current_projection = await self.local_projection(session, account)
        if local_projection_sha256(current_projection) != expected_local_projection_sha256:
            row.state = "FAILED"
            row.failure_code = "LOCAL_STATE_CHANGED"
            row.updated_at = recorded
            row.finished_at = recorded
            row.version += 1
            await session.flush()
            return _receipt(row, replayed=False)
        owned = dict(evidence)
        owned["drifts"] = [
            {
                "kind": item.kind.value,
                "subject_sha256": item.subject_sha256,
                "product_id": item.product_id,
                "external_exposure": item.external_exposure,
            }
            for item in drifts
        ]
        owned["uncertain"] = [
            {
                "attempt_id": str(item.attempt_id),
                "local_order_id": (
                    str(item.local_order_id) if item.local_order_id is not None else None
                ),
                "resolution": item.resolution.value,
                "evidence_sha256": item.evidence_sha256,
            }
            for item in uncertain
        ]
        owned["observed_at"] = observed.isoformat(timespec="microseconds")
        payload, digest = canonical_evidence(owned)
        external = any(item.external_exposure for item in drifts)
        ready = not drifts and not uncertain
        row.state = "FINISHED"
        row.readiness = "READY" if ready else "BLOCKED"
        row.evidence_payload = payload
        row.evidence_sha256 = digest
        row.drift_count = len(drifts)
        row.external_exposure = external
        row.updated_at = recorded
        row.finished_at = recorded
        row.version += 1
        await session.flush()
        return _receipt(row, replayed=False)

    async def fail(
        self,
        session: AsyncSession,
        account: AccountRef,
        *,
        request_id: UUID,
        failure_code: str,
        now: datetime,
    ) -> ReconciliationReceipt:
        """Persist only a classified failure; never fabricate venue observations."""
        now = _utc(now)
        row = await session.scalar(
            select(AccountReconciliationCheckpoint)
            .where(
                AccountReconciliationCheckpoint.id == request_id,
                *_scope(AccountReconciliationCheckpoint, account),
            )
            .with_for_update()
        )
        if row is None:
            raise ValueError("reconciliation checkpoint is missing")
        if row.state != "PENDING":
            return _receipt(row, replayed=True)
        if failure_code not in {
            "REQUEST_FAILED",
            "INVALID_RESPONSE",
            "PAGINATION_INCOMPLETE",
            "LIMIT_REACHED",
            "LOCAL_STATE_CHANGED",
            "UNKNOWN",
        }:
            raise ValueError("invalid reconciliation failure")
        row.state = "FAILED"
        row.failure_code = failure_code
        row.updated_at = now
        row.finished_at = now
        row.version += 1
        await session.flush()
        return _receipt(row, replayed=False)

    async def local_projection(
        self, session: AsyncSession, account: AccountRef
    ) -> LocalAccountProjection:
        """Load the latest local order/position projection without granting it authority."""
        rows = list(
            (
                await session.scalars(
                    select(Order).where(
                        *_scope(Order, account),
                        Order.origin.in_(("LOCAL_INTENT", "ADOPTED")),
                    )
                )
            ).all()
        )
        attempts = list(
            (
                await session.scalars(
                    select(OrderAttempt).where(
                        *_scope(OrderAttempt, account),
                        OrderAttempt.backend == "VENUE",
                    )
                )
            ).all()
        )
        latest_events: dict[UUID, str] = {}
        for event in (
            await session.scalars(
                select(OrderAttemptEvent)
                .where(
                    *_scope(OrderAttemptEvent, account),
                    OrderAttemptEvent.backend == "VENUE",
                )
                .order_by(OrderAttemptEvent.sequence.desc())
            )
        ).all():
            latest_events.setdefault(event.attempt_id, event.event_kind)
        uncertain_attempts = tuple(
            attempt for attempt in attempts if latest_events.get(attempt.id) == "UNCERTAIN"
        )
        uncertain_by_order: dict[UUID, list[UUID]] = {}
        unbound_uncertain: list[UUID] = []
        for attempt in uncertain_attempts:
            if attempt.order_id is None:
                unbound_uncertain.append(attempt.id)
            else:
                uncertain_by_order.setdefault(attempt.order_id, []).append(attempt.id)
        open_orders = tuple(
            LocalOrderTruth(
                local_order_id=row.id,
                product_id=row.product_id,
                client_order_id=row.client_order_id,
                exchange_order_id=(
                    int(row.exchange_order_id)
                    if row.exchange_order_id is not None
                    and row.exchange_order_id.isascii()
                    and row.exchange_order_id.isdigit()
                    else None
                ),
                uncertain_attempt_ids=tuple(
                    sorted(uncertain_by_order.get(row.id, ()), key=lambda item: item.int)
                ),
            )
            for row in rows
            if row.state not in _TERMINAL_ORDER_STATES or row.id in uncertain_by_order
        )
        venue_run_ids = select(ReconciliationRun.id).where(
            *_scope(ReconciliationRun, account),
            ReconciliationRun.backend == "VENUE",
            ReconciliationRun.state == "FINISHED",
            ReconciliationRun.completeness == "COMPLETE",
        )
        venue_resource_ids = select(ReconciliationResource.id).where(
            *_scope(ReconciliationResource, account),
            ReconciliationResource.run_id.in_(venue_run_ids),
            ReconciliationResource.state == "COLLECTED",
            ReconciliationResource.resource_kind.in_(("MARGINED_POSITIONS", "LATEST_POSITION")),
        )
        snapshots = list(
            (
                await session.scalars(
                    select(PositionSnapshot)
                    .where(
                        *_scope(PositionSnapshot, account),
                        PositionSnapshot.run_id.in_(venue_run_ids),
                        PositionSnapshot.resource_id.in_(venue_resource_ids),
                    )
                    .order_by(PositionSnapshot.recorded_at.desc())
                )
            ).all()
        )
        positions: dict[int, int] = {}
        for row in snapshots:
            positions.setdefault(row.product_id, row.signed_contracts)
        return LocalAccountProjection(
            open_orders=tuple(sorted(open_orders, key=lambda item: item.local_order_id.int)),
            signed_positions=tuple(sorted(positions.items())),
            unbound_uncertain_attempt_ids=tuple(
                sorted(unbound_uncertain, key=lambda item: item.int)
            ),
        )


__all__ = [
    "ReconciliationCheckpointRepository",
    "ReconciliationEvidenceLimitError",
    "canonical_evidence",
    "external_user_sha256",
    "local_projection_sha256",
    "request_sha256",
]
