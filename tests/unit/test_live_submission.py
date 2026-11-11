"""Focused deterministic and fencing checks for Live submission recovery."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from hmt_crypto.api.live_submission import (
    LiveSubmissionTransitionError,
    SubmissionFence,
    make_live_client_order_id,
    require_transition,
    submission_event_sha256,
)

pytestmark = pytest.mark.unit

ACCOUNT = UUID("10000000-0000-4000-8000-000000000001")
INTENT = UUID("20000000-0000-4000-8000-000000000002")
OWNER = UUID("30000000-0000-4000-8000-000000000003")


def test_client_order_id_is_stable_bounded_and_account_scoped() -> None:
    first = make_live_client_order_id(ACCOUNT, INTENT)

    assert first == make_live_client_order_id(ACCOUNT, INTENT)
    assert len(first) == 32
    assert first.startswith("HML1_")
    assert first != make_live_client_order_id(UUID("10000000-0000-4000-8000-000000000009"), INTENT)


def test_matching_fence_can_advance_reviewed_transition() -> None:
    fence = SubmissionFence(OWNER, 4, 2)

    require_transition(current="CLAIMED", target="PREPARED", stored_fence=fence, actor_fence=fence)


def test_stale_worker_fence_is_rejected() -> None:
    with pytest.raises(LiveSubmissionTransitionError):
        require_transition(
            current="SENDING",
            target="ACCEPTED",
            stored_fence=SubmissionFence(OWNER, 4, 2),
            actor_fence=SubmissionFence(OWNER, 3, 2),
        )


@pytest.mark.parametrize(
    ("current", "target"),
    [("PENDING", "SENDING"), ("PREPARED", "ACCEPTED"), ("ACCEPTED", "PENDING")],
)
def test_transition_graph_rejects_skips_and_terminal_reentry(current: str, target: str) -> None:
    fence = SubmissionFence(OWNER, 4, 2)
    with pytest.raises(LiveSubmissionTransitionError):
        require_transition(current=current, target=target, stored_fence=fence, actor_fence=fence)


def test_submission_event_hash_is_deterministic_and_fence_bound() -> None:
    occurred_at = datetime(2026, 11, 11, 10, 30, tzinfo=UTC)
    fence = SubmissionFence(OWNER, 4, 2)
    first = submission_event_sha256(
        submission_id=INTENT,
        sequence=1,
        from_status="PENDING",
        to_status="CLAIMED",
        fence=fence,
        occurred_at=occurred_at,
    )

    assert first == submission_event_sha256(
        submission_id=INTENT,
        sequence=1,
        from_status="PENDING",
        to_status="CLAIMED",
        fence=fence,
        occurred_at=occurred_at,
    )
    assert len(first) == 64
    assert first != submission_event_sha256(
        submission_id=INTENT,
        sequence=1,
        from_status="PENDING",
        to_status="CLAIMED",
        fence=SubmissionFence(OWNER, 5, 2),
        occurred_at=occurred_at,
    )
