"""Deterministic aggregate replay work and output accounting."""

from __future__ import annotations

from dataclasses import dataclass

from hmt_crypto.backtest.errors import BacktestDataError


@dataclass(slots=True)
class ReplayBudget:
    """Fail before configured aggregate work or output limits are exceeded."""

    max_work_units: int
    max_output_records: int
    work_units: int = 0
    output_records: int = 0

    def consume_work(self, units: int) -> None:
        """Charge deterministic work units or reject the replay."""

        if type(units) is not int or units < 0 or self.work_units + units > self.max_work_units:
            raise BacktestDataError("aggregate work budget is exceeded") from None
        self.work_units += units

    def consume_output(self, records: int) -> None:
        """Charge immutable report records or reject the replay."""

        if (
            type(records) is not int
            or records < 0
            or self.output_records + records > self.max_output_records
        ):
            raise BacktestDataError("aggregate output budget is exceeded") from None
        self.output_records += records


__all__: list[str] = []
