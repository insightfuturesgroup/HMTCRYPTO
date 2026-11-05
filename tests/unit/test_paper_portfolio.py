"""Account-owned PAPER state coordinates liquidity, reductions, and identities."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import UUID

import pytest

from hmt_crypto.oms.models import OmsExecutionMode
from hmt_crypto.oms.paper_brackets import PaperBracketAction, PaperBracketLifecycle
from hmt_crypto.oms.paper_identity import event_sha256, revision_sha256
from hmt_crypto.oms.paper_models import (
    PaperBracketSpec,
    PaperCandle,
    PaperFeeModel,
    PaperModelConfig,
    PaperOrderIntent,
    PaperOrderKind,
    PaperOrderStatus,
    PaperSide,
    PaperSimulationError,
    PaperTopOfBook,
    PaperTriggerDirection,
)
from hmt_crypto.oms.paper_portfolio import (
    PaperPosition,
    PaperSimulatedOrder,
    PaperSimulationState,
    create_paper_simulation,
    evaluate_simulation_event,
    simulation_order,
    simulation_position,
    submit_simulation_order,
)
from hmt_crypto.oms.paper_state import submit_paper_order

_NOW = datetime(2026, 11, 6, tzinfo=UTC)
_ACCOUNT_A = UUID(int=101)
_ACCOUNT_B = UUID(int=102)
_PRODUCT = 27
_STREAM = "delta-btcusd-l1"


def _config(**changes: object) -> PaperModelConfig:
    values: dict[str, object] = {
        "latency_events": 0,
        "slippage_bps": 0,
        "max_fill_contracts_per_event": 100,
        "fee_model": PaperFeeModel.SYNTHETIC_ZERO_FEE,
    }
    return PaperModelConfig(**(values | changes))


def _intent(
    order_number: int,
    *,
    account: UUID = _ACCOUNT_A,
    mode: OmsExecutionMode = OmsExecutionMode.PAPER,
    side: PaperSide = PaperSide.BUY,
    contracts: int = 5,
    kind: PaperOrderKind = PaperOrderKind.MARKET,
    limit_price: Decimal | None = None,
    stop_price: Decimal | None = None,
    trigger_direction: PaperTriggerDirection | None = None,
    reduce_only: bool = False,
    bracket: PaperBracketSpec | None = None,
) -> PaperOrderIntent:
    return PaperOrderIntent(
        order_id=UUID(int=order_number),
        paper_account_id=account,
        mode=mode,
        product_id=_PRODUCT,
        side=side,
        contracts=contracts,
        kind=kind,
        tick_size=Decimal("0.5"),
        limit_price=limit_price,
        stop_price=stop_price,
        trail_amount=None,
        trigger_direction=trigger_direction,
        reduce_only=reduce_only,
        bracket=bracket,
    )


def _book(
    index: int,
    *,
    observed_at: datetime | None = None,
    stream_id: str = _STREAM,
    bid_contracts: int = 20,
    ask_contracts: int = 20,
) -> PaperTopOfBook:
    return PaperTopOfBook(
        stream_id=stream_id,
        product_id=_PRODUCT,
        event_index=index,
        observed_at=observed_at or _NOW + timedelta(seconds=index),
        bid_price=Decimal("99.5"),
        ask_price=Decimal("100"),
        bid_contracts=bid_contracts,
        ask_contracts=ask_contracts,
        trigger_reference=Decimal("100"),
    )


def _candle(
    index: int,
    *,
    opening: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    volume: int = 20,
) -> PaperCandle:
    return PaperCandle(
        stream_id=_STREAM,
        product_id=_PRODUCT,
        event_index=index,
        observed_at=_NOW + timedelta(minutes=index),
        open_price=opening,
        high_price=high,
        low_price=low,
        close_price=close,
        volume_contracts=volume,
    )


def _submit(state: object, intent: PaperOrderIntent) -> object:
    return submit_simulation_order(
        state,
        intent,
        config=_config(),
        stream_id=_STREAM,
        submitted_event_index=1,
    )


def test_shared_event_liquidity_is_allocated_once_in_deterministic_order() -> None:
    """Two market orders must not each consume the full top-of-book quantity."""
    state = create_paper_simulation()
    state = _submit(state, _intent(1, contracts=5))
    state = _submit(state, _intent(2, contracts=5))

    event = _book(2, ask_contracts=6)
    result = evaluate_simulation_event(state, event)

    assert [fill.order_id for fill in result.fills] == [UUID(int=1), UUID(int=2)]
    assert [fill.contracts for fill in result.fills] == [5, 1]
    assert sum(fill.contracts for fill in result.fills) == 6
    assert {fill.event_sha256 for fill in result.fills} == {event_sha256(event)}
    assert simulation_position(result.state, _ACCOUNT_A, OmsExecutionMode.PAPER, _PRODUCT) == 6


def test_shared_liquidity_uses_price_then_time_priority() -> None:
    """A less aggressive UUID must not consume liquidity before the better price."""
    state = create_paper_simulation()
    state = _submit(
        state,
        _intent(1, contracts=1, kind=PaperOrderKind.LIMIT, limit_price=Decimal("100")),
    )
    state = _submit(
        state,
        _intent(2, contracts=1, kind=PaperOrderKind.LIMIT, limit_price=Decimal("101")),
    )

    result = evaluate_simulation_event(state, _book(2, ask_contracts=1))

    assert [fill.order_id for fill in result.fills] == [UUID(int=2)]


def test_owned_reservations_clip_stale_concurrent_reductions_before_fill() -> None:
    """A prior non-reduce fill must shrink a stale reduction reservation before use."""
    state = create_paper_simulation(
        (PaperPosition(_ACCOUNT_A, OmsExecutionMode.PAPER, _PRODUCT, 3),)
    )
    state = _submit(state, _intent(1, side=PaperSide.SELL, contracts=2))
    state = _submit(
        state,
        _intent(2, side=PaperSide.SELL, contracts=3, reduce_only=True),
    )

    result = evaluate_simulation_event(state, _book(2, bid_contracts=10))

    assert [fill.contracts for fill in result.fills] == [2, 1]
    assert simulation_position(result.state, _ACCOUNT_A, OmsExecutionMode.PAPER, _PRODUCT) == 0
    reduction = simulation_order(result.state, _ACCOUNT_A, OmsExecutionMode.PAPER, UUID(int=2))
    assert reduction.reserved_contracts == 0
    assert reduction.state.remaining_contracts == 2
    assert reduction.state.status.value == "CANCELLED"


def test_concurrent_reduce_only_submissions_share_one_position_reservation() -> None:
    """Reservation totals cannot exceed the owned position even before market data."""
    state = create_paper_simulation(
        (PaperPosition(_ACCOUNT_A, OmsExecutionMode.PAPER, _PRODUCT, 3),)
    )
    state = _submit(
        state,
        _intent(1, side=PaperSide.SELL, contracts=3, reduce_only=True),
    )
    state = _submit(
        state,
        _intent(2, side=PaperSide.SELL, contracts=3, reduce_only=True),
    )

    first = simulation_order(state, _ACCOUNT_A, OmsExecutionMode.PAPER, UUID(int=1))
    second = simulation_order(state, _ACCOUNT_A, OmsExecutionMode.PAPER, UUID(int=2))
    assert first.reserved_contracts == 3
    assert second.reserved_contracts == 0
    assert second.state.status.value == "CANCELLED"

    result = evaluate_simulation_event(state, _book(2, bid_contracts=10))
    assert sum(fill.contracts for fill in result.fills) == 3
    assert simulation_position(result.state, _ACCOUNT_A, OmsExecutionMode.PAPER, _PRODUCT) == 0


def test_fill_identity_binds_account_mode_intent_config_and_full_event() -> None:
    """Cross-account and distinct-event simulations must never share a fill key."""
    state = create_paper_simulation()
    shared_order_id = 9
    state = _submit(state, _intent(shared_order_id, account=_ACCOUNT_A, contracts=1))
    state = _submit(
        state,
        _intent(
            shared_order_id,
            account=_ACCOUNT_B,
            mode=OmsExecutionMode.BACKTEST,
            contracts=1,
        ),
    )
    first = evaluate_simulation_event(state, _book(2, ask_contracts=2))
    assert len({fill.fill_key_sha256 for fill in first.fills}) == 2

    replay_state = create_paper_simulation()
    replay_state = _submit(replay_state, _intent(shared_order_id, contracts=1))
    changed_event = _book(
        2,
        ask_contracts=1,
        observed_at=_NOW + timedelta(seconds=200),
    )
    changed = evaluate_simulation_event(replay_state, changed_event)
    assert changed.fills[0].fill_key_sha256 != first.fills[0].fill_key_sha256

    changed_config_state = create_paper_simulation()
    changed_config_state = submit_simulation_order(
        changed_config_state,
        _intent(shared_order_id, contracts=1),
        config=_config(max_fill_contracts_per_event=50),
        stream_id=_STREAM,
        submitted_event_index=1,
    )
    changed_config = evaluate_simulation_event(
        changed_config_state,
        _book(2, ask_contracts=2),
    )
    assert changed_config.fills[0].fill_key_sha256 != first.fills[0].fill_key_sha256


@pytest.mark.parametrize(
    "position,side,trigger_direction,trigger,trigger_candle,gap_candle",
    [
        (
            2,
            PaperSide.SELL,
            PaperTriggerDirection.ABOVE,
            Decimal("110"),
            _candle(
                2,
                opening=Decimal("100"),
                high=Decimal("111"),
                low=Decimal("99"),
                close=Decimal("109"),
            ),
            _candle(
                3,
                opening=Decimal("115"),
                high=Decimal("116"),
                low=Decimal("114"),
                close=Decimal("115"),
            ),
        ),
        (
            -2,
            PaperSide.BUY,
            PaperTriggerDirection.BELOW,
            Decimal("90"),
            _candle(
                2,
                opening=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("89"),
                close=Decimal("91"),
            ),
            _candle(
                3, opening=Decimal("85"), high=Decimal("86"), low=Decimal("84"), close=Decimal("85")
            ),
        ),
    ],
)
def test_take_profit_direction_handles_long_and_short_gap_execution(
    position: int,
    side: PaperSide,
    trigger_direction: PaperTriggerDirection,
    trigger: Decimal,
    trigger_candle: PaperCandle,
    gap_candle: PaperCandle,
) -> None:
    """Take-profit direction is independent of BUY/SELL execution side."""
    state = create_paper_simulation(
        (PaperPosition(_ACCOUNT_A, OmsExecutionMode.PAPER, _PRODUCT, position),)
    )
    state = _submit(
        state,
        _intent(
            1,
            side=side,
            contracts=2,
            kind=PaperOrderKind.STOP_MARKET,
            stop_price=trigger,
            trigger_direction=trigger_direction,
            reduce_only=True,
        ),
    )
    triggered = evaluate_simulation_event(state, trigger_candle)
    assert triggered.fills == ()
    filled = evaluate_simulation_event(triggered.state, gap_candle)
    assert filled.fills[0].price == gap_candle.open_price
    assert simulation_position(filled.state, _ACCOUNT_A, OmsExecutionMode.PAPER, _PRODUCT) == 0


def test_stream_identity_and_observed_time_are_monotonic() -> None:
    """A changed stream or backward timestamp must fail before evaluating orders."""
    state = create_paper_simulation()
    state = _submit(state, _intent(1, contracts=2))
    first = evaluate_simulation_event(state, _book(2, ask_contracts=1))
    with pytest.raises(PaperSimulationError):
        evaluate_simulation_event(
            first.state,
            _book(3, stream_id="replacement-stream", ask_contracts=1),
        )
    with pytest.raises(PaperSimulationError):
        evaluate_simulation_event(
            first.state,
            _book(3, observed_at=_NOW, ask_contracts=1),
        )


def test_bracket_identity_resize_and_executed_sibling_cancellation_are_stable() -> None:
    """Logical child IDs stay fixed while revisions resize and OCO execution cancels a sibling."""
    bracket = PaperBracketSpec(
        stop_loss_price=Decimal("90"),
        stop_loss_limit_price=Decimal("89"),
        stop_loss_trail_amount=None,
        take_profit_price=Decimal("120"),
        take_profit_limit_price=Decimal("119"),
    )
    state = _submit(create_paper_simulation(), _intent(77, contracts=5, bracket=bracket))
    created_result = evaluate_simulation_event(state, _book(2, ask_contracts=2))
    assert {item.action for item in created_result.children} == {PaperBracketAction.CREATE}
    assert {item.trigger_direction for item in created_result.children} == {
        PaperTriggerDirection.ABOVE,
        PaperTriggerDirection.BELOW,
    }

    resized_result = evaluate_simulation_event(created_result.state, _book(3, ask_contracts=3))
    resized = resized_result.state.brackets[0]
    assert {item.child_id_sha256 for item in resized_result.children} == {
        item.child_id_sha256 for item in created_result.children
    }
    assert {item.oco_group_sha256 for item in resized_result.children} == {
        item.oco_group_sha256 for item in created_result.children
    }
    assert {item.action for item in resized_result.children} == {PaperBracketAction.REPLACE}
    assert all(item.replaces_revision_sha256 is not None for item in resized_result.children)
    assert resized.executed_child_id_sha256 is None


def test_only_portfolio_transition_is_public_and_fill_identity_binds_allocation() -> None:
    """The unsafe single-order path is private and allocation evidence distinguishes fills."""
    import hmt_crypto.oms.paper_engine as engine

    assert not hasattr(engine, "evaluate_paper_order")
    assert "evaluate_paper_order" not in getattr(engine, "__all__", ())

    target = _intent(50, contracts=1)
    alone = submit_simulation_order(
        create_paper_simulation(),
        target,
        config=_config(max_fill_contracts_per_event=1),
        stream_id=_STREAM,
        submitted_event_index=1,
    )
    alone_fill = evaluate_simulation_event(alone, _book(2, ask_contracts=2)).fills[0]

    shared = create_paper_simulation()
    shared = _submit(shared, _intent(1, account=_ACCOUNT_B, contracts=1))
    shared = submit_simulation_order(
        shared,
        target,
        config=_config(max_fill_contracts_per_event=1),
        stream_id=_STREAM,
        submitted_event_index=1,
    )
    target_fill = evaluate_simulation_event(shared, _book(2, ask_contracts=2)).fills[1]
    assert target_fill.contracts == alone_fill.contracts == 1
    assert target_fill.event_sha256 == alone_fill.event_sha256
    assert target_fill.fill_key_sha256 != alone_fill.fill_key_sha256


def test_decimal_identity_is_exact_context_free_and_supports_38_digits() -> None:
    """Canonical hashes preserve Decimal tuples and ignore ambient precision."""
    exact = _book(
        2,
        bid_contracts=1,
        ask_contracts=1,
    )
    exact = PaperTopOfBook(
        stream_id=exact.stream_id,
        product_id=exact.product_id,
        event_index=exact.event_index,
        observed_at=exact.observed_at,
        bid_price=Decimal("12345678901234567890.123456789012345678"),
        ask_price=Decimal("12345678901234567890.123456789012345678"),
        bid_contracts=1,
        ask_contracts=1,
        trigger_reference=Decimal("12345678901234567890.123456789012345678"),
    )
    expected = event_sha256(exact)
    with localcontext() as context:
        context.prec = 6
        assert event_sha256(exact) == expected

    scaled = PaperTopOfBook(
        stream_id=_STREAM,
        product_id=_PRODUCT,
        event_index=2,
        observed_at=exact.observed_at,
        bid_price=Decimal("100.0"),
        ask_price=Decimal("100.0"),
        bid_contracts=1,
        ask_contracts=1,
        trigger_reference=Decimal("100.0"),
    )
    differently_scaled = PaperTopOfBook(
        stream_id=_STREAM,
        product_id=_PRODUCT,
        event_index=2,
        observed_at=exact.observed_at,
        bid_price=Decimal("100.00"),
        ask_price=Decimal("100.00"),
        bid_contracts=1,
        ask_contracts=1,
        trigger_reference=Decimal("100.00"),
    )
    assert event_sha256(scaled) != event_sha256(differently_scaled)


def test_reconstructed_flat_namespace_rejects_orphan_reduce_reservation() -> None:
    """Reservation validation covers order namespaces absent from positions."""
    config = _config()
    order = submit_paper_order(
        _intent(88, side=PaperSide.SELL, contracts=1, reduce_only=True),
        submitted_event_index=1,
        config=config,
        stream_id=_STREAM,
    )
    with pytest.raises(PaperSimulationError):
        PaperSimulationState((), (PaperSimulatedOrder(order, config, 1),), ())


def test_reconstructed_bracket_state_is_copied_and_tampering_fails_closed() -> None:
    """Portfolio reconstruction canonicalizes nested lifecycle state and rejects corruption."""
    bracket = PaperBracketSpec(
        stop_loss_price=Decimal("90"),
        stop_loss_limit_price=None,
        stop_loss_trail_amount=None,
        take_profit_price=Decimal("120"),
        take_profit_limit_price=None,
    )
    initial = _submit(create_paper_simulation(), _intent(69, contracts=2, bracket=bracket))
    state = evaluate_simulation_event(initial, _book(2, ask_contracts=2)).state
    rebuilt = PaperSimulationState(state.positions, state.orders, state.cursors, state.brackets)
    assert rebuilt.brackets[0] is not state.brackets[0]
    assert rebuilt.brackets[0].child_states[0] is not state.brackets[0].child_states[0]
    with pytest.raises(PaperSimulationError):
        PaperSimulationState((), rebuilt.orders, rebuilt.cursors, rebuilt.brackets)
    undercovered = (PaperPosition(_ACCOUNT_A, OmsExecutionMode.PAPER, _PRODUCT, 1),)
    with pytest.raises(PaperSimulationError):
        PaperSimulationState(undercovered, rebuilt.orders, rebuilt.cursors, rebuilt.brackets)

    lifecycle = rebuilt.brackets[0]
    cancelled = tuple(
        replace(
            proposal,
            action=PaperBracketAction.CANCEL,
            replaces_revision_sha256=proposal.revision_sha256,
            revision_sha256=revision_sha256(proposal.child_id_sha256, 0),
        )
        for proposal in lifecycle.current_children
    )
    cancelled_states = tuple(
        replace(child, status=PaperOrderStatus.CANCELLED) for child in lifecycle.child_states
    )
    with pytest.raises(PaperSimulationError):
        PaperBracketLifecycle(
            lifecycle.parent_order_id,
            lifecycle.oco_group_sha256,
            cancelled,
            None,
            cancelled_states,
        )

    stop_index = next(
        index
        for index, proposal in enumerate(lifecycle.current_children)
        if proposal.role.value == "STOP_LOSS"
    )
    forged_proposals = list(lifecycle.current_children)
    forged_states = list(lifecycle.child_states)
    forged_proposals[stop_index] = replace(forged_proposals[stop_index], stop_price=Decimal("80"))
    forged_states[stop_index] = replace(
        forged_states[stop_index],
        intent=replace(forged_states[stop_index].intent, stop_price=Decimal("80")),
    )
    forged_lifecycle = PaperBracketLifecycle(
        lifecycle.parent_order_id,
        lifecycle.oco_group_sha256,
        tuple(forged_proposals),
        lifecycle.executed_child_id_sha256,
        tuple(forged_states),
    )
    with pytest.raises(PaperSimulationError):
        PaperSimulationState(
            rebuilt.positions,
            rebuilt.orders,
            rebuilt.cursors,
            (forged_lifecycle,),
        )

    lifecycle = state.brackets[0]
    object.__setattr__(lifecycle, "oco_group_sha256", "0" * 64)
    with pytest.raises(PaperSimulationError):
        PaperSimulationState(state.positions, state.orders, state.cursors, state.brackets)


def test_portfolio_owns_bracket_resize_and_oco_cancel_lifecycle() -> None:
    """Parent partial fills create/replace children; a later child fill cancels its sibling."""
    bracket = PaperBracketSpec(
        stop_loss_price=Decimal("90"),
        stop_loss_limit_price=None,
        stop_loss_trail_amount=None,
        take_profit_price=Decimal("120"),
        take_profit_limit_price=None,
    )
    state = _submit(create_paper_simulation(), _intent(70, contracts=5, bracket=bracket))
    created = evaluate_simulation_event(state, _book(2, ask_contracts=2))
    assert {item.action for item in created.children} == {PaperBracketAction.CREATE}
    assert len(created.state.brackets) == 1

    resized = evaluate_simulation_event(created.state, _book(3, ask_contracts=3))
    assert {item.action for item in resized.children} == {PaperBracketAction.REPLACE}
    assert {item.contracts for item in resized.children} == {5}

    trigger = PaperTopOfBook(
        stream_id=_STREAM,
        product_id=_PRODUCT,
        event_index=4,
        observed_at=_NOW + timedelta(seconds=4),
        bid_price=Decimal("90"),
        ask_price=Decimal("90.5"),
        bid_contracts=5,
        ask_contracts=5,
        trigger_reference=Decimal("90"),
    )
    triggered = evaluate_simulation_event(resized.state, trigger)
    assert triggered.fills == ()
    execution = PaperTopOfBook(
        stream_id=_STREAM,
        product_id=_PRODUCT,
        event_index=5,
        observed_at=_NOW + timedelta(seconds=5),
        bid_price=Decimal("89"),
        ask_price=Decimal("89.5"),
        bid_contracts=2,
        ask_contracts=5,
        trigger_reference=Decimal("89"),
    )
    executed = evaluate_simulation_event(triggered.state, execution)
    assert len(executed.fills) == 1
    assert executed.fills[0].contracts == 2
    assert {item.action for item in executed.children} == {PaperBracketAction.CANCEL}
    assert executed.state.brackets[0].executed_child_id_sha256 is not None

    completion = PaperTopOfBook(
        stream_id=_STREAM,
        product_id=_PRODUCT,
        event_index=6,
        observed_at=_NOW + timedelta(seconds=6),
        bid_price=Decimal("88"),
        ask_price=Decimal("88.5"),
        bid_contracts=3,
        ask_contracts=3,
        trigger_reference=Decimal("88"),
    )
    completed = evaluate_simulation_event(executed.state, completion)
    assert completed.children == ()
    assert completed.fills[0].contracts == 3
    assert simulation_position(completed.state, _ACCOUNT_A, OmsExecutionMode.PAPER, _PRODUCT) == 0


def test_bracket_candle_that_touches_both_oco_legs_fails_closed() -> None:
    """An OHLC candle cannot reveal whether stop-loss or take-profit traded first."""
    bracket = PaperBracketSpec(
        stop_loss_price=Decimal("90"),
        stop_loss_limit_price=None,
        stop_loss_trail_amount=None,
        take_profit_price=Decimal("120"),
        take_profit_limit_price=None,
    )
    state = _submit(create_paper_simulation(), _intent(71, contracts=2, bracket=bracket))
    filled = evaluate_simulation_event(state, _book(2, ask_contracts=2))
    ambiguous = _candle(
        3,
        opening=Decimal("100"),
        high=Decimal("125"),
        low=Decimal("85"),
        close=Decimal("105"),
    )
    with pytest.raises(PaperSimulationError):
        evaluate_simulation_event(filled.state, ambiguous)


def test_mutated_nested_state_fails_without_invoking_caller_callbacks() -> None:
    """Public transitions must revalidate exact nested state before attribute access."""
    calls: list[str] = []

    class Hostile:
        def __getattribute__(self, name: str) -> object:
            calls.append(name)
            raise AssertionError("paper-callback-canary")

    state = create_paper_simulation()
    object.__setattr__(state, "orders", (Hostile(),))

    with pytest.raises(PaperSimulationError) as captured:
        evaluate_simulation_event(state, _book(1))
    assert "paper-callback-canary" not in str(captured.value)
    assert calls == []
