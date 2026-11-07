"""Pure daily-loss calculations over cursor-proven UTC snapshots."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hmt_crypto.risk.inputs import DailyAccounting
from hmt_crypto.risk.math import amount_from_scaled, scaled_amount
from hmt_crypto.risk.models import Completeness, RiskInputError


def expected_window_start(evaluated_at: object, reset_second_utc: object) -> datetime:
    """Return the current daily risk window's exact UTC reset instant."""
    if (
        type(evaluated_at) is not datetime
        or evaluated_at.tzinfo is not UTC
        or type(reset_second_utc) is not int
        or not 0 <= reset_second_utc < 86_400
    ):
        raise RiskInputError() from None
    midnight = evaluated_at.replace(hour=0, minute=0, second=0, microsecond=0)
    reset = midnight + timedelta(seconds=reset_second_utc)
    return reset if evaluated_at >= reset else reset - timedelta(days=1)


def conservative_daily_loss(accounting: object) -> Decimal:
    """Return max(PnL loss, equity drawdown) plus unresolved loss reserve."""
    if type(accounting) is not DailyAccounting:
        raise RiskInputError() from None
    if accounting.completeness is not Completeness.COMPLETE:
        raise RiskInputError() from None
    values = (
        accounting.baseline_equity,
        accounting.current_equity,
        accounting.realized_pnl,
        accounting.unrealized_pnl,
        accounting.unresolved_loss_reserve,
    )
    if any(value is None for value in values):
        raise RiskInputError() from None
    baseline, current, realized, unrealized, reserve = (scaled_amount(value) for value in values)
    pnl_loss = max(0, -realized) + max(0, -unrealized)
    equity_drawdown = max(0, baseline - current)
    return amount_from_scaled(max(pnl_loss, equity_drawdown) + reserve)


__all__ = ["conservative_daily_loss", "expected_window_start"]
