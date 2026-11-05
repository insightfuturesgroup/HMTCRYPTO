"""Pure WebSocket reconnect planning and rolling-attempt budget contracts."""

from __future__ import annotations

from decimal import ROUND_UP, Decimal, Inexact, localcontext
from importlib import import_module
from typing import Any

import pytest

from hmt_crypto.exchange.delta.websocket_transport import WebSocketHandshakeCategory
from hmt_crypto.market_data.public_stream_models import PublicGenerationStatus


def policy_module() -> Any:
    return import_module("hmt_crypto.market_data.reconnect_policy")


def test_default_policy_stays_below_delta_attempt_limit() -> None:
    module = policy_module()
    policy = module.DEFAULT_RECONNECT_POLICY

    assert policy.attempt_window_ns == 300_000_000_000
    assert 0 < policy.max_attempts_per_window < 150
    assert policy.rate_limit_min_delay_ns == 300_000_000_000
    assert policy.rate_limit_max_delay_ns == 600_000_000_000


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (PublicGenerationStatus.RECEIVER_ENDED, "receiver_ended"),
        (PublicGenerationStatus.CONTROL_TIMEOUT, "control_timeout"),
        (PublicGenerationStatus.PROTOCOL_DRIFT, "protocol_drift"),
        (PublicGenerationStatus.CATALOGUE_MISMATCH, "catalogue_mismatch"),
    ],
)
def test_generation_status_mapping_is_exact(status: object, expected: str) -> None:
    module = policy_module()
    assert module.cause_from_generation(status).value == expected


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        (WebSocketHandshakeCategory.RATE_LIMIT, "handshake_rate_limit"),
        (WebSocketHandshakeCategory.CLIENT_REJECTION, "handshake_client_rejection"),
        (WebSocketHandshakeCategory.SERVER_REJECTION, "handshake_server_rejection"),
        (WebSocketHandshakeCategory.NETWORK, "handshake_network"),
        (WebSocketHandshakeCategory.PROTOCOL, "handshake_protocol"),
    ],
)
def test_handshake_category_mapping_is_exact(category: object, expected: str) -> None:
    module = policy_module()
    assert module.cause_from_handshake(category).value == expected


@pytest.mark.parametrize(
    "cause_name",
    [
        "RECEIVER_ENDED",
        "CONTROL_TIMEOUT",
        "PROTOCOL_DRIFT",
        "CATALOGUE_MISMATCH",
        "HANDSHAKE_SERVER_REJECTION",
        "HANDSHAKE_NETWORK",
    ],
)
def test_transient_causes_retry_with_no_io(cause_name: str) -> None:
    module = policy_module()
    plan = module.plan_reconnect(
        cause=getattr(module.ReconnectCause, cause_name),
        consecutive_failures=1,
        jitter_unit="0",
        now_ns=10,
        attempted_at_ns=(),
    )

    assert plan.action is module.ReconnectAction.RETRY
    assert plan.delay_ns == module.DEFAULT_RECONNECT_POLICY.min_spacing_ns
    assert plan.cause is getattr(module.ReconnectCause, cause_name)


@pytest.mark.parametrize(
    "cause_name",
    ["HANDSHAKE_CLIENT_REJECTION", "HANDSHAKE_PROTOCOL"],
)
def test_definitive_handshake_failures_stop_without_delay(cause_name: str) -> None:
    module = policy_module()
    plan = module.plan_reconnect(
        cause=getattr(module.ReconnectCause, cause_name),
        consecutive_failures=1,
        jitter_unit="1",
        now_ns=0,
        attempted_at_ns=(),
    )

    assert plan.action is module.ReconnectAction.STOP
    assert plan.delay_ns is None


@pytest.mark.parametrize(
    ("jitter", "expected"),
    [("0", 1), ("0.5", 8_000_000_000), ("1", 16_000_000_000)],
)
def test_full_jitter_is_exact_and_bounded(jitter: str, expected: int) -> None:
    module = policy_module()
    custom = module.ReconnectPolicy(
        base_delay_ns=4_000_000_000,
        min_spacing_ns=1,
        max_backoff_ns=64_000_000_000,
    )
    plan = module.plan_reconnect(
        cause=module.ReconnectCause.HANDSHAKE_NETWORK,
        consecutive_failures=3,
        jitter_unit=jitter,
        now_ns=0,
        attempted_at_ns=(),
        policy=custom,
    )

    assert plan.delay_ns == expected


def test_backoff_caps_without_computing_an_unbounded_exponent() -> None:
    module = policy_module()
    plan = module.plan_reconnect(
        cause=module.ReconnectCause.HANDSHAKE_NETWORK,
        consecutive_failures=1_000_000,
        jitter_unit="1",
        now_ns=0,
        attempted_at_ns=(),
    )
    assert plan.delay_ns == module.DEFAULT_RECONNECT_POLICY.max_backoff_ns


@pytest.mark.parametrize(
    ("jitter", "expected"),
    [
        ("0", 300_000_000_000),
        ("0.5", 450_000_000_000),
        ("1", 600_000_000_000),
    ],
)
def test_429_pause_is_between_five_and_ten_minutes(jitter: str, expected: int) -> None:
    module = policy_module()
    plan = module.plan_reconnect(
        cause=module.ReconnectCause.HANDSHAKE_RATE_LIMIT,
        consecutive_failures=1,
        jitter_unit=jitter,
        now_ns=0,
        attempted_at_ns=(),
    )
    assert plan.action is module.ReconnectAction.RETRY
    assert plan.delay_ns == expected


def test_jitter_math_ignores_ambient_decimal_precision_rounding_and_traps() -> None:
    module = policy_module()
    custom = module.ReconnectPolicy(
        min_spacing_ns=1,
        max_backoff_ns=59_999_999_999,
        rate_limit_max_delay_ns=599_999_999_999,
    )
    with localcontext() as context:
        context.prec = 1
        context.rounding = ROUND_UP
        context.traps[Inexact] = True
        normal = module.plan_reconnect(
            cause=module.ReconnectCause.HANDSHAKE_NETWORK,
            consecutive_failures=1_000_000,
            jitter_unit="0.333",
            now_ns=0,
            attempted_at_ns=(),
            policy=custom,
        )
        limited = module.plan_reconnect(
            cause=module.ReconnectCause.HANDSHAKE_RATE_LIMIT,
            consecutive_failures=1,
            jitter_unit="0.333",
            now_ns=0,
            attempted_at_ns=(),
            policy=custom,
        )

    assert normal.delay_ns == 59_999_999_999 * 333 // 1000
    assert limited.delay_ns == 300_000_000_000 + 299_999_999_999 * 333 // 1000


@pytest.mark.parametrize("jitter", ["0." + "1" * 65, "1e-65", Decimal("1e-65")])
def test_jitter_representation_is_bounded_against_integer_ratio_abuse(jitter: object) -> None:
    module = policy_module()
    with pytest.raises(ValueError):
        module.plan_reconnect(
            cause=module.ReconnectCause.RECEIVER_ENDED,
            consecutive_failures=1,
            jitter_unit=jitter,
            now_ns=0,
            attempted_at_ns=(),
        )


def test_rolling_attempt_budget_delays_until_enough_attempts_expire() -> None:
    module = policy_module()
    attempts = tuple(index * 1_000_000_000 for index in range(121))
    now_ns = 250_000_000_000
    plan = module.plan_reconnect(
        cause=module.ReconnectCause.RECEIVER_ENDED,
        consecutive_failures=1,
        jitter_unit="0",
        now_ns=now_ns,
        attempted_at_ns=attempts,
    )

    # There are 121 active attempts and the default admits at most 120. The
    # second-oldest attempt must expire before one more attempt can be opened.
    assert plan.delay_ns == 51_000_000_001


def test_stale_attempts_do_not_consume_the_current_window() -> None:
    module = policy_module()
    plan = module.plan_reconnect(
        cause=module.ReconnectCause.RECEIVER_ENDED,
        consecutive_failures=1,
        jitter_unit="0",
        now_ns=600_000_000_000,
        attempted_at_ns=(0, 299_999_999_999),
    )
    assert plan.delay_ns == module.DEFAULT_RECONNECT_POLICY.min_spacing_ns


def test_record_attempt_prunes_stale_entries_and_returns_an_immutable_tuple() -> None:
    module = policy_module()
    recorded = module.record_connection_attempt(
        now_ns=600_000_000_000,
        attempted_at_ns=(0, 300_000_000_001, 599_000_000_000),
    )
    assert recorded == (300_000_000_001, 599_000_000_000, 600_000_000_000)
    assert type(recorded) is tuple


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cause": "receiver_ended"},
        {"consecutive_failures": True},
        {"consecutive_failures": 0},
        {"consecutive_failures": 1_000_001},
        {"jitter_unit": True},
        {"jitter_unit": 0.5},
        {"jitter_unit": "-0.1"},
        {"jitter_unit": "1.1"},
        {"jitter_unit": "NaN"},
        {"now_ns": True},
        {"now_ns": -1},
        {"attempted_at_ns": [1]},
        {"attempted_at_ns": (2, 1)},
        {"attempted_at_ns": (11,)},
        {"attempted_at_ns": (True,)},
        {"attempted_at_ns": (-1,)},
    ],
)
def test_plan_rejects_ambiguous_or_hostile_inputs(kwargs: dict[str, object]) -> None:
    module = policy_module()
    values: dict[str, object] = {
        "cause": module.ReconnectCause.RECEIVER_ENDED,
        "consecutive_failures": 1,
        "jitter_unit": Decimal("0.5"),
        "now_ns": 10,
        "attempted_at_ns": (),
    }
    values.update(kwargs)
    with pytest.raises((TypeError, ValueError)):
        module.plan_reconnect(**values)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"attempt_window_ns": True},
        {"attempt_window_ns": 0},
        {"max_attempts_per_window": 150},
        {"max_attempts_per_window": True},
        {"base_delay_ns": 0},
        {"min_spacing_ns": 0},
        {"max_backoff_ns": 0},
        {"rate_limit_min_delay_ns": 299_999_999_999},
        {"rate_limit_max_delay_ns": 600_000_000_001},
        {
            "rate_limit_min_delay_ns": 500_000_000_000,
            "rate_limit_max_delay_ns": 400_000_000_000,
        },
    ],
)
def test_policy_rejects_values_that_weaken_documented_bounds(
    kwargs: dict[str, object],
) -> None:
    module = policy_module()
    with pytest.raises((TypeError, ValueError)):
        module.ReconnectPolicy(**kwargs)


def test_mapping_helpers_reject_lookalikes() -> None:
    module = policy_module()
    with pytest.raises(TypeError):
        module.cause_from_generation("receiver_ended")
    with pytest.raises(TypeError):
        module.cause_from_handshake("network")


def test_attempt_history_is_bounded_against_memory_abuse() -> None:
    module = policy_module()
    attempts = tuple(range(4097))
    with pytest.raises(ValueError):
        module.plan_reconnect(
            cause=module.ReconnectCause.RECEIVER_ENDED,
            consecutive_failures=1,
            jitter_unit="0",
            now_ns=5000,
            attempted_at_ns=attempts,
        )


def test_record_never_returns_a_history_that_plan_will_reject() -> None:
    module = policy_module()
    accepted = module.record_connection_attempt(
        now_ns=4095,
        attempted_at_ns=tuple(range(4095)),
        policy=module.ReconnectPolicy(attempt_window_ns=10_000_000_000_000),
    )
    assert len(accepted) == 4096
    module.plan_reconnect(
        cause=module.ReconnectCause.RECEIVER_ENDED,
        consecutive_failures=1,
        jitter_unit="0",
        now_ns=4095,
        attempted_at_ns=accepted,
        policy=module.ReconnectPolicy(attempt_window_ns=10_000_000_000_000),
    )

    with pytest.raises(ValueError):
        module.record_connection_attempt(
            now_ns=4096,
            attempted_at_ns=tuple(range(4096)),
            policy=module.ReconnectPolicy(attempt_window_ns=10_000_000_000_000),
        )
