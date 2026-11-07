"""Canonical account/product state for the pure paper portfolio."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from hmt_crypto.oms.models import OmsExecutionMode
from hmt_crypto.oms.paper_bracket_values import _same_decimal
from hmt_crypto.oms.paper_brackets import PaperBracketLifecycle, _child_specs
from hmt_crypto.oms.paper_identity import config_sha256, oco_sha256
from hmt_crypto.oms.paper_models import (
    PaperChildOrderProposal,
    PaperFillProposal,
    PaperModelConfig,
    PaperOrderIntent,
    PaperOrderKind,
    PaperOrderState,
    PaperOrderStatus,
    PaperSide,
    PaperSimulationError,
)
from hmt_crypto.oms.paper_state import _copy_config, _copy_state
from hmt_crypto.oms.paper_values import _copy_stream_id, _copy_utc, _copy_uuid, _exact_int

_MAX_POSITION = 2**63 - 1
_MODES = (OmsExecutionMode.PAPER, OmsExecutionMode.BACKTEST)
_TERMINAL = (
    PaperOrderStatus.FILLED,
    PaperOrderStatus.CANCELLED,
    PaperOrderStatus.REJECTED,
)


def _fail() -> PaperSimulationError:
    return PaperSimulationError()


@dataclass(frozen=True, slots=True)
class PaperPosition:
    """Owned signed contracts for one account/mode/product namespace."""

    paper_account_id: UUID
    mode: OmsExecutionMode
    product_id: int
    contracts: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "paper_account_id", _copy_uuid(self.paper_account_id))
        if not any(self.mode is mode for mode in _MODES):
            raise _fail() from None
        object.__setattr__(self, "product_id", _exact_int(self.product_id, minimum=1))
        object.__setattr__(
            self,
            "contracts",
            _exact_int(self.contracts, minimum=-_MAX_POSITION, maximum=_MAX_POSITION),
        )


@dataclass(frozen=True, slots=True)
class PaperSimulatedOrder:
    """One working order plus its immutable config and reduction reservation."""

    state: PaperOrderState
    config: PaperModelConfig
    reserved_contracts: int

    def __post_init__(self) -> None:
        state = _copy_state(self.state)
        config = _copy_config(self.config)
        reserved = _exact_int(self.reserved_contracts)
        if (
            config_sha256(config) != state.config_sha256
            or reserved > state.remaining_contracts
            or (not state.intent.reduce_only and reserved != 0)
            or (state.intent.reduce_only and state.status not in _TERMINAL and reserved == 0)
            or (state.status in _TERMINAL and reserved != 0)
        ):
            raise _fail() from None
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "config", config)
        object.__setattr__(self, "reserved_contracts", reserved)


@dataclass(frozen=True, slots=True)
class PaperStreamCursor:
    """Last accepted event for one bound product stream."""

    product_id: int
    stream_id: str
    last_event_index: int
    last_observed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "product_id", _exact_int(self.product_id, minimum=1))
        object.__setattr__(self, "stream_id", _copy_stream_id(self.stream_id))
        object.__setattr__(self, "last_event_index", _exact_int(self.last_event_index, minimum=1))
        object.__setattr__(self, "last_observed_at", _copy_utc(self.last_observed_at))


@dataclass(frozen=True, slots=True)
class PaperSimulationState:
    """Complete inert state needed to evaluate all accounts deterministically."""

    positions: tuple[PaperPosition, ...]
    orders: tuple[PaperSimulatedOrder, ...]
    cursors: tuple[PaperStreamCursor, ...]
    brackets: tuple[PaperBracketLifecycle, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.positions) is not tuple
            or type(self.orders) is not tuple
            or type(self.cursors) is not tuple
            or type(self.brackets) is not tuple
            or any(type(item) is not PaperPosition for item in self.positions)
            or any(type(item) is not PaperSimulatedOrder for item in self.orders)
            or any(type(item) is not PaperStreamCursor for item in self.cursors)
            or any(type(item) is not PaperBracketLifecycle for item in self.brackets)
        ):
            raise _fail() from None
        positions, orders, cursors, brackets = _canonical_parts(self)
        if (
            len({_position_key(item) for item in positions}) != len(positions)
            or len({_order_key(item) for item in orders}) != len(orders)
            or len({item.product_id for item in cursors}) != len(cursors)
        ):
            raise _fail() from None
        _validate_reservations(positions, orders)
        _validate_brackets(positions, orders, brackets)
        object.__setattr__(self, "positions", positions)
        object.__setattr__(self, "orders", orders)
        object.__setattr__(self, "cursors", cursors)
        object.__setattr__(self, "brackets", brackets)


@dataclass(frozen=True, slots=True)
class PaperSimulationEvaluation:
    """Next simulation state and event-wide proposed fills."""

    state: PaperSimulationState
    fills: tuple[PaperFillProposal, ...]
    children: tuple[PaperChildOrderProposal, ...]


def _copy_simulation_state(value: object) -> PaperSimulationState:
    if type(value) is not PaperSimulationState:
        raise _fail() from None
    try:
        return PaperSimulationState(
            object.__getattribute__(value, "positions"),
            object.__getattribute__(value, "orders"),
            object.__getattribute__(value, "cursors"),
            object.__getattribute__(value, "brackets"),
        )
    except PaperSimulationError:
        raise
    except Exception:
        raise _fail() from None


def _position_key(position: PaperPosition) -> tuple[int, str, int]:
    return (position.paper_account_id.int, position.mode.value, position.product_id)


def _intent_key(intent: PaperOrderIntent) -> tuple[int, str, int]:
    return (intent.paper_account_id.int, intent.mode.value, intent.product_id)


def _order_key(order: PaperSimulatedOrder) -> tuple[int, str, int]:
    intent = order.state.intent
    return (intent.paper_account_id.int, intent.mode.value, intent.order_id.int)


def _priority(order: PaperSimulatedOrder) -> tuple[int, int, int, str]:
    intent = order.state.intent
    return (
        order.state.submitted_event_index,
        intent.order_id.int,
        intent.paper_account_id.int,
        intent.mode.value,
    )


def _execution_priority(
    order: PaperSimulatedOrder,
) -> tuple[int, Decimal, int, int, int, str]:
    intent = order.state.intent
    is_triggered = order.state.status is PaperOrderStatus.TRIGGERED
    if (
        intent.kind is PaperOrderKind.MARKET
        or is_triggered
        and intent.kind
        in (
            PaperOrderKind.STOP_MARKET,
            PaperOrderKind.TRAILING_STOP,
        )
    ):
        order_class, price = 0, Decimal(0)
    elif (
        intent.kind is PaperOrderKind.LIMIT
        or is_triggered
        and intent.kind is PaperOrderKind.STOP_LIMIT
    ):
        if intent.limit_price is None:
            raise _fail() from None
        order_class = 1
        price = (
            intent.limit_price.copy_negate() if intent.side is PaperSide.BUY else intent.limit_price
        )
    else:
        order_class, price = 2, Decimal(0)
    return (
        order_class,
        price,
        order.state.submitted_event_index,
        intent.order_id.int,
        intent.paper_account_id.int,
        intent.mode.value,
    )


def _canonical_parts(
    state: PaperSimulationState,
) -> tuple[
    tuple[PaperPosition, ...],
    tuple[PaperSimulatedOrder, ...],
    tuple[PaperStreamCursor, ...],
    tuple[PaperBracketLifecycle, ...],
]:
    try:
        positions = tuple(
            sorted(
                (
                    PaperPosition(item.paper_account_id, item.mode, item.product_id, item.contracts)
                    for item in state.positions
                ),
                key=_position_key,
            )
        )
        orders = tuple(
            sorted(
                (
                    PaperSimulatedOrder(item.state, item.config, item.reserved_contracts)
                    for item in state.orders
                ),
                key=_priority,
            )
        )
        cursors = tuple(
            sorted(
                (
                    PaperStreamCursor(
                        item.product_id,
                        item.stream_id,
                        item.last_event_index,
                        item.last_observed_at,
                    )
                    for item in state.cursors
                ),
                key=lambda item: item.product_id,
            )
        )
        brackets = tuple(
            sorted(
                (
                    PaperBracketLifecycle(
                        item.parent_order_id,
                        item.oco_group_sha256,
                        item.current_children,
                        item.executed_child_id_sha256,
                        item.child_states,
                    )
                    for item in state.brackets
                ),
                key=lambda item: item.oco_group_sha256,
            )
        )
    except PaperSimulationError:
        raise
    except Exception:
        raise _fail() from None
    return positions, orders, cursors, brackets


def _validate_reservations(
    positions: tuple[PaperPosition, ...], orders: tuple[PaperSimulatedOrder, ...]
) -> None:
    keys = {_position_key(item) for item in positions} | {
        _intent_key(item.state.intent)
        for item in orders
        if item.state.intent.reduce_only and item.state.status not in _TERMINAL
    }
    position_map = {_position_key(item): item.contracts for item in positions}
    for key in keys:
        position = position_map.get(key, 0)
        sell_reserved = sum(
            item.reserved_contracts
            for item in orders
            if item.state.status not in _TERMINAL
            and item.state.intent.reduce_only
            and _intent_key(item.state.intent) == key
            and item.state.intent.side is PaperSide.SELL
        )
        buy_reserved = sum(
            item.reserved_contracts
            for item in orders
            if item.state.status not in _TERMINAL
            and item.state.intent.reduce_only
            and _intent_key(item.state.intent) == key
            and item.state.intent.side is PaperSide.BUY
        )
        if sell_reserved > max(position, 0) or buy_reserved > max(-position, 0):
            raise _fail() from None


def _validate_brackets(
    positions: tuple[PaperPosition, ...],
    orders: tuple[PaperSimulatedOrder, ...],
    brackets: tuple[PaperBracketLifecycle, ...],
) -> None:
    if len({item.oco_group_sha256 for item in brackets}) != len(brackets):
        raise _fail() from None
    position_map = {_position_key(item): item.contracts for item in positions}
    active_coverage: dict[tuple[int, str, int], int] = {}
    for bracket in brackets:
        child = bracket.child_states[0]
        active_states = [state for state in bracket.child_states if state.status not in _TERMINAL]
        child_key = _intent_key(child.intent)
        position = position_map.get(child_key, 0)
        if active_states:
            active_coverage[child_key] = active_coverage.get(child_key, 0) + max(
                state.remaining_contracts for state in active_states
            )
        parents = [
            item
            for item in orders
            if item.state.intent.order_id == bracket.parent_order_id
            and item.state.intent.paper_account_id == child.intent.paper_account_id
            and item.state.intent.mode is child.intent.mode
        ]
        if (
            len(parents) != 1
            or parents[0].state.intent.bracket is None
            or oco_sha256(parents[0].state.intent, parents[0].state.config_sha256)
            != bracket.oco_group_sha256
            or active_states
            and (
                child.intent.side is PaperSide.SELL
                and position <= 0
                or child.intent.side is PaperSide.BUY
                and position >= 0
            )
            or any(
                proposal.contracts != parents[0].state.filled_contracts
                for proposal in bracket.current_children
            )
            or any(
                state.intent.paper_account_id != child.intent.paper_account_id
                or state.intent.mode is not child.intent.mode
                or state.intent.product_id != child.intent.product_id
                or state.stream_id != parents[0].state.stream_id
                or state.config_sha256 != parents[0].state.config_sha256
                for state in bracket.child_states
            )
        ):
            raise _fail() from None
        parent = parents[0].state
        expected = {
            role: (kind, stop, limit, trail, direction)
            for role, kind, stop, limit, trail, direction in _child_specs(parent)
        }
        child_side = PaperSide.SELL if parent.intent.side is PaperSide.BUY else PaperSide.BUY
        if len(expected) != len(bracket.current_children) or any(
            proposal.role not in expected
            or proposal.parent_order_id != parent.intent.order_id
            or proposal.product_id != parent.intent.product_id
            or proposal.side is not child_side
            or proposal.kind is not expected[proposal.role][0]
            or not _same_decimal(proposal.stop_price, expected[proposal.role][1])
            or not _same_decimal(proposal.limit_price, expected[proposal.role][2])
            or not _same_decimal(proposal.trail_amount, expected[proposal.role][3])
            or proposal.trigger_direction is not expected[proposal.role][4]
            for proposal in bracket.current_children
        ):
            raise _fail() from None
    if any(coverage > abs(position_map.get(key, 0)) for key, coverage in active_coverage.items()):
        raise _fail() from None


__all__ = [
    "PaperPosition",
    "PaperSimulatedOrder",
    "PaperSimulationEvaluation",
    "PaperSimulationState",
]
