"""Internal event allocation for state-owned protective bracket children."""

from __future__ import annotations

from dataclasses import dataclass, replace

from hmt_crypto.oms.paper_brackets import (
    PaperBracketLifecycle,
    advance_bracket_child,
)
from hmt_crypto.oms.paper_engine import _evaluate_allocated_order
from hmt_crypto.oms.paper_identity import revision_sha256
from hmt_crypto.oms.paper_models import (
    PaperBracketAction,
    PaperCandle,
    PaperChildOrderProposal,
    PaperFillProposal,
    PaperModelConfig,
    PaperOrderStatus,
    PaperSide,
    PaperSimulationError,
    PaperTopOfBook,
    PaperTriggerDirection,
)

_PositionKey = tuple[int, str, int]
_TERMINAL = (
    PaperOrderStatus.FILLED,
    PaperOrderStatus.CANCELLED,
    PaperOrderStatus.REJECTED,
)


@dataclass(frozen=True, slots=True)
class _BracketEventResult:
    brackets: tuple[PaperBracketLifecycle, ...]
    fills: tuple[PaperFillProposal, ...]
    proposals: tuple[PaperChildOrderProposal, ...]
    positions: dict[_PositionKey, int]
    bid_contracts: int
    ask_contracts: int
    candle_contracts: int
    next_ordinal: int


def _cancel_invalidated_brackets(
    brackets: list[PaperBracketLifecycle],
    account_key: _PositionKey,
) -> tuple[list[PaperBracketLifecycle], tuple[PaperChildOrderProposal, ...]]:
    kept: list[PaperBracketLifecycle] = []
    cancellations: list[PaperChildOrderProposal] = []
    for lifecycle in brackets:
        intent = lifecycle.child_states[0].intent
        child_key = (intent.paper_account_id.int, intent.mode.value, intent.product_id)
        if child_key != account_key:
            kept.append(lifecycle)
            continue
        cancellations.extend(
            replace(
                proposal,
                action=PaperBracketAction.CANCEL,
                replaces_revision_sha256=proposal.revision_sha256,
                revision_sha256=revision_sha256(proposal.child_id_sha256, 0),
            )
            for proposal in lifecycle.current_children
            if proposal.action is not PaperBracketAction.CANCEL
        )
    return kept, tuple(cancellations)


def _position_key(lifecycle: PaperBracketLifecycle, index: int) -> _PositionKey:
    intent = lifecycle.child_states[index].intent
    return (intent.paper_account_id.int, intent.mode.value, intent.product_id)


def _liquidity(
    event: PaperTopOfBook | PaperCandle,
    side: PaperSide,
    bid: int,
    ask: int,
    candle: int,
) -> int:
    if isinstance(event, PaperCandle):
        return candle
    return ask if side is PaperSide.BUY else bid


def _consume(
    event: PaperTopOfBook | PaperCandle,
    side: PaperSide,
    contracts: int,
    bid: int,
    ask: int,
    candle: int,
) -> tuple[int, int, int]:
    if isinstance(event, PaperCandle):
        return bid, ask, candle - contracts
    if side is PaperSide.BUY:
        return bid, ask - contracts, candle
    return bid - contracts, ask, candle


def _candle_has_ambiguous_oco(
    lifecycle: PaperBracketLifecycle,
    event: PaperCandle,
) -> bool:
    touched = 0
    for proposal, state in zip(lifecycle.current_children, lifecycle.child_states, strict=True):
        if state.status in _TERMINAL or state.triggered_event_index is not None:
            continue
        stop = proposal.stop_price
        if stop is None:
            continue
        touched += int(
            event.high_price >= stop
            if proposal.trigger_direction is PaperTriggerDirection.ABOVE
            else event.low_price <= stop
        )
    return touched > 1


def _evaluate_bracket_lifecycles(
    lifecycles: tuple[PaperBracketLifecycle, ...],
    event: PaperTopOfBook | PaperCandle,
    positions: dict[_PositionKey, int],
    configs: dict[str, PaperModelConfig],
    *,
    bid_contracts: int,
    ask_contracts: int,
    candle_contracts: int,
    first_ordinal: int,
) -> _BracketEventResult:
    """Advance active children before normal orders using the shared event budget."""
    bid, ask, candle = bid_contracts, ask_contracts, candle_contracts
    ordinal = first_ordinal
    owned_positions = dict(positions)
    updated: list[PaperBracketLifecycle] = []
    fills: list[PaperFillProposal] = []
    proposals: list[PaperChildOrderProposal] = []
    for lifecycle in sorted(lifecycles, key=lambda item: item.oco_group_sha256):
        current = lifecycle
        if (
            not current.child_states
            or current.child_states[0].intent.product_id != event.product_id
        ):
            updated.append(current)
            continue
        if isinstance(event, PaperCandle) and _candle_has_ambiguous_oco(current, event):
            raise PaperSimulationError() from None
        config = configs[current.oco_group_sha256]
        child_ids = [item.child_id_sha256 for item in current.current_children]
        for child_id in child_ids:
            pair = [
                (index, proposal, state)
                for index, (proposal, state) in enumerate(
                    zip(current.current_children, current.child_states, strict=True)
                )
                if proposal.child_id_sha256 == child_id
            ]
            if len(pair) != 1:
                raise PaperSimulationError() from None
            index, proposal, child_state = pair[0]
            if child_state.status in _TERMINAL or (
                current.executed_child_id_sha256 is not None
                and current.executed_child_id_sha256 != child_id
            ):
                continue
            key = _position_key(current, index)
            position = owned_positions.get(key, 0)
            reduction = -position if child_state.intent.side is PaperSide.BUY else position
            reservation = min(child_state.remaining_contracts, max(reduction, 0))
            if reservation == 0:
                continue
            available = _liquidity(event, child_state.intent.side, bid, ask, candle)
            evaluation = _evaluate_allocated_order(
                child_state,
                event,
                config=config,
                position_contracts=position,
                allocation_ordinal=ordinal,
                liquidity_before=available,
                reservation_before=reservation,
            )
            ordinal += 1
            fill = evaluation.fill
            advance = advance_bracket_child(
                current,
                proposal.child_id_sha256,
                evaluation.state,
                executed=fill is not None,
            )
            current = advance.lifecycle
            proposals.extend(advance.proposals)
            if fill is None:
                continue
            fills.append(fill)
            bid, ask, candle = _consume(
                event, child_state.intent.side, fill.contracts, bid, ask, candle
            )
            owned_positions[key] = (
                position + fill.contracts
                if child_state.intent.side is PaperSide.BUY
                else position - fill.contracts
            )
            break
        updated.append(current)
    return _BracketEventResult(
        tuple(updated),
        tuple(fills),
        tuple(proposals),
        owned_positions,
        bid,
        ask,
        candle,
        ordinal,
    )


__all__: list[str] = []
