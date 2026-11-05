"""Pure, bounded WebSocket reconnect planning for a later supervisor."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from hmt_crypto.exchange.delta.websocket_transport import WebSocketHandshakeCategory
from hmt_crypto.market_data.public_stream_models import PublicGenerationStatus

_FIVE_MINUTES_NS = 300_000_000_000
_TEN_MINUTES_NS = 600_000_000_000
_DELTA_ATTEMPT_LIMIT = 150
_MAX_HISTORY_ITEMS = 4096
_MAX_CONSECUTIVE_FAILURES = 1_000_000
_MAX_JITTER_DIGITS = 64
_MAX_JITTER_EXPONENT = 64


class ReconnectCause(StrEnum):
    """Sanitized terminal cause from one connection generation or handshake."""

    RECEIVER_ENDED = "receiver_ended"
    CONTROL_TIMEOUT = "control_timeout"
    PROTOCOL_DRIFT = "protocol_drift"
    CATALOGUE_MISMATCH = "catalogue_mismatch"
    HANDSHAKE_RATE_LIMIT = "handshake_rate_limit"
    HANDSHAKE_CLIENT_REJECTION = "handshake_client_rejection"
    HANDSHAKE_SERVER_REJECTION = "handshake_server_rejection"
    HANDSHAKE_NETWORK = "handshake_network"
    HANDSHAKE_PROTOCOL = "handshake_protocol"


class ReconnectAction(StrEnum):
    """Whether a supervisor may schedule another connection attempt."""

    RETRY = "retry"
    STOP = "stop"


def _positive_int(value: object, *, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _nonnegative_int(value: object, *, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _unit_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("jitter_unit must be an exact decimal")
    if not isinstance(value, (Decimal, int, str)):
        raise ValueError("jitter_unit must be an exact decimal")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValueError("jitter_unit must be an exact decimal") from error
    if not parsed.is_finite() or parsed < 0 or parsed > 1:
        raise ValueError("jitter_unit must be between zero and one")
    representation = parsed.as_tuple()
    exponent = representation.exponent
    if (
        type(exponent) is not int
        or len(representation.digits) > _MAX_JITTER_DIGITS
        or abs(exponent) > _MAX_JITTER_EXPONENT
    ):
        raise ValueError("jitter_unit representation exceeds its safety bound")
    return parsed


def _scale_floor(value: int, unit: Decimal) -> int:
    numerator, denominator = unit.as_integer_ratio()
    return value * numerator // denominator


@dataclass(frozen=True, slots=True)
class ReconnectPolicy:
    """Local safety bounds derived from Delta's current connection limits."""

    attempt_window_ns: int = _FIVE_MINUTES_NS
    max_attempts_per_window: int = 120
    base_delay_ns: int = 3_000_000_000
    min_spacing_ns: int = 3_000_000_000
    max_backoff_ns: int = 60_000_000_000
    rate_limit_min_delay_ns: int = _FIVE_MINUTES_NS
    rate_limit_max_delay_ns: int = _TEN_MINUTES_NS

    def __post_init__(self) -> None:
        window = _positive_int(self.attempt_window_ns, name="attempt_window_ns")
        attempts = _positive_int(
            self.max_attempts_per_window,
            name="max_attempts_per_window",
        )
        base = _positive_int(self.base_delay_ns, name="base_delay_ns")
        spacing = _positive_int(self.min_spacing_ns, name="min_spacing_ns")
        maximum = _positive_int(self.max_backoff_ns, name="max_backoff_ns")
        rate_min = _positive_int(
            self.rate_limit_min_delay_ns,
            name="rate_limit_min_delay_ns",
        )
        rate_max = _positive_int(
            self.rate_limit_max_delay_ns,
            name="rate_limit_max_delay_ns",
        )
        if window < _FIVE_MINUTES_NS:
            raise ValueError("attempt_window_ns cannot be shorter than five minutes")
        if attempts >= _DELTA_ATTEMPT_LIMIT:
            raise ValueError("max_attempts_per_window must stay below Delta's limit")
        if spacing > maximum or base > maximum:
            raise ValueError("backoff maximum must cover base delay and spacing")
        if maximum > rate_min:
            raise ValueError("normal backoff cannot exceed the rate-limit pause")
        if not _FIVE_MINUTES_NS <= rate_min <= rate_max <= _TEN_MINUTES_NS:
            raise ValueError("rate-limit delay must remain between five and ten minutes")


DEFAULT_RECONNECT_POLICY = ReconnectPolicy()


@dataclass(frozen=True, slots=True)
class ReconnectPlan:
    """One deterministic reconnect decision with no sleep or socket authority."""

    action: ReconnectAction
    cause: ReconnectCause
    delay_ns: int | None

    def __post_init__(self) -> None:
        if type(self.action) is not ReconnectAction or type(self.cause) is not ReconnectCause:
            raise TypeError("reconnect plan classification is invalid")
        if self.action is ReconnectAction.RETRY:
            if type(self.delay_ns) is not int or self.delay_ns <= 0:
                raise ValueError("retry plan requires a positive delay")
        elif self.delay_ns is not None:
            raise ValueError("terminal reconnect plan cannot carry a delay")


_GENERATION_CAUSES = {
    PublicGenerationStatus.RECEIVER_ENDED: ReconnectCause.RECEIVER_ENDED,
    PublicGenerationStatus.CONTROL_TIMEOUT: ReconnectCause.CONTROL_TIMEOUT,
    PublicGenerationStatus.PROTOCOL_DRIFT: ReconnectCause.PROTOCOL_DRIFT,
    PublicGenerationStatus.CATALOGUE_MISMATCH: ReconnectCause.CATALOGUE_MISMATCH,
}
_HANDSHAKE_CAUSES = {
    WebSocketHandshakeCategory.RATE_LIMIT: ReconnectCause.HANDSHAKE_RATE_LIMIT,
    WebSocketHandshakeCategory.CLIENT_REJECTION: ReconnectCause.HANDSHAKE_CLIENT_REJECTION,
    WebSocketHandshakeCategory.SERVER_REJECTION: ReconnectCause.HANDSHAKE_SERVER_REJECTION,
    WebSocketHandshakeCategory.NETWORK: ReconnectCause.HANDSHAKE_NETWORK,
    WebSocketHandshakeCategory.PROTOCOL: ReconnectCause.HANDSHAKE_PROTOCOL,
}
_TERMINAL_CAUSES = frozenset(
    {
        ReconnectCause.HANDSHAKE_CLIENT_REJECTION,
        ReconnectCause.HANDSHAKE_PROTOCOL,
    }
)


def cause_from_generation(status: PublicGenerationStatus) -> ReconnectCause:
    """Map an exact actor terminal status to its reconnect cause."""
    if type(status) is not PublicGenerationStatus:
        raise TypeError("generation status must be PublicGenerationStatus")
    return _GENERATION_CAUSES[status]


def cause_from_handshake(category: WebSocketHandshakeCategory) -> ReconnectCause:
    """Map an exact redacted handshake category to its reconnect cause."""
    if type(category) is not WebSocketHandshakeCategory:
        raise TypeError("handshake category must be WebSocketHandshakeCategory")
    return _HANDSHAKE_CAUSES[category]


def _validated_history(
    attempted_at_ns: tuple[int, ...],
    *,
    now_ns: int,
) -> tuple[int, ...]:
    if type(attempted_at_ns) is not tuple:
        raise TypeError("attempted_at_ns must be an immutable tuple")
    if len(attempted_at_ns) > _MAX_HISTORY_ITEMS:
        raise ValueError("attempt history exceeds its safety bound")
    previous = 0
    for attempted_ns in attempted_at_ns:
        if (
            type(attempted_ns) is not int
            or attempted_ns < 0
            or not previous <= attempted_ns <= now_ns
        ):
            raise ValueError("attempt history must be sorted, bounded monotonic time")
        previous = attempted_ns
    return attempted_at_ns


def _active_attempts(
    attempted_at_ns: tuple[int, ...],
    *,
    now_ns: int,
    policy: ReconnectPolicy,
) -> tuple[int, ...]:
    cutoff = now_ns - policy.attempt_window_ns
    return tuple(attempt for attempt in attempted_at_ns if attempt >= cutoff)


def record_connection_attempt(
    *,
    now_ns: int,
    attempted_at_ns: tuple[int, ...],
    policy: ReconnectPolicy = DEFAULT_RECONNECT_POLICY,
) -> tuple[int, ...]:
    """Prune one immutable rolling history and append the just-opened attempt."""
    current = _nonnegative_int(now_ns, name="now_ns")
    if type(policy) is not ReconnectPolicy:
        raise TypeError("policy must be ReconnectPolicy")
    history = _validated_history(attempted_at_ns, now_ns=current)
    active = _active_attempts(history, now_ns=current, policy=policy)
    if len(active) >= _MAX_HISTORY_ITEMS:
        raise ValueError("attempt history cannot be extended within its safety bound")
    return (*active, current)


def _budget_delay_ns(
    active: tuple[int, ...],
    *,
    now_ns: int,
    policy: ReconnectPolicy,
) -> int:
    if len(active) < policy.max_attempts_per_window:
        return 0
    expiry_index = len(active) - policy.max_attempts_per_window
    expiry_ns = active[expiry_index] + policy.attempt_window_ns + 1
    return max(0, expiry_ns - now_ns)


def _backoff_cap_ns(failures: int, policy: ReconnectPolicy) -> int:
    ratio = (policy.max_backoff_ns + policy.base_delay_ns - 1) // policy.base_delay_ns
    useful_exponent = ratio.bit_length()
    exponent = min(failures - 1, useful_exponent)
    return min(policy.max_backoff_ns, policy.base_delay_ns * (1 << exponent))


def plan_reconnect(
    *,
    cause: ReconnectCause,
    consecutive_failures: int,
    jitter_unit: Decimal | int | str,
    now_ns: int,
    attempted_at_ns: tuple[int, ...],
    policy: ReconnectPolicy = DEFAULT_RECONNECT_POLICY,
) -> ReconnectPlan:
    """Return a pure full-jitter plan honoring the shared rolling attempt budget."""
    if type(cause) is not ReconnectCause:
        raise TypeError("cause must be ReconnectCause")
    failures = _positive_int(consecutive_failures, name="consecutive_failures")
    if failures > _MAX_CONSECUTIVE_FAILURES:
        raise ValueError("consecutive_failures exceeds its safety bound")
    jitter = _unit_decimal(jitter_unit)
    current = _nonnegative_int(now_ns, name="now_ns")
    if type(policy) is not ReconnectPolicy:
        raise TypeError("policy must be ReconnectPolicy")
    history = _validated_history(attempted_at_ns, now_ns=current)
    active = _active_attempts(history, now_ns=current, policy=policy)

    if cause in _TERMINAL_CAUSES:
        return ReconnectPlan(action=ReconnectAction.STOP, cause=cause, delay_ns=None)

    budget_delay = _budget_delay_ns(active, now_ns=current, policy=policy)
    if cause is ReconnectCause.HANDSHAKE_RATE_LIMIT:
        rate_range = policy.rate_limit_max_delay_ns - policy.rate_limit_min_delay_ns
        delay = policy.rate_limit_min_delay_ns + _scale_floor(rate_range, jitter)
    else:
        backoff_cap = _backoff_cap_ns(failures, policy)
        delay = max(policy.min_spacing_ns, _scale_floor(backoff_cap, jitter))
    return ReconnectPlan(
        action=ReconnectAction.RETRY,
        cause=cause,
        delay_ns=max(delay, budget_delay),
    )


__all__ = [
    "DEFAULT_RECONNECT_POLICY",
    "ReconnectAction",
    "ReconnectCause",
    "ReconnectPlan",
    "ReconnectPolicy",
    "cause_from_generation",
    "cause_from_handshake",
    "plan_reconnect",
    "record_connection_attempt",
]
