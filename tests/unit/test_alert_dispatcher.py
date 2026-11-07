from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest

from hmt_crypto.observability.alerts import (
    AlertDeliveryError,
    AlertDispatcher,
    AlertEvent,
    AlertKind,
    AlertSeverity,
)

EVENT_ID = UUID("40000000-0000-0000-0000-000000000001")


def event() -> AlertEvent:
    return AlertEvent(
        event_id=EVENT_ID,
        kind=AlertKind.GLOBAL_KILL_INCOMPLETE,
        severity=AlertSeverity.CRITICAL,
        occurred_at=datetime(2026, 11, 6, 12, tzinfo=UTC),
        region="india",
        environment="testnet",
        mode="PAPER",
        correlation_id=UUID("40000000-0000-0000-0000-000000000002"),
    )


@pytest.mark.asyncio
async def test_dispatch_reuses_one_idempotency_key_across_bounded_retry() -> None:
    class Flaky:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def deliver(self, value: AlertEvent, *, idempotency_key: str) -> None:
            assert value is alert
            self.calls.append(idempotency_key)
            if len(self.calls) < 3:
                raise OSError("provider diagnostic must not escape")

    alert = event()
    transport = Flaky()
    await AlertDispatcher(
        transport, attempts=3, timeout_seconds=0.1, retry_delay_seconds=0.0
    ).dispatch(alert)

    assert transport.calls == [f"hmt-alert:{EVENT_ID}"] * 3


@pytest.mark.asyncio
async def test_delivery_failure_is_sanitized_after_exact_attempt_ceiling() -> None:
    class Down:
        calls = 0

        async def deliver(self, value: AlertEvent, *, idempotency_key: str) -> None:
            del value, idempotency_key
            self.calls += 1
            raise RuntimeError("provider-secret-canary")

    transport = Down()
    with pytest.raises(AlertDeliveryError) as captured:
        await AlertDispatcher(
            transport, attempts=2, timeout_seconds=0.1, retry_delay_seconds=0.0
        ).dispatch(event())

    assert transport.calls == 2
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "provider-secret-canary" not in str(captured.value)


@pytest.mark.asyncio
async def test_dispatch_preserves_cancellation_without_retry() -> None:
    class Blocking:
        calls = 0

        async def deliver(self, value: AlertEvent, *, idempotency_key: str) -> None:
            del value, idempotency_key
            self.calls += 1
            await asyncio.Event().wait()

    transport = Blocking()
    task = asyncio.create_task(
        AlertDispatcher(
            transport, attempts=3, timeout_seconds=5.0, retry_delay_seconds=0.0
        ).dispatch(event())
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert transport.calls == 1


def test_alert_text_is_closed_and_secret_free() -> None:
    rendered = event().safe_text()
    assert "GLOBAL_KILL_INCOMPLETE" in rendered
    assert "india/testnet/PAPER" in rendered
    assert "api" not in rendered.lower()
    assert "token" not in rendered.lower()
