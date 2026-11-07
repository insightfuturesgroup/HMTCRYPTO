"""PAPER bracket-child proposals remain reduce-only and non-authorizing."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from hmt_crypto.oms.models import OmsExecutionMode
from hmt_crypto.oms.paper_brackets import reconcile_bracket_children
from hmt_crypto.oms.paper_engine import _evaluate_allocated_order
from hmt_crypto.oms.paper_models import (
    PaperBracketAction,
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
)
from hmt_crypto.oms.paper_portfolio import (
    PaperPosition,
    create_paper_simulation,
    evaluate_simulation_event,
    simulation_order,
    simulation_position,
    submit_simulation_order,
)
from hmt_crypto.oms.paper_state import submit_paper_order

_NOW = datetime(2026, 11, 6, tzinfo=UTC)


def evaluate_paper_order(
    state: object,
    event: object,
    *,
    config: PaperModelConfig,
    position_contracts: int,
) -> PaperEvaluation:
    """Exercise the private kernel with explicit allocation and test-only child view."""
    assert isinstance(state, PaperOrderState)
    assert isinstance(event, (PaperTopOfBook, PaperCandle))
    liquidity = (
        event.volume_contracts
        if isinstance(event, PaperCandle)
        else event.ask_contracts
        if state.intent.side is PaperSide.BUY
        else event.bid_contracts
    )
    result = _evaluate_allocated_order(
        state,
        event,
        config=config,
        position_contracts=position_contracts,
        allocation_ordinal=0,
        liquidity_before=liquidity,
        reservation_before=state.remaining_contracts if state.intent.reduce_only else 0,
    )
    if result.fill is None or result.state.intent.bracket is None:
        return result
    update = reconcile_bracket_children(
        result.state,
        config=config,
        fill_price=result.fill.price,
        target_contracts=result.state.filled_contracts,
        lifecycle=None,
    )
    return PaperEvaluation(
        result.state,
        result.fill,
        () if update is None else update.proposals,
        result.reason,
    )


def _config() -> PaperModelConfig:
    return PaperModelConfig(
        latency_events=0,
        slippage_bps=0,
        max_fill_contracts_per_event=10,
        fee_model=PaperFeeModel.SYNTHETIC_ZERO_FEE,
    )


def _intent(
    *,
    order_id: int = 91,
    side: PaperSide = PaperSide.BUY,
    contracts: int = 5,
    reduce_only: bool = False,
    bracket: PaperBracketSpec | None,
) -> PaperOrderIntent:
    return PaperOrderIntent(
        order_id=UUID(int=order_id),
        paper_account_id=UUID(int=92),
        mode=OmsExecutionMode.PAPER,
        product_id=27,
        side=side,
        contracts=contracts,
        kind=PaperOrderKind.MARKET,
        tick_size=Decimal("0.5"),
        limit_price=None,
        stop_price=None,
        trail_amount=None,
        trigger_direction=None,
        reduce_only=reduce_only,
        bracket=bracket,
    )


def _book(index: int, *, size: int = 2, price: Decimal = Decimal("100")) -> PaperTopOfBook:
    return PaperTopOfBook(
        stream_id="paper-default",
        product_id=27,
        event_index=index,
        observed_at=_NOW + timedelta(seconds=index),
        bid_price=price,
        ask_price=price,
        bid_contracts=size,
        ask_contracts=size,
        trigger_reference=price,
    )


def test_partial_parent_fill_proposes_resized_reduce_only_oco_children() -> None:
    """Children must cover cumulative fills, share OCO identity, and never authorize I/O."""
    bracket = PaperBracketSpec(
        stop_loss_price=Decimal("90"),
        stop_loss_limit_price=Decimal("89"),
        stop_loss_trail_amount=None,
        take_profit_price=Decimal("120"),
        take_profit_limit_price=Decimal("119"),
    )
    config = _config()
    state = submit_paper_order(_intent(bracket=bracket), submitted_event_index=1, config=config)
    first = evaluate_paper_order(state, _book(2), config=config, position_contracts=0)
    assert first.fill is not None and first.fill.contracts == 2
    assert len(first.children) == 2
    assert {child.contracts for child in first.children} == {2}
    assert {child.side for child in first.children} == {PaperSide.SELL}
    assert {child.reduce_only for child in first.children} == {True}
    assert {child.oco_group_sha256 for child in first.children} == {
        first.children[0].oco_group_sha256
    }
    assert {child.kind for child in first.children} == {
        PaperOrderKind.STOP_LIMIT,
    }

    second = evaluate_paper_order(
        first.state,
        _book(3, size=3),
        config=config,
        position_contracts=2,
    )
    assert second.state.status is PaperOrderStatus.FILLED
    assert {child.contracts for child in second.children} == {5}
    assert {child.oco_group_sha256 for child in second.children} == {
        first.children[0].oco_group_sha256
    }


def test_trailing_bracket_child_is_explicit_and_take_profit_is_optional() -> None:
    """A supported trailing stop child must not invent a fixed stop price."""
    bracket = PaperBracketSpec(
        stop_loss_price=None,
        stop_loss_limit_price=None,
        stop_loss_trail_amount=Decimal("5"),
        take_profit_price=None,
        take_profit_limit_price=None,
    )
    state = submit_paper_order(_intent(bracket=bracket), submitted_event_index=1, config=_config())
    result = evaluate_paper_order(state, _book(2), config=_config(), position_contracts=0)
    assert len(result.children) == 1
    child = result.children[0]
    assert child.kind is PaperOrderKind.TRAILING_STOP
    assert child.trail_amount == Decimal("5")
    assert child.stop_price is None


@pytest.mark.parametrize(
    "side,stop_loss,take_profit",
    [
        (PaperSide.BUY, Decimal("101"), Decimal("120")),
        (PaperSide.BUY, Decimal("90"), Decimal("99")),
        (PaperSide.SELL, Decimal("99"), Decimal("80")),
        (PaperSide.SELL, Decimal("110"), Decimal("101")),
    ],
)
def test_bracket_that_does_not_protect_the_fill_rejects_parent_before_fill(
    side: PaperSide,
    stop_loss: Decimal,
    take_profit: Decimal,
) -> None:
    """A gap through invalid protection must fail closed before proposing a parent fill."""
    bracket = PaperBracketSpec(
        stop_loss_price=stop_loss,
        stop_loss_limit_price=None,
        stop_loss_trail_amount=None,
        take_profit_price=take_profit,
        take_profit_limit_price=None,
    )
    state = submit_paper_order(
        _intent(side=side, bracket=bracket), submitted_event_index=1, config=_config()
    )
    result = evaluate_paper_order(state, _book(2), config=_config(), position_contracts=0)
    assert result.fill is None
    assert result.children == ()
    assert result.state.status is PaperOrderStatus.REJECTED
    assert result.reason == "BRACKET_INVALID_FOR_FILL"


@pytest.mark.parametrize(
    "side,bracket",
    [
        (
            PaperSide.BUY,
            PaperBracketSpec(
                stop_loss_price=Decimal("90"),
                stop_loss_limit_price=Decimal("91"),
                stop_loss_trail_amount=None,
                take_profit_price=None,
                take_profit_limit_price=None,
            ),
        ),
        (
            PaperSide.BUY,
            PaperBracketSpec(
                stop_loss_price=None,
                stop_loss_limit_price=None,
                stop_loss_trail_amount=None,
                take_profit_price=Decimal("120"),
                take_profit_limit_price=Decimal("121"),
            ),
        ),
        (
            PaperSide.SELL,
            PaperBracketSpec(
                stop_loss_price=Decimal("110"),
                stop_loss_limit_price=Decimal("109"),
                stop_loss_trail_amount=None,
                take_profit_price=None,
                take_profit_limit_price=None,
            ),
        ),
        (
            PaperSide.SELL,
            PaperBracketSpec(
                stop_loss_price=None,
                stop_loss_limit_price=None,
                stop_loss_trail_amount=None,
                take_profit_price=Decimal("80"),
                take_profit_limit_price=Decimal("79"),
            ),
        ),
    ],
)
def test_bracket_rejects_limit_prices_that_cannot_protect_after_trigger(
    side: PaperSide,
    bracket: PaperBracketSpec,
) -> None:
    """A protective limit must remain executable beyond its stop trigger."""
    state = submit_paper_order(
        _intent(side=side, bracket=bracket), submitted_event_index=1, config=_config()
    )
    result = evaluate_paper_order(state, _book(2), config=_config(), position_contracts=0)
    assert result.fill is None
    assert result.state.status is PaperOrderStatus.REJECTED
    assert result.reason == "BRACKET_INVALID_FOR_FILL"


def test_bracket_is_forbidden_on_reduce_only_or_trigger_parent() -> None:
    """Nested protection on reduction/trigger parents is outside the current Delta contract."""
    bracket = PaperBracketSpec(
        stop_loss_price=Decimal("90"),
        stop_loss_limit_price=None,
        stop_loss_trail_amount=None,
        take_profit_price=None,
        take_profit_limit_price=None,
    )
    with pytest.raises(PaperSimulationError):
        PaperOrderIntent(
            order_id=UUID(int=1),
            paper_account_id=UUID(int=2),
            mode=OmsExecutionMode.PAPER,
            product_id=27,
            side=PaperSide.BUY,
            contracts=1,
            kind=PaperOrderKind.MARKET,
            tick_size=Decimal("1"),
            limit_price=None,
            stop_price=None,
            trail_amount=None,
            trigger_direction=None,
            reduce_only=True,
            bracket=bracket,
        )
    with pytest.raises(PaperSimulationError):
        PaperOrderIntent(
            order_id=UUID(int=1),
            paper_account_id=UUID(int=2),
            mode=OmsExecutionMode.PAPER,
            product_id=27,
            side=PaperSide.BUY,
            contracts=1,
            kind=PaperOrderKind.STOP_MARKET,
            tick_size=Decimal("1"),
            limit_price=None,
            stop_price=Decimal("100"),
            trail_amount=None,
            trigger_direction=None,
            reduce_only=False,
            bracket=bracket,
        )


@pytest.mark.parametrize("side,position", [(PaperSide.BUY, -1), (PaperSide.SELL, 1)])
def test_bracket_entry_rejects_an_opposite_existing_position(
    side: PaperSide, position: int
) -> None:
    """Bracket fill quantity cannot safely represent a close-and-reverse entry."""
    bracket = PaperBracketSpec(Decimal("90"), None, None, Decimal("120"), None)
    state = create_paper_simulation(
        (PaperPosition(UUID(int=92), OmsExecutionMode.PAPER, 27, position),)
    )

    with pytest.raises(PaperSimulationError):
        submit_simulation_order(
            state,
            _intent(side=side, bracket=bracket),
            config=_config(),
            stream_id="paper-default",
            submitted_event_index=1,
        )


@pytest.mark.parametrize("contracts,expected_position", [(1, 1), (5, -3)])
def test_external_reduction_cancels_stale_bracket_children_and_runtime(
    contracts: int, expected_position: int
) -> None:
    """A changed position must not retain protection sized for the old exposure."""
    bracket = PaperBracketSpec(Decimal("90"), Decimal("89"), None, Decimal("120"), None)
    state = submit_simulation_order(
        create_paper_simulation(),
        _intent(order_id=72, contracts=3, bracket=bracket),
        config=_config(),
        stream_id="paper-default",
        submitted_event_index=1,
    )
    state = evaluate_simulation_event(state, _book(2, size=3)).state
    trigger = replace(
        _book(3),
        bid_price=Decimal("90"),
        ask_price=Decimal("90.5"),
        trigger_reference=Decimal("90"),
    )
    state = evaluate_simulation_event(state, trigger).state
    partial = replace(
        _book(4, size=1),
        bid_price=Decimal("89"),
        ask_price=Decimal("89.5"),
        trigger_reference=Decimal("89"),
    )
    state = evaluate_simulation_event(state, partial).state
    state = submit_simulation_order(
        state,
        _intent(order_id=73, side=PaperSide.SELL, contracts=contracts, bracket=None),
        config=_config(),
        stream_id="paper-default",
        submitted_event_index=4,
    )
    reduction = replace(
        _book(5, size=contracts),
        bid_price=Decimal("88"),
        ask_price=Decimal("88.5"),
        trigger_reference=Decimal("88"),
    )
    reduced = evaluate_simulation_event(state, reduction)

    assert (
        simulation_position(reduced.state, UUID(int=92), OmsExecutionMode.PAPER, 27)
        == expected_position
    )
    assert {item.action for item in reduced.children} == {PaperBracketAction.CANCEL}
    assert reduced.state.brackets == ()


def test_triggered_child_cancels_unfilled_parent_remainder() -> None:
    """An entry must not add exposure after its own protective child has triggered."""
    bracket = PaperBracketSpec(Decimal("90"), None, None, Decimal("120"), None)
    state = submit_simulation_order(
        create_paper_simulation(),
        _intent(order_id=74, contracts=5, bracket=bracket),
        config=_config(),
        stream_id="paper-default",
        submitted_event_index=1,
    )
    state = evaluate_simulation_event(state, _book(2, size=2)).state
    trigger = replace(
        _book(3, size=3),
        bid_price=Decimal("89"),
        ask_price=Decimal("91"),
        trigger_reference=Decimal("89"),
    )

    result = evaluate_simulation_event(state, trigger)
    parent = simulation_order(result.state, UUID(int=92), OmsExecutionMode.PAPER, UUID(int=74))

    assert result.fills == ()
    assert parent.state.status is PaperOrderStatus.CANCELLED
    assert parent.state.filled_contracts == 2


def test_external_reduction_also_cancels_partial_bracket_parent() -> None:
    """Cancelling stale children must not leave their entry remainder able to regrow risk."""
    bracket = PaperBracketSpec(Decimal("90"), None, None, Decimal("120"), None)
    state = submit_simulation_order(
        create_paper_simulation(),
        _intent(order_id=75, contracts=5, bracket=bracket),
        config=_config(),
        stream_id="paper-default",
        submitted_event_index=1,
    )
    state = evaluate_simulation_event(state, _book(2, size=2)).state
    state = submit_simulation_order(
        state,
        _intent(order_id=76, side=PaperSide.SELL, contracts=1, bracket=None),
        config=_config(),
        stream_id="paper-default",
        submitted_event_index=2,
    )
    reduction = replace(_book(3, size=1), ask_contracts=0)

    reduced = evaluate_simulation_event(state, reduction)
    parent = simulation_order(reduced.state, UUID(int=92), OmsExecutionMode.PAPER, UUID(int=75))

    assert parent.state.status is PaperOrderStatus.CANCELLED
    assert {item.action for item in reduced.children} == {PaperBracketAction.CANCEL}
    assert reduced.state.brackets == ()


def test_paper_modules_import_without_private_exchange_or_persistence_authority() -> None:
    """Importing the simulator must not load any route that can send or persist an order."""
    script = """
import json, sys
import hmt_crypto.oms.paper_brackets
import hmt_crypto.oms.paper_bracket_execution
import hmt_crypto.oms.paper_bracket_values
import hmt_crypto.oms.paper_identity
import hmt_crypto.oms.paper_portfolio
import hmt_crypto.oms.paper_portfolio_models
import hmt_crypto.oms.paper_state
import hmt_crypto.oms.paper_values
import hmt_crypto.oms.paper_models
import hmt_crypto.oms.paper_engine
forbidden = sorted(name for name in sys.modules if (
    name.startswith('hmt_crypto.exchange')
    or name.startswith('hmt_crypto.persistence')
    or name.startswith('httpx')
    or name.startswith('sqlalchemy')
))
print(json.dumps(forbidden))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == []


def test_paper_modules_expose_no_client_transport_or_write_callback() -> None:
    """The public surface must contain data/evaluation only, never an exchange write hook."""
    modules = (
        importlib.import_module("hmt_crypto.oms.paper_models"),
        importlib.import_module("hmt_crypto.oms.paper_values"),
        importlib.import_module("hmt_crypto.oms.paper_identity"),
        importlib.import_module("hmt_crypto.oms.paper_state"),
        importlib.import_module("hmt_crypto.oms.paper_brackets"),
        importlib.import_module("hmt_crypto.oms.paper_bracket_execution"),
        importlib.import_module("hmt_crypto.oms.paper_bracket_values"),
        importlib.import_module("hmt_crypto.oms.paper_engine"),
        importlib.import_module("hmt_crypto.oms.paper_portfolio"),
        importlib.import_module("hmt_crypto.oms.paper_portfolio_models"),
    )
    forbidden = {"client", "transport", "send", "request", "place_order", "callback"}
    for module in modules:
        assert forbidden.isdisjoint(vars(module))
