"""Incomplete fill evidence must never clear uncertainty or permit entries."""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from hmt_crypto.persistence import models
from hmt_crypto.worker import live_submission_queue as subject

pytestmark = pytest.mark.safety


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["order_id", "size", "fills_payload"])
async def test_missing_evidence_preserves_uncertainty_and_entry_block(missing, monkeypatch):
    from hmt_crypto.worker import live_execution

    logger = SimpleNamespace(warning=Mock())
    monkeypatch.setattr(subject, "get_logger", lambda name: logger)
    now = datetime.now(UTC)
    submission = SimpleNamespace(status="UNCERTAIN", sent_at=now, id=uuid4(), intent_id=uuid4())
    intent = SimpleNamespace(preview_id=uuid4())
    preview = SimpleNamespace(order_type="market", product_id=27, side="buy", contracts=1)
    row = dict(order_id=123, size=1, product_id=27, side="buy", created_at=now.isoformat())
    if missing != "fills_payload":
        row[missing] = None
    snapshot = SimpleNamespace(
        outcome="ready",
        observed_at=now,
        fills_payload=None if missing == "fills_payload" else json.dumps([row]),
    )
    account_id = uuid4()
    before = subject.RECOVERY_MISSING.labels(reason=missing)._value.get()

    class Session:
        async def get(self, model, identity):
            return {
                models.LiveOrderSubmission: submission,
                models.LiveOrderIntent: intent,
                models.LiveOrderPreview: preview,
            }[model]

        async def scalar(self, statement):
            if statement.column_descriptions[0]["entity"] is models.PrivateReconciliationSnapshot:
                return snapshot
            assert "hidden_at" not in str(statement)
            return submission.id

    @asynccontextmanager
    async def transaction():
        yield Session()

    db = SimpleNamespace(transaction=transaction)
    claim = SimpleNamespace(submission_id=submission.id, account_id=account_id)
    assert await subject.recover_from_reconciliation(db, claim) is None
    assert submission.status == "UNCERTAIN"
    assert await subject.has_live_submission_recovery_debt(db, account_id=account_id)
    assert subject.RECOVERY_MISSING.labels(reason=missing)._value.get() == before + 1
    logger.warning.assert_called_once_with(
        "live_recovery_evidence_missing", reason=missing, outcome="UNCERTAIN"
    )
    allowed = []

    async def no_entry_claim(*args, **kwargs):
        allowed.append(kwargs["entries_allowed"])
        return None

    monkeypatch.setattr(live_execution, "claim_next_live_submission", no_entry_claim)
    result = await live_execution.execute_one_live_submission(
        db,
        settings=live_execution.LiveExecutionSettings(
            HMT_LIVE_ORDER_DISPATCH_ENABLED=True, PRODUCTION_WRITES_FORBIDDEN=False, _env_file=None
        ),
        account_id=account_id,
        owner_id=uuid4(),
        external_user_id="fixture-user",
        adapter=object(),
    )
    assert result.kind == "RECOVERY_REQUIRED"
    assert allowed == [False]


@pytest.mark.parametrize("missing", ["order_id", "size"])
def test_incomplete_row_cannot_be_ignored_in_favor_of_another_match(missing):
    now = datetime.now(UTC)
    complete = dict(order_id=123, size=1, product_id=27, side="buy", created_at=now.isoformat())
    incomplete = dict(complete, **{missing: None})
    assert (
        subject._fill_recovery_observation(
            json.dumps([complete, incomplete]), product_id=27, side="buy", contracts=1, sent_at=now
        )
        is None
    )
