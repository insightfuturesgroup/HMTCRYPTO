"""Patch 6 prerequisite: signed bytes preserve the reduce-only exit flag."""

import hmac
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from hmt_crypto.api import live_order_http as subject
from hmt_crypto.api.live_order_transport import prepare_live_order
from hmt_crypto.exchange.delta.signer import DeltaSigner

pytestmark = [pytest.mark.safety, pytest.mark.asyncio]


@pytest.mark.parametrize("side", ["buy", "sell"])
async def test_signed_exit_body_is_reduce_only(side, monkeypatch):
    monkeypatch.setattr(subject, "_ORDER_URL", "https://example.invalid/v2/orders")
    prepared = prepare_live_order(
        product_id=27,
        contracts=1,
        side=side,
        order_type="market",
        limit_price=None,
        reduce_only=True,
        client_order_id="HML1_ABCDEFGHIJKLMNOPQRSTUVWXYZA",
    )
    seen = []

    def handler(request):
        seen.append(request)
        assert json.loads(request.content)["reduce_only"] is True
        assert request.content == prepared.body
        expected = hmac.new(
            b"fixture-secret", b"POST1700000000/v2/orders" + request.content, sha256
        ).hexdigest()
        assert hmac.compare_digest(request.headers["signature"], expected)
        return httpx.Response(400, content=b'{"success":false}', request=request)

    adapter = subject.DeltaIndiaLiveOrderHttpAdapter(
        signer=DeltaSigner(
            api_key=SecretStr("fixture-key"),
            api_secret=SecretStr("fixture-secret"),
            user_agent="offline-test",
            clock=lambda: 1700000000,
        ),
        transport=httpx.MockTransport(handler),
    )
    permit = subject._issue_live_dispatch_permit(
        submission_id=uuid4(),
        body_sha256=prepared.body_sha256,
        sending_evidence_sha256="a" * 64,
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
    )
    try:
        await adapter.send_once(prepared, permit)
    finally:
        await adapter.aclose()
    assert len(seen) == 1
