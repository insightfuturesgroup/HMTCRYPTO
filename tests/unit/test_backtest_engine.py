"""Event-driven backtest replay, accounting, and safety behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from hmt_crypto.backtest import (
    BacktestConfig,
    BacktestDataError,
    BacktestFundingEvent,
    BacktestFundingSchedule,
    BacktestIncompleteError,
    BacktestLimits,
    BacktestMarketEvent,
    BacktestOrderDecision,
    BacktestProductAssumption,
    BacktestRunStatus,
    LiquidityRole,
    run_backtest,
)
from hmt_crypto.oms.models import OmsExecutionMode
from hmt_crypto.oms.paper_models import (
    PaperBracketSpec,
    PaperCandle,
    PaperModelConfig,
    PaperOrderIntent,
    PaperOrderKind,
    PaperSide,
    PaperTopOfBook,
    PaperTriggerDirection,
)

_NOW = datetime(2026, 11, 6, tzinfo=UTC)
_ACCOUNT = UUID(int=101)
_PRODUCT = 27
_STREAM = "delta-btcusd-backtest"
_SOURCE = "a" * 64


def _product(
    *,
    product_id: int = _PRODUCT,
    perpetual: bool = False,
    maker: Decimal = Decimal("0.001"),
    taker: Decimal = Decimal("0.01"),
) -> BacktestProductAssumption:
    return BacktestProductAssumption(
        product_id=product_id,
        contract_value=Decimal("1"),
        is_perpetual=perpetual,
        maker_fee_rate=maker,
        taker_fee_rate=taker,
        fee_schedule_version="delta-product-snapshot-v1",
        product_snapshot_sha256=_SOURCE,
    )


def _config(
    *,
    product: BacktestProductAssumption | None = None,
    schedules: tuple[BacktestFundingSchedule, ...] = (),
    strict: bool = True,
    limits: BacktestLimits | None = None,
) -> BacktestConfig:
    return BacktestConfig(
        paper_account_id=_ACCOUNT,
        initial_cash=Decimal("1000"),
        products=(product or _product(),),
        funding_schedules=schedules,
        strict_funding=strict,
        assumptions_version="phase4-v1",
        max_events=100,
        limits=limits or BacktestLimits(),
    )


def _model(*, maximum: int = 100, latency: int = 0) -> PaperModelConfig:
    return PaperModelConfig(
        latency_events=latency,
        slippage_bps=0,
        max_fill_contracts_per_event=maximum,
    )


def _intent(
    number: int,
    *,
    side: PaperSide = PaperSide.BUY,
    contracts: int = 1,
    kind: PaperOrderKind = PaperOrderKind.MARKET,
    limit: Decimal | None = None,
    stop: Decimal | None = None,
    trail: Decimal | None = None,
    direction: PaperTriggerDirection | None = None,
    reduce_only: bool = False,
    bracket: PaperBracketSpec | None = None,
    tick: Decimal = Decimal("0.5"),
    product_id: int = _PRODUCT,
) -> PaperOrderIntent:
    return PaperOrderIntent(
        order_id=UUID(int=number),
        paper_account_id=_ACCOUNT,
        mode=OmsExecutionMode.BACKTEST,
        product_id=product_id,
        side=side,
        contracts=contracts,
        kind=kind,
        tick_size=tick,
        limit_price=limit,
        stop_price=stop,
        trail_amount=trail,
        trigger_direction=direction,
        reduce_only=reduce_only,
        bracket=bracket,
    )


def _decision(
    intent: PaperOrderIntent,
    *,
    role: LiquidityRole = LiquidityRole.TAKER,
    model: PaperModelConfig | None = None,
    stop_role: LiquidityRole | None = None,
    profit_role: LiquidityRole | None = None,
) -> BacktestOrderDecision:
    return BacktestOrderDecision(
        intent=intent,
        model_config=model or _model(),
        liquidity_role=role,
        stop_loss_liquidity_role=stop_role,
        take_profit_liquidity_role=profit_role,
    )


def _book(
    index: int,
    *,
    second: int | None = None,
    bid: Decimal = Decimal("99.5"),
    ask: Decimal = Decimal("100"),
    bid_size: int = 100,
    ask_size: int = 100,
    reference: Decimal | None = None,
    product_id: int = _PRODUCT,
    stream_id: str = _STREAM,
) -> PaperTopOfBook:
    return PaperTopOfBook(
        stream_id=stream_id,
        product_id=product_id,
        event_index=index,
        observed_at=_NOW + timedelta(seconds=second if second is not None else index),
        bid_price=bid,
        ask_price=ask,
        bid_contracts=bid_size,
        ask_contracts=ask_size,
        trigger_reference=reference or ask,
    )


def _market(
    sequence: int,
    event: PaperTopOfBook | PaperCandle,
    *,
    mark: Decimal | None = None,
    decisions: tuple[BacktestOrderDecision, ...] = (),
    long_liquidation: Decimal | None = None,
    short_liquidation: Decimal | None = None,
) -> BacktestMarketEvent:
    default_mark = (
        event.trigger_reference if isinstance(event, PaperTopOfBook) else event.close_price
    )
    return BacktestMarketEvent(
        sequence=sequence,
        market_event=event,
        mark_price=mark or default_mark,
        decisions=decisions,
        long_liquidation_price=long_liquidation,
        short_liquidation_price=short_liquidation,
    )


def _schedule(
    *times: datetime,
    end: datetime | None = None,
    product_id: int = _PRODUCT,
) -> BacktestFundingSchedule:
    return BacktestFundingSchedule(
        product_id=product_id,
        version="delta-funding-calendar-v1",
        source_sha256="b" * 64,
        coverage_start=_NOW,
        coverage_end=end or (_NOW + timedelta(seconds=10)),
        realization_times=tuple(times),
    )


def test_decision_at_close_cannot_fill_until_a_later_market_event() -> None:
    decision = _decision(_intent(1, contracts=2))
    closed_candle = PaperCandle(
        stream_id=_STREAM,
        product_id=_PRODUCT,
        event_index=1,
        observed_at=_NOW + timedelta(seconds=1),
        open_price=Decimal("100"),
        high_price=Decimal("105"),
        low_price=Decimal("95"),
        close_price=Decimal("104"),
        volume_contracts=100,
    )
    source = _market(1, closed_candle, decisions=(decision,))
    execution = _market(2, _book(2, ask=Decimal("101")), mark=Decimal("101"))

    report = run_backtest(_config(), (source, execution))

    assert len(report.fills) == 1
    assert report.fills[0].observed_at == execution.observed_at
    assert report.fills[0].price == Decimal("101")
    assert report.fills[0].contracts == 2
    assert report.total_fees == Decimal("2.02")
    assert report.final_positions[0].contracts == 2


def test_partial_fills_share_paper_liquidity_and_preserve_order_role() -> None:
    decision = _decision(_intent(2, contracts=3), model=_model(maximum=2))
    events = (
        _market(1, _book(1), decisions=(decision,)),
        _market(2, _book(2, ask_size=1)),
        _market(3, _book(3, ask_size=10)),
    )

    report = run_backtest(_config(), events)

    assert [fill.contracts for fill in report.fills] == [1, 2]
    assert {fill.liquidity_role for fill in report.fills} == {LiquidityRole.TAKER}
    assert report.final_positions[0].contracts == 3


def test_explicit_maker_and_taker_rates_feed_round_trip_pnl() -> None:
    buy = _decision(_intent(10), role=LiquidityRole.TAKER)
    sell = _decision(
        _intent(
            11,
            side=PaperSide.SELL,
            kind=PaperOrderKind.LIMIT,
            limit=Decimal("110"),
            reduce_only=True,
        ),
        role=LiquidityRole.MAKER,
    )
    events = (
        _market(1, _book(1), decisions=(buy,)),
        _market(2, _book(2), decisions=(sell,)),
        _market(3, _book(3, bid=Decimal("110"), ask=Decimal("110.5")), mark=Decimal("110")),
    )

    report = run_backtest(_config(), events)

    assert [fill.liquidity_role for fill in report.fills] == [
        LiquidityRole.TAKER,
        LiquidityRole.MAKER,
    ]
    assert [fill.fee for fill in report.fills] == [Decimal("1.00"), Decimal("0.110")]
    assert report.gross_realized_pnl == Decimal("10")
    assert report.realized_net_pnl == Decimal("8.890")
    assert report.total_fees == Decimal("1.110")
    assert report.net_pnl == Decimal("8.890")
    assert report.trades[0].gross_pnl == Decimal("10")


@pytest.mark.parametrize(
    ("side", "rate", "expected", "expected_net"),
    [
        (PaperSide.BUY, Decimal("0.01"), Decimal("-2.00"), Decimal("-2.00")),
        (PaperSide.BUY, Decimal("-0.01"), Decimal("2.00"), Decimal("2.00")),
        (PaperSide.SELL, Decimal("0.01"), Decimal("2.00"), Decimal("1.00")),
        (PaperSide.SELL, Decimal("-0.01"), Decimal("-2.00"), Decimal("-3.00")),
    ],
)
def test_funding_uses_snapshot_position_value_and_signed_direction(
    side: PaperSide,
    rate: Decimal,
    expected: Decimal,
    expected_net: Decimal,
) -> None:
    realization = _NOW + timedelta(seconds=3)
    product = _product(perpetual=True, maker=Decimal("0"), taker=Decimal("0"))
    schedule = _schedule(realization, end=realization)
    funding = BacktestFundingEvent(
        sequence=3,
        observed_at=realization,
        product_id=_PRODUCT,
        rate=rate,
        reference_price=Decimal("100"),
        schedule_version=schedule.version,
        rate_source="delta-wallet-funding-v1",
    )
    events = (
        _market(1, _book(1), decisions=(_decision(_intent(20, side=side, contracts=2)),)),
        _market(2, _book(2)),
        funding,
    )

    report = run_backtest(_config(product=product, schedules=(schedule,)), events)

    assert report.status is BacktestRunStatus.COMPLETE
    assert report.total_funding == expected
    assert report.funding[0].position_contracts == (2 if side is PaperSide.BUY else -2)
    assert report.funding[0].position_value == Decimal("200")
    assert report.funding[0].cashflow == expected
    assert report.gross_realized_pnl == Decimal("0")
    assert report.realized_net_pnl == expected
    assert report.net_pnl == expected_net


def test_flat_position_at_funding_snapshot_has_zero_payment() -> None:
    realization = _NOW + timedelta(seconds=4)
    product = _product(perpetual=True, maker=Decimal("0"), taker=Decimal("0"))
    schedule = _schedule(realization, end=realization)
    close = _decision(
        _intent(31, side=PaperSide.SELL, reduce_only=True),
        role=LiquidityRole.TAKER,
    )
    events = (
        _market(1, _book(1), decisions=(_decision(_intent(30)),)),
        _market(2, _book(2), decisions=(close,)),
        _market(3, _book(3)),
        BacktestFundingEvent(
            sequence=4,
            observed_at=realization,
            product_id=_PRODUCT,
            rate=Decimal("0.02"),
            reference_price=Decimal("100"),
            schedule_version=schedule.version,
            rate_source="delta-wallet-funding-v1",
        ),
    )

    report = run_backtest(_config(product=product, schedules=(schedule,)), events)

    assert report.final_positions == ()
    assert report.funding[0].position_contracts == 0
    assert report.funding[0].cashflow == Decimal("0")


def test_missing_funding_rate_for_held_perpetual_fails_strict_mode() -> None:
    realization = _NOW + timedelta(seconds=3)
    product = _product(perpetual=True, maker=Decimal("0"), taker=Decimal("0"))
    schedule = _schedule(realization, end=realization)
    events = (
        _market(1, _book(1), decisions=(_decision(_intent(40)),)),
        _market(2, _book(2)),
        _market(3, _book(3)),
    )

    with pytest.raises(BacktestIncompleteError) as captured:
        run_backtest(_config(product=product, schedules=(schedule,)), events)
    assert "funding" in str(captured.value).lower()

    report = run_backtest(
        _config(product=product, schedules=(schedule,), strict=False),
        events,
    )
    assert report.status is BacktestRunStatus.INCOMPLETE
    assert report.incomplete_reasons


def test_missing_product_specific_funding_calendar_is_never_assumed_zero() -> None:
    product = _product(perpetual=True, maker=Decimal("0"), taker=Decimal("0"))
    events = (
        _market(1, _book(1), decisions=(_decision(_intent(41)),)),
        _market(2, _book(2)),
    )

    with pytest.raises(BacktestIncompleteError) as captured:
        run_backtest(_config(product=product), events)

    assert "schedule" in str(captured.value).lower()
    report = run_backtest(_config(product=product, strict=False), events)
    assert report.status is BacktestRunStatus.INCOMPLETE
    assert report.total_funding == Decimal("0")


def test_equity_curve_and_drawdown_use_explicit_marks() -> None:
    product = _product(maker=Decimal("0"), taker=Decimal("0"))
    events = (
        _market(1, _book(1), decisions=(_decision(_intent(42)),)),
        _market(2, _book(2), mark=Decimal("100")),
        _market(3, _book(3, bid=Decimal("89.5"), ask=Decimal("90")), mark=Decimal("90")),
        _market(
            4,
            _book(4, bid=Decimal("119.5"), ask=Decimal("120")),
            mark=Decimal("120"),
        ),
    )

    report = run_backtest(_config(product=product), events)

    assert [point.equity for point in report.equity_curve] == [
        Decimal("1000"),
        Decimal("1000"),
        Decimal("990"),
        Decimal("1020"),
    ]
    assert report.max_drawdown == Decimal("10")
    assert report.max_drawdown_fraction == Decimal("0.01")
    assert report.unrealized_pnl == Decimal("20")


def test_stop_and_trailing_orders_only_execute_after_their_trigger_event() -> None:
    stop = _decision(
        _intent(
            50,
            kind=PaperOrderKind.STOP_MARKET,
            stop=Decimal("105"),
            direction=PaperTriggerDirection.ABOVE,
        )
    )
    stop_report = run_backtest(
        _config(),
        (
            _market(1, _book(1), decisions=(stop,)),
            _market(2, _book(2, reference=Decimal("106"))),
            _market(3, _book(3, ask=Decimal("107")), mark=Decimal("107")),
        ),
    )
    assert [fill.observed_at for fill in stop_report.fills] == [_NOW + timedelta(seconds=3)]

    trailing = _decision(
        _intent(
            51,
            side=PaperSide.SELL,
            kind=PaperOrderKind.TRAILING_STOP,
            trail=Decimal("5"),
            direction=PaperTriggerDirection.BELOW,
        )
    )
    trailing_report = run_backtest(
        _config(),
        (
            _market(1, _book(1), decisions=(trailing,)),
            _market(2, _book(2, reference=Decimal("100"))),
            _market(3, _book(3, reference=Decimal("110")), mark=Decimal("110")),
            _market(4, _book(4, reference=Decimal("104")), mark=Decimal("104")),
            _market(5, _book(5, bid=Decimal("103"), ask=Decimal("103.5")), mark=Decimal("103")),
        ),
    )
    assert [fill.observed_at for fill in trailing_report.fills] == [_NOW + timedelta(seconds=5)]


def test_bracket_child_role_is_explicit_and_oco_closes_position() -> None:
    bracket = PaperBracketSpec(
        stop_loss_price=Decimal("90"),
        stop_loss_limit_price=None,
        stop_loss_trail_amount=None,
        take_profit_price=Decimal("110"),
        take_profit_limit_price=None,
    )
    entry = _decision(
        _intent(60, bracket=bracket),
        role=LiquidityRole.TAKER,
        stop_role=LiquidityRole.TAKER,
        profit_role=LiquidityRole.MAKER,
    )
    events = (
        _market(1, _book(1), decisions=(entry,)),
        _market(2, _book(2)),
        _market(3, _book(3, bid=Decimal("110"), ask=Decimal("110.5"), reference=Decimal("110"))),
        _market(4, _book(4, bid=Decimal("111"), ask=Decimal("111.5"), reference=Decimal("111"))),
    )

    report = run_backtest(_config(), events)

    assert report.final_positions == ()
    assert [fill.liquidity_role for fill in report.fills] == [
        LiquidityRole.TAKER,
        LiquidityRole.MAKER,
    ]
    assert len(report.trades) == 1


def test_ambiguous_bracket_ohlc_path_is_rejected() -> None:
    bracket = PaperBracketSpec(
        stop_loss_price=Decimal("90"),
        stop_loss_limit_price=None,
        stop_loss_trail_amount=None,
        take_profit_price=Decimal("120"),
        take_profit_limit_price=None,
    )
    entry = _decision(
        _intent(70, bracket=bracket),
        stop_role=LiquidityRole.TAKER,
        profit_role=LiquidityRole.MAKER,
    )
    candle = PaperCandle(
        stream_id=_STREAM,
        product_id=_PRODUCT,
        event_index=3,
        observed_at=_NOW + timedelta(seconds=3),
        open_price=Decimal("100"),
        high_price=Decimal("125"),
        low_price=Decimal("85"),
        close_price=Decimal("105"),
        volume_contracts=10,
    )

    with pytest.raises(BacktestDataError):
        run_backtest(
            _config(),
            (
                _market(1, _book(1), decisions=(entry,)),
                _market(2, _book(2)),
                _market(3, candle),
            ),
        )


def test_liquidation_threshold_halts_without_inventing_a_fill() -> None:
    events = (
        _market(1, _book(1), decisions=(_decision(_intent(80)),)),
        _market(2, _book(2)),
        _market(
            3,
            _book(3, bid=Decimal("79.5"), ask=Decimal("80")),
            mark=Decimal("80"),
            long_liquidation=Decimal("90"),
        ),
    )

    report = run_backtest(_config(), events)

    assert report.status is BacktestRunStatus.HALTED_LIQUIDATION_GUARD
    assert len(report.fills) == 1
    assert report.final_positions[0].contracts == 1
    assert report.halt_reason is not None


def test_event_index_gap_and_global_time_order_fail_closed() -> None:
    with pytest.raises(BacktestDataError):
        run_backtest(
            _config(),
            (
                _market(1, _book(1)),
                _market(2, _book(3)),
            ),
        )

    with pytest.raises(BacktestDataError):
        run_backtest(
            _config(),
            (
                _market(1, _book(1, second=2)),
                _market(2, _book(2, second=2)),
            ),
        )
