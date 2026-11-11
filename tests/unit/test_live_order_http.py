"""Offline signed single-attempt Live order transport tests."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

import hmt_crypto.api.live_order_http as subject
from hmt_crypto.api.live_order_http import (
    DeltaIndiaLiveOrderHttpAdapter,
    LiveOrderTransportUncertain,
    _issue_live_dispatch_permit,
)
from hmt_crypto.api.live_order_transport import prepare_live_order
from hmt_crypto.exchange.delta.signer import DeltaSigner

pytestmark = pytest.mark.unit


def test_adapter_has_one_fixed_production_origin() -> None:
    assert subject._ORDER_URL == "https://api.india.delta.exchange/v2/orders"


def prepared_order():
    return prepare_live_order(
        product_id=27,
        contracts=1,
        side="buy",
        order_type="limit",
        limit_price=Decimal("50000.5"),
        reduce_only=False,
        client_order_id="HML1_ABCDEFGHIJKLMNOPQRSTUVWXYZA",
    )


def permit(body_sha256: str):
    return _issue_live_dispatch_permit(
        submission_id=UUID("10000000-0000-4000-8000-000000000001"),
        body_sha256=body_sha256,
        sending_evidence_sha256=sha256(b"sending-evidence").hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
    )


@pytest.mark.asyncio
async def test_adapter_signs_exact_body_and_uses_only_order_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = prepared_order()
    seen = 0
    monkeypatch.setattr(subject, "_ORDER_URL", "https://example.invalid/v2/orders")

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen
        seen += 1
        assert str(request.url) == "https://example.invalid/v2/orders"
        assert request.method == "POST"
        assert request.content == prepared.body
        assert request.headers["api-key"] == "fixture-key"
        assert request.headers["signature"]
        return httpx.Response(400, content=b'{"success":false}', request=request)

    adapter = DeltaIndiaLiveOrderHttpAdapter(
        signer=DeltaSigner(
            api_key=SecretStr("fixture-key"),
            api_secret=SecretStr("fixture-secret"),
            user_agent="hmt-crypto-test",
            clock=lambda: 1_700_000_000,
        ),
        transport=httpx.MockTransport(handler),
    )
    try:
        response = await adapter.send_once(prepared, permit(prepared.body_sha256))
    finally:
        await adapter.aclose()

    assert response.status_code == 400
    assert seen == 1


@pytest.mark.asyncio
async def test_transport_failure_is_uncertain_and_never_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = prepared_order()
    calls = 0
    monkeypatch.setattr(subject, "_ORDER_URL", "https://example.invalid/v2/orders")

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("fixture timeout", request=request)

    adapter = DeltaIndiaLiveOrderHttpAdapter(
        signer=DeltaSigner(
            api_key=SecretStr("fixture-key"),
            api_secret=SecretStr("fixture-secret"),
            user_agent="hmt-crypto-test",
            clock=lambda: 1_700_000_000,
        ),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(LiveOrderTransportUncertain):
            await adapter.send_once(prepared, permit(prepared.body_sha256))
    finally:
        await adapter.aclose()

    assert calls == 1
