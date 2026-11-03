"""Pure operation-safe Delta retry and ambiguous-write classification."""

from __future__ import annotations

import asyncio
import ssl
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import cast

import httpx

from hmt_crypto.errors import AmbiguousExchangeResult, ConfigurationError
from hmt_crypto.exchange.delta.rate_limit_policy import parse_rate_limit_reset_ms


class OperationClass(StrEnum):
    """Retry-safety class independent of HTTP method names."""

    SAFE_READ = "safe_read"
    CREATE_ORDER = "create_order"
    MODIFY_ORDER = "modify_order"
    CANCEL_ORDER = "cancel_order"
    SET_LEVERAGE = "set_leverage"
    ADD_MARGIN = "add_margin"
    BATCH_WRITE = "batch_write"


class RetryAction(StrEnum):
    """Pure retry-classifier outcome."""

    RETRY = "retry"
    FAIL = "fail"
    AMBIGUOUS = "ambiguous"


def _strict_positive_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _strict_decimal(value: object, *, name: str, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"{name} must be an exact decimal")
    if not isinstance(value, (Decimal, int, str)):
        raise ValueError(f"{name} must be an exact decimal")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{name} must be an exact decimal") from error
    if not parsed.is_finite() or (positive and parsed <= 0):
        qualifier = "positive finite" if positive else "finite"
        raise ValueError(f"{name} must be {qualifier}")
    return parsed


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded exponential backoff policy for explicitly safe reads."""

    max_attempts: int = 3
    base_delay_seconds: Decimal | int | str = Decimal("0.25")
    max_delay_seconds: Decimal | int | str = Decimal("10")
    jitter_ratio: Decimal | int | str = Decimal("0.2")

    def __post_init__(self) -> None:
        max_attempts = _strict_positive_int(self.max_attempts, name="max_attempts")
        base = _strict_decimal(self.base_delay_seconds, name="base_delay_seconds", positive=True)
        maximum = _strict_decimal(self.max_delay_seconds, name="max_delay_seconds", positive=True)
        jitter = _strict_decimal(self.jitter_ratio, name="jitter_ratio")
        if maximum < base:
            raise ValueError("max_delay_seconds must be at least base_delay_seconds")
        if jitter < 0 or jitter > 1:
            raise ValueError("jitter_ratio must be between zero and one")
        object.__setattr__(self, "max_attempts", max_attempts)
        object.__setattr__(self, "base_delay_seconds", base)
        object.__setattr__(self, "max_delay_seconds", maximum)
        object.__setattr__(self, "jitter_ratio", jitter)


DEFAULT_RETRY_POLICY = RetryPolicy()


@dataclass(frozen=True, slots=True)
class RetryDecision:
    """Pure retry outcome; ambiguous writes can raise their typed terminal error."""

    action: RetryAction
    delay_seconds: Decimal | None
    reason: str
    operation: OperationClass

    def __post_init__(self) -> None:
        if not isinstance(self.action, RetryAction):
            raise ValueError("action must be a RetryAction")
        if not isinstance(self.operation, OperationClass):
            raise ValueError("operation must be an OperationClass")
        if type(self.reason) is not str or not self.reason:
            raise ValueError("reason must be a nonempty string")
        if self.action is RetryAction.RETRY:
            if not isinstance(self.delay_seconds, Decimal) or self.delay_seconds <= 0:
                raise ValueError("retry decisions require a positive Decimal delay")
            if self.operation is not OperationClass.SAFE_READ:
                raise ValueError("write operations cannot carry retry decisions")
        elif self.delay_seconds is not None:
            raise ValueError("terminal decisions cannot carry a retry delay")
        if self.action is RetryAction.AMBIGUOUS and self.operation is OperationClass.SAFE_READ:
            raise ValueError("safe reads cannot have ambiguous write outcomes")

    def raise_if_ambiguous(self) -> None:
        """Raise the normalized ambiguity error when reconciliation is required."""
        if self.action is RetryAction.AMBIGUOUS:
            raise AmbiguousExchangeResult(
                context={"operation": self.operation.value, "state": "unknown"}
            )


def _contains_ssl_error(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, ssl.SSLError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _safe_read_transient(error: BaseException | None) -> bool:
    return bool(
        error is not None
        and not _contains_ssl_error(error)
        and isinstance(
            error,
            (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ReadError),
        )
    )


def _fail(operation: OperationClass, reason: str) -> RetryDecision:
    return RetryDecision(
        action=RetryAction.FAIL,
        delay_seconds=None,
        reason=reason,
        operation=operation,
    )


def classify_retry(
    *,
    operation: OperationClass,
    attempt: int,
    status_code: int | None,
    reset_header: str | None,
    transport_error: BaseException | None,
    bytes_may_have_been_sent: bool,
    jitter_unit: Decimal | int | str,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> RetryDecision:
    """Classify retry safety without performing I/O or sleeping.

    ``attempt`` is one-based and counts the failed attempt. A three-attempt
    policy may retry failures from attempts one and two only.
    """
    if not isinstance(operation, OperationClass):
        raise ConfigurationError(context={"operation": "retry_classification"})
    attempt_number = _strict_positive_int(attempt, name="attempt")
    if status_code is not None and (
        isinstance(status_code, bool)
        or not isinstance(status_code, int)
        or status_code < 100
        or status_code > 599
    ):
        raise ValueError("status_code must be a valid HTTP status")
    if transport_error is not None and not isinstance(transport_error, BaseException):
        raise ValueError("transport_error must be an exception")
    if type(bytes_may_have_been_sent) is not bool:
        raise ValueError("bytes_may_have_been_sent must be boolean")
    if not isinstance(policy, RetryPolicy):
        raise ValueError("policy must be RetryPolicy")
    jitter = _strict_decimal(jitter_unit, name="jitter_unit")
    if jitter < 0 or jitter > 1:
        raise ValueError("jitter_unit must be between zero and one")

    if operation is not OperationClass.SAFE_READ:
        definitive = status_code is not None and (status_code < 500 or status_code == 429)
        uncertain_status = status_code is not None and 500 <= status_code <= 599
        uncertain_transport = isinstance(transport_error, httpx.TransportError)
        uncertain_cancellation = isinstance(transport_error, asyncio.CancelledError)
        if (
            bytes_may_have_been_sent
            and not definitive
            and (uncertain_status or uncertain_transport or uncertain_cancellation)
        ):
            return RetryDecision(
                action=RetryAction.AMBIGUOUS,
                delay_seconds=None,
                reason="write_outcome_unknown",
                operation=operation,
            )
        return _fail(operation, "write_not_retryable")

    if isinstance(transport_error, asyncio.CancelledError):
        return _fail(operation, "cancelled")
    if status_code is not None and status_code < 500 and status_code != 429:
        return _fail(operation, "definitive_http_response")
    retryable_status = status_code == 429 or (status_code is not None and 500 <= status_code <= 599)
    retryable_transport = _safe_read_transient(transport_error)
    if not retryable_status and not retryable_transport:
        return _fail(operation, "not_retryable")
    if attempt_number >= policy.max_attempts:
        return _fail(operation, "attempts_exhausted")

    base_delay = cast(Decimal, policy.base_delay_seconds)
    jitter_ratio = cast(Decimal, policy.jitter_ratio)
    maximum_delay = cast(Decimal, policy.max_delay_seconds)
    exponential = base_delay * (Decimal(2) ** (attempt_number - 1))
    jittered = exponential * (Decimal(1) + jitter_ratio * jitter)
    delay = min(maximum_delay, jittered)
    if status_code == 429 and reset_header is not None:
        reset_seconds = Decimal(parse_rate_limit_reset_ms(reset_header)) / Decimal(1000)
        delay = max(delay, reset_seconds)
    return RetryDecision(
        action=RetryAction.RETRY,
        delay_seconds=delay,
        reason="safe_read_transient",
        operation=operation,
    )


__all__ = [
    "DEFAULT_RETRY_POLICY",
    "OperationClass",
    "RetryAction",
    "RetryDecision",
    "RetryPolicy",
    "classify_retry",
]
