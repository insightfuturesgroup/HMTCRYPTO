"""Synchronous, closed OMS transitions with no side effects or authority."""

from __future__ import annotations

from hmt_crypto.oms.models import (
    CommandEvent,
    CommandState,
    CommandTransitionDecision,
    OmsExecutionMode,
    OmsTransitionError,
    OrderOrigin,
    OrderState,
    OrderTransitionDecision,
    OrderTransitionSource,
)

_MAX_I63 = 2**63 - 1

_COMMAND_STATES = tuple(CommandState)
_COMMAND_EVENTS = tuple(CommandEvent)
_ORDER_STATES = tuple(OrderState)
_ORDER_TRANSITION_SOURCES = tuple(OrderTransitionSource)
_EXECUTION_MODES = tuple(OmsExecutionMode)
_ORDER_ORIGINS = tuple(OrderOrigin)

_COMMAND_TRANSITIONS = {
    (CommandState.QUEUED, CommandEvent.CLAIM): CommandState.CLAIMED,
    (CommandState.CLAIMED, CommandEvent.RELEASE_UNSTARTED_CLAIM): CommandState.QUEUED,
    (CommandState.QUEUED, CommandEvent.CANCEL_BEFORE_DISPATCH): CommandState.CANCELLED,
    (CommandState.CLAIMED, CommandEvent.CANCEL_BEFORE_DISPATCH): CommandState.CANCELLED,
    (CommandState.CLAIMED, CommandEvent.BEGIN_SUBMISSION): CommandState.SUBMITTING,
    (CommandState.SUBMITTING, CommandEvent.DEFINITE_ACCEPTED): CommandState.COMPLETED,
    (CommandState.SUBMITTING, CommandEvent.DEFINITE_REJECTED): CommandState.FAILED,
    (CommandState.SUBMITTING, CommandEvent.DEFINITE_NOT_SENT): CommandState.FAILED,
    (CommandState.SUBMITTING, CommandEvent.OUTCOME_UNKNOWN): CommandState.UNCERTAIN,
    (CommandState.UNCERTAIN, CommandEvent.RECONCILED_ACCEPTED): CommandState.COMPLETED,
    (CommandState.UNCERTAIN, CommandEvent.RECONCILED_REJECTED): CommandState.FAILED,
    (CommandState.UNCERTAIN, CommandEvent.RECONCILED_NOT_SENT): CommandState.FAILED,
}

_ZERO_ATTEMPT_EVENTS = frozenset(
    (
        CommandEvent.CLAIM,
        CommandEvent.RELEASE_UNSTARTED_CLAIM,
        CommandEvent.CANCEL_BEFORE_DISPATCH,
    )
)

_ORDER_TRANSITIONS = frozenset(
    {
        (OrderState.PREPARED, OrderState.SUBMITTING),
        (OrderState.PREPARED, OrderState.CANCELLED),
        *(
            (OrderState.SUBMITTING, target)
            for target in (
                OrderState.OPEN,
                OrderState.PARTIALLY_FILLED,
                OrderState.FILLED,
                OrderState.CANCELLED,
                OrderState.REJECTED,
                OrderState.EXPIRED,
            )
        ),
        *(
            (OrderState.UNKNOWN, target)
            for target in (
                OrderState.OPEN,
                OrderState.PARTIALLY_FILLED,
                OrderState.FILLED,
                OrderState.CANCELLED,
                OrderState.REJECTED,
                OrderState.EXPIRED,
            )
        ),
        *(
            (OrderState.OPEN, target)
            for target in (
                OrderState.OPEN,
                OrderState.PARTIALLY_FILLED,
                OrderState.FILLED,
                OrderState.CANCELLED,
                OrderState.EXPIRED,
            )
        ),
        *(
            (OrderState.PARTIALLY_FILLED, target)
            for target in (
                OrderState.PARTIALLY_FILLED,
                OrderState.FILLED,
                OrderState.CANCELLED,
                OrderState.EXPIRED,
            )
        ),
        *((terminal, terminal) for terminal in OrderState if terminal.is_terminal),
    }
)


def _fail(operation: str) -> OmsTransitionError:
    del operation
    return OmsTransitionError()


def _is_canonical(value: object, members: tuple[object, ...]) -> bool:
    return any(value is member for member in members)


def decide_command_transition(
    current: CommandState,
    event: CommandEvent,
    *,
    attempt_count: int,
) -> CommandTransitionDecision:
    """Validate and classify one command transition without mutating state."""
    if (
        not _is_canonical(current, _COMMAND_STATES)
        or not _is_canonical(event, _COMMAND_EVENTS)
        or type(attempt_count) is not int
        or not 0 <= attempt_count <= _MAX_I63
    ):
        raise _fail("oms_command_transition") from None
    next_state = _COMMAND_TRANSITIONS.get((current, event))
    if next_state is None:
        raise _fail("oms_command_transition") from None
    if (event in _ZERO_ATTEMPT_EVENTS) != (attempt_count == 0):
        raise _fail("oms_command_transition") from None
    return CommandTransitionDecision(
        previous_state=current,
        event=event,
        next_state=next_state,
        attempt_count=attempt_count,
        is_terminal=next_state in CommandState.terminal_states(),
        is_uncertain=next_state is CommandState.UNCERTAIN,
    )


def _validate_order_provenance(
    *,
    current: OrderState,
    source: OrderTransitionSource,
    mode: OmsExecutionMode,
    origin: OrderOrigin,
    reduce_only: bool | None,
) -> None:
    if origin is OrderOrigin.ADOPTED:
        if (
            mode is not OmsExecutionMode.LIVE
            or reduce_only is not None
            or current in (OrderState.PREPARED, OrderState.SUBMITTING)
            or source not in (OrderTransitionSource.VENUE, OrderTransitionSource.RECONCILIATION)
        ):
            raise _fail("oms_order_transition") from None
        return
    if type(reduce_only) is not bool or current is OrderState.UNKNOWN:
        raise _fail("oms_order_transition") from None
    if origin is OrderOrigin.LOCAL_INTENT:
        if mode is not OmsExecutionMode.LIVE:
            raise _fail("oms_order_transition") from None
    elif origin is OrderOrigin.SIMULATOR_INTENT:
        if mode not in (OmsExecutionMode.PAPER, OmsExecutionMode.BACKTEST):
            raise _fail("oms_order_transition") from None
    else:
        raise _fail("oms_order_transition") from None
    if current is OrderState.PREPARED:
        if source is not OrderTransitionSource.LOCAL:
            raise _fail("oms_order_transition") from None
        return
    if source is OrderTransitionSource.LOCAL:
        raise _fail("oms_order_transition") from None
    if mode is OmsExecutionMode.LIVE:
        if source not in (OrderTransitionSource.VENUE, OrderTransitionSource.RECONCILIATION):
            raise _fail("oms_order_transition") from None
    elif source not in (OrderTransitionSource.SIMULATOR, OrderTransitionSource.RECONCILIATION):
        raise _fail("oms_order_transition") from None


def decide_order_transition(
    current: OrderState,
    target: OrderState,
    *,
    source: OrderTransitionSource,
    mode: OmsExecutionMode,
    origin: OrderOrigin,
    reduce_only: bool | None,
) -> OrderTransitionDecision:
    """Validate one order observation and expose any protection obligation."""
    if (
        not _is_canonical(current, _ORDER_STATES)
        or not _is_canonical(target, _ORDER_STATES)
        or not _is_canonical(source, _ORDER_TRANSITION_SOURCES)
        or not _is_canonical(mode, _EXECUTION_MODES)
        or not _is_canonical(origin, _ORDER_ORIGINS)
        or (reduce_only is not None and type(reduce_only) is not bool)
    ):
        raise _fail("oms_order_transition") from None
    _validate_order_provenance(
        current=current,
        source=source,
        mode=mode,
        origin=origin,
        reduce_only=reduce_only,
    )
    if (current, target) not in _ORDER_TRANSITIONS:
        raise _fail("oms_order_transition") from None
    requires_protection = (
        origin is not OrderOrigin.ADOPTED
        and reduce_only is False
        and target in (OrderState.PARTIALLY_FILLED, OrderState.FILLED)
    )
    return OrderTransitionDecision(
        previous_state=current,
        next_state=target,
        source=source,
        is_terminal=target.is_terminal,
        requires_protection_reconciliation=requires_protection,
    )


__all__ = ["decide_command_transition", "decide_order_transition"]
