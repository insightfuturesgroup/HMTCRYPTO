from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from hmt_crypto.observability.alert_transports import (
    HttpsJsonWebhookTransport,
    TelegramAlertTransport,
)
from hmt_crypto.observability.alerts import (
    AlertDeliveryError,
    AlertEvent,
    AlertKind,
    AlertSeverity,
)


def _event() -> AlertEvent:
    return AlertEvent(
        event_id=UUID("50000000-0000-0000-0000-000000000001"),
        kind=AlertKind.RECONCILIATION_FAILED,
        severity=AlertSeverity.CRITICAL,
        occurred_at=datetime(2026, 11, 6, 12, tzinfo=UTC),
        region="india",
        environment="testnet",
        mode="PAPER",
    )


@pytest.mark.asyncio
async def test_telegram_transport_sends_only_closed_text_and_stable_key() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = TelegramAlertTransport(
            client=client,
            bot_token=SecretStr("123456:telegram-test-token"),
            chat_id="-1001234567890",
        )
        await transport.deliver(_event(), idempotency_key="hmt-alert:test")

    assert len(requests) == 1
    request = requests[0]
    assert request.headers["Idempotency-Key"] == "hmt-alert:test"
    assert json.loads(request.content) == {
        "chat_id": "-1001234567890",
        "disable_web_page_preview": True,
        "text": _event().safe_text(),
    }


@pytest.mark.asyncio
async def test_telegram_provider_failure_cannot_leak_response_or_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(401, text="provider-secret-canary")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = TelegramAlertTransport(
            client=client,
            bot_token=SecretStr("123456:telegram-secret-canary"),
            chat_id="1234",
        )
        with pytest.raises(AlertDeliveryError) as captured:
            await transport.deliver(_event(), idempotency_key="hmt-alert:test")

    rendered = repr(captured.value) + str(captured.value)
    assert "provider-secret-canary" not in rendered
    assert "telegram-secret-canary" not in rendered
    assert captured.value.__cause__ is None


@pytest.mark.asyncio
async def test_https_webhook_uses_bearer_secret_without_body_credentials() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = HttpsJsonWebhookTransport(
            client=client,
            url="https://alerts.example.invalid/hmt",
            bearer_token=SecretStr("webhook-secret-canary"),
        )
        await transport.deliver(_event(), idempotency_key="hmt-alert:test")

    request = requests[0]
    assert request.headers["Authorization"] == "Bearer webhook-secret-canary"
    assert request.headers["Idempotency-Key"] == "hmt-alert:test"
    assert b"webhook-secret-canary" not in request.content
    assert json.loads(request.content) == {
        "event_id": str(_event().event_id),
        "kind": _event().kind.value,
        "message": _event().safe_text(),
        "severity": _event().severity.value,
    }


@pytest.mark.parametrize(
    "url",
    (
        "http://alerts.example.invalid/hmt",
        "https://user:password@alerts.example.invalid/hmt",
        "https://alerts.example.invalid/hmt?token=secret",
        "https://127.0.0.1/hmt",
    ),
)
def test_webhook_rejects_unsafe_or_ambiguous_destinations(url: str) -> None:
    with pytest.raises(ValueError):
        HttpsJsonWebhookTransport(client=httpx.AsyncClient(), url=url)
