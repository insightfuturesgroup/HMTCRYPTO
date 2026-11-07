"""Adversarial Decimal, accounting, ordering, and resource-bound replay tests."""

from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_FLOOR, Decimal, Inexact, localcontext

import pytest
from test_backtest_engine import (
    _ACCOUNT,
    _NOW,
    _PRODUCT,
    _SOURCE,
    _book,
    _config,
    _decision,
    _intent,
    _market,
    _model,
    _product,
    _schedule,
)

from hmt_crypto.backtest import (
    BacktestConfig,
    BacktestDataError,
    BacktestFundingEvent,
    BacktestFundingSchedule,
    BacktestLimits,
    BacktestProductAssumption,
    LiquidityRole,
    run_backtest,
)
from hmt_crypto.backtest.ledger import BacktestLedger
from hmt_crypto.oms.paper_models import PaperOrderKind, PaperSide


def test_replay_decimal_math_is_independent_of_hostile_ambient_context() -> None:
    product = _product(maker=Decimal("0"), taker=Decimal("0"))
    add_two = _decision(_intent(91, contracts=2))
    close_three = _decision(_intent(92, side=PaperSide.SELL, contracts=3, reduce_only=True))
    events = (
        _market(1, _book(1), decisions=(_decision(_intent(90)),)),
        _market(2, _book(2, ask=Decimal("100")), decisions=(add_two,)),
        _market(3, _book(3, ask=Decimal("101")), decisions=(close_three,)),
        _market(4, _book(4, bid=Decimal("110"), ask=Decimal("110.5")), mark=Decimal("110")),
    )
    baseline = run_backtest(_config(product=product), events)

    with localcontext() as hostile:
        hostile.prec = 6
        hostile.rounding = ROUND_FLOOR
        hostile.traps[Inexact] = True
        hostile.clear_flags()
        replayed = run_backtest(_config(product=product), events)
        leaked_inexact = hostile.flags[Inexact]

    assert replayed == baseline
    assert leaked_inexact is False


def test_notional_is_exact_at_the_admitted_decimal_and_contract_bounds() -> None:
    maximum_decimal = Decimal("99999999999999999999.999999999999999999")
    maximum_contracts = 2**63 - 1
    product = BacktestProductAssumption(
        product_id=_PRODUCT,
        contract_value=maximum_decimal,
        is_perpetual=False,
        maker_fee_rate=Decimal("0"),
        taker_fee_rate=Decimal("0"),
        fee_schedule_version="maximum-envelope-v1",
        product_snapshot_sha256=_SOURCE,
    )
    intent = _intent(
        93,
        contracts=maximum_contracts,
        tick=Decimal("0.000000000000000001"),
    )
    events = (
        _market(
            1,
            _book(1),
            decisions=(_decision(intent, model=_model(maximum=maximum_contracts)),),
        ),
        _market(
            2,
            _book(
                2,
                bid=maximum_decimal,
                ask=maximum_decimal,
                bid_size=maximum_contracts,
                ask_size=maximum_contracts,
            ),
            mark=maximum_decimal,
        ),
    )

    report = run_backtest(_config(product=product), events)

    assert report.fills[0].notional == Decimal(
        "92233720368547758069999999999999999998155325592629044838600."
        "000000000000000009223372036854775807"
    )


def test_partial_closes_release_residual_cost_and_conserve_equity() -> None:
    product = _product(maker=Decimal("0"), taker=Decimal("0"))
    events = (
        _market(1, _book(1), decisions=(_decision(_intent(100)),)),
        _market(
            2,
            _book(2, ask=Decimal("100")),
            decisions=(_decision(_intent(101, contracts=2)),),
        ),
        _market(
            3,
            _book(3, ask=Decimal("101")),
            decisions=(_decision(_intent(102, side=PaperSide.SELL, reduce_only=True)),),
        ),
        _market(
            4,
            _book(4, bid=Decimal("110"), ask=Decimal("110.5")),
            mark=Decimal("110"),
            decisions=(
                _decision(_intent(103, side=PaperSide.SELL, contracts=2, reduce_only=True)),
            ),
        ),
        _market(
            5,
            _book(5, bid=Decimal("120"), ask=Decimal("120.5")),
            mark=Decimal("120"),
        ),
    )

    report = run_backtest(_config(product=product), events)

    assert [point.equity for point in report.equity_curve] == [
        Decimal("1000"),
        Decimal("1000"),
        Decimal("1001"),
        Decimal("1028"),
        Decimal("1048"),
    ]
    assert report.final_positions == ()
    assert report.gross_realized_pnl == Decimal("48")
    assert report.realized_net_pnl == Decimal("48")
    assert report.net_pnl == Decimal("48")


def test_flat_position_exactly_conserves_the_adversarial_cash_flows() -> None:
    product = _product(maker=Decimal("0"), taker=Decimal("0"))
    ledger = BacktestLedger(Decimal("1000"), {_PRODUCT: product})
    fills = (
        (PaperSide.BUY, 6, Decimal("13")),
        (PaperSide.BUY, 5, Decimal("15")),
        (PaperSide.SELL, 1, Decimal("9")),
        (PaperSide.SELL, 10, Decimal("9")),
    )

    for index, (side, contracts, price) in enumerate(fills):
        ledger.apply_fill(
            product_id=_PRODUCT,
            side=side,
            contracts=contracts,
            price=price,
            fee=Decimal("0"),
            observed_at=_NOW + timedelta(seconds=index),
        )

    assert ledger.contracts(_PRODUCT) == 0
    assert ledger.gross_realized_pnl == Decimal("-54")
    assert ledger.cash == Decimal("946")


def test_flat_position_cash_flow_conservation_holds_for_a_deterministic_family() -> None:
    product = _product(maker=Decimal("0"), taker=Decimal("0"))
    cases = (
        (
            2,
            Decimal("7"),
            3,
            Decimal("11"),
            1,
            Decimal("5"),
            Decimal("13"),
            Decimal("10"),
            Decimal("10010"),
        ),
        (
            4,
            Decimal("13"),
            3,
            Decimal("17"),
            2,
            Decimal("9"),
            Decimal("19"),
            Decimal("10"),
            Decimal("10010"),
        ),
        (
            6,
            Decimal("19.25"),
            5,
            Decimal("23.75"),
            4,
            Decimal("17.5"),
            Decimal("29"),
            Decimal("38.75"),
            Decimal("10038.75"),
        ),
        (
            7,
            Decimal("101.125"),
            4,
            Decimal("97.375"),
            3,
            Decimal("88.5"),
            Decimal("111.25"),
            Decimal("58.125"),
            Decimal("10058.125"),
        ),
    )

    with localcontext() as hostile:
        hostile.prec = 6
        hostile.rounding = ROUND_FLOOR
        hostile.traps[Inexact] = True
        hostile.clear_flags()
        for (
            first_quantity,
            first_price,
            second_quantity,
            second_price,
            partial,
            first_exit,
            final_exit,
            expected,
            expected_cash,
        ) in cases:
            total = first_quantity + second_quantity
            ledger = BacktestLedger(Decimal("10000"), {_PRODUCT: product})
            for index, (side, contracts, price) in enumerate(
                (
                    (PaperSide.BUY, first_quantity, first_price),
                    (PaperSide.BUY, second_quantity, second_price),
                    (PaperSide.SELL, partial, first_exit),
                    (PaperSide.SELL, total - partial, final_exit),
                )
            ):
                ledger.apply_fill(
                    product_id=_PRODUCT,
                    side=side,
                    contracts=contracts,
                    price=price,
                    fee=Decimal("0"),
                    observed_at=_NOW + timedelta(seconds=index),
                )
            assert ledger.contracts(_PRODUCT) == 0
            assert ledger.gross_realized_pnl == expected
            assert ledger.cash == expected_cash
        assert hostile.flags[Inexact] is False


def test_equity_exactly_conserves_cash_flows_through_partial_closes_and_reversals() -> None:
    product = BacktestProductAssumption(
        product_id=_PRODUCT,
        contract_value=Decimal("0.125"),
        is_perpetual=False,
        maker_fee_rate=Decimal("0"),
        taker_fee_rate=Decimal("0"),
        fee_schedule_version="equity-invariant-v1",
        product_snapshot_sha256=_SOURCE,
    )
    fills = (
        (PaperSide.SELL, 19, Decimal("112.875")),
        (PaperSide.SELL, 10, Decimal("71.375")),
        (PaperSide.SELL, 14, Decimal("17.375")),
        (PaperSide.BUY, 20, Decimal("12.5")),
        (PaperSide.SELL, 12, Decimal("121.625")),
        (PaperSide.SELL, 17, Decimal("79.875")),
        (PaperSide.BUY, 10, Decimal("19.25")),
        (PaperSide.SELL, 17, Decimal("36.5")),
        (PaperSide.BUY, 17, Decimal("116.875")),
    )
    expected_equity = (
        Decimal("100000"),
        Decimal("100098.5625"),
        Decimal("100294.3125"),
        Decimal("100320.515625"),
        Decimal("100006.78125"),
        Decimal("100189.4375"),
        Decimal("100583.5"),
        Decimal("100492.9375"),
        Decimal("99900.171875"),
    )
    ledger = BacktestLedger(Decimal("100000"), {_PRODUCT: product})

    with localcontext() as hostile:
        hostile.prec = 6
        hostile.rounding = ROUND_FLOOR
        hostile.traps[Inexact] = True
        hostile.clear_flags()
        actual_equity = []
        for index, (side, contracts, price) in enumerate(fills):
            observed_at = _NOW + timedelta(seconds=index)
            ledger.apply_fill(
                product_id=_PRODUCT,
                side=side,
                contracts=contracts,
                price=price,
                fee=Decimal("0"),
                observed_at=observed_at,
            )
            ledger.update_mark(_PRODUCT, price)
            actual_equity.append(ledger.equity_point(index + 1, observed_at).equity)

        assert actual_equity == list(expected_equity)
        assert hostile.flags[Inexact] is False


def test_equal_timestamps_across_products_use_global_sequence_as_tie_break() -> None:
    second_product = _PRODUCT + 1
    realization = _NOW + timedelta(seconds=3)
    products = (
        _product(perpetual=True, maker=Decimal("0"), taker=Decimal("0")),
        _product(
            product_id=second_product,
            perpetual=True,
            maker=Decimal("0"),
            taker=Decimal("0"),
        ),
    )
    schedules = (
        _schedule(realization, end=realization),
        _schedule(realization, end=realization, product_id=second_product),
    )
    config = BacktestConfig(
        paper_account_id=_ACCOUNT,
        initial_cash=Decimal("1000"),
        products=products,
        funding_schedules=schedules,
        strict_funding=True,
        assumptions_version="simultaneous-products-v1",
        max_events=20,
    )
    events = (
        _market(1, _book(1, second=1), decisions=(_decision(_intent(110)),)),
        _market(
            2,
            _book(
                1,
                second=1,
                bid=Decimal("199.5"),
                ask=Decimal("200"),
                product_id=second_product,
                stream_id="delta-ethusd-backtest",
            ),
            decisions=(_decision(_intent(111, side=PaperSide.SELL, product_id=second_product)),),
        ),
        _market(3, _book(2, second=2)),
        _market(
            4,
            _book(
                2,
                second=2,
                bid=Decimal("199.5"),
                ask=Decimal("200"),
                product_id=second_product,
                stream_id="delta-ethusd-backtest",
            ),
        ),
        BacktestFundingEvent(
            sequence=5,
            observed_at=realization,
            product_id=_PRODUCT,
            rate=Decimal("0.01"),
            reference_price=Decimal("100"),
            schedule_version=schedules[0].version,
            rate_source="delta-wallet-funding-v1",
        ),
        BacktestFundingEvent(
            sequence=6,
            observed_at=realization,
            product_id=second_product,
            rate=Decimal("0.01"),
            reference_price=Decimal("200"),
            schedule_version=schedules[1].version,
            rate_source="delta-wallet-funding-v1",
        ),
    )

    report = run_backtest(config, events)

    assert [record.cashflow for record in report.funding] == [
        Decimal("-1.00"),
        Decimal("2.00"),
    ]
    assert report.total_funding == Decimal("1.00")


def test_negative_maker_commission_is_a_signed_cash_rebate() -> None:
    product = _product(maker=Decimal("-0.001"), taker=Decimal("0"))
    entry = _decision(_intent(120))
    exit_order = _decision(
        _intent(
            121,
            side=PaperSide.SELL,
            kind=PaperOrderKind.LIMIT,
            limit=Decimal("110"),
            reduce_only=True,
        ),
        role=LiquidityRole.MAKER,
    )
    events = (
        _market(1, _book(1), decisions=(entry,)),
        _market(2, _book(2), decisions=(exit_order,)),
        _market(3, _book(3, bid=Decimal("110"), ask=Decimal("110.5")), mark=Decimal("110")),
    )

    report = run_backtest(_config(product=product), events)

    assert [fill.fee for fill in report.fills] == [Decimal("0"), Decimal("-0.110")]
    assert report.total_fees == Decimal("-0.110")
    assert report.final_equity == Decimal("1010.110")
    assert report.net_pnl == Decimal("10.110")


def test_reversal_conserves_cost_and_report_separates_gross_from_realized_net() -> None:
    product = _product(maker=Decimal("0"), taker=Decimal("0"))
    events = (
        _market(1, _book(1), decisions=(_decision(_intent(130)),)),
        _market(
            2,
            _book(2, ask=Decimal("100")),
            decisions=(_decision(_intent(131, contracts=2)),),
        ),
        _market(
            3,
            _book(3, ask=Decimal("101")),
            decisions=(_decision(_intent(132, side=PaperSide.SELL, contracts=5)),),
        ),
        _market(
            4,
            _book(4, bid=Decimal("110"), ask=Decimal("110.5")),
            mark=Decimal("110"),
            decisions=(_decision(_intent(133, side=PaperSide.BUY, contracts=2, reduce_only=True)),),
        ),
        _market(
            5,
            _book(5, bid=Decimal("89.5"), ask=Decimal("90")),
            mark=Decimal("90"),
        ),
    )

    report = run_backtest(_config(product=product), events)

    assert [point.equity for point in report.equity_curve] == [
        Decimal("1000"),
        Decimal("1000"),
        Decimal("1001"),
        Decimal("1028"),
        Decimal("1068"),
    ]
    assert report.final_positions == ()
    assert report.gross_realized_pnl == Decimal("68")
    assert report.realized_net_pnl == Decimal("68")
    assert report.net_pnl == report.realized_net_pnl


def test_decisions_per_event_limit_rejects_before_order_submission() -> None:
    limits = BacktestLimits(max_decisions_per_event=1)
    event = _market(
        1,
        _book(1),
        decisions=(_decision(_intent(140)), _decision(_intent(141))),
    )

    with pytest.raises(BacktestDataError, match="decision"):
        run_backtest(_config(limits=limits), (event,))


def test_total_decision_limit_rejects_across_events() -> None:
    limits = BacktestLimits(max_total_decisions=1)
    events = (
        _market(1, _book(1), decisions=(_decision(_intent(150)),)),
        _market(2, _book(2), decisions=(_decision(_intent(151)),)),
    )

    with pytest.raises(BacktestDataError, match="total decision"):
        run_backtest(_config(limits=limits), events)


def test_aggregate_work_budget_bounds_replay_cost() -> None:
    limits = BacktestLimits(max_work_units=3)
    events = (_market(1, _book(1)), _market(2, _book(2)))

    with pytest.raises(BacktestDataError, match="work budget"):
        run_backtest(_config(limits=limits), events)


def test_aggregate_output_budget_bounds_report_records() -> None:
    limits = BacktestLimits(max_output_records=1)
    events = (_market(1, _book(1)), _market(2, _book(2)))

    with pytest.raises(BacktestDataError, match="output budget"):
        run_backtest(_config(limits=limits), events)


def _funding_gap_resource_case(*, limits: BacktestLimits) -> tuple[BacktestConfig, tuple]:
    product = _product(perpetual=True, maker=Decimal("0"), taker=Decimal("0"))
    realization_times = (
        _NOW + timedelta(seconds=2),
        _NOW + timedelta(seconds=2, milliseconds=500),
        _NOW + timedelta(seconds=3),
        _NOW + timedelta(seconds=3, milliseconds=500),
        _NOW + timedelta(seconds=4),
    )
    schedule = BacktestFundingSchedule(
        product_id=_PRODUCT,
        version="bounded-gap-calendar-v1",
        source_sha256="b" * 64,
        coverage_start=_NOW,
        coverage_end=_NOW + timedelta(seconds=5),
        realization_times=realization_times,
    )
    events = (
        _market(
            1,
            _book(1),
            decisions=(_decision(_intent(160, contracts=3), model=_model(maximum=1)),),
        ),
        _market(2, _book(2)),
        _market(3, _book(3)),
        _market(4, _book(4)),
        _market(5, _book(5)),
    )
    config = BacktestConfig(
        paper_account_id=_ACCOUNT,
        initial_cash=Decimal("1000"),
        products=(product,),
        funding_schedules=(schedule,),
        strict_funding=False,
        assumptions_version="funding-gap-resource-v1",
        max_events=5,
        limits=limits,
    )
    return config, events


def test_funding_gap_history_scan_is_charged_before_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, events = _funding_gap_resource_case(
        limits=BacktestLimits(max_work_units=29, max_output_records=100)
    )

    def unexpected_scan(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("position history was scanned before reserving work")

    monkeypatch.setattr(BacktestLedger, "held_interval", unexpected_scan)

    with pytest.raises(BacktestDataError, match="work budget"):
        run_backtest(config, events)


def test_funding_gap_output_exhaustion_precedes_reason_string_allocation() -> None:
    config, events = _funding_gap_resource_case(
        limits=BacktestLimits(max_work_units=100, max_output_records=8)
    )

    with pytest.raises(BacktestDataError, match="output budget") as caught:
        run_backtest(config, events)

    traceback = caught.value.__traceback__
    gap_frame = None
    while traceback is not None:
        if traceback.tb_frame.f_code.co_name == "_funding_gaps":
            gap_frame = traceback.tb_frame
            break
        traceback = traceback.tb_next
    assert gap_frame is not None
    assert gap_frame.f_locals["reasons"] == []
