"""Fail-closed tests for the disabled Live execution orchestrator."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

import pytest
from pydantic import ValidationError

import hmt_crypto.worker.live_execution as subject
from hmt_crypto.api.live_order_http import LiveOrderTransportUncertain
from hmt_crypto.api.live_order_transport import LiveOrderResponseOutcome, prepare_live_order
from hmt_crypto.api.live_submission import SubmissionFence
from hmt_crypto.worker.live_execution import LiveExecutionSettings, execute_one_live_submission
from hmt_crypto.worker.live_submission_queue import ClaimedLiveSubmission, SendingLiveSubmission

pytestmark = pytest.mark.unit

ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000001")
OWNER_ID = UUID("20000000-0000-4000-8000-000000000001")
SUBMISSION_ID = UUID("30000000-0000-4000-8000-000000000001")


def enabled_settings() -> LiveExecutionSettings:
    return LiveExecutionSettings(
        HMT_LIVE_ORDER_DISPATCH_ENABLED=True,
        PRODUCTION_WRITES_FORBIDDEN=False,
        _env_file=None,
    )


def test_live_dispatch_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HMT_LIVE_ORDER_DISPATCH_ENABLED", raising=False)
    settings = LiveExecutionSettings(_env_file=None)

    assert settings.enabled is False
    assert settings.production_writes_forbidden is True


def test_enabled_dispatch_uses_persisted_qualification_evidence() -> None:
    settings = LiveExecutionSettings(
        HMT_LIVE_ORDER_DISPATCH_ENABLED=True,
        PRODUCTION_WRITES_FORBIDDEN=False,
        _env_file=None,
    )
    assert settings.enabled is True


def test_production_write_prohibition_is_an_independent_gate() -> None:
    with pytest.raises(ValidationError):
        LiveExecutionSettings(
            HMT_LIVE_ORDER_DISPATCH_ENABLED=True,
            PRODUCTION_WRITES_FORBIDDEN=True,
            _env_file=None,
        )


@pytest.mark.asyncio
async def test_disabled_orchestrator_cannot_claim() -> None:
    with pytest.raises(ValueError, match="not explicitly qualified"):
        await execute_one_live_submission(
            object(),
            settings=LiveExecutionSettings(_env_file=None),
            account_id=ACCOUNT_ID,
            owner_id=OWNER_ID,
            external_user_id="delta-user",
            adapter=object(),
        )


@pytest.mark.asyncio
async def test_transport_failure_is_persisted_uncertain_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = prepare_live_order(
        product_id=27,
        contracts=1,
        side="buy",
        order_type="limit",
        limit_price=Decimal("50000.5"),
        reduce_only=False,
        client_order_id="HML1_ABCDEFGHIJKLMNOPQRSTUVWXYZA",
    )
    claim_until = datetime.now(UTC) + timedelta(seconds=30)
    claim = ClaimedLiveSubmission(
        submission_id=SUBMISSION_ID,
        account_id=ACCOUNT_ID,
        intent_id=UUID("40000000-0000-4000-8000-000000000001"),
        preview_id=UUID("50000000-0000-4000-8000-000000000001"),
        client_order_id=prepared.request.client_order_id,
        request_sha256=sha256(b"request").hexdigest(),
        fence=SubmissionFence(OWNER_ID, 1, 1),
        claim_until=claim_until,
    )
    sending = SendingLiveSubmission(prepared, sha256(b"sending").hexdigest(), claim_until)
    outcomes: list[LiveOrderResponseOutcome] = []
    calls = 0

    async def claim_one(*_args, **_kwargs):
        return claim

    async def no_recovery_debt(*_args, **_kwargs):
        return False

    async def prepare(*_args, **_kwargs):
        return prepared

    async def mark_sending(*_args, **_kwargs):
        return sending

    async def record(_database, _claim, outcome):
        outcomes.append(outcome)

    class Adapter:
        async def send_once(self, _prepared, _permit):
            nonlocal calls
            calls += 1
            raise LiveOrderTransportUncertain

    monkeypatch.setattr(subject, "claim_next_live_submission", claim_one)
    monkeypatch.setattr(subject, "has_live_submission_recovery_debt", no_recovery_debt)
    monkeypatch.setattr(subject, "prepare_claimed_live_submission", prepare)
    monkeypatch.setattr(subject, "mark_live_submission_sending", mark_sending)
    monkeypatch.setattr(subject, "record_live_submission_outcome", record)

    result = await execute_one_live_submission(
        object(),
        settings=enabled_settings(),
        account_id=ACCOUNT_ID,
        owner_id=OWNER_ID,
        external_user_id="delta-user",
        adapter=Adapter(),
    )

    assert result.kind == "UNCERTAIN"
    assert result.submission_id == SUBMISSION_ID
    assert calls == 1
    assert outcomes == [LiveOrderResponseOutcome("UNCERTAIN", None)]


@pytest.mark.asyncio
async def test_recovery_debt_blocks_a_new_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    async def recovery_debt(*_args, **_kwargs):
        return True

    async def unexpected_claim(*_args, **_kwargs):
        raise AssertionError("a new submission must not be claimed")

    monkeypatch.setattr(subject, "has_live_submission_recovery_debt", recovery_debt)
    monkeypatch.setattr(subject, "claim_next_live_submission", unexpected_claim)

    result = await execute_one_live_submission(
        object(),
        settings=enabled_settings(),
        account_id=ACCOUNT_ID,
        owner_id=OWNER_ID,
        external_user_id="delta-user",
        adapter=object(),
    )

    assert result == subject.LiveExecutionResult("RECOVERY_REQUIRED", None)


@pytest.mark.asyncio
async def test_closed_entry_gate_only_claims_reduce_only_submissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[bool] = []

    async def no_recovery_debt(*_args, **_kwargs):
        return False

    async def claim_one(*_args, **kwargs):
        captured.append(kwargs["entries_allowed"])
        return None

    monkeypatch.setattr(subject, "has_live_submission_recovery_debt", no_recovery_debt)
    monkeypatch.setattr(subject, "claim_next_live_submission", claim_one)

    result = await execute_one_live_submission(
        object(),
        settings=enabled_settings(),
        account_id=ACCOUNT_ID,
        owner_id=OWNER_ID,
        external_user_id="delta-user",
        adapter=object(),
        entries_allowed=False,
    )

    assert result == subject.LiveExecutionResult("IDLE", None)
    assert captured == [False]
