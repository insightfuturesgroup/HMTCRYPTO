"""Pure UTC clock evidence and future signed-request readiness assessment."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from email.utils import format_datetime, parsedate_to_datetime
from enum import StrEnum
from typing import Any, Literal, NamedTuple, Self, cast

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
    LOCAL_CLOCK_EVIDENCE_STALE = "local_clock_evidence_stale"
    LOCAL_CLOCK_EVIDENCE_FUTURE = "local_clock_evidence_future"
    HTTP_DATE_EVIDENCE_STALE = "http_date_evidence_stale"
    HTTP_DATE_EVIDENCE_FUTURE = "http_date_evidence_future"
    SIGNED_PROBE_EVIDENCE_STALE = "signed_probe_evidence_stale"
    SIGNED_PROBE_EVIDENCE_FUTURE = "signed_probe_evidence_future"
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


class SignedProbeEvidence(_ClockModel):
    """Timestamped normalized result of one bounded signed private read."""

    outcome: SignedProbeOutcome
    observed_at: UtcDateTime


class HttpDateObservation(_ClockModel):
    """Server HTTP Date evidence adjusted by the local request midpoint."""

    server_time: UtcDateTime
    request_started_at: UtcDateTime
    response_received_at: UtcDateTime
    request_midpoint: UtcDateTime
    round_trip_seconds: ExactDecimal
    estimated_skew_seconds: ExactDecimal

    @model_validator(mode="after")
    def validate_derived_fields(self) -> Self:
        """Reject fabricated midpoint, duration, or skew evidence."""
        if self.response_received_at < self.request_started_at:
            raise ValueError("HTTP response time cannot precede request time")
        duration = self.response_received_at - self.request_started_at
        expected_round_trip = _decimal_seconds(duration)
        expected_midpoint = self.request_started_at + duration / 2
        expected_skew = _decimal_seconds(
            self.server_time - self.request_started_at
        ) - expected_round_trip / Decimal(2)
        if (
            self.round_trip_seconds != expected_round_trip
            or self.request_midpoint != expected_midpoint
            or self.estimated_skew_seconds != expected_skew
        ):
            raise ValueError("HTTP Date derived evidence is inconsistent")
        return self


class ClockReadiness(_ClockModel):
    """Fail-closed readiness result; this is not runtime order authorization."""

    checked_at: UtcDateTime
    max_skew_seconds: ExactDecimal
    max_evidence_age_seconds: ExactDecimal
    local_clock_diagnostics: LocalClockDiagnostics
    http_date_observation: HttpDateObservation | None
    signed_probe_evidence: SignedProbeEvidence
    effective_skew_seconds: ExactDecimal | None
    clock_ready: bool
    signed_probe_ready: bool
    order_write_prerequisites_met: bool
    reasons: tuple[ClockReadinessReason, ...]
    time_endpoint_documented: Literal[False] = False

    @property
    def signed_probe_outcome(self) -> SignedProbeOutcome:
        """Return the normalized outcome attached to the timestamped probe."""
        return self.signed_probe_evidence.outcome

    @model_validator(mode="after")
    def validate_readiness_relationships(self) -> Self:
        """Reject directly constructed results that contradict their evidence."""
        if self.max_skew_seconds <= 0 or self.max_evidence_age_seconds <= 0:
            raise ValueError("readiness policies must be positive finite decimals")
        derived = _derive_readiness(
            local=self.local_clock_diagnostics,
            signed_probe=self.signed_probe_evidence,
            maximum_skew=self.max_skew_seconds,
            maximum_age=self.max_evidence_age_seconds,
            checked=self.checked_at,
            http_observation=self.http_date_observation,
        )
        actual = _DerivedReadiness(
            self.effective_skew_seconds,
            self.clock_ready,
            self.signed_probe_ready,
            self.order_write_prerequisites_met,
            self.reasons,
        )
        if actual != derived:
            raise ValueError("clock readiness fields contradict supplied evidence")
        return self


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


class _DerivedReadiness(NamedTuple):
    effective_skew_seconds: Decimal | None
    clock_ready: bool
    signed_probe_ready: bool
    order_write_prerequisites_met: bool
    reasons: tuple[ClockReadinessReason, ...]


def _temporal_reason(
    *,
    earliest: object,
    latest: object,
    checked: object,
    maximum_age: Decimal,
    stale_reason: ClockReadinessReason,
    future_reason: ClockReadinessReason,
) -> ClockReadinessReason | None:
    earliest_time = parse_utc_datetime(earliest)
    latest_time = parse_utc_datetime(latest)
    checked_time = parse_utc_datetime(checked)
    if earliest_time > latest_time:
        return future_reason
    if latest_time > checked_time:
        return future_reason
    if _decimal_seconds(checked_time - earliest_time) > maximum_age:
        return stale_reason
    return None


def _derive_readiness(
    *,
    local: LocalClockDiagnostics,
    signed_probe: SignedProbeEvidence,
    maximum_skew: Decimal,
    maximum_age: Decimal,
    checked: object,
    http_observation: HttpDateObservation | None,
) -> _DerivedReadiness:
    reasons: list[ClockReadinessReason] = []
    if local.synchronized is False:
        reasons.append(ClockReadinessReason.LOCAL_CLOCK_UNSYNCHRONIZED)
    elif local.synchronized is None:
        reasons.append(ClockReadinessReason.LOCAL_CLOCK_SYNC_UNKNOWN)

    local_time_reason = _temporal_reason(
        earliest=local.observed_at,
        latest=local.observed_at,
        checked=checked,
        maximum_age=maximum_age,
        stale_reason=ClockReadinessReason.LOCAL_CLOCK_EVIDENCE_STALE,
        future_reason=ClockReadinessReason.LOCAL_CLOCK_EVIDENCE_FUTURE,
    )
    if local_time_reason is not None:
        reasons.append(local_time_reason)

    http_time_reason: ClockReadinessReason | None = None
    if http_observation is not None:
        http_time_reason = _temporal_reason(
            earliest=http_observation.request_started_at,
            latest=http_observation.response_received_at,
            checked=checked,
            maximum_age=maximum_age,
            stale_reason=ClockReadinessReason.HTTP_DATE_EVIDENCE_STALE,
            future_reason=ClockReadinessReason.HTTP_DATE_EVIDENCE_FUTURE,
        )
        if http_time_reason is not None:
            reasons.append(http_time_reason)

    skew_evidence = [value for value in (local.offset_seconds,) if value is not None]
    if http_observation is not None:
        skew_evidence.append(http_observation.estimated_skew_seconds)
    effective_skew = max(skew_evidence, key=abs) if skew_evidence else None
    if effective_skew is None:
        reasons.append(ClockReadinessReason.CLOCK_SKEW_UNAVAILABLE)
    elif abs(effective_skew) > maximum_skew:
        reasons.append(ClockReadinessReason.CLOCK_SKEW_EXCEEDED)

    if signed_probe.outcome is not SignedProbeOutcome.OK:
        reasons.append(_PROBE_REASON[signed_probe.outcome])
    probe_time_reason = _temporal_reason(
        earliest=signed_probe.observed_at,
        latest=signed_probe.observed_at,
        checked=checked,
        maximum_age=maximum_age,
        stale_reason=ClockReadinessReason.SIGNED_PROBE_EVIDENCE_STALE,
        future_reason=ClockReadinessReason.SIGNED_PROBE_EVIDENCE_FUTURE,
    )
    if probe_time_reason is not None:
        reasons.append(probe_time_reason)

    clock_ready = (
        local.synchronized is True
        and local_time_reason is None
        and http_time_reason is None
        and effective_skew is not None
        and abs(effective_skew) <= maximum_skew
        and signed_probe.outcome is not SignedProbeOutcome.SIGNATURE_EXPIRED
    )
    signed_probe_ready = signed_probe.outcome is SignedProbeOutcome.OK and probe_time_reason is None
    return _DerivedReadiness(
        effective_skew,
        clock_ready,
        signed_probe_ready,
        clock_ready and signed_probe_ready,
        tuple(reasons),
    )


def assess_clock_readiness(
    *,
    local: LocalClockDiagnostics,
    signed_probe_evidence: SignedProbeEvidence,
    max_skew_seconds: object,
    max_evidence_age_seconds: object,
    checked_at: object,
    http_date_observation: HttpDateObservation | None = None,
) -> ClockReadiness:
    """Assess future write prerequisites from injected clock and signed-read evidence."""
    maximum = parse_decimal(max_skew_seconds)
    if maximum <= 0:
        raise ValueError("max_skew_seconds must be a positive finite decimal")
    maximum_age = parse_decimal(max_evidence_age_seconds)
    if maximum_age <= 0:
        raise ValueError("max_evidence_age_seconds must be a positive finite decimal")
    checked = parse_utc_datetime(checked_at)
    if not isinstance(local, LocalClockDiagnostics):
        raise ValueError("local clock diagnostics are required")
    if not isinstance(signed_probe_evidence, SignedProbeEvidence):
        raise ValueError("timestamped signed_probe_evidence is required")
    if http_date_observation is not None and not isinstance(
        http_date_observation, HttpDateObservation
    ):
        raise ValueError("http_date_observation must be validated evidence")

    derived = _derive_readiness(
        local=local,
        signed_probe=signed_probe_evidence,
        maximum_skew=maximum,
        maximum_age=maximum_age,
        checked=checked,
        http_observation=http_date_observation,
    )
    return ClockReadiness(
        checked_at=checked,
        max_skew_seconds=maximum,
        max_evidence_age_seconds=maximum_age,
        local_clock_diagnostics=local,
        http_date_observation=http_date_observation,
        signed_probe_evidence=signed_probe_evidence,
        effective_skew_seconds=derived.effective_skew_seconds,
        clock_ready=derived.clock_ready,
        signed_probe_ready=derived.signed_probe_ready,
        order_write_prerequisites_met=derived.order_write_prerequisites_met,
        reasons=derived.reasons,
        time_endpoint_documented=False,
    )


__all__ = [
    "ClockReadiness",
    "ClockReadinessReason",
    "HttpDateObservation",
    "LocalClockDiagnostics",
    "SignedProbeEvidence",
    "SignedProbeOutcome",
    "assess_clock_readiness",
    "observe_http_date",
]
