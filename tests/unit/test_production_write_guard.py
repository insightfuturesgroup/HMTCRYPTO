"""Removing or weakening the suite guard must fail these tests before I/O."""

import os
import subprocess
import sys
from pathlib import Path

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
        pytest.fail("Forbidden production request reached transport")

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(pytest.fail.Exception, match="Production Delta write forbidden"):
            client.request(method, f"https://{host}/v2/orders")


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["api.india.delta.exchange", "api.delta.exchange"])
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_async_production_writes_fail_before_transport(host: str, method: str) -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        pytest.fail("Forbidden production request reached transport")

    with pytest.raises(pytest.fail.Exception, match="Production Delta write forbidden"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
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
        pytest.fail("Forbidden production redirect reached transport")

    with httpx.Client(transport=httpx.MockTransport(transport), follow_redirects=True) as client:
        with pytest.raises(pytest.fail.Exception, match="Production Delta write forbidden"):
            client.post("https://testnet-api.delta.exchange/v2/orders")


def test_guard_environment_is_forced_on() -> None:
    value = os.getenv("PRODUCTION_WRITES_FORBIDDEN")
    assert value == "1"


@pytest.mark.parametrize("phase", ["setup", "teardown"])
def test_guard_covers_session_fixture_lifetime(tmp_path: Path, phase: str) -> None:
    conftest = Path(__file__).parents[1] / "conftest.py"
    (tmp_path / "conftest.py").write_text(conftest.read_text())
    (tmp_path / "test_session_guard.py").write_text(
        f'''import httpx
import pytest


def send_production_write():
    def transport(request):
        pytest.fail("TRANSPORT_REACHED")

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        client.post("https://api.india.delta.exchange/v2/orders")


@pytest.fixture(scope="session", autouse=True)
def session_resource():
    if "{phase}" == "setup":
        send_production_write()
    yield
    if "{phase}" == "teardown":
        send_production_write()


def test_session_resource():
    pass
'''
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Production Delta write forbidden" in result.stdout, result.stdout
    assert "TRANSPORT_REACHED" not in result.stdout, result.stdout
