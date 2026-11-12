from datetime import UTC, datetime, timedelta

from hmt_crypto.live_qualification_policy import (
    LIVE_QUALIFICATION_WINDOW,
    LIVE_QUALIFICATION_WINDOW_HOURS,
    qualification_window_is_complete,
)
from hmt_crypto.qualification import _continuous


def test_live_qualification_window_is_six_hours() -> None:
    assert LIVE_QUALIFICATION_WINDOW_HOURS == 6
    assert LIVE_QUALIFICATION_WINDOW == timedelta(hours=6)


def test_live_qualification_window_rejects_five_hours_fifty_nine_minutes() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)

    assert not qualification_window_is_complete(
        started_at=start,
        completed_at=start + timedelta(hours=5, minutes=59),
    )


def test_live_qualification_window_accepts_six_hours() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)

    assert qualification_window_is_complete(
        started_at=start,
        completed_at=start + timedelta(hours=6),
    )


def test_continuity_requires_window_edges_and_bounded_gaps() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + LIVE_QUALIFICATION_WINDOW
    observations = [start + timedelta(seconds=30 * index) for index in range(721)]

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
