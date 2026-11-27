"""Claim boundaries must observe revocation after receipt, under a row lock."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as Row
from uuid import uuid4

import pytest
from test_mandatory_risk_policy import valid_policy

from hmt_crypto.persistence import models as m
from hmt_crypto.worker.tradingview_runner import admit_one_tradingview_signal

pytestmark = [pytest.mark.safety, pytest.mark.asyncio]


@pytest.mark.parametrize(
    "change,reason",
    [
        ("disabled", "risk_policy_disabled"),
        ("reduced", "risk_policy_inconsistent_max_order_notional"),
    ],
)
async def test_signal_claim_reads_current_locked_policy(change, reason):
    account_id, owner_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    policy = valid_policy()
    if change == "disabled":
        policy.enabled = False
    else:
        policy.max_order_notional = policy.max_order_notional / 2
    signal = Row(
        id=uuid4(),
        account_id=account_id,
        strategy_id=uuid4(),
        configuration_id=None,
        received_at=now,
    )
    strategy = Row(
        enabled=True,
        account_id=account_id,
        product_id=27,
        symbol="BTCUSD",
        contracts=1,
        order_type="market",
        reduce_only=False,
        created_by_user_id=uuid4(),
    )
    locked = []

    class Session:
        async def scalar(self, statement):
            model = statement.column_descriptions[0]["entity"]
            if model is m.AccountRiskPolicy:
                locked.append(statement._for_update_arg is not None)
                return policy
            return signal if model is m.TradingViewSignal else None

        async def get(self, model, identity):
            return strategy if model is m.TradingViewStrategy else None

    @asynccontextmanager
    async def transaction():
        yield Session()

    result = await admit_one_tradingview_signal(
        Row(transaction=transaction), account_id=account_id, owner_id=owner_id
    )
    assert result.reason == reason
    assert signal.status == "rejected"
    assert locked == [True]


@pytest.mark.parametrize("reduce_only", [True, False])
async def test_gtt_exit_exempt_but_entry_requires_policy(reduce_only):
    from unittest.mock import AsyncMock

    from hmt_crypto.worker.gtt_runner import evaluate_one_gtt

    now, account_id, owner_id = datetime.now(UTC), uuid4(), uuid4()
    lease = Row(
        owner_id=owner_id,
        generation=1,
        credential_version=1,
        lease_until=now + timedelta(minutes=2),
    )
    control = Row(exits_enabled=True, reconciliation_state="ready")
    reconciliation = Row(
        outcome="ready",
        observed_at=now,
        worker_owner_id=owner_id,
        worker_generation=1,
        credential_version=1,
    )
    gtt = Row(id=uuid4(), reduce_only=reduce_only, product_id=27, last_error_code=None)
    session = Row(
        scalar=AsyncMock(side_effect=[lease, control, reconciliation, None, gtt, None, None, None])
    )

    @asynccontextmanager
    async def transaction():
        yield session

    await evaluate_one_gtt(Row(transaction=transaction), account_id=account_id, owner_id=owner_id)
    queries = [
        call.args[0].column_descriptions[0]["entity"] for call in session.scalar.call_args_list
    ]
    assert (m.AccountRiskPolicy in queries) is (not reduce_only)
    assert gtt.last_error_code == (None if reduce_only else "risk_policy_missing")


@pytest.mark.parametrize("change", ["disabled", "reduced", "reduced_consistent"])
async def test_submission_claim_rejects_current_locked_policy(change):
    from hmt_crypto.worker.live_submission_queue import claim_next_live_submission

    account_id, owner_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    policy = valid_policy()
    policy.version = 2
    if change == "disabled":
        policy.enabled = False
    else:
        policy.max_order_notional /= 2
        if change == "reduced_consistent":
            policy.max_order_limit_inr /= 2
    lease = Row(
        owner_id=owner_id,
        generation=1,
        credential_version=1,
        lease_until=now + timedelta(minutes=2),
    )
    account = Row(
        mode="LIVE", environment="prod", current_credential_version=1, identity_verified_at=now
    )
    control = Row(
        entries_enabled=True,
        exits_enabled=True,
        credential_state="verified",
        reconciliation_state="ready",
        reconciled_at=now,
    )
    reconciliation = Row(
        outcome="ready",
        observed_at=now,
        worker_owner_id=owner_id,
        worker_generation=1,
        credential_version=1,
    )
    submission = Row(id=uuid4(), intent_id=uuid4(), status="PENDING")
    intent = Row(preview_id=uuid4())
    preview = Row(account_id=account_id, reduce_only=False, risk_policy_version=1)
    locked = []
    calls = 0

    class Session:
        async def scalar(self, statement):
            nonlocal calls
            model = statement.column_descriptions[0]["entity"]
            if model is m.AccountRiskPolicy:
                locked.append(statement._for_update_arg is not None)
                return policy
            if model is m.LiveOrderSubmission:
                calls += 1
                return None if calls == 1 else submission
            return {
                m.PrivateWorkerLease: lease,
                m.AccountLiveControl: control,
                m.PrivateReconciliationSnapshot: reconciliation,
            }.get(model)

        async def get(self, model, identity):
            return {
                m.Account: account,
                m.PlatformLiveControl: Row(entries_enabled=True),
                m.LiveOrderIntent: intent,
                m.LiveOrderPreview: preview,
            }.get(model)

    @asynccontextmanager
    async def transaction():
        yield Session()

    result = await claim_next_live_submission(
        Row(transaction=transaction), account_id=account_id, owner_id=owner_id
    )
    assert result is None
    assert submission.status == "PENDING"
    assert submission.last_error_code.startswith("risk_policy_")
    assert locked == [True]
