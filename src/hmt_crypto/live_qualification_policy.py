"""Immutable policy shared by Live qualification and order admission."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal

LIVE_QUALIFICATION_WINDOW_HOURS = 1
LIVE_QUALIFICATION_WINDOW = timedelta(hours=LIVE_QUALIFICATION_WINDOW_HOURS)
LIVE_RECONCILIATION_MAX_GAP_SECONDS = 60
# Store unchanged reconciliation evidence frequently enough that ordinary
# worker scheduling jitter cannot violate the qualification continuity bound.
LIVE_RECONCILIATION_EVIDENCE_INTERVAL_SECONDS = 30


def _canonical_decimal(value: object) -> str:
    normalized = Decimal(str(value)).normalize()
    rendered = format(normalized, "f")
    return "0" if Decimal(rendered) == 0 else rendered


def canonical_risk_policy_scope(
    *,
    enabled: object,
    limit_currency: object,
    usd_inr_rate: object,
    max_order_notional: object,
    max_position_notional: object,
    max_daily_loss: object,
    max_open_orders: object,
    max_inflight_signals: object,
) -> dict[str, object]:
    """Return the immutable, semantic risk scope used for qualification."""
    return {
        "enabled": bool(enabled),
        "limitCurrency": str(limit_currency).upper(),
        "usdInrRate": _canonical_decimal(usd_inr_rate),
        "maxOrderNotional": _canonical_decimal(max_order_notional),
        "maxPositionNotional": _canonical_decimal(max_position_notional),
        "maxDailyLoss": _canonical_decimal(max_daily_loss),
        "maxOpenOrders": int(max_open_orders),
        "maxInflightSignals": int(max_inflight_signals),
    }


APPROVED_LIVE_RISK_SCOPE = canonical_risk_policy_scope(
    enabled=True,
    limit_currency="INR",
    usd_inr_rate=100,
    max_order_notional=50,
    max_position_notional=100,
    max_daily_loss=20,
    max_open_orders=5,
    max_inflight_signals=5,
)


def risk_scope_is_approved(scope: Mapping[str, object]) -> bool:
    """Return whether a semantic risk scope matches the approved release scope."""
    return dict(scope) == APPROVED_LIVE_RISK_SCOPE


def qualification_window_is_complete(*, started_at: datetime, completed_at: datetime) -> bool:
    """Return whether immutable evidence covers the approved minimum window."""
    return completed_at - started_at >= LIVE_QUALIFICATION_WINDOW


__all__ = [
    "APPROVED_LIVE_RISK_SCOPE",
    "LIVE_QUALIFICATION_WINDOW",
    "LIVE_QUALIFICATION_WINDOW_HOURS",
    "LIVE_RECONCILIATION_EVIDENCE_INTERVAL_SECONDS",
    "LIVE_RECONCILIATION_MAX_GAP_SECONDS",
    "canonical_risk_policy_scope",
    "qualification_window_is_complete",
    "risk_scope_is_approved",
]
