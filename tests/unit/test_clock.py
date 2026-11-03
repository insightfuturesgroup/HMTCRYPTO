"""Pure UTC clock evidence and signed-probe readiness contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib import import_module
from typing import Any

import pytest
from pydantic import ValidationError


def clock_api() -> Any:
    """Load the wished-for clock API without turning RED into collection failure."""
    try:
        return import_module("hmt_crypto.clock")
    except ModuleNotFoundError:
        pytest.fail("clock-readiness API is missing", pytrace=False)


NOW = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)


def local_clock(
    *, synchronized: bool | None = True, offset: object = "0.20", observed_at: object = NOW
) -> Any:
    api = clock_api()
    return api.LocalClockDiagnostics(
        synchronized=synchronized,
        offset_seconds=offset,
        observed_at=observed_at,
        source="unit-test-ntp",
    )


def signed_probe(*, outcome: str = "ok", observed_at: object = NOW) -> Any:
    api = clock_api()
    evidence_type = getattr(api, "SignedProbeEvidence", None)
    if evidence_type is None:
        pytest.fail("timestamped signed-probe evidence API is missing", pytrace=False)
    return evidence_type(outcome=api.SignedProbeOutcome(outcome), observed_at=observed_at)


def assess_with_timestamped_evidence(
    *,
    local: Any | None = None,
    probe: Any | None = None,
    http: Any | None = None,
    checked_at: object = NOW,
    max_age: object = "5",
) -> Any:
    api = clock_api()
    return api.assess_clock_readiness(
        local=local if local is not None else local_clock(),
        signed_probe_evidence=probe if probe is not None else signed_probe(),
        max_skew_seconds="1",
        max_evidence_age_seconds=max_age,
        http_date_observation=http,
        checked_at=checked_at,
    )


def test_http_date_observation_uses_request_midpoint_and_utc() -> None:
    api = clock_api()
    observation = api.observe_http_date(
        "Tue, 03 Nov 2026 10:00:00 GMT",
        request_started_at=NOW - timedelta(milliseconds=500),
        response_received_at=NOW + timedelta(milliseconds=500),
    )
    assert observation.server_time == NOW
    assert observation.request_midpoint == NOW
    assert observation.round_trip_seconds == Decimal("1")
    assert observation.estimated_skew_seconds == Decimal("0")


def test_http_date_skew_preserves_sign_and_exact_microseconds() -> None:
    observation = clock_api().observe_http_date(
        "Tue, 03 Nov 2026 10:00:01 GMT",
        request_started_at=NOW - timedelta(milliseconds=250),
        response_received_at=NOW + timedelta(milliseconds=250),
    )
    assert observation.estimated_skew_seconds == Decimal("1")
    assert observation.round_trip_seconds == Decimal("0.5")


@pytest.mark.parametrize(
    ("header", "start", "end"),
    [
        ("not-a-date", NOW, NOW),
        ("Tue, 03 Nov 2026 10:00:00 GMT", NOW.replace(tzinfo=None), NOW),
        ("Tue, 03 Nov 2026 10:00:00 GMT", NOW, NOW.replace(tzinfo=None)),
        ("Tue, 03 Nov 2026 10:00:00 GMT", NOW, NOW - timedelta(microseconds=1)),
        ("Tue, 03 Nov 2026 10:00:00 GMT", 1_794_000_000_000, 1_794_000_001_000),
    ],
)
def test_http_date_observation_rejects_invalid_or_ambiguous_times(
    header: str, start: object, end: object
) -> None:
    with pytest.raises((ValueError, ValidationError)):
        clock_api().observe_http_date(header, request_started_at=start, response_received_at=end)


@pytest.mark.parametrize(
    ("offset", "observed_at"),
    [
        (0.1, NOW),
        (True, NOW),
        ("NaN", NOW),
        ("Infinity", NOW),
        ("0.1", NOW.replace(tzinfo=None)),
        ("0.1", 1_794_000_000_000),
    ],
)
def test_local_diagnostics_reject_binary_float_nonfinite_or_ambiguous_time(
    offset: object, observed_at: object
) -> None:
    with pytest.raises(ValidationError):
        local_clock(offset=offset, observed_at=observed_at)


def test_local_diagnostics_normalize_explicit_offset_and_epoch_seconds_to_utc() -> None:
    first = local_clock(observed_at="2026-11-03T15:30:00+05:30")
    second = local_clock(observed_at=int(NOW.timestamp()))
    assert first.observed_at == NOW
    assert second.observed_at == NOW


def test_ready_requires_positive_sync_bounded_skew_and_successful_signed_probe() -> None:
    api = clock_api()
    observation = api.observe_http_date(
        "Tue, 03 Nov 2026 10:00:00 GMT",
        request_started_at=NOW - timedelta(milliseconds=100),
        response_received_at=NOW + timedelta(milliseconds=100),
    )
    result = api.assess_clock_readiness(
        local=local_clock(offset="0.20"),
        signed_probe_evidence=signed_probe(observed_at=NOW + timedelta(milliseconds=100)),
        max_skew_seconds="1",
        max_evidence_age_seconds="5",
        http_date_observation=observation,
        checked_at=NOW + timedelta(milliseconds=100),
    )
    assert result.clock_ready is True
    assert result.signed_probe_ready is True
    assert result.order_write_prerequisites_met is True
    assert result.effective_skew_seconds == Decimal("0.20")
    assert result.reasons == ()
    assert result.time_endpoint_documented is False


@pytest.mark.parametrize(
    ("synchronized", "expected_reason"),
    [(False, "local_clock_unsynchronized"), (None, "local_clock_sync_unknown")],
)
def test_local_sync_must_be_positively_known(
    synchronized: bool | None, expected_reason: str
) -> None:
    api = clock_api()
    result = api.assess_clock_readiness(
        local=local_clock(synchronized=synchronized),
        signed_probe_evidence=signed_probe(),
        max_skew_seconds="1",
        max_evidence_age_seconds="5",
        checked_at=NOW,
    )
    assert result.order_write_prerequisites_met is False
    assert expected_reason in {reason.value for reason in result.reasons}


def test_numeric_skew_evidence_is_required_even_when_os_reports_sync() -> None:
    api = clock_api()
    result = api.assess_clock_readiness(
        local=local_clock(offset=None),
        signed_probe_evidence=signed_probe(),
        max_skew_seconds="1",
        max_evidence_age_seconds="5",
        checked_at=NOW,
    )
    assert result.clock_ready is False
    assert "clock_skew_unavailable" in {reason.value for reason in result.reasons}


@pytest.mark.parametrize(
    ("offset", "ready"), [("1", True), ("-1", True), ("1.000001", False), ("-1.1", False)]
)
def test_maximum_skew_boundary_is_inclusive(offset: str, ready: bool) -> None:
    api = clock_api()
    result = api.assess_clock_readiness(
        local=local_clock(offset=offset),
        signed_probe_evidence=signed_probe(),
        max_skew_seconds="1",
        max_evidence_age_seconds="5",
        checked_at=NOW,
    )
    assert result.clock_ready is ready
    assert result.order_write_prerequisites_met is ready


def test_conservative_strongest_observation_fails_when_http_date_exceeds_policy() -> None:
    api = clock_api()
    observation = api.observe_http_date(
        "Tue, 03 Nov 2026 10:00:02 GMT",
        request_started_at=NOW,
        response_received_at=NOW,
    )
    result = api.assess_clock_readiness(
        local=local_clock(offset="0.1"),
        signed_probe_evidence=signed_probe(),
        max_skew_seconds="1",
        max_evidence_age_seconds="5",
        http_date_observation=observation,
        checked_at=NOW,
    )
    assert result.effective_skew_seconds == Decimal("2")
    assert result.clock_ready is False
    assert "clock_skew_exceeded" in {reason.value for reason in result.reasons}


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        ("not_attempted", "signed_probe_not_attempted"),
        ("signature_expired", "signed_probe_signature_expired_check_clock"),
        ("unauthorized", "signed_probe_unauthorized"),
        ("transport_failure", "signed_probe_transport_failure"),
        ("protocol_failure", "signed_probe_protocol_failure"),
    ],
)
def test_every_unsuccessful_signed_probe_fails_closed_without_conflating_auth_and_clock(
    outcome: str, reason: str
) -> None:
    api = clock_api()
    result = api.assess_clock_readiness(
        local=local_clock(offset="0.1"),
        signed_probe_evidence=signed_probe(outcome=outcome),
        max_skew_seconds="1",
        max_evidence_age_seconds="5",
        checked_at=NOW,
    )
    reason_values = {item.value for item in result.reasons}
    assert result.signed_probe_ready is False
    assert result.order_write_prerequisites_met is False
    assert reason in reason_values
    if outcome == "unauthorized":
        assert not any("clock" in item for item in reason_values)
    if outcome == "signature_expired":
        assert result.clock_ready is False


@pytest.mark.parametrize("maximum", [0.1, True, "0", "-1", "NaN", "Infinity"])
def test_maximum_skew_policy_rejects_float_bool_nonpositive_or_nonfinite(maximum: object) -> None:
    api = clock_api()
    with pytest.raises(ValueError):
        api.assess_clock_readiness(
            local=local_clock(),
            signed_probe_evidence=signed_probe(),
            max_skew_seconds=maximum,
            max_evidence_age_seconds="5",
            checked_at=NOW,
        )


def test_readiness_rejects_millisecond_looking_checked_at() -> None:
    api = clock_api()
    with pytest.raises(ValueError):
        api.assess_clock_readiness(
            local=local_clock(),
            signed_probe_evidence=signed_probe(),
            max_skew_seconds="1",
            max_evidence_age_seconds="5",
            checked_at=1_794_000_000_000,
        )


def test_readiness_result_and_evidence_are_frozen() -> None:
    api = clock_api()
    result = api.assess_clock_readiness(
        local=local_clock(),
        signed_probe_evidence=signed_probe(),
        max_skew_seconds="1",
        max_evidence_age_seconds="5",
        checked_at=NOW,
    )
    with pytest.raises(ValidationError):
        result.clock_ready = False
    with pytest.raises(ValidationError):
        result.reasons = ()


@pytest.mark.parametrize(
    ("evidence", "reason"),
    [
        ("local_stale", "local_clock_evidence_stale"),
        ("local_future", "local_clock_evidence_future"),
        ("http_stale", "http_date_evidence_stale"),
        ("http_future", "http_date_evidence_future"),
        ("probe_stale", "signed_probe_evidence_stale"),
        ("probe_future", "signed_probe_evidence_future"),
    ],
)
def test_future_or_stale_evidence_fails_closed(evidence: str, reason: str) -> None:
    local = local_clock()
    probe = signed_probe()
    http = None
    if evidence == "local_stale":
        local = local_clock(observed_at=NOW - timedelta(seconds=6))
    elif evidence == "local_future":
        local = local_clock(observed_at=NOW + timedelta(microseconds=1))
    elif evidence.startswith("http_"):
        shift = timedelta(seconds=-6 if evidence == "http_stale" else 1)
        observed_at = NOW + shift
        http = clock_api().observe_http_date(
            observed_at.strftime("%a, %d %b %Y %H:%M:%S GMT"),
            request_started_at=observed_at,
            response_received_at=observed_at,
        )
    elif evidence == "probe_stale":
        probe = signed_probe(observed_at=NOW - timedelta(seconds=6))
    else:
        probe = signed_probe(observed_at=NOW + timedelta(microseconds=1))
    result = assess_with_timestamped_evidence(local=local, probe=probe, http=http)
    assert result.order_write_prerequisites_met is False
    assert reason in {item.value for item in result.reasons}


@pytest.mark.parametrize(
    ("age", "ready"),
    [(timedelta(seconds=5), True), (timedelta(seconds=5, microseconds=1), False)],
)
def test_evidence_age_boundary_is_inclusive(age: timedelta, ready: bool) -> None:
    result = assess_with_timestamped_evidence(
        local=local_clock(observed_at=NOW - age),
        probe=signed_probe(observed_at=NOW - age),
    )
    assert result.order_write_prerequisites_met is ready


@pytest.mark.parametrize("maximum", [0.1, True, "0", "-1", "NaN", "Infinity"])
def test_maximum_evidence_age_rejects_float_bool_nonpositive_or_nonfinite(
    maximum: object,
) -> None:
    with pytest.raises(ValueError):
        assess_with_timestamped_evidence(max_age=maximum)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_midpoint", NOW + timedelta(seconds=1)),
        ("round_trip_seconds", Decimal("99")),
        ("estimated_skew_seconds", Decimal("0.25")),
    ],
)
def test_http_date_observation_rejects_fabricated_derived_fields(field: str, value: object) -> None:
    api = clock_api()
    valid = api.observe_http_date(
        "Tue, 03 Nov 2026 10:00:00 GMT",
        request_started_at=NOW,
        response_received_at=NOW,
    )
    payload = valid.model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        api.HttpDateObservation(**payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("signed_probe_ready", False),
        ("order_write_prerequisites_met", False),
        ("clock_ready", False),
        ("time_endpoint_documented", True),
    ],
)
def test_readiness_rejects_contradictory_direct_construction(field: str, value: object) -> None:
    result = assess_with_timestamped_evidence()
    payload = result.model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        clock_api().ClockReadiness(**payload)


def test_readiness_rejects_success_boolean_with_not_attempted_probe() -> None:
    result = assess_with_timestamped_evidence()
    payload = result.model_dump()
    payload["signed_probe_evidence"] = {
        "outcome": "not_attempted",
        "observed_at": NOW,
    }
    with pytest.raises(ValidationError):
        clock_api().ClockReadiness(**payload)
