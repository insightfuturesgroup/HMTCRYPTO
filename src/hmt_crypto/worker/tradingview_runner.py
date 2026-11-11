"""Risk-checked admission of replay-safe TradingView signals."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from sqlalchemy import desc, select

from hmt_crypto.api.live_order_admission import (
    LiveOrderAdmissionError,
    evaluate_order_sizing,
    position_notional,
    projected_position_contracts,
)
from hmt_crypto.api.live_submission import make_live_client_order_id
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.models import (
    Account,
    AccountLiveControl,
    AccountRiskPolicy,
    CatalogueProduct,
    CatalogueSnapshot,
    LiveOrderIntent,
    LiveOrderPreview,
    LiveOrderSubmission,
    MarketQuote,
    PlatformLiveControl,
    PrivateReconciliationSnapshot,
    PrivateWorkerLease,
    TradingViewSignal,
    TradingViewStrategy,
)


@dataclass(frozen=True, slots=True)
class TradingViewCycleResult:
    kind: str
    signal_id: UUID | None
    submission_id: UUID | None
    reason: str | None


def _payload_rows(value: str | None) -> list[object]:
    try:
        rows = json.loads(value or "")
    except (TypeError, ValueError):
        raise ValueError("invalid reconciliation payload") from None
    if not isinstance(rows, list):
        raise ValueError("invalid reconciliation payload")
    return rows


def _reject(signal: TradingViewSignal, *, now: datetime, reason: str) -> TradingViewCycleResult:
    signal.status = "rejected"
    signal.rejection_code = reason
    signal.processed_at = now
    return TradingViewCycleResult("REJECTED", signal.id, None, reason)


async def admit_one_tradingview_signal(
    database: Database, *, account_id: UUID, owner_id: UUID
) -> TradingViewCycleResult:
    """Admit at most one signal under the current lease and exchange snapshot."""
    if (
        type(account_id) is not UUID
        or account_id.int == 0
        or type(owner_id) is not UUID
        or owner_id.int == 0
    ):
        raise ValueError("invalid TradingView worker scope")
    now = datetime.now(UTC)
    async with database.transaction() as session:
        signal = await session.scalar(
            select(TradingViewSignal)
            .where(
                TradingViewSignal.account_id == account_id,
                TradingViewSignal.status == "received",
            )
            .order_by(TradingViewSignal.received_at, TradingViewSignal.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if signal is None:
            return TradingViewCycleResult("IDLE", None, None, None)
        strategy = await session.get(TradingViewStrategy, signal.strategy_id)
        if strategy is None or not strategy.enabled or strategy.account_id != account_id:
            return _reject(signal, now=now, reason="strategy_disabled")
        if now - signal.received_at > timedelta(seconds=60):
            return _reject(signal, now=now, reason="signal_expired")
        account = await session.get(Account, account_id)
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
        global_control = await session.get(PlatformLiveControl, "global")
        gate_open = bool(
            control and (control.exits_enabled if strategy.reduce_only else control.entries_enabled)
        )
        if (
            account is None
            or account.environment != "prod"
            or account.mode != "LIVE"
            or account.identity_verified_at is None
            or lease is None
            or lease.owner_id != owner_id
            or lease.lease_until <= now
            or account.current_credential_version != lease.credential_version
            or control is None
            or control.credential_state != "verified"
            or control.reconciliation_state != "ready"
            or not gate_open
            or risk is None
            or not risk.enabled
            or reconciliation is None
            or reconciliation.outcome != "ready"
            or reconciliation.observed_at < now - timedelta(seconds=60)
            or reconciliation.worker_owner_id != lease.owner_id
            or reconciliation.worker_generation != lease.generation
            or reconciliation.credential_version != lease.credential_version
            or (
                not strategy.reduce_only
                and (global_control is None or not global_control.entries_enabled)
            )
        ):
            return TradingViewCycleResult("DEFERRED", signal.id, None, "readiness_gate")
        try:
            allowed_products = set(json.loads(risk.allowed_product_ids))
        except (TypeError, ValueError):
            return _reject(signal, now=now, reason="invalid_risk_policy")
        if strategy.product_id not in allowed_products:
            return _reject(signal, now=now, reason="product_not_allowed")
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
                    CatalogueProduct.product_id == strategy.product_id,
                    CatalogueProduct.symbol == strategy.symbol,
                )
            )
        )
        quote = await session.scalar(
            select(MarketQuote).where(
                MarketQuote.region == "india",
                MarketQuote.environment == "prod",
                MarketQuote.product_id == strategy.product_id,
                MarketQuote.observed_at >= now - timedelta(seconds=15),
            )
        )
        if catalogue is None or product_row is None or quote is None:
            return TradingViewCycleResult("DEFERRED", signal.id, None, "market_state")
        market_price = quote.best_ask if signal.side == "buy" else quote.best_bid
        try:
            sizing = evaluate_order_sizing(
                product=product_row.to_product(),
                contracts=strategy.contracts,
                order_type=strategy.order_type,
                limit_price=signal.limit_price,
                market_price=market_price,
                reduce_only=strategy.reduce_only,
                max_order_notional=risk.max_order_notional,
            )
            positions = _payload_rows(reconciliation.positions_payload)
            open_orders = _payload_rows(reconciliation.open_orders_payload)
            wallet = _payload_rows(reconciliation.wallet_transactions_payload)
        except (LiveOrderAdmissionError, ValueError):
            return _reject(signal, now=now, reason="invalid_order_or_state")
        if len(open_orders) >= risk.max_open_orders and not strategy.reduce_only:
            return _reject(signal, now=now, reason="open_order_limit")
        signed_position = 0
        for item in positions:
            if not isinstance(item, dict):
                return _reject(signal, now=now, reason="invalid_position_state")
            if item.get("product_id") == strategy.product_id:
                value = item.get("size")
                if type(value) is not int:
                    return _reject(signal, now=now, reason="invalid_position_state")
                signed_position = value
        direction = 1 if signal.side == "buy" else -1
        if strategy.reduce_only and (
            signed_position == 0
            or signed_position * direction >= 0
            or strategy.contracts > abs(signed_position)
        ):
            return _reject(signal, now=now, reason="reduce_only_violation")
        try:
            projected = projected_position_contracts(
                positions=positions,
                open_orders=open_orders,
                product_id=strategy.product_id,
                new_side=signal.side,
                new_contracts=strategy.contracts,
                new_reduce_only=strategy.reduce_only,
            )
            projected_notional = (
                Decimal(0)
                if projected == 0
                else position_notional(
                    product=product_row.to_product(),
                    contracts=projected,
                    price=sizing.price,
                )
            )
        except LiveOrderAdmissionError:
            return _reject(signal, now=now, reason="position_sizing_invalid")
        if projected_notional > risk.max_position_notional:
            return _reject(signal, now=now, reason="position_limit")
        daily_net = Decimal(0)
        for item in wallet:
            if not isinstance(item, dict):
                return _reject(signal, now=now, reason="invalid_wallet_state")
            if (
                type(item.get("created_at")) is str
                and str(item["created_at"])[:10] == now.date().isoformat()
                and item.get("transaction_type")
                in {"cashflow", "commission", "funding", "liquidation_fee", "settlement"}
            ):
                try:
                    daily_net += Decimal(str(item.get("amount")))
                except InvalidOperation:
                    return _reject(signal, now=now, reason="invalid_wallet_state")
        if max(Decimal(0), -daily_net) >= risk.max_daily_loss and not strategy.reduce_only:
            return _reject(signal, now=now, reason="daily_loss_limit")
        preview_id, intent_id, submission_id = uuid4(), uuid4(), uuid4()
        evidence = hashlib.sha256(
            (
                f"tradingview-admission:v1:{signal.payload_sha256}:{reconciliation.id}:"
                f"{catalogue.id}:{risk.version}:{format(sizing.notional, 'f')}"
            ).encode("ascii")
        ).hexdigest()
        session.add(
            LiveOrderPreview(
                id=preview_id,
                account_id=account_id,
                created_by_user_id=strategy.created_by_user_id,
                request_id=signal.id,
                product_id=strategy.product_id,
                side=signal.side,
                contracts=strategy.contracts,
                order_type=strategy.order_type,
                limit_price=None if signal.limit_price is None else sizing.price,
                reduce_only=strategy.reduce_only,
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
                created_by_user_id=strategy.created_by_user_id,
                idempotency_key=signal.id,
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
        signal.status = "admitted"
        signal.intent_id = intent_id
        signal.rejection_code = None
        signal.processed_at = now
        return TradingViewCycleResult("ADMITTED", signal.id, submission_id, None)


__all__ = ["TradingViewCycleResult", "admit_one_tradingview_signal"]
