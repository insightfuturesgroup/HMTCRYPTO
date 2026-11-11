"""Lease-fenced GTT evaluation and atomic reduce-only exit admission."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import InvalidOperation
from uuid import UUID, uuid4

from sqlalchemy import desc, select

from hmt_crypto.api.gtt_trigger import GttTriggerError, evaluate_gtt_trigger
from hmt_crypto.api.live_order_admission import LiveOrderAdmissionError, evaluate_order_sizing
from hmt_crypto.api.live_submission import make_live_client_order_id
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.models import (
    AccountLiveControl,
    AccountRiskPolicy,
    CatalogueProduct,
    CatalogueSnapshot,
    GttOrder,
    LiveOrderIntent,
    LiveOrderPreview,
    LiveOrderSubmission,
    MarketQuote,
    PrivateReconciliationSnapshot,
    PrivateWorkerLease,
)


@dataclass(frozen=True, slots=True)
class GttCycleResult:
    kind: str
    gtt_id: UUID | None
    submission_id: UUID | None


def _position_contracts(payload: str | None, product_id: int) -> int:
    try:
        rows = json.loads(payload or "")
    except (TypeError, ValueError):
        raise ValueError("invalid reconciled positions") from None
    if not isinstance(rows, list):
        raise ValueError("invalid reconciled positions")
    found = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid reconciled positions")
        if row.get("product_id") == product_id:
            value = row.get("size")
            if type(value) is not int:
                raise ValueError("invalid reconciled position size")
            found = value
    return found


async def evaluate_one_gtt(
    database: Database, *, account_id: UUID, owner_id: UUID
) -> GttCycleResult:
    """Evaluate at most one armed GTT and atomically queue its exit when triggered."""
    if (
        type(account_id) is not UUID
        or account_id.int == 0
        or type(owner_id) is not UUID
        or owner_id.int == 0
    ):
        raise ValueError("invalid GTT worker scope")
    now = datetime.now(UTC)
    async with database.transaction() as session:
        lease = await session.scalar(
            select(PrivateWorkerLease)
            .where(PrivateWorkerLease.account_id == account_id)
            .with_for_update()
        )
        control = await session.scalar(
            select(AccountLiveControl).where(AccountLiveControl.account_id == account_id)
        )
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
            or control is None
            or not control.exits_enabled
            or control.reconciliation_state != "ready"
            or risk is None
            or not risk.enabled
            or reconciliation is None
            or reconciliation.outcome != "ready"
            or reconciliation.observed_at < now - timedelta(seconds=60)
            or reconciliation.worker_owner_id != lease.owner_id
            or reconciliation.worker_generation != lease.generation
            or reconciliation.credential_version != lease.credential_version
        ):
            return GttCycleResult("BLOCKED", None, None)
        active_gtt = await session.scalar(
            select(GttOrder)
            .where(
                GttOrder.account_id == account_id,
                GttOrder.status.in_(("submitting", "uncertain")),
            )
            .order_by(GttOrder.triggered_at, GttOrder.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if active_gtt is not None and active_gtt.triggered_order_id is not None:
            submission = await session.scalar(
                select(LiveOrderSubmission).where(
                    LiveOrderSubmission.intent_id == active_gtt.triggered_order_id
                )
            )
            if submission is None:
                return GttCycleResult("BLOCKED", active_gtt.id, None)
            terminal = {
                "ACCEPTED": "completed",
                "REJECTED": "failed",
                "UNCERTAIN": "uncertain",
            }.get(submission.status)
            if terminal is not None and active_gtt.status != terminal:
                active_gtt.status = terminal
                active_gtt.last_error_code = "submission_rejected" if terminal == "failed" else None
                active_gtt.updated_at = now
                return GttCycleResult(terminal.upper(), active_gtt.id, submission.id)
            if active_gtt.status == "uncertain":
                return GttCycleResult("UNCERTAIN", active_gtt.id, submission.id)
            return GttCycleResult("SUBMITTING", active_gtt.id, submission.id)
        gtt = await session.scalar(
            select(GttOrder)
            .where(GttOrder.account_id == account_id, GttOrder.status == "armed")
            .order_by(GttOrder.created_at, GttOrder.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if gtt is None:
            return GttCycleResult("IDLE", None, None)
        quote = await session.scalar(
            select(MarketQuote).where(
                MarketQuote.region == "india",
                MarketQuote.environment == "prod",
                MarketQuote.product_id == gtt.product_id,
                MarketQuote.observed_at >= now - timedelta(seconds=15),
            )
        )
        catalogue = await session.scalar(
            select(CatalogueSnapshot)
            .where(
                CatalogueSnapshot.region == "india",
                CatalogueSnapshot.environment == "prod",
                CatalogueSnapshot.fresh_until > now,
            )
            .order_by(desc(CatalogueSnapshot.validated_at))
            .limit(1)
        )
        product_row = (
            None
            if catalogue is None
            else await session.scalar(
                select(CatalogueProduct).where(
                    CatalogueProduct.snapshot_id == catalogue.id,
                    CatalogueProduct.product_id == gtt.product_id,
                )
            )
        )
        if quote is None or catalogue is None or product_row is None:
            return GttCycleResult("BLOCKED", gtt.id, None)
        try:
            decision = evaluate_gtt_trigger(
                gtt_id=str(gtt.id),
                side=gtt.side,
                trigger_kind=gtt.trigger_kind,
                trigger_price=gtt.trigger_price,
                trail_offset=gtt.trail_offset,
                prior_anchor=gtt.trail_anchor,
                best_bid=quote.best_bid,
                best_ask=quote.best_ask,
                quote_id=str(quote.id),
            )
        except GttTriggerError:
            return GttCycleResult("BLOCKED", gtt.id, None)
        gtt.last_quote_id = quote.id
        gtt.trail_anchor = decision.trail_anchor
        gtt.updated_at = now
        if not decision.triggered:
            return GttCycleResult("ARMED", gtt.id, None)
        try:
            signed_position = _position_contracts(reconciliation.positions_payload, gtt.product_id)
            direction = 1 if gtt.side == "buy" else -1
            if (
                signed_position == 0
                or signed_position * direction >= 0
                or gtt.contracts > abs(signed_position)
            ):
                raise ValueError("reduce-only position mismatch")
            order_type = "limit" if gtt.limit_price is not None else "market"
            sizing = evaluate_order_sizing(
                product=product_row.to_product(),
                contracts=gtt.contracts,
                order_type=order_type,
                limit_price=gtt.limit_price,
                market_price=format(decision.observed_price, "f"),
                reduce_only=True,
                max_order_notional=risk.max_order_notional,
            )
        except (ValueError, InvalidOperation, LiveOrderAdmissionError):
            return GttCycleResult("BLOCKED", gtt.id, None)
        preview_id, intent_id, submission_id = uuid4(), uuid4(), uuid4()
        evidence = hashlib.sha256(
            (
                f"gtt-admission:v1:{decision.evidence_sha256}:{reconciliation.id}:"
                f"{catalogue.id}:{risk.version}:{format(sizing.notional, 'f')}"
            ).encode("ascii")
        ).hexdigest()
        session.add(
            LiveOrderPreview(
                id=preview_id,
                account_id=account_id,
                created_by_user_id=gtt.created_by_user_id,
                request_id=gtt.id,
                product_id=gtt.product_id,
                side=gtt.side,
                contracts=gtt.contracts,
                order_type=order_type,
                limit_price=None if gtt.limit_price is None else sizing.price,
                reduce_only=True,
                price_basis=sizing.price_basis,
                price=sizing.price,
                notional=sizing.notional,
                catalogue_snapshot_id=catalogue.id,
                reconciliation_snapshot_id=reconciliation.id,
                risk_policy_version=risk.version,
                evidence_sha256=evidence,
                created_at=now,
                expires_at=now + timedelta(seconds=15),
            )
        )
        intent_hash = hashlib.sha256(
            f"{intent_id}:{preview_id}:{evidence}".encode("ascii")
        ).hexdigest()
        session.add(
            LiveOrderIntent(
                id=intent_id,
                account_id=account_id,
                preview_id=preview_id,
                created_by_user_id=gtt.created_by_user_id,
                idempotency_key=gtt.id,
                status="ADMITTED",
                intent_sha256=intent_hash,
                created_at=now,
            )
        )
        client_order_id = make_live_client_order_id(account_id, intent_id)
        request_hash = hashlib.sha256(
            f"submission:v1:{intent_id}:{client_order_id}:{evidence}".encode("ascii")
        ).hexdigest()
        session.add(
            LiveOrderSubmission(
                id=submission_id,
                account_id=account_id,
                intent_id=intent_id,
                client_order_id=client_order_id,
                status="PENDING",
                request_sha256=request_hash,
                prepared_request_payload=None,
                prepared_request_sha256=None,
                worker_owner_id=None,
                worker_generation=None,
                credential_version=None,
                claim_until=None,
                attempt_number=0,
                exchange_order_id=None,
                last_error_code=None,
                sent_at=None,
                resolved_at=None,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        gtt.status = "submitting"
        gtt.triggered_order_id = intent_id
        gtt.triggered_at = now
        gtt.trigger_evidence_sha256 = decision.evidence_sha256
        gtt.last_error_code = None
        return GttCycleResult("SUBMITTING", gtt.id, submission_id)


__all__ = ["GttCycleResult", "evaluate_one_gtt"]
