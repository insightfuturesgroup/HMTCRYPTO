"""Closed errors for the authority-free backtest engine."""

from __future__ import annotations


class BacktestError(Exception):
    """Base class for deterministic backtest failures."""

    code = "backtest_error"

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"{self.code}: {detail}")


class BacktestInputError(BacktestError):
    """An immutable run assumption is malformed or unsupported."""

    code = "backtest_input"


class BacktestDataError(BacktestError):
    """Replay data is duplicated, unordered, ambiguous, or inconsistent."""

    code = "backtest_data"


class BacktestIncompleteError(BacktestError):
    """Required funding evidence is absent in strict mode."""

    code = "backtest_incomplete"

    def __init__(self, reasons: tuple[str, ...]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons))


__all__ = [
    "BacktestDataError",
    "BacktestError",
    "BacktestIncompleteError",
    "BacktestInputError",
]
