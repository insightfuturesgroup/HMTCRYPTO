"""Reconcile accepted entry fills into one durable three-leg OCO group."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from sqlalchemy import desc, select

from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.models import (
    AccountRiskPolicy,
    CatalogueProduct,
    CatalogueSnapshot,
    GttOrder,
    LiveOrderIntent,
    LiveOrderPreview,
    LiveOrderSubmission,
    LiveQualificationEvidence,
    PrivateReconciliationSnapshot,
    PrivateWorkerLease,
    ProtectionGroup,
    TradingViewSignal,
    TradingViewStrategy,
)


def _rows(payload: str | None) -> list[dict[str, object]]:
    try:
        value = json.loads(payload or "")
    except (TypeError, ValueError):
        return []
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


async def sync_one_protection_group(
    database: Database,
    *,
    account_id: UUID,
    owner_id: UUID,
) -> bool:
    """Create or resize one fill-bound group; never dispatch an exchange mutation."""
    now = datetime.now(UTC)
    async with database.transaction() as session:
        lease = await session.scalar(
            select(PrivateWorkerLease).where(
                PrivateWorkerLease.account_id == account_id,
                PrivateWorkerLease.owner_id == owner_id,
                PrivateWorkerLease.lease_until > now,
            )
        )
        reconciliation = await session.scalar(
            select(PrivateReconciliationSnapshot)
            .where(
                PrivateReconciliationSnapshot.account_id == account_id,
                PrivateReconciliationSnapshot.outcome == "ready",
            )
            .order_by(desc(PrivateReconciliationSnapshot.observed_at))
            .limit(1)
        )
        risk = await session.scalar(
            select(AccountRiskPolicy).where(AccountRiskPolicy.account_id == account_id)
        )
        qualification = await session.scalar(
            select(LiveQualificationEvidence)
            .where(
                LiveQualificationEvidence.account_id == account_id,
                LiveQualificationEvidence.status == "qualified",
            )
            .order_by(desc(LiveQualificationEvidence.created_at))
            .limit(1)
        )
        if lease is None or reconciliation is None or risk is None or qualification is None:
            return False
        submission = await session.scalar(
            select(LiveOrderSubmission)
            .join(LiveOrderIntent, LiveOrderIntent.id == LiveOrderSubmission.intent_id)
            .join(LiveOrderPreview, LiveOrderPreview.id == LiveOrderIntent.preview_id)
            .where(
                LiveOrderSubmission.account_id == account_id,
                LiveOrderSubmission.status == "ACCEPTED",
                LiveOrderSubmission.exchange_order_id.is_not(None),
                LiveOrderPreview.reduce_only.is_(False),
            )
            .order_by(desc(LiveOrderSubmission.resolved_at), desc(LiveOrderSubmission.id))
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if submission is None or submission.exchange_order_id is None:
            return False
        intent = await session.get(LiveOrderIntent, submission.intent_id)
        if intent is None:
            return False
        preview = await session.get(LiveOrderPreview, intent.preview_id)
        if preview is None:
            return False
        matching_fills = [
            row
            for row in _rows(reconciliation.fills_payload)
            if str(row.get("order_id")) == str(submission.exchange_order_id)
            and str(row.get("product_id")) == str(preview.product_id)
        ]
        if not matching_fills:
            return False
        try:
            quantities = [int(str(row["size"])) for row in matching_fills]
            prices = [Decimal(str(row["price"])) for row in matching_fills]
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return False
        contracts = sum(abs(value) for value in quantities)
        if contracts <= 0:
            return False
        average = sum(price * abs(size) for price, size in zip(prices, quantities, strict=True))
        average /= Decimal(contracts)
        catalogue = await session.get(CatalogueSnapshot, preview.catalogue_snapshot_id)
        product = None
        if catalogue is not None:
            product = await session.scalar(
                select(CatalogueProduct).where(
                    CatalogueProduct.snapshot_id == catalogue.id,
                    CatalogueProduct.product_id == preview.product_id,
                )
            )
        if catalogue is None or product is None:
            return False
        signal = await session.scalar(
            select(TradingViewSignal).where(TradingViewSignal.intent_id == intent.id)
        )
        strategy = None
        if signal is not None:
            strategy = await session.get(TradingViewStrategy, signal.strategy_id)
        stop_pct = Decimal("2") if strategy is None else strategy.stop_loss_pct
        target_pct = Decimal("4") if strategy is None else strategy.target_pct
        trailing_pct = Decimal("1") if strategy is None else strategy.trailing_offset_pct
        multiplier = Decimal("1") if preview.side == "buy" else Decimal("-1")
        stop_price = average * (Decimal("1") - multiplier * stop_pct / Decimal("100"))
        target_price = average * (Decimal("1") + multiplier * target_pct / Decimal("100"))
        trail_offset = average * trailing_pct / Decimal("100")
        existing_group = await session.scalar(
            select(ProtectionGroup)
            .where(ProtectionGroup.entry_intent_id == intent.id)
            .with_for_update()
        )
        if existing_group is not None:
            if existing_group.status != "armed" or existing_group.claimed_leg_id is not None:
                return False
            if (
                existing_group.protected_contracts == contracts
                and existing_group.average_fill_price == average
                and existing_group.reconciliation_snapshot_id == reconciliation.id
            ):
                return False
            existing_group.protected_contracts = contracts
            existing_group.average_fill_price = average
            existing_group.catalogue_revision = catalogue.revision
            existing_group.reconciliation_snapshot_id = reconciliation.id
            existing_group.risk_policy_version = risk.version
            existing_group.credential_version = lease.credential_version
            existing_group.qualification_evidence_id = qualification.id
            existing_group.protection_version += 1
            existing_group.version += 1
            existing_group.updated_at = now
            legs = (
                await session.scalars(
                    select(GttOrder)
                    .where(
                        GttOrder.protection_group_id == existing_group.id,
                        GttOrder.status == "armed",
                    )
                    .with_for_update()
                )
            ).all()
            for leg in legs:
                leg.contracts = contracts
                if leg.trigger_kind == "stop_loss":
                    leg.trigger_price = format(stop_price, "f")
                elif leg.trigger_kind == "target":
                    leg.trigger_price = format(target_price, "f")
                elif leg.trigger_kind == "trailing_stop":
                    leg.trail_offset = format(trail_offset, "f")
                leg.version += 1
                leg.updated_at = now
            return True
        group_id = uuid4()
        session.add(
            ProtectionGroup(
                id=group_id,
                account_id=account_id,
                entry_intent_id=intent.id,
                product_id=preview.product_id,
                symbol=product.symbol,
                exit_side="sell" if preview.side == "buy" else "buy",
                protected_contracts=contracts,
                average_fill_price=average,
                strategy_version=None if strategy is None else strategy.version,
                protection_version=1,
                catalogue_revision=catalogue.revision,
                reconciliation_snapshot_id=reconciliation.id,
                risk_policy_version=risk.version,
                credential_version=lease.credential_version,
                qualification_evidence_id=qualification.id,
                claimed_leg_id=None,
                status="armed",
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        exit_side = "sell" if preview.side == "buy" else "buy"
        for kind, trigger, offset in (
            ("stop_loss", stop_price, None),
            ("target", target_price, None),
            ("trailing_stop", None, trail_offset),
        ):
            session.add(
                GttOrder(
                    id=uuid4(),
                    account_id=account_id,
                    created_by_user_id=preview.created_by_user_id,
                    protection_group_id=group_id,
                    product_id=preview.product_id,
                    symbol=product.symbol,
                    side=exit_side,
                    contracts=contracts,
                    trigger_kind=kind,
                    trigger_price=None if trigger is None else format(trigger, "f"),
                    trail_offset=None if offset is None else format(offset, "f"),
                    limit_price=None,
                    trail_anchor=None,
                    last_quote_id=None,
                    reduce_only=True,
                    status="armed",
                    idempotency_key=uuid4(),
                    triggered_order_id=None,
                    triggered_at=None,
                    trigger_evidence_sha256=None,
                    last_error_code=None,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
        return True


__all__ = ["sync_one_protection_group"]
