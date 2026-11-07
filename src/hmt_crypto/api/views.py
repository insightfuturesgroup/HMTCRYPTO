"""Explicit allowlist serialization for control-API response bodies."""

from __future__ import annotations

from datetime import UTC, datetime

from hmt_crypto.api.control_plane import (
    DashboardSnapshot,
    HealthSnapshot,
    OrderView,
    PositionView,
    QueuedCommand,
)


def utc_text(value: datetime) -> str:
    """Serialize one aware timestamp in canonical UTC display form."""
    if value.tzinfo is None:
        raise ValueError("timestamp must be aware") from None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def health_view(value: HealthSnapshot) -> dict[str, object]:
    """Serialize safe component health without implementation details."""
    return {
        "live": value.live,
        "ready": value.ready,
        "sequence": value.sequence,
        "observedAt": utc_text(value.observed_at),
        "components": [
            {
                "component": item.component,
                "state": item.state,
                "reason": item.reason,
                "observedAt": utc_text(item.observed_at),
            }
            for item in value.components
        ],
    }


def _position(value: PositionView) -> dict[str, object]:
    return {
        "productId": value.product_id,
        "symbol": value.symbol,
        "side": value.side,
        "contracts": value.contracts,
        "entryPrice": value.entry_price,
        "markPrice": value.mark_price,
        "lastPrice": value.last_price,
        "leverage": value.leverage,
        "liquidationPrice": value.liquidation_price,
        "unrealisedPnl": value.unrealised_pnl,
        "realisedPnl": value.realised_pnl,
        "marginUsed": value.margin_used,
        "fundingRate": value.funding_rate,
        "protectionState": value.protection_state,
        "protectionObservedAt": (
            None if value.protection_observed_at is None else utc_text(value.protection_observed_at)
        ),
        "protectionReconciliationGeneration": value.protection_reconciliation_generation,
    }


def _order(value: OrderView) -> dict[str, object]:
    return {
        "localOrderId": str(value.local_order_id),
        "exchangeOrderId": value.exchange_order_id,
        "clientOrderId": value.client_order_id,
        "symbol": value.symbol,
        "side": value.side,
        "orderType": value.order_type,
        "contracts": value.contracts,
        "limitPrice": value.limit_price,
        "status": value.status,
        "reduceOnly": value.reduce_only,
        "updatedAt": utc_text(value.updated_at),
    }


def dashboard_view(value: DashboardSnapshot) -> dict[str, object]:
    """Serialize the exact dashboard contract; raw exchange data never enters it."""
    return {
        "sequence": value.sequence,
        "generatedAt": utc_text(value.generated_at),
        "evidenceObservedAt": (
            None if value.evidence_observed_at is None else utc_text(value.evidence_observed_at)
        ),
        "region": value.region,
        "environment": value.environment,
        "mode": value.mode,
        "accountLabel": value.account_label,
        "catalogueFresh": value.catalogue_fresh,
        "streamConnected": value.stream_connected,
        "reconciliationState": value.reconciliation_state,
        "riskHalted": value.risk_halted,
        "collateralAsset": value.collateral_asset,
        "equity": value.equity,
        "availableBalance": value.available_balance,
        "totalExposure": value.total_exposure,
        "dailyPnl": value.daily_pnl,
        "marginUsagePercent": value.margin_usage_percent,
        "positions": [_position(item) for item in value.positions],
        "openOrders": [_order(item) for item in value.open_orders],
    }


def command_view(value: QueuedCommand) -> dict[str, object]:
    """Serialize a durable command receipt/status."""
    return {
        "commandId": str(value.command_id),
        "state": value.state,
        "acceptedAt": utc_text(value.accepted_at),
    }


__all__ = ["command_view", "dashboard_view", "health_view", "utc_text"]
