"""Pure deterministic BACKTEST mode with explicit fee and funding evidence."""

from hmt_crypto.backtest.engine import run_backtest
from hmt_crypto.backtest.errors import (
    BacktestDataError,
    BacktestError,
    BacktestIncompleteError,
    BacktestInputError,
)
from hmt_crypto.backtest.models import (
    BacktestConfig,
    BacktestFundingEvent,
    BacktestFundingSchedule,
    BacktestLimits,
    BacktestMarketEvent,
    BacktestOrderDecision,
    BacktestProductAssumption,
    BacktestRunStatus,
    LiquidityRole,
)
from hmt_crypto.backtest.reports import (
    BacktestEquityPoint,
    BacktestFill,
    BacktestFundingRecord,
    BacktestPositionSnapshot,
    BacktestReport,
    BacktestTrade,
)

__all__ = [
    "BacktestConfig",
    "BacktestDataError",
    "BacktestEquityPoint",
    "BacktestError",
    "BacktestFill",
    "BacktestFundingEvent",
    "BacktestFundingRecord",
    "BacktestFundingSchedule",
    "BacktestIncompleteError",
    "BacktestInputError",
    "BacktestLimits",
    "BacktestMarketEvent",
    "BacktestOrderDecision",
    "BacktestPositionSnapshot",
    "BacktestProductAssumption",
    "BacktestReport",
    "BacktestRunStatus",
    "BacktestTrade",
    "LiquidityRole",
    "run_backtest",
]
