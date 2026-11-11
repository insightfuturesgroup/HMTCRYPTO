from datetime import UTC, datetime, timedelta

from hmt_crypto.qualification import _continuous


def test_continuity_requires_window_edges_and_bounded_gaps() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=24)
    observations = [start + timedelta(seconds=30 * index) for index in range(2881)]

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
