"""Regression coverage for database-driver UUIDs at Live worker boundaries."""

from __future__ import annotations

from uuid import UUID

import pytest

from hmt_crypto.worker import private_runner
from hmt_crypto.worker.live_execution import LiveExecutionResult, LiveExecutionSettings
from hmt_crypto.worker.live_order_actions import LiveOrderActionResult
from hmt_crypto.worker.live_recovery import LiveRecoveryResult
from hmt_crypto.worker.private_runner import PrivateWorkerSettings

pytestmark = pytest.mark.unit


class _DriverUUID(UUID):
    """Model asyncpg's UUID subtype returned by PostgreSQL."""


def _claim() -> private_runner._Claim:
    return private_runner._Claim(
        account_id=_DriverUUID("32468e34-b2a5-4d5e-9a82-64aa3e8ab148"),
        credential_version=1,
        external_user_id="12345",
        secret_arn="arn:aws:secretsmanager:ap-southeast-2:123456789012:secret:hmt-crypto/delta/test",
        generation=1,
        product_ids=frozenset({27, 3136}),
    )


def _process() -> PrivateWorkerSettings:
    return PrivateWorkerSettings(
        HMT_PRIVATE_WORKER_ID="00000000-0000-0000-0000-000000000123",
        HMT_AWS_REGION="ap-southeast-2",
        HMT_PRIVATE_READ_ONLY="false",
    )


def _execution() -> LiveExecutionSettings:
    return LiveExecutionSettings(
        HMT_LIVE_ORDER_DISPATCH_ENABLED="true",
        PRODUCTION_WRITES_FORBIDDEN="false",
        HMT_LIVE_ORDER_QUALIFICATION_SHA256="a" * 64,
    )


@pytest.mark.asyncio
async def test_live_worker_normalizes_driver_uuid_for_all_execution_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, type[object], type[object]]] = []

    async def qualified(*_args: object, **_kwargs: object) -> bool:
        return True

    async def submission(*_args: object, **kwargs: object) -> LiveExecutionResult:
        observed.append(("submission", type(kwargs["account_id"]), type(kwargs["owner_id"])))
        return LiveExecutionResult("IDLE", None)

    async def action(*_args: object, **kwargs: object) -> LiveOrderActionResult:
        observed.append(("action", type(kwargs["account_id"]), type(kwargs["owner_id"])))
        return LiveOrderActionResult("IDLE", None)

    async def recovery(*_args: object, **kwargs: object) -> LiveRecoveryResult:
        observed.append(("recovery", type(kwargs["account_id"]), type(kwargs["owner_id"])))
        return LiveRecoveryResult("IDLE", None)

    monkeypatch.setattr(private_runner, "_dispatch_qualification_is_current", qualified)
    monkeypatch.setattr(private_runner, "execute_one_live_submission", submission)
    monkeypatch.setattr(private_runner, "execute_one_live_order_action", action)
    monkeypatch.setattr(private_runner, "recover_one_live_submission", recovery)

    database = object()
    process = _process()
    claim = _claim()
    execution = _execution()
    await private_runner._execute_qualified_live_action(
        database,
        process,
        claim,
        execution,
        api_key="key",
        api_secret="secret",  # type: ignore[arg-type]
    )
    await private_runner._recover_live_submission(
        database,
        process,
        claim,
        api_key="key",
        api_secret="secret",  # type: ignore[arg-type]
    )
    await private_runner._execute_qualified_live_submission(
        database,
        process,
        claim,
        execution,
        api_key="key",
        api_secret="secret",  # type: ignore[arg-type]
    )

    assert observed == [
        ("action", UUID, UUID),
        ("recovery", UUID, UUID),
        ("submission", UUID, UUID),
    ]
