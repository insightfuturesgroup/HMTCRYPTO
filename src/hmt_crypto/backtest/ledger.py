"""Exact linear-contract position, fee, funding, and equity accounting."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from hmt_crypto.backtest.arithmetic import allocation_context, arithmetic_context
from hmt_crypto.backtest.errors import BacktestDataError
from hmt_crypto.backtest.models import BacktestProductAssumption
from hmt_crypto.backtest.reports import BacktestEquityPoint, BacktestPositionSnapshot
from hmt_crypto.oms.paper_models import PaperSide

_ZERO = Decimal(0)


@dataclass(slots=True)
class _OpenPosition:
    contracts: int
    entry_cost: Decimal
    lifecycle_cashflow: Decimal
    allocated_realized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class FillAccounting:
    """Closing evidence returned by one ledger fill transition."""

    gross_realized_pnl: Decimal
    closed_contracts: int
    entry_price: Decimal


class BacktestLedger:
    """Run-local mutable accumulator; no state escapes except immutable snapshots."""

    def __init__(
        self,
        initial_cash: Decimal,
        products: dict[int, BacktestProductAssumption],
    ) -> None:
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self._completed_gross_realized_pnl = _ZERO
        self.total_fees = _ZERO
        self.total_funding = _ZERO
        self._products = products
        self._positions: dict[int, _OpenPosition] = {}
        self._marks: dict[int, Decimal] = {}
        self._history: list[tuple[datetime, int, int]] = []

    def contracts(self, product_id: int) -> int:
        """Return current signed contracts for one product."""
        position = self._positions.get(product_id)
        return 0 if position is None else position.contracts

    @property
    def gross_realized_pnl(self) -> Decimal:
        """Return completed exact cash-flow PnL plus open-position allocations."""

        with arithmetic_context():
            total = self._completed_gross_realized_pnl
            for position in self._positions.values():
                total += position.allocated_realized_pnl
        return total

    def update_mark(self, product_id: int, mark_price: Decimal) -> None:
        """Replace the latest explicit mark used for equity only."""
        self._marks[product_id] = mark_price

    def apply_fill(
        self,
        *,
        product_id: int,
        side: PaperSide,
        contracts: int,
        price: Decimal,
        fee: Decimal,
        observed_at: datetime,
    ) -> FillAccounting:
        """Apply one fill while preserving aggregate entry cost exactly."""
        product = self._products[product_id]
        delta = contracts if side is PaperSide.BUY else -contracts
        prior = self._positions.get(product_id)
        old_contracts = 0 if prior is None else prior.contracts
        realized = _ZERO
        closed = 0
        with arithmetic_context():
            old_entry_cost = _ZERO if prior is None else prior.entry_cost
            if old_contracts == 0:
                entry_price = price
            else:
                entry_denominator = Decimal(abs(old_contracts)) * product.contract_value
                with allocation_context():
                    entry_price = old_entry_cost / entry_denominator
            if old_contracts == 0 or (old_contracts > 0) == (delta > 0):
                next_contracts = old_contracts + delta
                added_cost = price * Decimal(abs(delta)) * product.contract_value
                entry_direction = Decimal(1 if delta > 0 else -1)
                lifecycle_cashflow = (
                    _ZERO if prior is None else prior.lifecycle_cashflow
                ) - entry_direction * added_cost
                allocated_realized = _ZERO if prior is None else prior.allocated_realized_pnl
                self._positions[product_id] = _OpenPosition(
                    next_contracts,
                    old_entry_cost + added_cost,
                    lifecycle_cashflow,
                    allocated_realized,
                )
            else:
                assert prior is not None
                closed = min(abs(old_contracts), abs(delta))
                if closed == abs(old_contracts):
                    released_cost = old_entry_cost
                else:
                    release_numerator = old_entry_cost * Decimal(closed)
                    with allocation_context():
                        released_cost = release_numerator / Decimal(abs(old_contracts))
                exit_value = price * Decimal(closed) * product.contract_value
                direction = Decimal(1 if old_contracts > 0 else -1)
                realized = (exit_value - released_cost) * direction
                lifecycle_cashflow = prior.lifecycle_cashflow + direction * exit_value
                next_contracts = old_contracts + delta
                if next_contracts == 0:
                    realized = lifecycle_cashflow - prior.allocated_realized_pnl
                    self._completed_gross_realized_pnl += lifecycle_cashflow
                    self._positions.pop(product_id)
                elif (next_contracts > 0) == (old_contracts > 0):
                    self._positions[product_id] = _OpenPosition(
                        next_contracts,
                        old_entry_cost - released_cost,
                        lifecycle_cashflow,
                        prior.allocated_realized_pnl + realized,
                    )
                else:
                    reversal_cost = price * Decimal(abs(next_contracts)) * product.contract_value
                    realized = lifecycle_cashflow - prior.allocated_realized_pnl
                    self._completed_gross_realized_pnl += lifecycle_cashflow
                    reversal_direction = Decimal(1 if next_contracts > 0 else -1)
                    self._positions[product_id] = _OpenPosition(
                        next_contracts,
                        reversal_cost,
                        -(reversal_direction * reversal_cost),
                        _ZERO,
                    )
            self.total_fees += fee
            self.cash = (
                self.initial_cash + self.gross_realized_pnl - self.total_fees + self.total_funding
            )
        self._history.append((observed_at, product_id, self.contracts(product_id)))
        return FillAccounting(realized, closed, entry_price)

    def apply_funding(self, cashflow: Decimal) -> None:
        """Apply signed funding cashflow (receipt positive, payment negative)."""
        with arithmetic_context():
            self.total_funding += cashflow
            self.cash = (
                self.initial_cash + self.gross_realized_pnl - self.total_fees + self.total_funding
            )

    def position_at(self, product_id: int, observed_at: datetime) -> int:
        """Return the last signed position at or before a realization snapshot."""
        contracts = 0
        for changed_at, changed_product, changed_contracts in self._history:
            if changed_at > observed_at:
                break
            if changed_product == product_id:
                contracts = changed_contracts
        return contracts

    @property
    def position_history_size(self) -> int:
        """Return the exact number of position transitions available to scan."""

        return len(self._history)

    def positions_at(
        self,
        product_id: int,
        observed_times: tuple[datetime, ...],
    ) -> Iterator[tuple[datetime, int]]:
        """Yield snapshot positions in one ordered history-and-schedule sweep."""

        history_index = 0
        contracts = 0
        for observed_at in observed_times:
            while (
                history_index < len(self._history)
                and self._history[history_index][0] <= observed_at
            ):
                _, changed_product, changed_contracts = self._history[history_index]
                if changed_product == product_id:
                    contracts = changed_contracts
                history_index += 1
            yield observed_at, contracts

    def held_interval(
        self,
        product_id: int,
        run_end: datetime,
    ) -> tuple[datetime, datetime] | None:
        """Return the conservative first-open through final-close held interval."""
        first: datetime | None = None
        last: datetime | None = None
        current = 0
        for observed_at, changed_product, contracts in self._history:
            if changed_product != product_id:
                continue
            if first is None and contracts != 0:
                first = observed_at
            if current != 0 and contracts == 0:
                last = observed_at
            current = contracts
        if first is None:
            return None
        if current != 0:
            last = run_end
        if last is None:
            last = first
        return first, last

    def unrealized_pnl(self) -> Decimal:
        """Return exact unrealized PnL using the latest explicit marks."""
        total = _ZERO
        with arithmetic_context():
            for product_id, position in self._positions.items():
                mark = self._marks.get(product_id)
                if mark is None:
                    raise BacktestDataError("open position is missing a current mark") from None
                product = self._products[product_id]
                mark_value = mark * Decimal(abs(position.contracts)) * product.contract_value
                direction = Decimal(1 if position.contracts > 0 else -1)
                total += (mark_value - position.entry_cost) * direction
        return total

    def equity_point(
        self,
        sequence: int,
        observed_at: datetime,
    ) -> BacktestEquityPoint:
        """Snapshot cash, unrealized PnL, and equity after an event."""
        unrealized = self.unrealized_pnl()
        with arithmetic_context():
            return BacktestEquityPoint(
                sequence=sequence,
                observed_at=observed_at,
                cash=self.cash,
                unrealized_pnl=unrealized,
                equity=self.cash + unrealized,
            )

    def final_positions(self) -> tuple[BacktestPositionSnapshot, ...]:
        """Build sorted immutable final position snapshots."""
        snapshots: list[BacktestPositionSnapshot] = []
        with arithmetic_context():
            for product_id, position in sorted(self._positions.items()):
                mark = self._marks.get(product_id)
                if mark is None:
                    raise BacktestDataError("open position is missing a final mark") from None
                product = self._products[product_id]
                average = position.entry_cost / (
                    Decimal(abs(position.contracts)) * product.contract_value
                )
                mark_value = mark * Decimal(abs(position.contracts)) * product.contract_value
                direction = Decimal(1 if position.contracts > 0 else -1)
                unrealized = (mark_value - position.entry_cost) * direction
                snapshots.append(
                    BacktestPositionSnapshot(
                        product_id,
                        position.contracts,
                        average,
                        mark,
                        unrealized,
                    )
                )
        return tuple(snapshots)


__all__ = ["BacktestLedger", "FillAccounting"]
