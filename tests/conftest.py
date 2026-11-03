"""Suite-wide safety boundary: production Delta writes fail before transport I/O.

HTTPX's single-request boundary covers sync/async clients, custom transports,
and every redirect hop. Keep its narrow version range in pyproject.toml and
verify these behavioral tests whenever HTTPX is upgraded. This test fixture is
not the runtime transport policy (introduced separately in Task 1.9).
"""

from collections.abc import Iterator

import httpx
import pytest

_PRODUCTION_HOSTS = frozenset({"api.india.delta.exchange", "api.delta.exchange"})
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _reject_production_write(request: httpx.Request) -> None:
    host = request.url.host.lower().rstrip(".")
    if host in _PRODUCTION_HOSTS and request.method.upper() not in _READ_METHODS:
        pytest.fail(
            f"Production Delta write forbidden: {request.method} {host}",
            pytrace=False,
        )


@pytest.fixture(autouse=True)
def forbid_production_writes(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Unconditionally enforce the guard; environment variables cannot disable it."""
    monkeypatch.setenv("PRODUCTION_WRITES_FORBIDDEN", "1")
    sync_send = httpx.Client._send_single_request
    async_send = httpx.AsyncClient._send_single_request

    def guarded_send(client: httpx.Client, request: httpx.Request) -> httpx.Response:
        _reject_production_write(request)
        return sync_send(client, request)

    async def guarded_async_send(
        client: httpx.AsyncClient, request: httpx.Request
    ) -> httpx.Response:
        _reject_production_write(request)
        return await async_send(client, request)

    monkeypatch.setattr(httpx.Client, "_send_single_request", guarded_send)
    monkeypatch.setattr(httpx.AsyncClient, "_send_single_request", guarded_async_send)
    yield
