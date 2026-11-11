"""Durable Live cancellation and modification safety contracts."""

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import Table

import hmt_crypto.worker.live_order_actions as subject
from hmt_crypto.exchange.delta.signer import DeltaSigner
from hmt_crypto.persistence.models import LiveOrderAction
from hmt_crypto.worker.live_order_actions import (
    _PERMIT_ISSUER,
    DeltaIndiaLiveOrderActionAdapter,
    LiveOrderActionHttpResponse,
    _ActionPermit,
    _classify_response,
    _find_order,
    _modify_matches,
)

pytestmark = pytest.mark.unit


def _action(*, action: str = "CANCEL") -> LiveOrderAction:
    return LiveOrderAction(
        id=uuid4(),
        account_id=uuid4(),
        created_by_user_id=uuid4(),
        idempotency_key=uuid4(),
        action=action,
        order_id=41,
        product_id=27,
        target_size=2 if action == "MODIFY" else None,
        target_limit_price=Decimal("59000") if action == "MODIFY" else None,
        request_payload="{}",
        request_sha256=hashlib.sha256(b"{}").hexdigest(),
        status="PENDING",
        worker_owner_id=None,
        worker_generation=None,
        credential_version=None,
        exchange_response_sha256=None,
        last_error_code=None,
        sent_at=None,
        resolved_at=None,
        version=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_reconciliation_order_selection_is_exact() -> None:
    rows: list[object] = [{"id": 41, "product_id": 27, "size": 2, "limit_price": "59000"}]

    assert _find_order(rows, 41, 27) == rows[0]
    assert _find_order(rows, 41, 28) is None
    assert isinstance(rows[0], dict)
    assert _modify_matches(_action(action="MODIFY"), rows[0]) is True


def test_response_requires_matching_order_identity() -> None:
    action = _action()
    accepted = LiveOrderActionHttpResponse(
        200,
        b'{"success":true,"result":{"id":41,"product_id":27}}',
    )
    mismatched = LiveOrderActionHttpResponse(
        200,
        b'{"success":true,"result":{"id":42,"product_id":27}}',
    )

    assert _classify_response(action, accepted) == ("SUCCEEDED", None)
    assert _classify_response(action, mismatched) == (
        "UNCERTAIN",
        "invalid_exchange_response",
    )
    assert _classify_response(action, LiveOrderActionHttpResponse(400, b"{}")) == (
        "REJECTED",
        "exchange_http_400",
    )


@pytest.mark.asyncio
async def test_adapter_sends_exact_signed_delete_body(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[httpx.Request] = []
    monkeypatch.setattr(subject, "_ORDER_URL", "https://example.invalid/v2/orders")

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(
            200,
            json={"success": True, "result": {"id": 41, "product_id": 27}},
        )

    signer = DeltaSigner(
        api_key=SecretStr("test-key"),
        api_secret=SecretStr("test-secret"),
        user_agent="test-agent",
        clock=lambda: 1_700_000_000,
    )
    adapter = DeltaIndiaLiveOrderActionAdapter(
        signer=signer, transport=httpx.MockTransport(handler)
    )
    body = b'{"id":41,"product_id":27}'
    permit = _ActionPermit(
        action_id=uuid4(),
        method="DELETE",
        body=body,
        body_sha256=hashlib.sha256(body).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(seconds=10),
        issuer=_PERMIT_ISSUER,
    )
    try:
        response = await adapter.send_once(permit)
    finally:
        await adapter.aclose()

    assert response.status_code == 200
    assert len(observed) == 1
    assert observed[0].method == "DELETE"
    assert observed[0].url == httpx.URL("https://example.invalid/v2/orders")
    assert observed[0].content == body


def test_schema_enforces_action_shape_and_idempotency() -> None:
    table = LiveOrderAction.__table__
    assert isinstance(table, Table)
    names = {constraint.name for constraint in table.constraints}

    assert "ck_live_order_actions_action_shape" in names
    assert "uq_live_actions_request" in names
