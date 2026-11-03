"""Removing or weakening the suite guard must fail these tests before I/O."""

import os

import httpx
import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "host",
    ["api.india.delta.exchange", "api.delta.exchange", "API.DELTA.EXCHANGE."],
)
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "TRACE"])
def test_sync_production_writes_fail_before_transport(host: str, method: str) -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(pytest.fail.Exception, match="Production Delta write forbidden"):
            client.request(method, f"https://{host}/v2/orders")


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["api.india.delta.exchange", "api.delta.exchange"])
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_async_production_writes_fail_before_transport(host: str, method: str) -> None:
    with pytest.raises(pytest.fail.Exception, match="Production Delta write forbidden"):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200))
        ) as client:
            await client.request(method, f"https://{host}/v2/orders")


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_production_reads_reach_mock_transport(method: str) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
        assert client.request(method, "https://api.delta.exchange/v2/products").status_code == 200


@pytest.mark.parametrize("host", ["cdn-ind.testnet.deltaex.org", "testnet-api.delta.exchange"])
def test_testnet_requests_reach_mock_transport(host: str) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
        assert client.post(f"https://{host}/v2/orders").status_code == 200


def test_redirect_cannot_forward_write_to_production() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.host == "testnet-api.delta.exchange":
            return httpx.Response(307, headers={"Location": "https://api.delta.exchange/v2/orders"})
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(transport), follow_redirects=True) as client:
        with pytest.raises(pytest.fail.Exception, match="Production Delta write forbidden"):
            client.post("https://testnet-api.delta.exchange/v2/orders")


def test_guard_environment_is_forced_on() -> None:
    value = os.getenv("PRODUCTION_WRITES_FORBIDDEN")
    assert value == "1"
