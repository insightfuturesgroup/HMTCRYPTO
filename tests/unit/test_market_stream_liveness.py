"""Pure Delta WebSocket heartbeat, ping/pong, and inactivity contracts."""

from __future__ import annotations

import pytest

from hmt_crypto.exchange.delta.websocket_transport import InboundKind
from hmt_crypto.market_data.stream_liveness import (
    DEFAULT_STREAM_LIVENESS_POLICY,
    StreamLivenessAction,
    StreamLivenessDeadlineExpired,
    StreamLivenessPolicy,
    StreamLivenessState,
    initial_liveness,
    next_liveness_action,
    observe_inbound,
    reserve_due_ping,
)

SECOND = 1_000_000_000


def test_defaults_match_current_documented_deadlines() -> None:
    policy = DEFAULT_STREAM_LIVENESS_POLICY
    assert policy.heartbeat_deadline_ns == 35 * SECOND
    assert policy.ping_interval_ns == 30 * SECOND
    assert policy.pong_deadline_ns == 5 * SECOND
    assert policy.inactivity_deadline_ns == 60 * SECOND


def test_first_ping_is_due_at_exact_interval() -> None:
    state = initial_liveness(now_ns=10)
    assert next_liveness_action(state, now_ns=10 + 30 * SECOND - 1).action is (
        StreamLivenessAction.NONE
    )
    decision = next_liveness_action(state, now_ns=10 + 30 * SECOND)
    assert decision.action is StreamLivenessAction.SEND_PING
    assert decision.deadline_ns == 10 + 30 * SECOND


def test_missing_server_heartbeat_closes_at_35_seconds_even_with_data() -> None:
    state = initial_liveness(now_ns=0)
    state = observe_inbound(state, kind=InboundKind.DATA, now_ns=34 * SECOND)
    decision = next_liveness_action(state, now_ns=35 * SECOND)
    assert decision.action is StreamLivenessAction.CLOSE_HEARTBEAT_TIMEOUT
    assert decision.deadline_ns == 35 * SECOND


def test_heartbeat_advances_its_own_deadline() -> None:
    state = initial_liveness(now_ns=0)
    state = observe_inbound(state, kind=InboundKind.HEARTBEAT, now_ns=30 * SECOND)
    assert next_liveness_action(state, now_ns=64 * SECOND).action is (
        StreamLivenessAction.SEND_PING
    )
    assert next_liveness_action(state, now_ns=65 * SECOND).action is (
        StreamLivenessAction.CLOSE_HEARTBEAT_TIMEOUT
    )


def test_ping_requires_pong_within_five_seconds() -> None:
    state = initial_liveness(now_ns=0)
    state = observe_inbound(state, kind=InboundKind.HEARTBEAT, now_ns=30 * SECOND)
    state = reserve_due_ping(state, now_ns=30 * SECOND)
    assert next_liveness_action(state, now_ns=35 * SECOND - 1).action is (StreamLivenessAction.NONE)
    decision = next_liveness_action(state, now_ns=35 * SECOND)
    assert decision.action is StreamLivenessAction.CLOSE_PONG_TIMEOUT
    assert decision.deadline_ns == 35 * SECOND


def test_only_pong_clears_a_pending_ping() -> None:
    state = initial_liveness(now_ns=0)
    state = reserve_due_ping(state, now_ns=30 * SECOND)
    state = observe_inbound(state, kind=InboundKind.DATA, now_ns=34 * SECOND)
    assert state.pending_ping_at_ns == 30 * SECOND
    state = observe_inbound(state, kind=InboundKind.PONG, now_ns=34 * SECOND + 1)
    assert state.pending_ping_at_ns is None
    assert state.last_pong_at_ns == 34 * SECOND + 1


def test_sixty_second_inactivity_is_exposed_even_if_heartbeat_already_expired() -> None:
    state = initial_liveness(now_ns=0)
    decision = next_liveness_action(state, now_ns=60 * SECOND)
    assert decision.action is StreamLivenessAction.CLOSE_HEARTBEAT_TIMEOUT
    assert decision.inactivity_expired is True


@pytest.mark.parametrize("kind", tuple(InboundKind))
def test_every_bounded_inbound_kind_advances_inactivity(
    kind: InboundKind,
) -> None:
    state = observe_inbound(initial_liveness(now_ns=5), kind=kind, now_ns=10)
    assert state.last_inbound_at_ns == 10


def test_repeated_ping_is_forbidden_while_pong_is_pending() -> None:
    state = reserve_due_ping(initial_liveness(now_ns=0), now_ns=30 * SECOND)
    with pytest.raises(ValueError):
        reserve_due_ping(state, now_ns=31 * SECOND)


def test_unsolicited_pong_is_safe_but_does_not_change_ping_schedule() -> None:
    state = initial_liveness(now_ns=0)
    state = observe_inbound(state, kind=InboundKind.PONG, now_ns=1 * SECOND)
    assert state.last_pong_at_ns == 1 * SECOND
    assert state.pending_ping_at_ns is None
    assert next_liveness_action(state, now_ns=30 * SECOND).action is (
        StreamLivenessAction.SEND_PING
    )


def test_same_tick_stale_pong_cannot_collide_with_a_new_ping_reservation() -> None:
    state = initial_liveness(now_ns=0)
    state = observe_inbound(state, kind=InboundKind.PONG, now_ns=30 * SECOND)
    reserved = reserve_due_ping(state, now_ns=30 * SECOND)
    assert reserved.pending_ping_at_ns == 30 * SECOND
    assert reserved.last_pong_at_ns is None

    satisfied = observe_inbound(
        reserved,
        kind=InboundKind.PONG,
        now_ns=30 * SECOND,
    )
    assert satisfied.pending_ping_at_ns is None
    assert satisfied.last_pong_at_ns == 30 * SECOND


def test_late_heartbeat_cannot_revive_an_expired_generation() -> None:
    state = initial_liveness(now_ns=0)
    with pytest.raises(StreamLivenessDeadlineExpired) as error:
        observe_inbound(state, kind=InboundKind.HEARTBEAT, now_ns=36 * SECOND)
    assert error.value.decision.action is StreamLivenessAction.CLOSE_HEARTBEAT_TIMEOUT
    assert error.value.decision.deadline_ns == 35 * SECOND


def test_late_pong_cannot_clear_an_expired_pending_ping() -> None:
    state = observe_inbound(
        initial_liveness(now_ns=0),
        kind=InboundKind.HEARTBEAT,
        now_ns=30 * SECOND,
    )
    state = reserve_due_ping(state, now_ns=30 * SECOND)
    with pytest.raises(StreamLivenessDeadlineExpired) as error:
        observe_inbound(state, kind=InboundKind.PONG, now_ns=35 * SECOND)
    assert error.value.decision.action is StreamLivenessAction.CLOSE_PONG_TIMEOUT
    assert error.value.decision.deadline_ns == 35 * SECOND


def test_ping_is_reserved_before_send_so_a_fast_pong_cannot_be_lost() -> None:
    state = observe_inbound(
        initial_liveness(now_ns=0),
        kind=InboundKind.HEARTBEAT,
        now_ns=30 * SECOND,
    )
    reserved = reserve_due_ping(state, now_ns=30 * SECOND)
    satisfied = observe_inbound(
        reserved,
        kind=InboundKind.PONG,
        now_ns=30 * SECOND + 1,
    )
    assert satisfied.pending_ping_at_ns is None
    assert next_liveness_action(satisfied, now_ns=35 * SECOND).action is (StreamLivenessAction.NONE)


def test_reserved_ping_without_a_proven_pong_fails_closed_after_send_ambiguity() -> None:
    state = reserve_due_ping(initial_liveness(now_ns=0), now_ns=30 * SECOND)
    decision = next_liveness_action(state, now_ns=35 * SECOND)
    assert decision.action is StreamLivenessAction.CLOSE_PONG_TIMEOUT


def test_ping_reservation_requires_the_exact_due_decision() -> None:
    state = initial_liveness(now_ns=0)
    with pytest.raises(ValueError):
        reserve_due_ping(state, now_ns=0)
    state = observe_inbound(state, kind=InboundKind.PONG, now_ns=1)
    with pytest.raises(ValueError):
        reserve_due_ping(state, now_ns=29 * SECOND)


def test_earliest_expired_deadline_wins_over_a_later_pong_timeout() -> None:
    state = initial_liveness(now_ns=0)
    state = reserve_due_ping(state, now_ns=34 * SECOND)
    decision = next_liveness_action(state, now_ns=39 * SECOND)
    assert decision.action is StreamLivenessAction.CLOSE_HEARTBEAT_TIMEOUT
    assert decision.deadline_ns == 35 * SECOND


def test_pending_ping_and_later_recorded_pong_is_rejected_as_contradictory() -> None:
    with pytest.raises(ValueError):
        StreamLivenessState(
            started_at_ns=0,
            last_inbound_at_ns=10,
            last_heartbeat_at_ns=None,
            last_ping_at_ns=5,
            pending_ping_at_ns=5,
            last_pong_at_ns=10,
        )


@pytest.mark.parametrize("last_pong_at_ns", [None, 29 * SECOND])
def test_completed_ping_cannot_disappear_without_its_matching_pong(
    last_pong_at_ns: int | None,
) -> None:
    with pytest.raises(ValueError):
        StreamLivenessState(
            started_at_ns=0,
            last_inbound_at_ns=30 * SECOND,
            last_heartbeat_at_ns=30 * SECOND,
            last_ping_at_ns=30 * SECOND,
            pending_ping_at_ns=None,
            last_pong_at_ns=last_pong_at_ns,
        )


def test_direct_completed_ping_state_requires_a_current_pong() -> None:
    state = StreamLivenessState(
        started_at_ns=0,
        last_inbound_at_ns=30 * SECOND + 1,
        last_heartbeat_at_ns=30 * SECOND,
        last_ping_at_ns=30 * SECOND,
        pending_ping_at_ns=None,
        last_pong_at_ns=30 * SECOND + 1,
    )
    assert next_liveness_action(state, now_ns=35 * SECOND).action is (StreamLivenessAction.NONE)


def test_inactivity_is_diagnostic_because_heartbeat_deadline_is_stricter() -> None:
    assert "close_inactivity" not in {item.value for item in StreamLivenessAction}
    decision = next_liveness_action(initial_liveness(now_ns=0), now_ns=60 * SECOND)
    assert decision.action is StreamLivenessAction.CLOSE_HEARTBEAT_TIMEOUT
    assert decision.inactivity_expired is True


@pytest.mark.parametrize(
    "factory",
    [
        lambda: initial_liveness(now_ns=True),
        lambda: initial_liveness(now_ns=-1),
        lambda: StreamLivenessPolicy(heartbeat_deadline_ns=True),
        lambda: StreamLivenessPolicy(heartbeat_deadline_ns=36 * SECOND),
        lambda: StreamLivenessPolicy(ping_interval_ns=31 * SECOND),
        lambda: StreamLivenessPolicy(pong_deadline_ns=6 * SECOND),
        lambda: StreamLivenessPolicy(inactivity_deadline_ns=61 * SECOND),
        lambda: StreamLivenessPolicy(ping_interval_ns=4 * SECOND),
    ],
)
def test_policy_and_initial_state_reject_weakened_or_ambiguous_values(factory: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()  # type: ignore[operator]


def test_state_operations_reject_time_regression_and_enum_lookalikes() -> None:
    state = initial_liveness(now_ns=10)
    with pytest.raises(ValueError):
        observe_inbound(state, kind=InboundKind.DATA, now_ns=9)
    with pytest.raises(TypeError):
        observe_inbound(state, kind="data", now_ns=11)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        reserve_due_ping(state, now_ns=9)
    with pytest.raises(ValueError):
        next_liveness_action(state, now_ns=9)


def test_decisions_are_immutable_and_carry_exact_deadline() -> None:
    state = initial_liveness(now_ns=7)
    decision = next_liveness_action(state, now_ns=7)
    assert decision.action is StreamLivenessAction.NONE
    assert decision.deadline_ns == 7 + 30 * SECOND
    with pytest.raises((AttributeError, TypeError)):
        decision.deadline_ns = 0  # type: ignore[misc]
