"""Pure UTC clock evidence and future signed-request readiness assessment."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from email.utils import format_datetime, parsedate_to_datetime
from enum import StrEnum
from typing import Any, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    StrictBool,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import InitErrorDetails

from hmt_crypto.domain.money import ExactDecimal, UtcDateTime, parse_decimal, parse_utc_datetime


class SignedProbeOutcome(StrEnum):
    """Normalized outcome of the future startup signed-private-read probe."""

    OK = "ok"
    NOT_ATTEMPTED = "not_attempted"
    SIGNATURE_EXPIRED = "signature_expired"
    UNAUTHORIZED = "unauthorized"
    TRANSPORT_FAILURE = "transport_failure"
    PROTOCOL_FAILURE = "protocol_failure"


class ClockReadinessReason(StrEnum):
    """Stable, operator-safe reasons why future write prerequisites are unmet."""

    LOCAL_CLOCK_UNSYNCHRONIZED = "local_clock_unsynchronized"
    LOCAL_CLOCK_SYNC_UNKNOWN = "local_clock_sync_unknown"
    CLOCK_SKEW_UNAVAILABLE = "clock_skew_unavailable"
    CLOCK_SKEW_EXCEEDED = "clock_skew_exceeded"
    SIGNED_PROBE_NOT_ATTEMPTED = "signed_probe_not_attempted"
    SIGNED_PROBE_SIGNATURE_EXPIRED = "signed_probe_signature_expired_check_clock"
    SIGNED_PROBE_UNAUTHORIZED = "signed_probe_unauthorized"
    SIGNED_PROBE_TRANSPORT_FAILURE = "signed_probe_transport_failure"
    SIGNED_PROBE_PROTOCOL_FAILURE = "signed_probe_protocol_failure"


class _ClockModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    @model_validator(mode="wrap")
    @classmethod
    def redact_validation_inputs(
        cls, values: Any, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        try:
            return handler(values)
        except ValidationError as error:
            details = cast(list[InitErrorDetails], error.errors(include_input=False))
            raise ValidationError.from_exception_data(
                cls.__name__, details, hide_input=True
            ) from None


class LocalClockDiagnostics(_ClockModel):
    """Injectable OS/NTP synchronization evidence observed at a UTC instant."""

    synchronized: StrictBool | None
    offset_seconds: ExactDecimal | None
    observed_at: UtcDateTime
    source: str = Field(min_length=1, max_length=80)

    @field_validator("source", mode="before")
    @classmethod
    def validate_source(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("clock diagnostic source must be nonempty text")
        return value.strip()


class HttpDateObservation(_ClockModel):
    """Server HTTP Date evidence adjusted by the local request midpoint."""

    server_time: UtcDateTime
    request_started_at: UtcDateTime
    response_received_at: UtcDateTime
    request_midpoint: UtcDateTime
    round_trip_seconds: ExactDecimal
    estimated_skew_seconds: ExactDecimal


class ClockReadiness(_ClockModel):
    """Fail-closed readiness result; this is not runtime order authorization."""

    checked_at: UtcDateTime
    max_skew_seconds: ExactDecimal
    effective_skew_seconds: ExactDecimal | None
    clock_ready: bool
    signed_probe_ready: bool
    order_write_prerequisites_met: bool
    signed_probe_outcome: SignedProbeOutcome
    reasons: tuple[ClockReadinessReason, ...]
    time_endpoint_documented: bool = False


def _decimal_seconds(value: timedelta) -> Decimal:
    total_microseconds = (value.days * 86_400 + value.seconds) * 1_000_000 + value.microseconds
    return Decimal(total_microseconds) / Decimal(1_000_000)


def observe_http_date(
    header_value: str,
    *,
    request_started_at: object,
    response_received_at: object,
) -> HttpDateObservation:
    """Parse a strict HTTP Date and estimate server skew at request midpoint."""
    if not isinstance(header_value, str):
        raise ValueError("HTTP Date must be IMF-fixdate text")
    try:
        server_time = parsedate_to_datetime(header_value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("HTTP Date must be valid IMF-fixdate text") from error
    if server_time is None or server_time.tzinfo is None:
        raise ValueError("HTTP Date must include GMT timezone")
    server_time = parse_utc_datetime(server_time)
    if format_datetime(server_time, usegmt=True) != header_value:
        raise ValueError("HTTP Date must use canonical IMF-fixdate format")
    started = parse_utc_datetime(request_started_at)
    received = parse_utc_datetime(response_received_at)
    if received < started:
        raise ValueError("HTTP response time cannot precede request time")
    duration = received - started
    round_trip = _decimal_seconds(duration)
    midpoint = started + duration / 2
    skew = _decimal_seconds(server_time - started) - round_trip / Decimal(2)
    return HttpDateObservation(
        server_time=server_time,
        request_started_at=started,
        response_received_at=received,
        request_midpoint=midpoint,
        round_trip_seconds=round_trip,
        estimated_skew_seconds=skew,
    )


_PROBE_REASON: dict[SignedProbeOutcome, ClockReadinessReason] = {
    SignedProbeOutcome.NOT_ATTEMPTED: ClockReadinessReason.SIGNED_PROBE_NOT_ATTEMPTED,
    SignedProbeOutcome.SIGNATURE_EXPIRED: (ClockReadinessReason.SIGNED_PROBE_SIGNATURE_EXPIRED),
    SignedProbeOutcome.UNAUTHORIZED: ClockReadinessReason.SIGNED_PROBE_UNAUTHORIZED,
    SignedProbeOutcome.TRANSPORT_FAILURE: ClockReadinessReason.SIGNED_PROBE_TRANSPORT_FAILURE,
    SignedProbeOutcome.PROTOCOL_FAILURE: ClockReadinessReason.SIGNED_PROBE_PROTOCOL_FAILURE,
}


def assess_clock_readiness(
    *,
    local: LocalClockDiagnostics,
    signed_probe_outcome: SignedProbeOutcome,
    max_skew_seconds: object,
    checked_at: object,
    http_date_observation: HttpDateObservation | None = None,
) -> ClockReadiness:
    """Assess future write prerequisites from injected clock and signed-read evidence."""
    maximum = parse_decimal(max_skew_seconds)
    if maximum <= 0:
        raise ValueError("max_skew_seconds must be a positive finite decimal")
    checked = parse_utc_datetime(checked_at)
    if not isinstance(local, LocalClockDiagnostics):
        raise ValueError("local clock diagnostics are required")
    if not isinstance(signed_probe_outcome, SignedProbeOutcome):
        raise ValueError("signed_probe_outcome must be a supported outcome")
    if http_date_observation is not None and not isinstance(
        http_date_observation, HttpDateObservation
    ):
        raise ValueError("http_date_observation must be validated evidence")

    reasons: list[ClockReadinessReason] = []
    if local.synchronized is False:
        reasons.append(ClockReadinessReason.LOCAL_CLOCK_UNSYNCHRONIZED)
    elif local.synchronized is None:
        reasons.append(ClockReadinessReason.LOCAL_CLOCK_SYNC_UNKNOWN)

    skew_evidence = [value for value in (local.offset_seconds,) if value is not None]
    if http_date_observation is not None:
        skew_evidence.append(http_date_observation.estimated_skew_seconds)
    effective_skew = max(skew_evidence, key=abs) if skew_evidence else None
    if effective_skew is None:
        reasons.append(ClockReadinessReason.CLOCK_SKEW_UNAVAILABLE)
    elif abs(effective_skew) > maximum:
        reasons.append(ClockReadinessReason.CLOCK_SKEW_EXCEEDED)

    if signed_probe_outcome is not SignedProbeOutcome.OK:
        reasons.append(_PROBE_REASON[signed_probe_outcome])
    clock_ready = (
        local.synchronized is True
        and effective_skew is not None
        and abs(effective_skew) <= maximum
        and signed_probe_outcome is not SignedProbeOutcome.SIGNATURE_EXPIRED
    )
    signed_probe_ready = signed_probe_outcome is SignedProbeOutcome.OK
    return ClockReadiness(
        checked_at=checked,
        max_skew_seconds=maximum,
        effective_skew_seconds=effective_skew,
        clock_ready=clock_ready,
        signed_probe_ready=signed_probe_ready,
        order_write_prerequisites_met=clock_ready and signed_probe_ready,
        signed_probe_outcome=signed_probe_outcome,
        reasons=tuple(reasons),
        time_endpoint_documented=False,
    )


__all__ = [
    "ClockReadiness",
    "ClockReadinessReason",
    "HttpDateObservation",
    "LocalClockDiagnostics",
    "SignedProbeOutcome",
    "assess_clock_readiness",
    "observe_http_date",
]
