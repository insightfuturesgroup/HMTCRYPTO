"""Deterministic PAPER/BACKTEST execution without exchange authority."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from hmt_crypto.oms.models import OmsExecutionMode
from hmt_crypto.oms.paper_engine import _evaluate_allocated_order
from hmt_crypto.oms.paper_models import (
    PaperBracketSpec,
    PaperCandle,
    PaperEvaluation,
    PaperFeeModel,
    PaperModelConfig,
    PaperOrderIntent,
    PaperOrderKind,
    PaperOrderState,
    PaperOrderStatus,
    PaperSide,
    PaperSimulationError,
    PaperTopOfBook,
    PaperTriggerDirection,
)
from hmt_crypto.oms.paper_state import submit_paper_order

_NOW = datetime(2026, 11, 6, 0, 0, tzinfo=UTC)
_ORDER_ID = UUID("00112233-4455-6677-8899-aabbccddeeff")
_ACCOUNT_ID = UUID("10213243-5465-7687-98a9-bacbdcedfe0f")


def evaluate_paper_order(
    state: object,
    event: object,
    *,
    config: object,
    position_contracts: int,
) -> PaperEvaluation:
    """Exercise the private kernel with an explicit test-only whole-event allocation."""
    if type(state) is not PaperOrderState or type(event) not in (PaperTopOfBook, PaperCandle):
        return _evaluate_allocated_order(  # type: ignore[arg-type]
            state,
            event,
            config=config,
            position_contracts=position_contracts,
            allocation_ordinal=0,
            liquidity_before=0,
            reservation_before=0,
        )
    liquidity = (
        event.volume_contracts
        if isinstance(event, PaperCandle)
        else event.ask_contracts
        if state.intent.side is PaperSide.BUY
        else event.bid_contracts
    )
    return _evaluate_allocated_order(
        state,
        event,
        config=config,  # type: ignore[arg-type]
        position_contracts=position_contracts,
        allocation_ordinal=0,
        liquidity_before=liquidity,
        reservation_before=state.remaining_contracts if state.intent.reduce_only else 0,
    )


def _config(**changes: object) -> PaperModelConfig:
    values: dict[str, object] = {
        "latency_events": 0,
        "slippage_bps": 25,
        "max_fill_contracts_per_event": 100,
        "fee_model": PaperFeeModel.SYNTHETIC_ZERO_FEE,
    }
    return PaperModelConfig(**(values | changes))


def _intent(**changes: object) -> PaperOrderIntent:
    values: dict[str, object] = {
        "order_id": _ORDER_ID,
        "paper_account_id": _ACCOUNT_ID,
        "mode": OmsExecutionMode.PAPER,
        "product_id": 27,
        "side": PaperSide.BUY,
        "contracts": 5,
        "kind": PaperOrderKind.MARKET,
        "tick_size": Decimal("0.05"),
        "limit_price": None,
        "stop_price": None,
        "trail_amount": None,
        "trigger_direction": None,
        "reduce_only": False,
        "bracket": None,
    }
    merged = values | changes
    if (
        merged["kind"]
        in (
            PaperOrderKind.STOP_MARKET,
            PaperOrderKind.STOP_LIMIT,
            PaperOrderKind.TRAILING_STOP,
        )
        and "trigger_direction" not in changes
    ):
        merged["trigger_direction"] = (
            PaperTriggerDirection.ABOVE
            if merged["side"] is PaperSide.BUY
            else PaperTriggerDirection.BELOW
        )
    return PaperOrderIntent(**merged)


def _book(index: int, **changes: object) -> PaperTopOfBook:
    values: dict[str, object] = {
        "stream_id": "paper-default",
        "product_id": 27,
        "event_index": index,
        "observed_at": _NOW + timedelta(seconds=index),
        "bid_price": Decimal("99.95"),
        "ask_price": Decimal("100.00"),
        "bid_contracts": 20,
        "ask_contracts": 20,
        "trigger_reference": Decimal("100.00"),
    }
    return PaperTopOfBook(**(values | changes))


def _candle(index: int, **changes: object) -> PaperCandle:
    values: dict[str, object] = {
        "stream_id": "paper-default",
        "product_id": 27,
        "event_index": index,
        "observed_at": _NOW + timedelta(minutes=index),
        "open_price": Decimal("100"),
        "high_price": Decimal("105"),
        "low_price": Decimal("95"),
        "close_price": Decimal("102"),
        "volume_contracts": 20,
    }
    return PaperCandle(**(values | changes))


def test_market_order_partially_fills_from_top_of_book_and_replays_deterministically() -> None:
    """Removing event liquidity or deterministic fill keys must fail this behavior."""
    config = _config(max_fill_contracts_per_event=4)
    state = submit_paper_order(_intent(), submitted_event_index=10, config=config)
    event = _book(11, ask_contracts=3)

    first = evaluate_paper_order(state, event, config=config, position_contracts=0)
    replay = evaluate_paper_order(state, event, config=config, position_contracts=0)

    assert first == replay
    assert first.state.status is PaperOrderStatus.PARTIALLY_FILLED
    assert first.state.filled_contracts == 3
    assert first.state.remaining_contracts == 2
    assert first.fill is not None
    assert first.fill.contracts == 3
    assert first.fill.price == Decimal("100.25")
    assert first.fill.fee == Decimal("0")
    assert first.fill.funding == Decimal("0")
    assert first.fill.model_label == "SYNTHETIC_ZERO_FEE_NOT_LIVE_DELTA"
    assert len(first.fill.fill_key_sha256) == 64

    second = evaluate_paper_order(
        first.state,
        _book(12, ask_price=Decimal("101"), ask_contracts=9),
        config=config,
        position_contracts=0,
    )
    assert second.state.status is PaperOrderStatus.FILLED
    assert second.fill is not None
    assert second.fill.contracts == 2
    assert second.fill.price == Decimal("101.30")
    assert second.fill.fill_key_sha256 != first.fill.fill_key_sha256


def test_latency_consumes_only_later_events_and_out_of_order_data_is_rejected() -> None:
    """Filling on submission/latency events or replaying old data must fail."""
    config = _config(latency_events=2)
    state = submit_paper_order(_intent(), submitted_event_index=7, config=config)
    assert state.eligible_event_index == 10

    with pytest.raises(PaperSimulationError):
        evaluate_paper_order(state, _book(9), config=config, position_contracts=0)

    waiting = evaluate_paper_order(state, _book(8), config=config, position_contracts=0)
    assert waiting.fill is None
    assert waiting.reason == "WAITING_FOR_LATENCY"
    waiting = evaluate_paper_order(
        waiting.state,
        _book(9),
        config=config,
        position_contracts=0,
    )
    assert waiting.fill is None
    filled = evaluate_paper_order(
        waiting.state,
        _book(10),
        config=config,
        position_contracts=0,
    )
    assert filled.fill is not None

    with pytest.raises(PaperSimulationError):
        evaluate_paper_order(filled.state, _book(10), config=config, position_contracts=0)


@pytest.mark.parametrize(
    "side,limit_price,event,expected_price",
    [
        (
            PaperSide.BUY,
            Decimal("101"),
            _book(2, ask_price=Decimal("100")),
            Decimal("100.25"),
        ),
        (
            PaperSide.SELL,
            Decimal("99"),
            _book(2, bid_price=Decimal("100")),
            Decimal("99.75"),
        ),
        (
            PaperSide.BUY,
            Decimal("101"),
            _candle(2, open_price=Decimal("99"), low_price=Decimal("98")),
            Decimal("99.25"),
        ),
        (
            PaperSide.SELL,
            Decimal("99"),
            _candle(2, open_price=Decimal("101"), high_price=Decimal("102")),
            Decimal("100.70"),
        ),
    ],
)
def test_limit_order_uses_price_improvement_without_crossing_its_limit(
    side: PaperSide,
    limit_price: Decimal,
    event: PaperTopOfBook | PaperCandle,
    expected_price: Decimal,
) -> None:
    """Using the limit unconditionally or allowing adverse slippage through it must fail."""
    state = submit_paper_order(
        _intent(side=side, kind=PaperOrderKind.LIMIT, limit_price=limit_price),
        submitted_event_index=1,
        config=_config(),
    )
    result = evaluate_paper_order(state, event, config=_config(), position_contracts=0)
    assert result.fill is not None
    assert result.fill.price == expected_price
    if side is PaperSide.BUY:
        assert result.fill.price <= limit_price
    else:
        assert result.fill.price >= limit_price


def test_non_marketable_limit_waits_without_consuming_quantity() -> None:
    """A buy above the current ask condition must remain working until touched."""
    config = _config()
    state = submit_paper_order(
        _intent(kind=PaperOrderKind.LIMIT, limit_price=Decimal("99")),
        submitted_event_index=1,
        config=config,
    )
    result = evaluate_paper_order(
        state,
        _book(2, ask_price=Decimal("100")),
        config=config,
        position_contracts=0,
    )
    assert result.fill is None
    assert result.reason == "NOT_MARKETABLE"
    assert result.state.remaining_contracts == 5


def test_stop_market_triggers_without_same_event_fill_then_executes_the_next_gap() -> None:
    """Filling from the trigger candle itself would introduce intrabar lookahead."""
    config = _config(slippage_bps=0)
    state = submit_paper_order(
        _intent(kind=PaperOrderKind.STOP_MARKET, stop_price=Decimal("105")),
        submitted_event_index=1,
        config=config,
    )
    triggered = evaluate_paper_order(
        state,
        _candle(2, high_price=Decimal("106")),
        config=config,
        position_contracts=0,
    )
    assert triggered.fill is None
    assert triggered.reason == "TRIGGERED_NEXT_EVENT_EXECUTION"
    assert triggered.state.status is PaperOrderStatus.TRIGGERED
    assert triggered.state.triggered_event_index == 2

    filled = evaluate_paper_order(
        triggered.state,
        _candle(
            3,
            open_price=Decimal("110"),
            high_price=Decimal("112"),
            low_price=Decimal("109"),
            close_price=Decimal("111"),
        ),
        config=config,
        position_contracts=0,
    )
    assert filled.fill is not None
    assert filled.fill.price == Decimal("110")


def test_stop_limit_triggers_then_waits_for_its_limit_on_later_data() -> None:
    """A triggered stop-limit must not silently become a market order."""
    config = _config(slippage_bps=0)
    state = submit_paper_order(
        _intent(
            kind=PaperOrderKind.STOP_LIMIT,
            stop_price=Decimal("105"),
            limit_price=Decimal("106"),
        ),
        submitted_event_index=1,
        config=config,
    )
    triggered = evaluate_paper_order(
        state,
        _book(2, trigger_reference=Decimal("105"), ask_price=Decimal("108")),
        config=config,
        position_contracts=0,
    )
    assert triggered.state.status is PaperOrderStatus.TRIGGERED
    assert triggered.fill is None
    waiting = evaluate_paper_order(
        triggered.state,
        _book(3, trigger_reference=Decimal("108"), ask_price=Decimal("107")),
        config=config,
        position_contracts=0,
    )
    assert waiting.fill is None
    filled = evaluate_paper_order(
        waiting.state,
        _book(4, trigger_reference=Decimal("104"), ask_price=Decimal("105")),
        config=config,
        position_contracts=0,
    )
    assert filled.fill is not None
    assert filled.fill.price == Decimal("105")


def test_trailing_stop_watermark_is_monotonic_and_executes_after_trigger() -> None:
    """Reversing the favorable watermark or filling on its trigger tick must fail."""
    config = _config(slippage_bps=0)
    state = submit_paper_order(
        _intent(
            side=PaperSide.SELL,
            kind=PaperOrderKind.TRAILING_STOP,
            trail_amount=Decimal("5"),
            reduce_only=True,
            contracts=4,
        ),
        submitted_event_index=1,
        config=config,
    )
    initialized = evaluate_paper_order(
        state,
        _book(2, trigger_reference=Decimal("100")),
        config=config,
        position_contracts=4,
    )
    assert initialized.state.trail_watermark == Decimal("100")
    raised = evaluate_paper_order(
        initialized.state,
        _book(3, trigger_reference=Decimal("110")),
        config=config,
        position_contracts=4,
    )
    assert raised.state.trail_watermark == Decimal("110")
    not_lowered = evaluate_paper_order(
        raised.state,
        _book(4, trigger_reference=Decimal("108")),
        config=config,
        position_contracts=4,
    )
    assert not_lowered.state.trail_watermark == Decimal("110")
    triggered = evaluate_paper_order(
        not_lowered.state,
        _book(5, trigger_reference=Decimal("105")),
        config=config,
        position_contracts=4,
    )
    assert triggered.fill is None
    assert triggered.state.status is PaperOrderStatus.TRIGGERED
    filled = evaluate_paper_order(
        triggered.state,
        _book(
            6,
            bid_price=Decimal("104"),
            ask_price=Decimal("104.05"),
            trigger_reference=Decimal("104"),
        ),
        config=config,
        position_contracts=4,
    )
    assert filled.fill is not None
    assert filled.fill.price == Decimal("104")


def test_trailing_stop_rejects_candle_data_because_intrabar_path_is_unknown() -> None:
    """Pretending a candle reveals high/low ordering would permit lookahead."""
    state = submit_paper_order(
        _intent(kind=PaperOrderKind.TRAILING_STOP, trail_amount=Decimal("5")),
        submitted_event_index=1,
        config=_config(),
    )
    with pytest.raises(PaperSimulationError):
        evaluate_paper_order(state, _candle(2), config=_config(), position_contracts=0)


def test_reduce_only_never_reverses_and_cancels_unfillable_remainder() -> None:
    """An oversized reduce-only order must stop exactly at flat."""
    config = _config(slippage_bps=0)
    state = submit_paper_order(
        _intent(side=PaperSide.SELL, contracts=5, reduce_only=True),
        submitted_event_index=1,
        config=config,
    )
    result = evaluate_paper_order(
        state,
        _book(2, bid_contracts=20),
        config=config,
        position_contracts=3,
    )
    assert result.fill is not None
    assert result.fill.contracts == 3
    assert result.state.status is PaperOrderStatus.CANCELLED
    assert result.state.remaining_contracts == 2
    assert result.reason == "REDUCE_ONLY_REMAINDER_CANCELLED"

    no_position = submit_paper_order(
        _intent(side=PaperSide.SELL, reduce_only=True),
        submitted_event_index=1,
        config=config,
    )
    rejected = evaluate_paper_order(
        no_position,
        _book(2),
        config=config,
        position_contracts=0,
    )
    assert rejected.fill is None
    assert rejected.state.status is PaperOrderStatus.CANCELLED
    assert rejected.reason == "REDUCE_ONLY_NO_POSITION"


def test_reduce_only_keeps_working_after_liquidity_limited_partial_fill() -> None:
    """Surplus contracts cancel only after fills have actually flattened the position."""
    config = _config(slippage_bps=0)
    state = submit_paper_order(
        _intent(side=PaperSide.SELL, contracts=5, reduce_only=True),
        submitted_event_index=1,
        config=config,
    )
    partial = evaluate_paper_order(
        state,
        _book(2, bid_contracts=2),
        config=config,
        position_contracts=3,
    )
    assert partial.fill is not None and partial.fill.contracts == 2
    assert partial.state.status is PaperOrderStatus.PARTIALLY_FILLED
    assert partial.state.remaining_contracts == 3

    flattened = evaluate_paper_order(
        partial.state,
        _book(3, bid_contracts=10),
        config=config,
        position_contracts=1,
    )
    assert flattened.fill is not None and flattened.fill.contracts == 1
    assert flattened.state.status is PaperOrderStatus.CANCELLED
    assert flattened.state.remaining_contracts == 2
    assert flattened.reason == "REDUCE_ONLY_REMAINDER_CANCELLED"


def test_reduce_only_cancels_remainder_when_owned_reservation_is_exhausted() -> None:
    """New exposure must not silently enlarge a prior atomic reduction reservation."""
    config = _config(slippage_bps=0)
    state = submit_paper_order(
        _intent(side=PaperSide.BUY, contracts=4, reduce_only=True),
        submitted_event_index=1,
        config=config,
    )

    result = _evaluate_allocated_order(
        state,
        _book(2, ask_contracts=4),
        config=config,
        position_contracts=-4,
        allocation_ordinal=0,
        liquidity_before=4,
        reservation_before=2,
    )

    assert result.fill is not None and result.fill.contracts == 2
    assert result.state.status is PaperOrderStatus.CANCELLED
    assert result.state.remaining_contracts == 2


def test_config_is_bound_at_submission_and_cannot_change_mid_order() -> None:
    """Changing latency/slippage after submission must fail closed."""
    state = submit_paper_order(_intent(), submitted_event_index=1, config=_config())
    with pytest.raises(PaperSimulationError):
        evaluate_paper_order(
            state,
            _book(2),
            config=_config(slippage_bps=0),
            position_contracts=0,
        )


def test_submission_rejects_event_index_latency_overflow() -> None:
    """The derived eligibility index must remain an exact signed 63-bit integer."""
    with pytest.raises(PaperSimulationError):
        submit_paper_order(
            _intent(),
            submitted_event_index=2**63 - 1,
            config=_config(latency_events=1),
        )


def test_live_mode_and_any_nonzero_fee_model_are_structurally_unavailable() -> None:
    """PAPER simulation must never masquerade as LIVE or authoritative fees."""
    with pytest.raises(PaperSimulationError):
        _intent(mode=OmsExecutionMode.LIVE)
    assert tuple(PaperFeeModel) == (PaperFeeModel.SYNTHETIC_ZERO_FEE,)
    defaulted = PaperModelConfig(
        latency_events=0,
        slippage_bps=0,
        max_fill_contracts_per_event=1,
    )
    assert defaulted.fee_model is PaperFeeModel.SYNTHETIC_ZERO_FEE


@pytest.mark.parametrize(
    "changes",
    [
        {
            "kind": PaperOrderKind.LIMIT,
            "limit_price": Decimal("100"),
            "stop_price": Decimal("100"),
        },
        {
            "kind": PaperOrderKind.STOP_MARKET,
            "stop_price": Decimal("100"),
            "limit_price": Decimal("100"),
        },
        {
            "kind": PaperOrderKind.TRAILING_STOP,
            "trail_amount": Decimal("5"),
            "stop_price": Decimal("5"),
        },
    ],
)
def test_order_kind_rejects_equal_valued_fields_outside_its_exact_shape(
    changes: dict[str, object],
) -> None:
    """An extra field must not hide by equaling the legitimate price field."""
    with pytest.raises(PaperSimulationError):
        _intent(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {
            "kind": PaperOrderKind.LIMIT,
            "limit_price": Decimal("100.03"),
        },
        {
            "kind": PaperOrderKind.STOP_MARKET,
            "stop_price": Decimal("100.03"),
        },
        {
            "kind": PaperOrderKind.TRAILING_STOP,
            "trail_amount": Decimal("0.03"),
        },
        {
            "bracket": PaperBracketSpec(
                stop_loss_price=Decimal("90.03"),
                stop_loss_limit_price=None,
                stop_loss_trail_amount=None,
                take_profit_price=None,
                take_profit_limit_price=None,
            )
        },
    ],
)
def test_order_prices_and_trailing_amount_must_align_to_product_tick(
    changes: dict[str, object],
) -> None:
    """The simulator must fail closed before a non-product price can be proposed."""
    with pytest.raises(PaperSimulationError):
        _intent(**changes)


def test_candle_market_fill_uses_next_event_open_and_explicit_volume() -> None:
    """Using candle close/future high-low for a market fill must fail this fixture."""
    config = _config(slippage_bps=0, max_fill_contracts_per_event=3)
    state = submit_paper_order(_intent(), submitted_event_index=4, config=config)
    result = evaluate_paper_order(
        state,
        _candle(5, open_price=Decimal("98"), volume_contracts=2),
        config=config,
        position_contracts=0,
    )
    assert result.fill is not None
    assert result.fill.contracts == 2
    assert result.fill.price == Decimal("98")


def test_public_models_reject_reconstructed_or_hostile_outer_values_safely() -> None:
    """Caller-controlled attribute callbacks must not escape the safe error boundary."""
    calls: list[str] = []

    class Hostile:
        def __getattribute__(self, name: str) -> object:
            calls.append(name)
            raise AssertionError("paper-secret-canary")

    state = submit_paper_order(_intent(), submitted_event_index=1, config=_config())
    forged = object.__new__(PaperModelConfig)
    for bad_state, bad_event, bad_config in (
        (Hostile(), _book(2), _config()),
        (state, Hostile(), _config()),
        (state, _book(2), forged),
    ):
        with pytest.raises(PaperSimulationError) as captured:
            evaluate_paper_order(
                bad_state,
                bad_event,
                config=bad_config,
                position_contracts=0,
            )
        assert "paper-secret-canary" not in str(captured.value)
    assert calls == []


def test_reconstructed_state_rejects_impossible_trigger_and_fill_combinations() -> None:
    """Persistence reconstruction must not bypass the simulator's state graph."""
    config = _config(slippage_bps=0)
    market = submit_paper_order(_intent(), submitted_event_index=1, config=config)
    stop = submit_paper_order(
        _intent(kind=PaperOrderKind.STOP_MARKET, stop_price=Decimal("105")),
        submitted_event_index=1,
        config=config,
    )
    trailing = submit_paper_order(
        _intent(kind=PaperOrderKind.TRAILING_STOP, trail_amount=Decimal("5")),
        submitted_event_index=1,
        config=config,
    )
    impossible = (
        replace(market, status=PaperOrderStatus.PARTIALLY_FILLED),
        replace(market, remaining_contracts=4, filled_contracts=1),
        replace(
            stop,
            last_event_index=2,
            triggered_event_index=2,
        ),
        replace(
            stop,
            status=PaperOrderStatus.PARTIALLY_FILLED,
            remaining_contracts=4,
            filled_contracts=1,
            last_event_index=2,
        ),
        replace(
            trailing,
            status=PaperOrderStatus.TRIGGERED,
            last_event_index=2,
            triggered_event_index=2,
        ),
    )
    for state in impossible:
        with pytest.raises(PaperSimulationError):
            evaluate_paper_order(
                state,
                _book(3, trigger_reference=Decimal("106")),
                config=config,
                position_contracts=0,
            )
