"""Offline tests for the exact Delta client-order-ID recovery lookup."""

import json
import time
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

import hmt_crypto.api.live_order_recovery_http as subject
from hmt_crypto.api.live_order_recovery_http import (
    DeltaIndiaLiveOrderRecoveryAdapter,
    LiveOrderRecoveryUnavailable,
)
from hmt_crypto.exchange.delta.signer import DeltaSigner

pytestmark = pytest.mark.unit

CLIENT_ID = "HMT_FIXTURE_1"


def signer() -> DeltaSigner:
    return DeltaSigner(
        api_key=SecretStr("fixture-key"),
        api_secret=SecretStr("fixture-secret"),
        user_agent="hmt-crypto-recovery-test/1.0",
        clock=lambda: int(time.time()),
    )


def found_body() -> bytes:
    path = (
        Path(__file__).parents[1]
        / "fixtures/delta/private/mutations/orders/offline_candidate_order_success.json"
    )
    return path.read_bytes()


@pytest.mark.asyncio
async def test_lookup_signs_exact_fixed_path_and_validates_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subject, "_ORIGIN", "https://example.invalid")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == f"/v2/orders/client_order_id/{CLIENT_ID}"
        assert request.headers["api-key"] == "fixture-key"
        assert request.headers["signature"]
        return httpx.Response(200, content=found_body(), request=request)

    adapter = DeltaIndiaLiveOrderRecoveryAdapter(
        signer=signer(), transport=httpx.MockTransport(handler)
    )
    try:
        observation = await adapter.lookup(client_order_id=CLIENT_ID, expected_user_id="12345")
    finally:
        await adapter.aclose()

    assert observation.result == "FOUND"
    assert observation.exchange_order_id == 7001


@pytest.mark.asyncio
async def test_only_http_404_becomes_nonterminal_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subject, "_ORIGIN", "https://example.invalid")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"success": False}, request=request)

    adapter = DeltaIndiaLiveOrderRecoveryAdapter(
        signer=signer(), transport=httpx.MockTransport(handler)
    )
    try:
        observation = await adapter.lookup(client_order_id=CLIENT_ID, expected_user_id="12345")
    finally:
        await adapter.aclose()

    assert observation.result == "NOT_FOUND"
    assert observation.exchange_order_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 401, 429, 500, 503])
async def test_all_other_http_failures_remain_unavailable(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    monkeypatch.setattr(subject, "_ORIGIN", "https://example.invalid")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=b"{}", request=request)

    adapter = DeltaIndiaLiveOrderRecoveryAdapter(
        signer=signer(), transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(LiveOrderRecoveryUnavailable):
            await adapter.lookup(client_order_id=CLIENT_ID, expected_user_id="12345")
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_identity_mismatch_cannot_resolve_uncertainty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subject, "_ORIGIN", "https://example.invalid")
    payload = json.loads(found_body())
    payload["result"]["user_id"] = 99999

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    adapter = DeltaIndiaLiveOrderRecoveryAdapter(
        signer=signer(), transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(LiveOrderRecoveryUnavailable):
            await adapter.lookup(client_order_id=CLIENT_ID, expected_user_id="12345")
    finally:
        await adapter.aclose()
