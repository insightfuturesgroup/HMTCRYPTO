"""Retryable HTTP status takes precedence over an unusable response body."""

from __future__ import annotations

import httpx
import pytest
from delta_public_test_support import RecordingSleeper, fixture_bytes, new_client, public_api

from hmt_crypto.errors import ExchangeDownError, RateLimitError

pytestmark = pytest.mark.contract


async def _ticker_call(client: object) -> object:
    return await client.get_ticker(public_api().base.ProductRef(symbol="XBTUSD_TEST"))


class _ClosingBytes(httpx.AsyncByteStream):
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.closed = False

    async def __aiter__(self):
        yield self.body

    async def aclose(self) -> None:
        self.closed = True


def _broken_response(
    request: httpx.Request,
    *,
    status_code: int,
    body_kind: str,
) -> tuple[httpx.Response, _ClosingBytes | None]:
    reset = [(b"x-rate-limit-reset", b"0")] if status_code == 429 else []
    if body_kind == "text":
        return (
            httpx.Response(
                status_code,
                content=b"fixture-secret-plain-error",
                headers={
                    "Content-Type": "text/plain",
                    **({"X-Rate-Limit-Reset": "0"} if reset else {}),
                },
                request=request,
            ),
            None,
        )
    if body_kind == "empty":
        return (
            httpx.Response(
                status_code,
                content=b"",
                headers={
                    "Content-Type": "application/json",
                    **({"X-Rate-Limit-Reset": "0"} if reset else {}),
                },
                request=request,
            ),
            None,
        )
    if body_kind == "html":
        return (
            httpx.Response(
                status_code,
                content=b"<html>fixture-secret-cdn</html>",
                headers={
                    "Content-Type": "text/html",
                    **({"X-Rate-Limit-Reset": "0"} if reset else {}),
                },
                request=request,
            ),
            None,
        )
    if body_kind == "malformed_json":
        return (
            httpx.Response(
                status_code,
                content=b'{"fixture-secret":',
                headers={
                    "Content-Type": "application/json",
                    **({"X-Rate-Limit-Reset": "0"} if reset else {}),
                },
                request=request,
            ),
            None,
        )
    stream = _ClosingBytes(b"not-a-gzip-stream")
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
        (b"content-encoding", b"gzip"),
        *reset,
    ]
    return httpx.Response(status_code, headers=headers, stream=stream, request=request), stream


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [(429, RateLimitError), (500, ExchangeDownError), (503, ExchangeDownError)],
)
@pytest.mark.parametrize(
    "body_kind", ["text", "empty", "html", "malformed_json", "malformed_compression"]
)
async def test_retryable_status_retries_then_maps_terminal_error_without_body_dependency(
    status_code: int,
    error_type: type[Exception],
    body_kind: str,
) -> None:
    calls = 0
    streams: list[_ClosingBytes] = []
    sleeper = RecordingSleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        response, stream = _broken_response(request, status_code=status_code, body_kind=body_kind)
        if stream is not None:
            streams.append(stream)
        return response

    client = new_client(handler, sleeper=sleeper)
    try:
        with pytest.raises(error_type) as raised:
            await _ticker_call(client)
        snapshot = await client._transport._limiter.snapshot()
    finally:
        await client.aclose()
    assert calls == 3
    assert sleeper.delays == [0.001, 0.002]
    assert snapshot.rest_used == 9
    assert all(stream.closed for stream in streams)
    assert "fixture-secret" not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [429, 500, 503])
async def test_unusable_retryable_response_can_recover_on_next_attempt(status_code: int) -> None:
    calls = 0
    sleeper = RecordingSleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _broken_response(request, status_code=status_code, body_kind="text")[0]
        return httpx.Response(
            200,
            content=fixture_bytes("public_ticker.json"),
            headers={"Content-Type": "application/json"},
            request=request,
        )

    client = new_client(handler, sleeper=sleeper)
    try:
        ticker = await _ticker_call(client)
    finally:
        await client.aclose()
    assert ticker.product_id == 101
    assert calls == 2
    assert sleeper.delays == [0.001]
