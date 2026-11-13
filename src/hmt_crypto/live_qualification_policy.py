"""Immutable policy shared by Live qualification and order admission."""

from __future__ import annotations

from datetime import datetime, timedelta

LIVE_QUALIFICATION_WINDOW_HOURS = 6
LIVE_QUALIFICATION_WINDOW = timedelta(hours=LIVE_QUALIFICATION_WINDOW_HOURS)
LIVE_RECONCILIATION_MAX_GAP_SECONDS = 60
# Store unchanged reconciliation evidence frequently enough that ordinary
# worker scheduling jitter cannot violate the qualification continuity bound.
LIVE_RECONCILIATION_EVIDENCE_INTERVAL_SECONDS = 30


def qualification_window_is_complete(*, started_at: datetime, completed_at: datetime) -> bool:
    """Return whether immutable evidence covers the approved minimum window."""
    return completed_at - started_at >= LIVE_QUALIFICATION_WINDOW


__all__ = [
    "LIVE_QUALIFICATION_WINDOW",
    "LIVE_QUALIFICATION_WINDOW_HOURS",
    "LIVE_RECONCILIATION_EVIDENCE_INTERVAL_SECONDS",
    "LIVE_RECONCILIATION_MAX_GAP_SECONDS",
    "qualification_window_is_complete",
]
