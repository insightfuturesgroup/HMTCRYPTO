"""Supabase Auth boundary tests for administrative recovery operations."""

from __future__ import annotations

import httpx
import pytest

from hmt_crypto.api.platform_auth import AuthenticationRejected, SupabaseAuthGateway


@pytest.mark.asyncio
async def test_password_reset_uses_public_recovery_route_without_secret_key() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    gateway = SupabaseAuthGateway(
        base_url="https://project.supabase.co",
        publishable_key="publishable-key",
        secret_key="server-secret-key",
    )
    await gateway._client.aclose()  # noqa: SLF001 - replace owned transport in a boundary test
    gateway._client = httpx.AsyncClient(  # noqa: SLF001
        transport=httpx.MockTransport(handler), follow_redirects=False
    )
    try:
        await gateway.send_password_reset(
            "trader@example.com", redirect_to="https://crypto.helpmetrade.club"
        )
    finally:
        await gateway.aclose()

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/auth/v1/recover"
    assert request.url.params["redirect_to"] == "https://crypto.helpmetrade.club"
    assert request.headers["apikey"] == "publishable-key"
    assert request.headers.get("authorization") is None
    assert b"server-secret-key" not in request.content


@pytest.mark.asyncio
async def test_password_reset_rejects_non_success_response() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    gateway = SupabaseAuthGateway(
        base_url="https://project.supabase.co",
        publishable_key="publishable-key",
        secret_key="server-secret-key",
    )
    await gateway._client.aclose()  # noqa: SLF001
    gateway._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # noqa: SLF001
    try:
        with pytest.raises(AuthenticationRejected):
            await gateway.send_password_reset(
                "trader@example.com", redirect_to="https://crypto.helpmetrade.club"
            )
    finally:
        await gateway.aclose()


@pytest.mark.asyncio
async def test_password_update_uses_recovery_bearer_and_returns_verified_identity() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": "00000000-0000-4000-8000-000000000123",
                "email": "trader@example.com",
            },
        )

    gateway = SupabaseAuthGateway(
        base_url="https://project.supabase.co",
        publishable_key="publishable-key",
        secret_key="server-secret-key",
    )
    await gateway._client.aclose()  # noqa: SLF001
    gateway._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # noqa: SLF001
    try:
        identity = await gateway.update_password("recovery-access-token", "new-safe-password")
    finally:
        await gateway.aclose()

    assert identity.email == "trader@example.com"
    assert len(seen) == 1
    assert seen[0].method == "PUT"
    assert seen[0].url.path == "/auth/v1/user"
    assert seen[0].headers["authorization"] == "Bearer recovery-access-token"
    assert b"new-safe-password" in seen[0].content
    assert b"server-secret-key" not in seen[0].content
