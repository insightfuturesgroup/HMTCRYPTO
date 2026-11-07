"""Mid-stream transport failures remain retryable transport outcomes."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from delta_public_test_support import RecordingSleeper, fixture_bytes, new_client, public_api

from hmt_crypto.errors import ExchangeDownError, ExchangeTimeoutError

pytestmark = pytest.mark.contract


async def _ticker_call(client: object) -> object:
    return await client.get_ticker(public_api().base.ProductRef(symbol="XBTUSD_TEST"))


class _FailingStream(httpx.AsyncByteStream):
    def __init__(
        self,
        request: httpx.Request,
        error_factory: Callable[[httpx.Request], httpx.RequestError],
    ) -> None:
        self.request = request
        self.error_factory = error_factory
        self.closed = False

    async def __aiter__(self):
        yield b'{"success":true,"result":'
        raise self.error_factory(self.request)

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_factory", "error_type"),
    [
        (
            lambda request: httpx.ReadTimeout("fixture-secret-timeout", request=request),
            ExchangeTimeoutError,
        ),
        (
            lambda request: httpx.ReadError("fixture-secret-read", request=request),
            ExchangeDownError,
        ),
    ],
)
async def test_partial_stream_failure_retries_with_fresh_permits_and_typed_terminal_error(
    error_factory: Callable[[httpx.Request], httpx.RequestError],
    error_type: type[Exception],
) -> None:
    calls = 0
    streams: list[_FailingStream] = []
    sleeper = RecordingSleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        stream = _FailingStream(request, error_factory)
        streams.append(stream)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=stream,
            request=request,
        )

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
@pytest.mark.parametrize(
    "error_factory",
    [
        lambda request: httpx.ReadTimeout("first-attempt", request=request),
        lambda request: httpx.ReadError("first-attempt", request=request),
    ],
)
async def test_partial_stream_failure_can_recover_on_next_attempt(
    error_factory: Callable[[httpx.Request], httpx.RequestError],
) -> None:
    calls = 0
    streams: list[_FailingStream] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            stream = _FailingStream(request, error_factory)
            streams.append(stream)
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                stream=stream,
                request=request,
            )
        return httpx.Response(
            200,
            content=fixture_bytes("public_ticker.json"),
            headers={"Content-Type": "application/json"},
            request=request,
        )

    client = new_client(handler)
    try:
        ticker = await _ticker_call(client)
    finally:
        await client.aclose()
    assert ticker.product_id == 101
    assert calls == 2
    assert streams[0].closed is True
