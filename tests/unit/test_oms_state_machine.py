"""Pure OMS transition behavior without persistence or exchange authority."""

from __future__ import annotations

import dataclasses
import importlib
import json
import subprocess
import sys
from itertools import product

import pytest

from hmt_crypto.oms.models import (
    CommandEvent,
    CommandState,
    OmsExecutionMode,
    OmsTransitionError,
    OrderOrigin,
    OrderState,
    OrderTransitionDecision,
    OrderTransitionSource,
)
from hmt_crypto.oms.state_machine import decide_command_transition, decide_order_transition

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
_COMMAND_TERMINALS = {
    CommandState.COMPLETED,
    CommandState.FAILED,
    CommandState.CANCELLED,
}
_ORDER_TERMINALS = {
    OrderState.FILLED,
    OrderState.CANCELLED,
    OrderState.REJECTED,
    OrderState.EXPIRED,
}

_ORDER_TRANSITIONS = {
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
    *((terminal, terminal) for terminal in _ORDER_TERMINALS),
}


def _command_attempt_count(event: CommandEvent) -> int:
    if event in {
        CommandEvent.BEGIN_SUBMISSION,
        CommandEvent.DEFINITE_ACCEPTED,
        CommandEvent.DEFINITE_REJECTED,
        CommandEvent.DEFINITE_NOT_SENT,
        CommandEvent.OUTCOME_UNKNOWN,
        CommandEvent.RECONCILED_ACCEPTED,
        CommandEvent.RECONCILED_REJECTED,
        CommandEvent.RECONCILED_NOT_SENT,
    }:
        return 1
    return 0


def test_command_transition_table_is_closed_and_exhaustive() -> None:
    """A missing or extra command edge must fail this complete matrix."""
    for state, event in product(CommandState, CommandEvent):
        expected = _COMMAND_TRANSITIONS.get((state, event))
        attempt_count = _command_attempt_count(event)
        if expected is None:
            with pytest.raises(OmsTransitionError):
                decide_command_transition(state, event, attempt_count=attempt_count)
            continue
        decision = decide_command_transition(state, event, attempt_count=attempt_count)
        assert decision.previous_state is state
        assert decision.event is event
        assert decision.next_state is expected
        assert decision.attempt_count == attempt_count
        assert decision.is_terminal is (expected in _COMMAND_TERMINALS)
        assert decision.is_uncertain is (expected is CommandState.UNCERTAIN)


@pytest.mark.parametrize(
    "state,event,attempt_count",
    [
        (CommandState.QUEUED, CommandEvent.CLAIM, 1),
        (CommandState.QUEUED, CommandEvent.CANCEL_BEFORE_DISPATCH, 1),
        (CommandState.CLAIMED, CommandEvent.RELEASE_UNSTARTED_CLAIM, 1),
        (CommandState.CLAIMED, CommandEvent.CANCEL_BEFORE_DISPATCH, 1),
        (CommandState.CLAIMED, CommandEvent.BEGIN_SUBMISSION, 0),
        (CommandState.SUBMITTING, CommandEvent.OUTCOME_UNKNOWN, 0),
        (CommandState.UNCERTAIN, CommandEvent.RECONCILED_ACCEPTED, 0),
    ],
)
def test_command_attempt_count_prevents_requeue_or_dispatch_without_evidence(
    state: CommandState,
    event: CommandEvent,
    attempt_count: int,
) -> None:
    """Wrong attempt cardinality must not release or advance a command."""
    with pytest.raises(OmsTransitionError):
        decide_command_transition(state, event, attempt_count=attempt_count)


def test_uncertain_and_terminal_commands_cannot_be_retried_or_cancelled() -> None:
    """Ambiguity must resolve through reconciliation, never another submission edge."""
    forbidden = {
        CommandEvent.CLAIM,
        CommandEvent.RELEASE_UNSTARTED_CLAIM,
        CommandEvent.CANCEL_BEFORE_DISPATCH,
        CommandEvent.BEGIN_SUBMISSION,
        CommandEvent.DEFINITE_ACCEPTED,
        CommandEvent.DEFINITE_REJECTED,
        CommandEvent.DEFINITE_NOT_SENT,
        CommandEvent.OUTCOME_UNKNOWN,
    }
    for event in forbidden:
        with pytest.raises(OmsTransitionError):
            decide_command_transition(CommandState.UNCERTAIN, event, attempt_count=1)
    for terminal in _COMMAND_TERMINALS:
        for event in CommandEvent:
            with pytest.raises(OmsTransitionError):
                decide_command_transition(terminal, event, attempt_count=1)


def test_command_decision_is_frozen_and_deterministic_for_exact_replay() -> None:
    """Repeating an exact request must yield identical immutable evidence."""
    first = decide_command_transition(
        CommandState.SUBMITTING,
        CommandEvent.DEFINITE_ACCEPTED,
        attempt_count=2,
    )
    second = decide_command_transition(
        CommandState.SUBMITTING,
        CommandEvent.DEFINITE_ACCEPTED,
        attempt_count=2,
    )
    assert first == second
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.next_state = CommandState.FAILED


def _valid_source(
    current: OrderState,
    *,
    mode: OmsExecutionMode,
) -> OrderTransitionSource:
    if current is OrderState.PREPARED:
        return OrderTransitionSource.LOCAL
    if current is OrderState.UNKNOWN or mode is OmsExecutionMode.LIVE:
        return OrderTransitionSource.VENUE
    return OrderTransitionSource.SIMULATOR


@pytest.mark.parametrize(
    "mode,origin",
    [
        (OmsExecutionMode.LIVE, OrderOrigin.LOCAL_INTENT),
        (OmsExecutionMode.PAPER, OrderOrigin.SIMULATOR_INTENT),
        (OmsExecutionMode.BACKTEST, OrderOrigin.SIMULATOR_INTENT),
    ],
)
def test_order_transition_table_is_closed_for_managed_intents(
    mode: OmsExecutionMode,
    origin: OrderOrigin,
) -> None:
    """Every managed-order state pair must match the reviewed graph exactly."""
    for current, target in product(OrderState, repeat=2):
        source = _valid_source(current, mode=mode)
        if current is OrderState.UNKNOWN or (current, target) not in _ORDER_TRANSITIONS:
            with pytest.raises(OmsTransitionError):
                decide_order_transition(
                    current,
                    target,
                    source=source,
                    mode=mode,
                    origin=origin,
                    reduce_only=False,
                )
            continue
        decision = decide_order_transition(
            current,
            target,
            source=source,
            mode=mode,
            origin=origin,
            reduce_only=False,
        )
        assert decision.previous_state is current
        assert decision.next_state is target
        assert decision.source is source
        assert decision.is_terminal is (target in _ORDER_TERMINALS)


def test_adopted_order_starts_unknown_and_accepts_only_venue_truth() -> None:
    """An external order must not acquire fabricated local intent provenance."""
    for target in OrderState:
        allowed = (OrderState.UNKNOWN, target) in _ORDER_TRANSITIONS

        def operation(target: OrderState = target) -> OrderTransitionDecision:
            return decide_order_transition(
                OrderState.UNKNOWN,
                target,
                source=OrderTransitionSource.VENUE,
                mode=OmsExecutionMode.LIVE,
                origin=OrderOrigin.ADOPTED,
                reduce_only=None,
            )

        if allowed:
            assert operation().next_state is target
        else:
            with pytest.raises(OmsTransitionError):
                operation()
    with pytest.raises(OmsTransitionError):
        decide_order_transition(
            OrderState.UNKNOWN,
            OrderState.OPEN,
            source=OrderTransitionSource.LOCAL,
            mode=OmsExecutionMode.LIVE,
            origin=OrderOrigin.ADOPTED,
            reduce_only=None,
        )


@pytest.mark.parametrize(
    "mode,origin,source",
    [
        (OmsExecutionMode.LIVE, OrderOrigin.SIMULATOR_INTENT, OrderTransitionSource.VENUE),
        (OmsExecutionMode.PAPER, OrderOrigin.LOCAL_INTENT, OrderTransitionSource.SIMULATOR),
        (OmsExecutionMode.BACKTEST, OrderOrigin.ADOPTED, OrderTransitionSource.RECONCILIATION),
        (OmsExecutionMode.PAPER, OrderOrigin.SIMULATOR_INTENT, OrderTransitionSource.VENUE),
        (OmsExecutionMode.LIVE, OrderOrigin.LOCAL_INTENT, OrderTransitionSource.SIMULATOR),
    ],
)
def test_order_mode_origin_and_source_cannot_cross_authority_boundaries(
    mode: OmsExecutionMode,
    origin: OrderOrigin,
    source: OrderTransitionSource,
) -> None:
    """Venue, simulator, and adopted evidence must remain mode-scoped."""
    with pytest.raises(OmsTransitionError):
        decide_order_transition(
            OrderState.SUBMITTING,
            OrderState.OPEN,
            source=source,
            mode=mode,
            origin=origin,
            reduce_only=None if origin is OrderOrigin.ADOPTED else False,
        )


def test_same_state_replay_requires_observation_or_reconciliation() -> None:
    """A repeated projection may add evidence but cannot masquerade as a local action."""
    for source in (OrderTransitionSource.VENUE, OrderTransitionSource.RECONCILIATION):
        result = decide_order_transition(
            OrderState.OPEN,
            OrderState.OPEN,
            source=source,
            mode=OmsExecutionMode.LIVE,
            origin=OrderOrigin.LOCAL_INTENT,
            reduce_only=False,
        )
        assert result.next_state is OrderState.OPEN
    with pytest.raises(OmsTransitionError):
        decide_order_transition(
            OrderState.OPEN,
            OrderState.OPEN,
            source=OrderTransitionSource.LOCAL,
            mode=OmsExecutionMode.LIVE,
            origin=OrderOrigin.LOCAL_INTENT,
            reduce_only=False,
        )


@pytest.mark.parametrize("target", [OrderState.PARTIALLY_FILLED, OrderState.FILLED])
def test_entry_fill_always_creates_a_protection_reconciliation_obligation(
    target: OrderState,
) -> None:
    """A non-reduce-only managed fill must never look fully handled by state alone."""
    for reduce_only, expected in ((False, True), (True, False)):
        decision = decide_order_transition(
            OrderState.SUBMITTING,
            target,
            source=OrderTransitionSource.VENUE,
            mode=OmsExecutionMode.LIVE,
            origin=OrderOrigin.LOCAL_INTENT,
            reduce_only=reduce_only,
        )
        assert decision.requires_protection_reconciliation is expected


def test_hostile_transition_inputs_fail_without_running_callbacks_or_leaking_values() -> None:
    """Outer exact-type checks must precede equality, hash, repr, and attributes."""
    calls: list[str] = []

    class Hostile:
        def __getattribute__(self, name: str) -> object:
            calls.append(f"get:{name}")
            raise AssertionError("transition-secret-canary")

        def __eq__(self, other: object) -> bool:
            calls.append("eq")
            raise AssertionError("transition-secret-canary")

        def __hash__(self) -> int:
            calls.append("hash")
            raise AssertionError("transition-secret-canary")

        def __repr__(self) -> str:
            calls.append("repr")
            raise AssertionError("transition-secret-canary")

    hostile = Hostile()
    operations = (
        lambda: decide_command_transition(hostile, CommandEvent.CLAIM, attempt_count=0),
        lambda: decide_command_transition(CommandState.QUEUED, hostile, attempt_count=0),
        lambda: decide_command_transition(
            CommandState.QUEUED, CommandEvent.CLAIM, attempt_count=hostile
        ),
        lambda: decide_order_transition(
            hostile,
            OrderState.OPEN,
            source=OrderTransitionSource.VENUE,
            mode=OmsExecutionMode.LIVE,
            origin=OrderOrigin.LOCAL_INTENT,
            reduce_only=False,
        ),
        lambda: decide_order_transition(
            OrderState.SUBMITTING,
            hostile,
            source=OrderTransitionSource.VENUE,
            mode=OmsExecutionMode.LIVE,
            origin=OrderOrigin.LOCAL_INTENT,
            reduce_only=False,
        ),
        lambda: decide_order_transition(
            OrderState.SUBMITTING,
            OrderState.OPEN,
            source=hostile,
            mode=OmsExecutionMode.LIVE,
            origin=OrderOrigin.LOCAL_INTENT,
            reduce_only=False,
        ),
        lambda: decide_order_transition(
            OrderState.SUBMITTING,
            OrderState.OPEN,
            source=OrderTransitionSource.VENUE,
            mode=hostile,
            origin=OrderOrigin.LOCAL_INTENT,
            reduce_only=False,
        ),
        lambda: decide_order_transition(
            OrderState.SUBMITTING,
            OrderState.OPEN,
            source=OrderTransitionSource.VENUE,
            mode=OmsExecutionMode.LIVE,
            origin=hostile,
            reduce_only=False,
        ),
        lambda: decide_order_transition(
            OrderState.SUBMITTING,
            OrderState.OPEN,
            source=OrderTransitionSource.VENUE,
            mode=OmsExecutionMode.LIVE,
            origin=OrderOrigin.LOCAL_INTENT,
            reduce_only=hostile,
        ),
    )
    for operation in operations:
        with pytest.raises(OmsTransitionError) as captured:
            operation()
        assert "transition-secret-canary" not in repr(captured.value)
        assert "transition-secret-canary" not in str(captured.value)
    assert calls == []


def test_reconstructed_enum_objects_fail_before_equality_or_hash_in_every_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact-type StrEnum reconstructions must not alias canonical transition members."""
    calls: list[str] = []

    def forbidden_hash(value: object) -> int:
        del value
        calls.append("hash")
        raise AssertionError("reconstructed-enum-callback")

    def forbidden_eq(left: object, right: object) -> bool:
        del left, right
        calls.append("eq")
        raise AssertionError("reconstructed-enum-callback")

    cases = (
        (
            CommandState,
            "QUEUED",
            lambda value: decide_command_transition(value, CommandEvent.CLAIM, attempt_count=0),
        ),
        (
            CommandEvent,
            "CLAIM",
            lambda value: decide_command_transition(CommandState.QUEUED, value, attempt_count=0),
        ),
        (
            OrderState,
            "SUBMITTING",
            lambda value: decide_order_transition(
                value,
                OrderState.OPEN,
                source=OrderTransitionSource.VENUE,
                mode=OmsExecutionMode.LIVE,
                origin=OrderOrigin.LOCAL_INTENT,
                reduce_only=False,
            ),
        ),
        (
            OrderState,
            "OPEN",
            lambda value: decide_order_transition(
                OrderState.SUBMITTING,
                value,
                source=OrderTransitionSource.VENUE,
                mode=OmsExecutionMode.LIVE,
                origin=OrderOrigin.LOCAL_INTENT,
                reduce_only=False,
            ),
        ),
        (
            OrderTransitionSource,
            "VENUE",
            lambda value: decide_order_transition(
                OrderState.SUBMITTING,
                OrderState.OPEN,
                source=value,
                mode=OmsExecutionMode.LIVE,
                origin=OrderOrigin.LOCAL_INTENT,
                reduce_only=False,
            ),
        ),
        (
            OmsExecutionMode,
            "LIVE",
            lambda value: decide_order_transition(
                OrderState.SUBMITTING,
                OrderState.OPEN,
                source=OrderTransitionSource.VENUE,
                mode=value,
                origin=OrderOrigin.LOCAL_INTENT,
                reduce_only=False,
            ),
        ),
        (
            OrderOrigin,
            "LOCAL_INTENT",
            lambda value: decide_order_transition(
                OrderState.SUBMITTING,
                OrderState.OPEN,
                source=OrderTransitionSource.VENUE,
                mode=OmsExecutionMode.LIVE,
                origin=value,
                reduce_only=False,
            ),
        ),
    )
    for enum_type, member_literal, operation in cases:
        for literal in (member_literal, "NOT_A_CANONICAL_MEMBER"):
            reconstructed = str.__new__(enum_type, literal)
            assert type(reconstructed) is enum_type
            calls.clear()
            with monkeypatch.context() as patch:
                patch.setattr(enum_type, "__hash__", forbidden_hash)
                patch.setattr(enum_type, "__eq__", forbidden_eq)
                with pytest.raises(OmsTransitionError):
                    operation(reconstructed)
            assert calls == []


def test_oms_import_is_inert_and_has_no_authority_dependencies() -> None:
    """Importing the pure OMS slice must not load stateful execution components."""
    script = """
import importlib, json, sys
modules = [
    importlib.import_module('hmt_crypto.oms.models'),
    importlib.import_module('hmt_crypto.oms.state_machine'),
    importlib.import_module('hmt_crypto.oms.client_order_id'),
]
forbidden = (
    'httpx', 'sqlalchemy', 'hmt_crypto.exchange.delta.transport',
    'hmt_crypto.exchange.credentials', 'hmt_crypto.exchange.delta.signer',
    'hmt_crypto.exchange.delta.weighted_limiter', 'hmt_crypto.logging',
)
print(json.dumps({
    'forbidden': [name for name in forbidden if name in sys.modules],
    'module_names': [module.__name__ for module in modules],
}))
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", "import sys; sys.path.insert(0, 'src');" + script],
        cwd=".",
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(completed.stdout)
    assert report == {
        "forbidden": [],
        "module_names": [
            "hmt_crypto.oms.models",
            "hmt_crypto.oms.state_machine",
            "hmt_crypto.oms.client_order_id",
        ],
    }
    package = importlib.import_module("hmt_crypto.oms")
    assert not hasattr(package, "decide_command_transition")
    assert not hasattr(package, "make_client_order_id")
