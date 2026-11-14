from datetime import UTC, datetime, timedelta

from hmt_crypto.live_qualification_policy import (
    APPROVED_LIVE_RISK_SCOPE,
    LIVE_QUALIFICATION_WINDOW,
    LIVE_QUALIFICATION_WINDOW_HOURS,
    LIVE_RECONCILIATION_EVIDENCE_INTERVAL_SECONDS,
    LIVE_RECONCILIATION_MAX_GAP_SECONDS,
    canonical_risk_policy_scope,
    qualification_window_is_complete,
    risk_scope_is_approved,
)
from hmt_crypto.qualification import _continuous


def test_live_qualification_window_is_one_hour() -> None:
    assert LIVE_QUALIFICATION_WINDOW_HOURS == 1
    assert LIVE_QUALIFICATION_WINDOW == timedelta(hours=1)


def test_reconciliation_evidence_cadence_fits_continuity_policy() -> None:
    assert LIVE_RECONCILIATION_EVIDENCE_INTERVAL_SECONDS == 30
    assert LIVE_RECONCILIATION_MAX_GAP_SECONDS == 60
    assert (
        LIVE_RECONCILIATION_EVIDENCE_INTERVAL_SECONDS
        < LIVE_RECONCILIATION_MAX_GAP_SECONDS
    )


def test_live_qualification_window_rejects_fifty_nine_minutes() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)

    assert not qualification_window_is_complete(
        started_at=start,
        completed_at=start + timedelta(minutes=59),
    )


def test_live_qualification_window_accepts_one_hour() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)

    assert qualification_window_is_complete(
        started_at=start,
        completed_at=start + timedelta(hours=1),
    )


def test_continuity_requires_window_edges_and_bounded_gaps() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + LIVE_QUALIFICATION_WINDOW
    observations = [start + timedelta(seconds=30 * index) for index in range(121)]

    assert _continuous(observations, start=start, end=end, gap=45)
    assert not _continuous(observations[2:], start=start, end=end, gap=45)
    assert not _continuous(observations[:-2], start=start, end=end, gap=45)


def test_continuity_rejects_an_internal_gap() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(minutes=3)
    observations = [
        start,
        start + timedelta(seconds=30),
        start + timedelta(seconds=120),
        end,
    ]

    assert not _continuous(observations, start=start, end=end, gap=45)


def test_approved_risk_scope_uses_semantic_decimal_values() -> None:
    scope = canonical_risk_policy_scope(
        enabled=True,
        limit_currency="inr",
        usd_inr_rate="100.000000",
        max_order_notional="50.000000000000000000",
        max_position_notional="100.0",
        max_daily_loss="20.00",
        max_open_orders=5,
        max_inflight_signals=5,
    )

    assert scope == APPROVED_LIVE_RISK_SCOPE
    assert risk_scope_is_approved(scope)


def test_changed_risk_scope_requires_requalification() -> None:
    scope = canonical_risk_policy_scope(
        enabled=True,
        limit_currency="INR",
        usd_inr_rate="100",
        max_order_notional="51",
        max_position_notional="100",
        max_daily_loss="20",
        max_open_orders=5,
        max_inflight_signals=5,
    )

    assert not risk_scope_is_approved(scope)
