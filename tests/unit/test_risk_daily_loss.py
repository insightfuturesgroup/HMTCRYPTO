"""Conservative UTC-window daily-loss accounting."""

from datetime import UTC, datetime
from decimal import Decimal, localcontext

import pytest

from hmt_crypto.risk.daily_loss import conservative_daily_loss, expected_window_start
from hmt_crypto.risk.inputs import DailyAccounting
from hmt_crypto.risk.models import Completeness, RiskInputError

NOW = datetime(2026, 11, 4, 12, tzinfo=UTC)
HASH = "a" * 64


def _accounting(**changes: object) -> DailyAccounting:
    values: dict[str, object] = {
        "completeness": Completeness.COMPLETE,
        "observed_at": NOW,
        "window_start_at": datetime(2026, 11, 4, tzinfo=UTC),
        "baseline_observed_at": datetime(2026, 11, 4, tzinfo=UTC),
        "baseline_equity": Decimal("100"),
        "current_equity": Decimal("95"),
        "realized_pnl": Decimal("-7"),
        "unrealized_pnl": Decimal("-1"),
        "unresolved_loss_reserve": Decimal("2"),
        "ledger_cursor_start": "cursor-before-reset",
        "ledger_cursor_end": "cursor-now",
        "ledger_evidence_sha256": HASH,
        "included_reservation_keys": ("b" * 64,),
    }
    values.update(changes)
    return DailyAccounting(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "evaluated_at,reset_second,expected",
    [
        (
            datetime(2026, 11, 4, 0, 0, 0, tzinfo=UTC),
            0,
            datetime(2026, 11, 4, tzinfo=UTC),
        ),
        (
            datetime(2026, 11, 4, 0, 59, 59, tzinfo=UTC),
            3_600,
            datetime(2026, 11, 3, 1, tzinfo=UTC),
        ),
        (
            datetime(2026, 11, 4, 1, 0, 0, tzinfo=UTC),
            3_600,
            datetime(2026, 11, 4, 1, tzinfo=UTC),
        ),
    ],
)
def test_expected_window_start_uses_exact_configured_utc_reset(
    evaluated_at: datetime, reset_second: int, expected: datetime
) -> None:
    assert expected_window_start(evaluated_at, reset_second) == expected


def test_daily_loss_uses_more_conservative_pnl_or_equity_drawdown_then_reserve() -> None:
    # Realised + negative unrealised is 8, equity drawdown is 5, reserve is 2.
    assert conservative_daily_loss(_accounting()) == Decimal("10")
    # Equity drawdown dominates when it is larger than the PnL view.
    accounting = _accounting(current_equity=Decimal("80"))
    assert conservative_daily_loss(accounting) == Decimal("22")


def test_daily_loss_math_ignores_the_ambient_decimal_context() -> None:
    with localcontext() as context:
        context.prec = 3
        accounting = _accounting(
            baseline_equity=Decimal("100.000000000000000001"),
            current_equity=Decimal("99"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            unresolved_loss_reserve=Decimal("0.000000000000000001"),
        )
        assert conservative_daily_loss(accounting) == Decimal("1.000000000000000002")


@pytest.mark.parametrize(
    "changes",
    [
        {"completeness": Completeness.AMBIGUOUS},
        {"ledger_cursor_start": None},
        {"baseline_observed_at": NOW},
        {"observed_at": datetime(2026, 11, 3, 23, 59, tzinfo=UTC)},
        {"baseline_equity": Decimal("0")},
        {"unresolved_loss_reserve": Decimal("-1")},
    ],
)
def test_malformed_or_incomplete_accounting_cannot_yield_a_loss_number(
    changes: dict[str, object],
) -> None:
    with pytest.raises(RiskInputError):
        conservative_daily_loss(_accounting(**changes))
