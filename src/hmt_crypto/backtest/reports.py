"""Immutable in-memory results produced by deterministic replay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from hmt_crypto.backtest.models import BacktestRunStatus, LiquidityRole
from hmt_crypto.oms.paper_models import PaperSide


@dataclass(frozen=True, slots=True)
class BacktestFill:
    """One fee-bearing PAPER fill included in the report."""

    fill_key_sha256: str
    order_id: UUID
    product_id: int
    side: PaperSide
    contracts: int
    price: Decimal
    notional: Decimal
    liquidity_role: LiquidityRole
    fee_rate: Decimal
    fee: Decimal
    gross_realized_pnl: Decimal
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    """The closing component of one fill, including gross realized PnL."""

    order_id: UUID
    product_id: int
    contracts: int
    entry_price: Decimal
    exit_price: Decimal
    gross_pnl: Decimal
    exit_fee: Decimal
    closed_at: datetime


@dataclass(frozen=True, slots=True)
class BacktestFundingRecord:
    """Funding snapshot evidence and signed cashflow."""

    product_id: int
    observed_at: datetime
    position_contracts: int
    reference_price: Decimal
    rate: Decimal
    position_value: Decimal
    cashflow: Decimal
    schedule_version: str
    rate_source: str


@dataclass(frozen=True, slots=True)
class BacktestPositionSnapshot:
    """One final open position with explicit valuation evidence."""

    product_id: int
    contracts: int
    average_entry_price: Decimal
    mark_price: Decimal
    unrealized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class BacktestEquityPoint:
    """Equity after one accepted replay event."""

    sequence: int
    observed_at: datetime
    cash: Decimal
    unrealized_pnl: Decimal
    equity: Decimal


@dataclass(frozen=True, slots=True)
class BacktestReport:
    """Complete in-memory result and deterministic provenance identities."""

    status: BacktestRunStatus
    run_id_sha256: str
    config_sha256: str
    data_sha256: str
    initial_cash: Decimal
    final_equity: Decimal
    net_pnl: Decimal
    gross_realized_pnl: Decimal
    realized_net_pnl: Decimal
    unrealized_pnl: Decimal
    total_fees: Decimal
    total_funding: Decimal
    max_drawdown: Decimal
    max_drawdown_fraction: Decimal
    fills: tuple[BacktestFill, ...]
    trades: tuple[BacktestTrade, ...]
    funding: tuple[BacktestFundingRecord, ...]
    equity_curve: tuple[BacktestEquityPoint, ...]
    final_positions: tuple[BacktestPositionSnapshot, ...]
    assumptions: tuple[str, ...]
    incomplete_reasons: tuple[str, ...]
    halt_reason: str | None


__all__ = [
    "BacktestEquityPoint",
    "BacktestFill",
    "BacktestFundingRecord",
    "BacktestPositionSnapshot",
    "BacktestReport",
    "BacktestTrade",
]
