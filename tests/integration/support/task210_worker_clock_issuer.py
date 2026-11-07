"""Marker-gated test adapter for the real Task 2.10 owner seams."""

from __future__ import annotations

from typing import Any

import pytest

from hmt_crypto.worker import clock, leadership

_FIXTURE_MARKER = object()
_LEADERSHIP_MARKER = leadership._task210_fixture_marker_for_tests()
_CLOCK_MARKER = clock._task210_fixture_marker_for_tests()


@pytest.fixture
def task210_worker_clock_marker() -> object:
    """Return the exact source-tree fixture marker accepted by this adapter."""
    return _FIXTURE_MARKER


def _worker_marker(marker: object) -> object:
    return _LEADERSHIP_MARKER if marker is _FIXTURE_MARKER else marker


def _clock_marker(marker: object) -> object:
    return _CLOCK_MARKER if marker is _FIXTURE_MARKER else marker


def issue_single_private_worker(marker: object, **values: Any) -> tuple[object, object]:
    """Issue one real sealed worker proof and currentness owner for tests."""
    return leadership._issue_single_private_worker_for_tests(
        _worker_marker(marker),
        **values,
    )


def advance_worker_observed_at(
    marker: object,
    currentness: object,
    observed_at: object,
) -> None:
    """Advance the owner-published UTC snapshot without reading wall time."""
    leadership._advance_worker_observed_at_for_tests(
        _worker_marker(marker),
        currentness,
        observed_at,
    )


def invalidate_worker_ownership(marker: object, currentness: object) -> None:
    """Publish terminal ownership loss through the real owner state."""
    leadership._invalidate_worker_ownership_for_tests(
        _worker_marker(marker),
        currentness,
    )


def replace_single_private_worker(
    marker: object,
    currentness: object,
    **values: Any,
) -> object:
    """Replace one current proof while preserving its exact owner state."""
    return leadership._replace_single_private_worker_for_tests(
        _worker_marker(marker),
        currentness,
        **values,
    )


def issue_trusted_dispatch_clock(marker: object, **values: Any) -> tuple[object, object]:
    """Issue one real sealed prewarmed clock and currentness owner for tests."""
    return clock._issue_trusted_dispatch_clock_for_tests(
        _clock_marker(marker),
        **values,
    )
