"""Recovery-first orchestration tests without broker or database mutation."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

import pytest

import hmt_crypto.worker.live_recovery as subject
from hmt_crypto.api.live_order_recovery import RecoveryObservation
from hmt_crypto.api.live_order_recovery_http import LiveOrderRecoveryUnavailable
from hmt_crypto.api.live_submission import SubmissionFence
from hmt_crypto.worker.live_recovery import recover_one_live_submission
from hmt_crypto.worker.live_submission_queue import ClaimedLiveSubmission

pytestmark = pytest.mark.unit

ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000001")
OWNER_ID = UUID("20000000-0000-4000-8000-000000000001")
SUBMISSION_ID = UUID("30000000-0000-4000-8000-000000000001")


def recovery_claim() -> ClaimedLiveSubmission:
    return ClaimedLiveSubmission(
        submission_id=SUBMISSION_ID,
        account_id=ACCOUNT_ID,
        intent_id=UUID("40000000-0000-4000-8000-000000000001"),
        preview_id=UUID("50000000-0000-4000-8000-000000000001"),
        client_order_id="HML1_ABCDEFGHIJKLMNOPQRSTUVWXYZA",
        request_sha256=sha256(b"request").hexdigest(),
        fence=SubmissionFence(OWNER_ID, 2, 3),
        claim_until=datetime.now(UTC) + timedelta(seconds=30),
    )


@pytest.mark.asyncio
async def test_positive_lookup_is_recorded_and_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    claim = recovery_claim()
    recorded: list[RecoveryObservation] = []

    async def claim_one(*_args, **_kwargs):
        return claim

    async def record(_database, _claim, observation):
        recorded.append(observation)

    class Adapter:
        async def lookup(self, **_kwargs):
            return RecoveryObservation("FOUND", 7001)

    monkeypatch.setattr(subject, "claim_live_submission_recovery", claim_one)
    monkeypatch.setattr(subject, "record_uncertain_recovery_lookup", record)

    result = await recover_one_live_submission(
        object(),
        account_id=ACCOUNT_ID,
        owner_id=OWNER_ID,
        external_user_id="12345",
        adapter=Adapter(),
    )

    assert result == subject.LiveRecoveryResult("FOUND", SUBMISSION_ID)
    assert recorded == [RecoveryObservation("FOUND", 7001)]


@pytest.mark.asyncio
async def test_unavailable_lookup_leaves_recovery_debt_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = recovery_claim()

    async def claim_one(*_args, **_kwargs):
        return claim

    async def unexpected_record(*_args, **_kwargs):
        raise AssertionError("unavailable evidence must not change the recovery result")

    class Adapter:
        async def lookup(self, **_kwargs):
            raise LiveOrderRecoveryUnavailable

    monkeypatch.setattr(subject, "claim_live_submission_recovery", claim_one)
    monkeypatch.setattr(subject, "record_uncertain_recovery_lookup", unexpected_record)

    result = await recover_one_live_submission(
        object(),
        account_id=ACCOUNT_ID,
        owner_id=OWNER_ID,
        external_user_id="12345",
        adapter=Adapter(),
    )

    assert result == subject.LiveRecoveryResult("UNAVAILABLE", SUBMISSION_ID)
