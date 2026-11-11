import hashlib
import hmac

import httpx
import pytest

from hmt_crypto.api.delta_verification import (
    DeltaVerificationRejected,
    verify_delta_credentials,
)


@pytest.mark.asyncio
async def test_verifier_signs_canonical_production_wallet_read_and_binds_user() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.india.delta.exchange/v2/wallet/balances"
        assert request.headers["api-key"] == "key-12345678"
        timestamp = request.headers["timestamp"]
        expected = hmac.new(
            b"secret-1234567890",
            f"GET{timestamp}/v2/wallet/balances".encode(),
            hashlib.sha256,
        ).hexdigest()
        assert request.headers["signature"] == expected
        assert request.headers["user-agent"] == "hmt-crypto-account-verifier/1.0"
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": [
                    {"user_id": 7721, "asset_symbol": "USD"},
                    {"user_id": 7721, "asset_symbol": "BTC"},
                ],
            },
        )

    evidence = await verify_delta_credentials(
        environment="prod",
        api_key="key-12345678",
        api_secret="secret-1234567890",
        transport=httpx.MockTransport(handler),
    )
    assert evidence.external_user_id == "7721"


@pytest.mark.asyncio
async def test_verifier_rejects_credential_or_ip_denial() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"success": False})

    with pytest.raises(DeltaVerificationRejected):
        await verify_delta_credentials(
            environment="prod",
            api_key="key-12345678",
            api_secret="secret-1234567890",
            transport=httpx.MockTransport(handler),
        )


@pytest.mark.asyncio
async def test_verifier_rejects_inconsistent_wallet_identity() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"success": True, "result": [{"user_id": 7}, {"user_id": 8}]},
        )

    with pytest.raises(DeltaVerificationRejected):
        await verify_delta_credentials(
            environment="prod",
            api_key="key-12345678",
            api_secret="secret-1234567890",
            transport=httpx.MockTransport(handler),
        )
