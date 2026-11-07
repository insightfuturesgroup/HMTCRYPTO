"""Immutable account/product PAPER state with shared event liquidity."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from hmt_crypto.oms.models import OmsExecutionMode
from hmt_crypto.oms.paper_bracket_execution import (
    _cancel_invalidated_brackets,
    _evaluate_bracket_lifecycles,
)
from hmt_crypto.oms.paper_brackets import (
    reconcile_bracket_children,
)
from hmt_crypto.oms.paper_engine import _evaluate_allocated_order
from hmt_crypto.oms.paper_identity import oco_sha256
from hmt_crypto.oms.paper_models import (
    PaperCandle,
    PaperChildOrderProposal,
    PaperFillProposal,
    PaperModelConfig,
    PaperOrderIntent,
    PaperOrderStatus,
    PaperSide,
    PaperSimulationError,
    PaperTopOfBook,
)
from hmt_crypto.oms.paper_portfolio_models import (
    PaperPosition,
    PaperSimulatedOrder,
    PaperSimulationEvaluation,
    PaperSimulationState,
    PaperStreamCursor,
    _copy_simulation_state,
    _execution_priority,
    _intent_key,
    _order_key,
    _position_key,
    _priority,
)
from hmt_crypto.oms.paper_state import _copy_event, submit_paper_order
from hmt_crypto.oms.paper_values import (
    _copy_stream_id,
    _copy_utc,
    _copy_uuid,
    _exact_int,
)

_MODES = (OmsExecutionMode.PAPER, OmsExecutionMode.BACKTEST)
_TERMINAL = (
    PaperOrderStatus.FILLED,
    PaperOrderStatus.CANCELLED,
    PaperOrderStatus.REJECTED,
)


def _fail() -> PaperSimulationError:
    return PaperSimulationError()


def create_paper_simulation(
    positions: tuple[PaperPosition, ...] = (),
) -> PaperSimulationState:
    """Create a simulation with exact, unique initial position namespaces."""
    if type(positions) is not tuple or any(type(item) is not PaperPosition for item in positions):
        raise _fail() from None
    copied = tuple(
        PaperPosition(item.paper_account_id, item.mode, item.product_id, item.contracts)
        for item in positions
    )
    if len({_position_key(item) for item in copied}) != len(copied):
        raise _fail() from None
    return PaperSimulationState(tuple(sorted(copied, key=_position_key)), (), ())


def simulation_position(
    state: PaperSimulationState,
    paper_account_id: UUID,
    mode: OmsExecutionMode,
    product_id: int,
) -> int:
    """Return owned signed contracts, defaulting an absent namespace to flat."""
    state = _copy_simulation_state(state)
    account = _copy_uuid(paper_account_id)
    product = _exact_int(product_id, minimum=1)
    matches = [
        item
        for item in state.positions
        if item.paper_account_id == account and item.mode is mode and item.product_id == product
    ]
    if len(matches) > 1 or not any(mode is candidate for candidate in _MODES):
        raise _fail() from None
    return 0 if not matches else matches[0].contracts


def simulation_order(
    state: PaperSimulationState,
    paper_account_id: UUID,
    mode: OmsExecutionMode,
    order_id: UUID,
) -> PaperSimulatedOrder:
    """Return one exact account/mode-scoped simulated order."""
    state = _copy_simulation_state(state)
    account = _copy_uuid(paper_account_id)
    local_order_id = _copy_uuid(order_id)
    matches = [
        item
        for item in state.orders
        if item.state.intent.paper_account_id == account
        and item.state.intent.mode is mode
        and item.state.intent.order_id == local_order_id
    ]
    if len(matches) != 1:
        raise _fail() from None
    return matches[0]


def _reserved_for_side(
    state: PaperSimulationState,
    intent: PaperOrderIntent,
) -> int:
    return sum(
        item.reserved_contracts
        for item in state.orders
        if item.state.intent.reduce_only
        and _intent_key(item.state.intent) == _intent_key(intent)
        and item.state.intent.side is intent.side
        and item.state.status not in _TERMINAL
    )


def _reduction_capacity(state: PaperSimulationState, intent: PaperOrderIntent) -> int:
    position = simulation_position(state, intent.paper_account_id, intent.mode, intent.product_id)
    gross = -position if intent.side is PaperSide.BUY else position
    return max(0, gross - _reserved_for_side(state, intent))


def submit_simulation_order(
    state: PaperSimulationState,
    intent: PaperOrderIntent,
    *,
    config: PaperModelConfig,
    stream_id: str,
    submitted_event_index: int,
) -> PaperSimulationState:
    """Submit locally and atomically reserve any reduce-only capacity."""
    state = _copy_simulation_state(state)
    owned_stream = _copy_stream_id(stream_id)
    order_state = submit_paper_order(
        intent,
        submitted_event_index=submitted_event_index,
        config=config,
        stream_id=owned_stream,
    )
    intent = order_state.intent
    position = _position_contracts(state.positions, intent)
    if intent.bracket is not None and (
        intent.side is PaperSide.BUY
        and position < 0
        or intent.side is PaperSide.SELL
        and position > 0
    ):
        raise _fail() from None
    if any(
        item.state.intent.paper_account_id == intent.paper_account_id
        and item.state.intent.mode is intent.mode
        and item.state.intent.order_id == intent.order_id
        for item in state.orders
    ):
        raise _fail() from None
    cursors = [item for item in state.cursors if item.product_id == intent.product_id]
    if cursors and (
        len(cursors) != 1
        or cursors[0].stream_id != owned_stream
        or cursors[0].last_event_index != submitted_event_index
    ):
        raise _fail() from None
    peer_orders = [
        item
        for item in state.orders
        if item.state.intent.product_id == intent.product_id and item.state.status not in _TERMINAL
    ]
    peer_streams = {item.state.stream_id for item in peer_orders}
    if peer_streams and (
        peer_streams != {owned_stream}
        or any(item.state.last_event_index != submitted_event_index for item in peer_orders)
    ):
        raise _fail() from None
    reserved = 0
    if intent.reduce_only:
        reserved = min(intent.contracts, _reduction_capacity(state, intent))
        if reserved == 0:
            order_state = replace(order_state, status=PaperOrderStatus.CANCELLED)
    order = PaperSimulatedOrder(order_state, config, reserved)
    orders = tuple(sorted((*state.orders, order), key=_priority))
    return PaperSimulationState(state.positions, orders, state.cursors, state.brackets)


def _replace_position(
    positions: tuple[PaperPosition, ...],
    intent: PaperOrderIntent,
    contracts: int,
) -> tuple[PaperPosition, ...]:
    key = _intent_key(intent)
    kept = [item for item in positions if _position_key(item) != key]
    if contracts != 0:
        kept.append(
            PaperPosition(intent.paper_account_id, intent.mode, intent.product_id, contracts)
        )
    return tuple(sorted(kept, key=_position_key))


def _clip_reservations(
    positions: tuple[PaperPosition, ...],
    existing_orders: tuple[PaperSimulatedOrder, ...],
    account_key: tuple[int, str, int],
) -> tuple[PaperSimulatedOrder, ...]:
    matching_position = [item for item in positions if _position_key(item) == account_key]
    position = 0 if not matching_position else matching_position[0].contracts
    sell_capacity = max(position, 0)
    buy_capacity = max(-position, 0)
    orders: list[PaperSimulatedOrder] = []
    for order in sorted(existing_orders, key=_priority):
        intent = order.state.intent
        if (
            not intent.reduce_only
            or _intent_key(intent) != account_key
            or order.state.status in _TERMINAL
        ):
            orders.append(order)
            continue
        capacity = sell_capacity if intent.side is PaperSide.SELL else buy_capacity
        kept = min(order.reserved_contracts, capacity)
        if intent.side is PaperSide.SELL:
            sell_capacity -= kept
        else:
            buy_capacity -= kept
        next_state = order.state
        if kept == 0:
            next_state = replace(next_state, status=PaperOrderStatus.CANCELLED)
        orders.append(PaperSimulatedOrder(next_state, order.config, kept))
    return tuple(orders)


def _position_contracts(positions: tuple[PaperPosition, ...], intent: PaperOrderIntent) -> int:
    matches = [item.contracts for item in positions if _position_key(item) == _intent_key(intent)]
    if len(matches) > 1:
        raise _fail() from None
    return 0 if not matches else matches[0]


def _positions_from_map(values: dict[tuple[int, str, int], int]) -> tuple[PaperPosition, ...]:
    modes = {item.value: item for item in _MODES}
    if any(key[1] not in modes for key in values):
        raise _fail() from None
    return tuple(
        sorted(
            (
                PaperPosition(UUID(int=key[0]), modes[key[1]], key[2], contracts)
                for key, contracts in values.items()
                if contracts != 0
            ),
            key=_position_key,
        )
    )


def evaluate_simulation_event(
    state: PaperSimulationState,
    event: PaperTopOfBook | PaperCandle,
) -> PaperSimulationEvaluation:
    """Evaluate one event across all orders with one shared liquidity budget."""
    state = _copy_simulation_state(state)
    event = _copy_event(event)
    stream = _copy_stream_id(event.stream_id)
    observed = _copy_utc(event.observed_at)
    cursors = [item for item in state.cursors if item.product_id == event.product_id]
    if cursors and (
        len(cursors) != 1
        or cursors[0].stream_id != stream
        or event.event_index != cursors[0].last_event_index + 1
        or observed <= cursors[0].last_observed_at
    ):
        raise _fail() from None
    bid = event.bid_contracts if isinstance(event, PaperTopOfBook) else 0
    ask = event.ask_contracts if isinstance(event, PaperTopOfBook) else 0
    candle = event.volume_contracts if isinstance(event, PaperCandle) else 0
    fills: list[PaperFillProposal] = []
    children: list[PaperChildOrderProposal] = []
    positions = state.positions
    orders = list(sorted(state.orders, key=_priority))
    brackets = list(state.brackets)
    configs: dict[str, PaperModelConfig] = {}
    for lifecycle in brackets:
        child_intent = lifecycle.child_states[0].intent
        parents = [
            item
            for item in orders
            if item.state.intent.order_id == lifecycle.parent_order_id
            and item.state.intent.paper_account_id == child_intent.paper_account_id
            and item.state.intent.mode is child_intent.mode
        ]
        if len(parents) != 1:
            raise _fail() from None
        configs[lifecycle.oco_group_sha256] = parents[0].config
    bracket_result = _evaluate_bracket_lifecycles(
        tuple(brackets),
        event,
        {_position_key(item): item.contracts for item in positions},
        configs,
        bid_contracts=bid,
        ask_contracts=ask,
        candle_contracts=candle,
        first_ordinal=0,
    )
    brackets = list(bracket_result.brackets)
    fills.extend(bracket_result.fills)
    children.extend(bracket_result.proposals)
    positions = _positions_from_map(bracket_result.positions)
    bid, ask, candle = (
        bracket_result.bid_contracts,
        bracket_result.ask_contracts,
        bracket_result.candle_contracts,
    )
    executed_groups = {
        item.oco_group_sha256 for item in brackets if item.executed_child_id_sha256 is not None
    }
    blocked_parent_groups = executed_groups | {
        item.oco_group_sha256
        for item in brackets
        if any(child.status is PaperOrderStatus.TRIGGERED for child in item.child_states)
    }
    orders = [
        PaperSimulatedOrder(
            replace(item.state, status=PaperOrderStatus.CANCELLED),
            item.config,
            0,
        )
        if item.state.status not in _TERMINAL
        and item.state.intent.bracket is not None
        and oco_sha256(item.state.intent, item.state.config_sha256) in blocked_parent_groups
        else item
        for item in orders
    ]
    for fill in bracket_result.fills:
        orders = list(
            _clip_reservations(
                positions,
                tuple(orders),
                (fill.paper_account_id.int, fill.mode.value, fill.product_id),
            )
        )
    execution_keys = tuple(
        _order_key(item)
        for item in sorted(
            (
                item
                for item in orders
                if item.state.status not in _TERMINAL
                and item.state.intent.product_id == event.product_id
            ),
            key=_execution_priority,
        )
    )
    for allocation_index, execution_key in enumerate(execution_keys):
        matching_indices = [
            index for index, item in enumerate(orders) if _order_key(item) == execution_key
        ]
        if len(matching_indices) != 1:
            raise _fail() from None
        index = matching_indices[0]
        order = orders[index]
        intent = order.state.intent
        if order.state.status in _TERMINAL or intent.product_id != event.product_id:
            continue
        position = _position_contracts(positions, intent)
        liquidity = (
            candle
            if isinstance(event, PaperCandle)
            else ask
            if intent.side is PaperSide.BUY
            else bid
        )
        evaluation = _evaluate_allocated_order(
            order.state,
            event,
            config=order.config,
            position_contracts=position,
            allocation_ordinal=bracket_result.next_ordinal + allocation_index,
            liquidity_before=liquidity,
            reservation_before=order.reserved_contracts,
        )
        reserved = order.reserved_contracts
        if evaluation.fill is not None:
            fill = evaluation.fill
            fills.append(fill)
            if isinstance(event, PaperTopOfBook):
                if intent.side is PaperSide.BUY:
                    ask -= fill.contracts
                else:
                    bid -= fill.contracts
            else:
                candle -= fill.contracts
            next_position = (
                position + fill.contracts
                if intent.side is PaperSide.BUY
                else position - fill.contracts
            )
            positions = _replace_position(positions, intent, next_position)
            if intent.reduce_only:
                reserved -= fill.contracts
        updated = PaperSimulatedOrder(evaluation.state, order.config, max(0, reserved))
        orders[index] = updated
        if evaluation.fill is not None and intent.bracket is not None:
            group = oco_sha256(intent, evaluation.state.config_sha256)
            matching_brackets = [item for item in brackets if item.oco_group_sha256 == group]
            if len(matching_brackets) > 1:
                raise _fail() from None
            update = reconcile_bracket_children(
                evaluation.state,
                config=order.config,
                fill_price=evaluation.fill.price,
                target_contracts=evaluation.state.filled_contracts,
                lifecycle=None if not matching_brackets else matching_brackets[0],
            )
            if update is None:
                raise _fail() from None
            brackets = [item for item in brackets if item.oco_group_sha256 != group]
            brackets.append(update.lifecycle)
            children.extend(update.proposals)
        if evaluation.fill is not None:
            orders = list(
                _clip_reservations(
                    positions,
                    tuple(orders),
                    _intent_key(intent),
                )
            )
            bracket_invalidated = (
                intent.bracket is None
                and position != 0
                and (
                    next_position == 0
                    or (position > 0) is not (next_position > 0)
                    or abs(next_position) < abs(position)
                )
            )
            if bracket_invalidated:
                account_key = _intent_key(intent)
                invalidated_parent_ids = {
                    lifecycle.parent_order_id
                    for lifecycle in brackets
                    if _intent_key(lifecycle.child_states[0].intent) == account_key
                }
                brackets, cancelled = _cancel_invalidated_brackets(brackets, account_key)
                children.extend(cancelled)
                orders = [
                    PaperSimulatedOrder(
                        replace(item.state, status=PaperOrderStatus.CANCELLED), item.config, 0
                    )
                    if item.state.status not in _TERMINAL
                    and item.state.intent.bracket is not None
                    and item.state.intent.order_id in invalidated_parent_ids
                    and _intent_key(item.state.intent) == account_key
                    else item
                    for item in orders
                ]
    next_cursor = PaperStreamCursor(event.product_id, stream, event.event_index, observed)
    other_cursors = [item for item in state.cursors if item.product_id != event.product_id]
    next_state = PaperSimulationState(
        positions,
        tuple(orders),
        tuple(sorted((*other_cursors, next_cursor), key=lambda item: item.product_id)),
        tuple(brackets),
    )
    return PaperSimulationEvaluation(next_state, tuple(fills), tuple(children))


__all__ = [
    "PaperPosition",
    "PaperSimulatedOrder",
    "PaperSimulationEvaluation",
    "PaperSimulationState",
    "create_paper_simulation",
    "evaluate_simulation_event",
    "simulation_order",
    "simulation_position",
    "submit_simulation_order",
]
