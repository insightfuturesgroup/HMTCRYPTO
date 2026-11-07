"""Exact immutable backtest inputs and deterministic identity."""

from __future__ import annotations

import copy
import pickle
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, Inexact, localcontext
from uuid import UUID

import pytest

import hmt_crypto.backtest as backtest_api
from hmt_crypto.backtest import (
    BacktestConfig,
    BacktestDataError,
    BacktestFundingSchedule,
    BacktestInputError,
    BacktestMarketEvent,
    BacktestOrderDecision,
    BacktestProductAssumption,
    LiquidityRole,
    run_backtest,
)
from hmt_crypto.oms.models import OmsExecutionMode
from hmt_crypto.oms.paper_models import (
    PaperModelConfig,
    PaperOrderIntent,
    PaperOrderKind,
    PaperSide,
    PaperTopOfBook,
)

_NOW = datetime(2026, 11, 6, tzinfo=UTC)
_ACCOUNT = UUID(int=999)


def _config() -> BacktestConfig:
    return BacktestConfig(
        paper_account_id=_ACCOUNT,
        initial_cash=Decimal("1000"),
        products=(
            BacktestProductAssumption(
                product_id=1,
                contract_value=Decimal("0.001"),
                is_perpetual=False,
                maker_fee_rate=Decimal("0.0002"),
                taker_fee_rate=Decimal("0.0005"),
                fee_schedule_version="fixture-v1",
                product_snapshot_sha256="c" * 64,
            ),
        ),
        funding_schedules=(),
        strict_funding=True,
        assumptions_version="phase4-v1",
        max_events=10,
    )


def _event(*, sequence: int = 1) -> BacktestMarketEvent:
    intent = PaperOrderIntent(
        order_id=UUID(int=1),
        paper_account_id=_ACCOUNT,
        mode=OmsExecutionMode.BACKTEST,
        product_id=1,
        side=PaperSide.BUY,
        contracts=1,
        kind=PaperOrderKind.MARKET,
        tick_size=Decimal("0.5"),
        limit_price=None,
        stop_price=None,
        trail_amount=None,
        trigger_direction=None,
        reduce_only=False,
        bracket=None,
    )
    decision = BacktestOrderDecision(
        intent=intent,
        model_config=PaperModelConfig(
            latency_events=0,
            slippage_bps=0,
            max_fill_contracts_per_event=1,
        ),
        liquidity_role=LiquidityRole.TAKER,
        stop_loss_liquidity_role=None,
        take_profit_liquidity_role=None,
    )
    book = PaperTopOfBook(
        stream_id="fixture",
        product_id=1,
        event_index=1,
        observed_at=_NOW + timedelta(seconds=1),
        bid_price=Decimal("99.5"),
        ask_price=Decimal("100"),
        bid_contracts=1,
        ask_contracts=1,
        trigger_reference=Decimal("100"),
    )
    return BacktestMarketEvent(
        sequence=sequence,
        market_event=book,
        mark_price=Decimal("100"),
        decisions=(decision,),
        long_liquidation_price=None,
        short_liquidation_price=None,
    )


def test_exact_replay_copy_pickle_and_inputs_are_not_mutated() -> None:
    config = _config()
    events = (_event(),)
    before = pickle.dumps((config, events))

    first = run_backtest(config, events)
    second = run_backtest(copy.deepcopy(config), pickle.loads(pickle.dumps(events)))

    assert first == second
    assert first.run_id_sha256 == second.run_id_sha256
    assert first.config_sha256 != first.data_sha256
    assert pickle.dumps((config, events)) == before
    with pytest.raises(FrozenInstanceError):
        config.max_events = 11  # type: ignore[misc]


def test_data_and_config_provenance_hashes_change_independently() -> None:
    baseline = run_backtest(_config(), (_event(),))
    event = _event()
    changed_event = BacktestMarketEvent(
        sequence=event.sequence,
        market_event=event.market_event,
        mark_price=Decimal("101"),
        decisions=event.decisions,
        long_liquidation_price=None,
        short_liquidation_price=None,
    )
    changed_data = run_backtest(_config(), (changed_event,))
    config = _config()
    changed_config = BacktestConfig(
        paper_account_id=config.paper_account_id,
        initial_cash=Decimal("1001"),
        products=config.products,
        funding_schedules=config.funding_schedules,
        strict_funding=config.strict_funding,
        assumptions_version=config.assumptions_version,
        max_events=config.max_events,
    )
    changed_assumptions = run_backtest(changed_config, (_event(),))

    assert changed_data.config_sha256 == baseline.config_sha256
    assert changed_data.data_sha256 != baseline.data_sha256
    assert changed_assumptions.config_sha256 != baseline.config_sha256
    assert changed_assumptions.data_sha256 == baseline.data_sha256
    assert (
        len({baseline.run_id_sha256, changed_data.run_id_sha256, changed_assumptions.run_id_sha256})
        == 3
    )


@pytest.mark.parametrize(
    "value",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"), Decimal("1e20")],
)
def test_nonfinite_and_out_of_bounds_decimals_are_rejected(value: Decimal) -> None:
    with pytest.raises(BacktestInputError):
        BacktestProductAssumption(
            product_id=1,
            contract_value=value,
            is_perpetual=False,
            maker_fee_rate=Decimal("0"),
            taker_fee_rate=Decimal("0"),
            fee_schedule_version="fixture-v1",
            product_snapshot_sha256="d" * 64,
        )


def test_subclass_and_noncontiguous_global_sequence_are_rejected() -> None:
    class DerivedMarketEvent(BacktestMarketEvent):
        pass

    owned = _event()
    derived = DerivedMarketEvent(
        sequence=owned.sequence,
        market_event=owned.market_event,
        mark_price=owned.mark_price,
        decisions=owned.decisions,
        long_liquidation_price=None,
        short_liquidation_price=None,
    )
    with pytest.raises(BacktestDataError):
        run_backtest(_config(), (derived,))

    with pytest.raises(BacktestDataError):
        run_backtest(_config(), (_event(sequence=2),))


def test_wrong_mode_account_and_ambiguous_bracket_fee_roles_are_rejected() -> None:
    event = _event()
    intent = event.decisions[0].intent
    wrong_mode = PaperOrderIntent(
        order_id=intent.order_id,
        paper_account_id=intent.paper_account_id,
        mode=OmsExecutionMode.PAPER,
        product_id=intent.product_id,
        side=intent.side,
        contracts=intent.contracts,
        kind=intent.kind,
        tick_size=intent.tick_size,
        limit_price=None,
        stop_price=None,
        trail_amount=None,
        trigger_direction=None,
        reduce_only=False,
        bracket=None,
    )
    decision = BacktestOrderDecision(
        intent=wrong_mode,
        model_config=event.decisions[0].model_config,
        liquidity_role=LiquidityRole.TAKER,
        stop_loss_liquidity_role=None,
        take_profit_liquidity_role=None,
    )
    changed = BacktestMarketEvent(
        sequence=1,
        market_event=event.market_event,
        mark_price=event.mark_price,
        decisions=(decision,),
        long_liquidation_price=None,
        short_liquidation_price=None,
    )
    with pytest.raises(BacktestDataError):
        run_backtest(_config(), (changed,))


def test_decimal_input_copy_does_not_use_or_mutate_ambient_context() -> None:
    with localcontext() as hostile:
        hostile.prec = 3
        hostile.rounding = ROUND_DOWN
        hostile.traps[Inexact] = True
        hostile.clear_flags()
        product = BacktestProductAssumption(
            product_id=2,
            contract_value=Decimal("0.000000000000000001"),
            is_perpetual=False,
            maker_fee_rate=Decimal("-0.123456789012345678"),
            taker_fee_rate=Decimal("0.123456789012345678"),
            fee_schedule_version="hostile-context-v1",
            product_snapshot_sha256="e" * 64,
        )
        leaked_inexact = hostile.flags[Inexact]

    assert product.maker_fee_rate == Decimal("-0.123456789012345678")
    assert leaked_inexact is False


def test_configurable_product_limit_rejects_an_oversized_universe() -> None:
    first = _config().products[0]
    second = BacktestProductAssumption(
        product_id=2,
        contract_value=Decimal("0.001"),
        is_perpetual=False,
        maker_fee_rate=Decimal("0"),
        taker_fee_rate=Decimal("0"),
        fee_schedule_version="fixture-v1",
        product_snapshot_sha256="f" * 64,
    )
    limits = backtest_api.BacktestLimits(max_products=1)

    with pytest.raises(BacktestInputError):
        BacktestConfig(
            paper_account_id=_ACCOUNT,
            initial_cash=Decimal("1000"),
            products=(first, second),
            funding_schedules=(),
            strict_funding=True,
            assumptions_version="bounded-products-v1",
            max_events=10,
            limits=limits,
        )


def test_configurable_funding_schedule_limit_rejects_oversized_history() -> None:
    product = BacktestProductAssumption(
        product_id=1,
        contract_value=Decimal("1"),
        is_perpetual=True,
        maker_fee_rate=Decimal("0"),
        taker_fee_rate=Decimal("0"),
        fee_schedule_version="fixture-v1",
        product_snapshot_sha256="a" * 64,
    )
    schedule = BacktestFundingSchedule(
        product_id=1,
        version="funding-v1",
        source_sha256="b" * 64,
        coverage_start=_NOW,
        coverage_end=_NOW + timedelta(seconds=1),
        realization_times=(_NOW, _NOW + timedelta(seconds=1)),
    )
    limits = backtest_api.BacktestLimits(max_funding_realizations_per_schedule=1)

    with pytest.raises(BacktestInputError):
        BacktestConfig(
            paper_account_id=_ACCOUNT,
            initial_cash=Decimal("1000"),
            products=(product,),
            funding_schedules=(schedule,),
            strict_funding=True,
            assumptions_version="bounded-funding-v1",
            max_events=10,
            limits=limits,
        )


def test_config_work_budget_bounds_aggregate_product_and_schedule_inputs() -> None:
    product = BacktestProductAssumption(
        product_id=1,
        contract_value=Decimal("1"),
        is_perpetual=True,
        maker_fee_rate=Decimal("0"),
        taker_fee_rate=Decimal("0"),
        fee_schedule_version="fixture-v1",
        product_snapshot_sha256="c" * 64,
    )
    schedule = BacktestFundingSchedule(
        product_id=1,
        version="funding-v1",
        source_sha256="d" * 64,
        coverage_start=_NOW,
        coverage_end=_NOW,
        realization_times=(_NOW,),
    )

    with pytest.raises(BacktestInputError, match="work"):
        BacktestConfig(
            paper_account_id=_ACCOUNT,
            initial_cash=Decimal("1000"),
            products=(product,),
            funding_schedules=(schedule,),
            strict_funding=True,
            assumptions_version="bounded-config-work-v1",
            max_events=10,
            limits=backtest_api.BacktestLimits(max_work_units=1),
        )
