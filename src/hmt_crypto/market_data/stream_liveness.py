"""Pure Delta stream heartbeat, ping/pong, and inactivity tracking."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from hmt_crypto.exchange.delta.websocket_transport import InboundKind

_SECOND_NS = 1_000_000_000
_MAX_HEARTBEAT_NS = 35 * _SECOND_NS
_MAX_PING_INTERVAL_NS = 30 * _SECOND_NS
_MAX_PONG_NS = 5 * _SECOND_NS
_MAX_INACTIVITY_NS = 60 * _SECOND_NS
_MIN_PING_INTERVAL_NS = 5 * _SECOND_NS


def _nonnegative_int(value: object, *, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _positive_int(value: object, *, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


class StreamLivenessAction(StrEnum):
    """One supervisor action derived from bounded monotonic evidence."""

    NONE = "none"
    SEND_PING = "send_ping"
    CLOSE_HEARTBEAT_TIMEOUT = "close_heartbeat_timeout"
    CLOSE_PONG_TIMEOUT = "close_pong_timeout"


@dataclass(frozen=True, slots=True)
class StreamLivenessPolicy:
    """Current local maximums from Delta's WebSocket liveness contract."""

    heartbeat_deadline_ns: int = _MAX_HEARTBEAT_NS
    ping_interval_ns: int = _MAX_PING_INTERVAL_NS
    pong_deadline_ns: int = _MAX_PONG_NS
    inactivity_deadline_ns: int = _MAX_INACTIVITY_NS

    def __post_init__(self) -> None:
        heartbeat = _positive_int(
            self.heartbeat_deadline_ns,
            name="heartbeat_deadline_ns",
        )
        ping = _positive_int(self.ping_interval_ns, name="ping_interval_ns")
        pong = _positive_int(self.pong_deadline_ns, name="pong_deadline_ns")
        inactivity = _positive_int(
            self.inactivity_deadline_ns,
            name="inactivity_deadline_ns",
        )
        if heartbeat > _MAX_HEARTBEAT_NS:
            raise ValueError("heartbeat deadline exceeds Delta's bound")
        if not _MIN_PING_INTERVAL_NS <= ping <= _MAX_PING_INTERVAL_NS:
            raise ValueError("ping interval is outside the local safe range")
        if pong > _MAX_PONG_NS or pong > ping:
            raise ValueError("pong deadline exceeds the safe bound")
        if not heartbeat <= inactivity <= _MAX_INACTIVITY_NS:
            raise ValueError("inactivity deadline is outside the safe range")


DEFAULT_STREAM_LIVENESS_POLICY = StreamLivenessPolicy()


@dataclass(frozen=True, slots=True)
class StreamLivenessState:
    """Immutable monotonic evidence for one physical connection generation."""

    started_at_ns: int
    last_inbound_at_ns: int
    last_heartbeat_at_ns: int | None
    last_ping_at_ns: int
    pending_ping_at_ns: int | None
    last_pong_at_ns: int | None

    def __post_init__(self) -> None:
        started = _nonnegative_int(self.started_at_ns, name="started_at_ns")
        inbound = _nonnegative_int(self.last_inbound_at_ns, name="last_inbound_at_ns")
        ping = _nonnegative_int(self.last_ping_at_ns, name="last_ping_at_ns")
        optional = (
            self.last_heartbeat_at_ns,
            self.pending_ping_at_ns,
            self.last_pong_at_ns,
        )
        if any(value is not None and type(value) is not int for value in optional):
            raise ValueError("liveness timestamps must be exact integers")
        if any(value is not None and value < started for value in optional):
            raise ValueError("liveness timestamp predates the generation")
        if inbound < started or ping < started:
            raise ValueError("liveness timestamp predates the generation")
        observed = tuple(
            value
            for value in (self.last_heartbeat_at_ns, self.last_pong_at_ns)
            if value is not None
        )
        if observed and inbound < max(observed):
            raise ValueError("inbound evidence cannot predate an observed control frame")
        if self.pending_ping_at_ns is not None and self.pending_ping_at_ns != ping:
            raise ValueError("pending ping must identify the most recent ping")
        if (
            self.pending_ping_at_ns is not None
            and self.last_pong_at_ns is not None
            and self.last_pong_at_ns >= self.pending_ping_at_ns
        ):
            raise ValueError("a pending ping cannot already have a later pong")
        if (
            self.pending_ping_at_ns is None
            and ping > started
            and (self.last_pong_at_ns is None or self.last_pong_at_ns < ping)
        ):
            raise ValueError("a completed ping requires its matching pong")


@dataclass(frozen=True, slots=True)
class StreamLivenessDecision:
    """Pure action plus the exact monotonic threshold that selected it."""

    action: StreamLivenessAction
    deadline_ns: int
    inactivity_expired: bool

    def __post_init__(self) -> None:
        if type(self.action) is not StreamLivenessAction:
            raise TypeError("liveness action is invalid")
        _nonnegative_int(self.deadline_ns, name="deadline_ns")
        if type(self.inactivity_expired) is not bool:
            raise TypeError("inactivity_expired must be boolean")


class StreamLivenessDeadlineExpired(ValueError):
    """A frame arrived only after a fail-closed liveness deadline."""

    __slots__ = ("_decision",)

    def __init__(self, decision: StreamLivenessDecision) -> None:
        if type(decision) is not StreamLivenessDecision or decision.action not in {
            StreamLivenessAction.CLOSE_HEARTBEAT_TIMEOUT,
            StreamLivenessAction.CLOSE_PONG_TIMEOUT,
        }:
            raise TypeError("deadline expiration requires a close decision")
        self._decision = decision
        super().__init__("WebSocket liveness deadline expired")

    @property
    def decision(self) -> StreamLivenessDecision:
        """Return the sanitized close decision that won the deadline race."""
        return self._decision


def initial_liveness(*, now_ns: int) -> StreamLivenessState:
    """Create evidence immediately after heartbeat enablement is sent."""
    current = _nonnegative_int(now_ns, name="now_ns")
    return StreamLivenessState(
        started_at_ns=current,
        last_inbound_at_ns=current,
        last_heartbeat_at_ns=None,
        last_ping_at_ns=current,
        pending_ping_at_ns=None,
        last_pong_at_ns=None,
    )


def _require_state(value: object) -> StreamLivenessState:
    if type(value) is not StreamLivenessState:
        raise TypeError("state must be StreamLivenessState")
    return value


def _current(value: object, state: StreamLivenessState) -> int:
    now_ns = _nonnegative_int(value, name="now_ns")
    latest = max(
        timestamp
        for timestamp in (
            state.started_at_ns,
            state.last_inbound_at_ns,
            state.last_ping_at_ns,
            state.last_heartbeat_at_ns,
            state.pending_ping_at_ns,
            state.last_pong_at_ns,
        )
        if timestamp is not None
    )
    if now_ns < latest:
        raise ValueError("monotonic time regressed")
    return now_ns


def observe_inbound(
    state: StreamLivenessState,
    *,
    kind: InboundKind,
    now_ns: int,
    policy: StreamLivenessPolicy = DEFAULT_STREAM_LIVENESS_POLICY,
) -> StreamLivenessState:
    """Apply one sanitized inbound classification to liveness evidence."""
    retained = _require_state(state)
    if type(kind) is not InboundKind:
        raise TypeError("kind must be InboundKind")
    current = _current(now_ns, retained)
    decision = next_liveness_action(retained, now_ns=current, policy=policy)
    if decision.action in {
        StreamLivenessAction.CLOSE_HEARTBEAT_TIMEOUT,
        StreamLivenessAction.CLOSE_PONG_TIMEOUT,
    }:
        raise StreamLivenessDeadlineExpired(decision)
    if kind is InboundKind.HEARTBEAT:
        return replace(
            retained,
            last_inbound_at_ns=current,
            last_heartbeat_at_ns=current,
        )
    if kind is InboundKind.PONG:
        return replace(
            retained,
            last_inbound_at_ns=current,
            last_pong_at_ns=current,
            pending_ping_at_ns=None,
        )
    return replace(retained, last_inbound_at_ns=current)


def reserve_due_ping(
    state: StreamLivenessState,
    *,
    now_ns: int,
    policy: StreamLivenessPolicy = DEFAULT_STREAM_LIVENESS_POLICY,
) -> StreamLivenessState:
    """Reserve a due ping before send; failed or ambiguous sends must close.

    Reserving before physical I/O lets a very fast Pong satisfy the exact pending
    ping without a send-completion race. The supervisor must close the generation
    if the subsequent send raises or is cancelled.
    """
    retained = _require_state(state)
    current = _current(now_ns, retained)
    decision = next_liveness_action(retained, now_ns=current, policy=policy)
    if decision.action is not StreamLivenessAction.SEND_PING:
        raise ValueError("ping is not due")
    return replace(
        retained,
        last_ping_at_ns=current,
        pending_ping_at_ns=current,
        last_pong_at_ns=None,
    )


def next_liveness_action(
    state: StreamLivenessState,
    *,
    now_ns: int,
    policy: StreamLivenessPolicy = DEFAULT_STREAM_LIVENESS_POLICY,
) -> StreamLivenessDecision:
    """Classify current evidence without sleeping, sending, closing, or logging."""
    retained = _require_state(state)
    current = _current(now_ns, retained)
    if type(policy) is not StreamLivenessPolicy:
        raise TypeError("policy must be StreamLivenessPolicy")

    heartbeat_base = retained.last_heartbeat_at_ns or retained.started_at_ns
    heartbeat_deadline = heartbeat_base + policy.heartbeat_deadline_ns
    inactivity_deadline = retained.last_inbound_at_ns + policy.inactivity_deadline_ns
    inactivity_expired = current >= inactivity_deadline
    expired: list[tuple[int, int, StreamLivenessAction]] = [
        (
            heartbeat_deadline,
            1,
            StreamLivenessAction.CLOSE_HEARTBEAT_TIMEOUT,
        )
    ]
    if retained.pending_ping_at_ns is not None:
        pong_deadline = retained.pending_ping_at_ns + policy.pong_deadline_ns
        expired.append((pong_deadline, 0, StreamLivenessAction.CLOSE_PONG_TIMEOUT))
    else:
        pong_deadline = None
    reached = tuple(candidate for candidate in expired if current >= candidate[0])
    if reached:
        deadline, _priority, action = min(reached)
        return StreamLivenessDecision(
            action=action,
            deadline_ns=deadline,
            inactivity_expired=inactivity_expired,
        )
    ping_deadline = retained.last_ping_at_ns + policy.ping_interval_ns
    if retained.pending_ping_at_ns is None and current >= ping_deadline:
        return StreamLivenessDecision(
            action=StreamLivenessAction.SEND_PING,
            deadline_ns=ping_deadline,
            inactivity_expired=False,
        )
    deadlines = [heartbeat_deadline, inactivity_deadline]
    deadlines.append(pong_deadline if pong_deadline is not None else ping_deadline)
    return StreamLivenessDecision(
        action=StreamLivenessAction.NONE,
        deadline_ns=min(deadline for deadline in deadlines if deadline > current),
        inactivity_expired=False,
    )


__all__ = [
    "DEFAULT_STREAM_LIVENESS_POLICY",
    "StreamLivenessAction",
    "StreamLivenessDeadlineExpired",
    "StreamLivenessDecision",
    "StreamLivenessPolicy",
    "StreamLivenessState",
    "initial_liveness",
    "next_liveness_action",
    "observe_inbound",
    "reserve_due_ping",
]
