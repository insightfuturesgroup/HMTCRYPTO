"""Operation-safe Delta retry and ambiguous-write classification contracts."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest
from rate_limit_test_support import limiter_api

from hmt_crypto.errors import AmbiguousExchangeResult

pytestmark = pytest.mark.unit


def retry_policy(**overrides: object) -> object:
    api = limiter_api()
    values: dict[str, object] = {
        "max_attempts": 3,
        "base_delay_seconds": "1",
        "max_delay_seconds": "10",
        "jitter_ratio": "0.2",
    }
    values.update(overrides)
    return api.RetryPolicy(**values)


def decision(
    *,
    operation: str = "safe_read",
    attempt: int = 1,
    status_code: int | None = None,
    reset_header: str | None = None,
    transport_error: BaseException | None = None,
    bytes_may_have_been_sent: bool = False,
    jitter_unit: object = "0.5",
) -> object:
    api = limiter_api()
    return api.classify_retry(
        operation=api.OperationClass(operation),
        attempt=attempt,
        status_code=status_code,
        reset_header=reset_header,
        transport_error=transport_error,
        bytes_may_have_been_sent=bytes_may_have_been_sent,
        jitter_unit=jitter_unit,
        policy=retry_policy(),
    )


def test_direct_retry_evidence_rejects_contradictory_construction() -> None:
    api = limiter_api()
    with pytest.raises(ValueError):
        api.RetryDecision(
            action=api.RetryAction.RETRY,
            delay_seconds=None,
            reason="contradictory",
            operation=api.OperationClass.SAFE_READ,
        )
    with pytest.raises(ValueError):
        api.RetryDecision(
            action=api.RetryAction.RETRY,
            delay_seconds=Decimal("1"),
            reason="write-retry",
            operation=api.OperationClass.CREATE_ORDER,
        )
    with pytest.raises(ValueError):
        api.RetryDecision(
            action=api.RetryAction.AMBIGUOUS,
            delay_seconds=None,
            reason="read-ambiguity",
            operation=api.OperationClass.SAFE_READ,
        )


@pytest.mark.parametrize("status", [429, 500, 502, 503, 599])
def test_safe_reads_retry_429_and_every_5xx(status: int) -> None:
    api = limiter_api()
    result = decision(status_code=status)
    assert result.action is api.RetryAction.RETRY
    assert result.delay_seconds == Decimal("1.1")


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda: httpx.ConnectError("connect"),
        lambda: httpx.ConnectTimeout("connect-timeout"),
        lambda: httpx.ReadTimeout("read-timeout"),
        lambda: httpx.ReadError("read"),
    ],
)
def test_safe_reads_retry_only_narrow_transient_transport_errors(
    error_factory: Callable[[], BaseException],
) -> None:
    assert decision(transport_error=error_factory()).action is limiter_api().RetryAction.RETRY


def test_tls_connect_failure_is_not_classified_as_transient() -> None:
    import ssl

    try:
        raise ssl.SSLError("certificate failure")
    except ssl.SSLError as cause:
        error = httpx.ConnectError("TLS failed")
        error.__cause__ = cause
    assert decision(transport_error=error).action is limiter_api().RetryAction.FAIL


@pytest.mark.parametrize("status", [400, 401, 403, 404, 405, 406])
def test_non_429_4xx_are_never_retried(status: int) -> None:
    result = decision(status_code=status)
    assert result.action is limiter_api().RetryAction.FAIL
    assert result.delay_seconds is None


@pytest.mark.parametrize(
    "error",
    [
        ValueError("programming"),
        httpx.RemoteProtocolError("protocol"),
        asyncio.CancelledError(),
    ],
)
def test_programming_protocol_and_cancellation_errors_are_not_retried(error: BaseException) -> None:
    assert decision(transport_error=error).action is limiter_api().RetryAction.FAIL


def test_definitive_http_response_and_cancellation_take_precedence_over_other_signals() -> None:
    api = limiter_api()
    assert (
        decision(status_code=401, transport_error=httpx.ConnectError("also-present")).action
        is api.RetryAction.FAIL
    )
    assert (
        decision(status_code=503, transport_error=asyncio.CancelledError()).action
        is api.RetryAction.FAIL
    )


def test_backoff_is_exponential_bounded_and_stops_at_max_attempts() -> None:
    api = limiter_api()
    assert decision(status_code=503, attempt=1).delay_seconds == Decimal("1.1")
    assert decision(status_code=503, attempt=2).delay_seconds == Decimal("2.2")
    exhausted = decision(status_code=503, attempt=3)
    assert exhausted.action is api.RetryAction.FAIL
    assert exhausted.reason == "attempts_exhausted"

    capped = api.classify_retry(
        operation=api.OperationClass.SAFE_READ,
        attempt=3,
        status_code=503,
        reset_header=None,
        transport_error=None,
        bytes_may_have_been_sent=False,
        jitter_unit="1",
        policy=retry_policy(max_attempts=5, base_delay_seconds="4", max_delay_seconds="5"),
    )
    assert capped.delay_seconds == Decimal("5")


def test_429_reset_is_a_minimum_delay_and_never_interpreted_as_seconds() -> None:
    result = decision(status_code=429, reset_header="2500", jitter_unit="0")
    assert result.delay_seconds == Decimal("2.5")


@pytest.mark.parametrize("jitter", [True, -1, "-0.1", "1.1", float("nan")])
def test_jitter_input_is_strict_and_bounded(jitter: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        decision(status_code=500, jitter_unit=jitter)


@pytest.mark.parametrize(
    "operation",
    ["create_order", "modify_order", "cancel_order", "set_leverage", "add_margin", "batch_write"],
)
def test_every_write_class_is_categorically_never_auto_retried(operation: str) -> None:
    assert decision(operation=operation, status_code=429).action is limiter_api().RetryAction.FAIL
    assert decision(operation=operation, status_code=503).action is limiter_api().RetryAction.FAIL
    assert (
        decision(operation=operation, transport_error=httpx.ConnectError("x")).action
        is limiter_api().RetryAction.FAIL
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status_code": 503},
        {"transport_error": httpx.ReadTimeout("uncertain")},
        {"transport_error": httpx.WriteError("uncertain")},
    ],
)
def test_write_failures_after_bytes_may_have_been_sent_are_ambiguous(
    kwargs: dict[str, object],
) -> None:
    api = limiter_api()
    result = decision(operation="create_order", bytes_may_have_been_sent=True, **kwargs)  # type: ignore[arg-type]
    assert result.action is api.RetryAction.AMBIGUOUS
    assert result.delay_seconds is None
    with pytest.raises(AmbiguousExchangeResult):
        result.raise_if_ambiguous()


@pytest.mark.parametrize(
    "operation",
    ["create_order", "modify_order", "cancel_order", "set_leverage", "add_margin", "batch_write"],
)
def test_post_send_write_cancellation_is_ambiguous_for_every_write_class(
    operation: str,
) -> None:
    api = limiter_api()
    result = decision(
        operation=operation,
        transport_error=asyncio.CancelledError(),
        bytes_may_have_been_sent=True,
    )
    assert result.action is api.RetryAction.AMBIGUOUS
    with pytest.raises(AmbiguousExchangeResult):
        result.raise_if_ambiguous()


@pytest.mark.parametrize(
    ("operation", "sent"),
    [("safe_read", True), ("create_order", False)],
)
def test_safe_read_and_pre_send_write_cancellation_remain_non_retryable(
    operation: str,
    sent: bool,
) -> None:
    result = decision(
        operation=operation,
        transport_error=asyncio.CancelledError(),
        bytes_may_have_been_sent=sent,
    )
    assert result.action is limiter_api().RetryAction.FAIL


def test_definitive_write_rejection_and_no_send_fail_without_ambiguity() -> None:
    api = limiter_api()
    assert (
        decision(operation="create_order", status_code=400, bytes_may_have_been_sent=True).action
        is api.RetryAction.FAIL
    )
    assert (
        decision(
            operation="create_order",
            status_code=503,
            bytes_may_have_been_sent=False,
        ).action
        is api.RetryAction.FAIL
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": True},
        {"max_attempts": 0},
        {"base_delay_seconds": 1.0},
        {"base_delay_seconds": "0"},
        {"max_delay_seconds": "0"},
        {"jitter_ratio": True},
        {"jitter_ratio": "-0.1"},
        {"jitter_ratio": "1.1"},
    ],
)
def test_retry_policy_rejects_ambiguous_numeric_inputs(kwargs: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        retry_policy(**kwargs)
