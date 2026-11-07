"""Response bounds, cancellation, lifecycle, and safe logging contracts."""

from __future__ import annotations

import asyncio
import gzip
import io
import json
from datetime import UTC, datetime

import httpx
import pytest
from delta_public_test_support import fixture_bytes, json_response, new_client, public_api

from hmt_crypto.errors import ConfigurationError, ExchangeProtocolError
from hmt_crypto.logging import configure_logging

pytestmark = pytest.mark.contract


async def _ticker_call(client: object) -> object:
    return await client.get_ticker(public_api().base.ProductRef(symbol="XBTUSD_TEST"))


class ClosingStream(httpx.AsyncByteStream):
    """Chunked response stream whose close/cancellation behavior is observable."""

    def __init__(self, chunks: list[bytes], *, block: bool = False) -> None:
        self.chunks = chunks
        self.block = block
        self.closed = False
        self.started = asyncio.Event()

    async def __aiter__(self):
        for chunk in self.chunks:
            self.started.set()
            yield chunk
        if self.block:
            self.started.set()
            await asyncio.Future()

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_successful_stream_is_always_closed() -> None:
    stream = ClosingStream([fixture_bytes("public_ticker.json")])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=stream,
            request=request,
        )

    client = new_client(handler)
    try:
        assert (await _ticker_call(client)).product_id == 101
    finally:
        await client.aclose()
    assert stream.closed is True


@pytest.mark.asyncio
async def test_declared_and_actual_response_size_bounds_close_streams() -> None:
    spec = public_api().routes.resolve_public_request("GET", "/v2/tickers/XBTUSD_TEST")
    streams: list[ClosingStream] = []
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(spec.max_response_bytes + 1),
                },
                content=b"{}",
                request=request,
            )
        stream = ClosingStream([b"x" * spec.max_response_bytes, b"x"])
        streams.append(stream)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=stream,
            request=request,
        )

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError):
            await _ticker_call(client)
        with pytest.raises(ExchangeProtocolError):
            await _ticker_call(client)
    finally:
        await client.aclose()
    assert streams[0].closed is True


@pytest.mark.asyncio
async def test_decompressed_size_is_bounded_not_only_content_length() -> None:
    spec = public_api().routes.resolve_public_request("GET", "/v2/tickers/XBTUSD_TEST")
    raw = b" " * (spec.max_response_bytes + 1)
    compressed = gzip.compress(raw)
    assert len(compressed) < spec.max_response_bytes

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=compressed,
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
                "Content-Length": str(len(compressed)),
            },
            request=request,
        )

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError):
            await _ticker_call(client)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_malformed_compressed_payload_is_protocol_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"not-a-gzip-stream",
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
            request=request,
        )

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError):
            await _ticker_call(client)
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "length_headers",
    [
        [(b"content-length", b"1"), (b"content-length", b"2")],
        [(b"content-length", b"-1")],
        [(b"content-length", b"1.5")],
    ],
)
async def test_duplicate_or_malformed_declared_lengths_fail_closed(
    length_headers: list[tuple[bytes, bytes]],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=fixture_bytes("public_ticker.json"),
            headers=[(b"content-type", b"application/json"), *length_headers],
            request=request,
        )

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError):
            await _ticker_call(client)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_uncompressed_lying_content_length_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=fixture_bytes("public_ticker.json"),
            headers={"Content-Type": "application/json", "Content-Length": "1"},
            request=request,
        )

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError):
            await _ticker_call(client)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_single_structured_json_media_type_is_accepted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=fixture_bytes("public_ticker.json"),
            headers={"Content-Type": "application/vnd.delta+json; charset=utf-8"},
            request=request,
        )

    client = new_client(handler)
    try:
        assert (await _ticker_call(client)).product_id == 101
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_cancellation_propagates_and_closes_active_stream() -> None:
    stream = ClosingStream([b"{"], block=True)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=stream,
            request=request,
        )

    client = new_client(handler)
    task = asyncio.create_task(_ticker_call(client))
    await stream.started.wait()
    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await client.aclose()
    assert stream.closed is True


@pytest.mark.asyncio
async def test_close_is_idempotent_and_use_after_close_is_typed() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return json_response(request, fixture_bytes("public_ticker.json"))

    client = new_client(handler)
    await client.aclose()
    await client.aclose()
    with pytest.raises(ConfigurationError):
        await _ticker_call(client)
    assert calls == 0


@pytest.mark.asyncio
async def test_async_context_manager_owns_and_closes_client() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(request, fixture_bytes("public_ticker.json"))

    client = new_client(handler)
    async with client as entered:
        assert entered is client
        assert (await _ticker_call(client)).product_id == 101
    with pytest.raises(ConfigurationError):
        await _ticker_call(client)


@pytest.mark.asyncio
async def test_attempt_logs_are_structured_and_do_not_contain_raw_response() -> None:
    stream = io.StringIO()
    configure_logging(json_output=True, stream=stream)
    secret = "fixture-secret-response-message"
    body = json.dumps({"success": False, "error": {"code": "bad", "message": secret}}).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(request, body, status_code=400, headers={"X-Request-ID": "req-1"})

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError):
            await _ticker_call(client)
    finally:
        await client.aclose()
    output = stream.getvalue()
    assert "exchange_request_attempt" in output
    assert "req-1" in output
    assert secret not in output
    assert body.decode() not in output


@pytest.mark.asyncio
async def test_attempt_logs_never_include_url_query_values() -> None:
    stream = io.StringIO()
    configure_logging(json_output=True, stream=stream)

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(request, fixture_bytes("public_candles.json"))

    api = public_api().base
    candle_request = api.CandleRequest(
        product=api.ProductRef(symbol="XBTUSD_TEST"),
        resolution=api.CandleResolution.ONE_MINUTE,
        start=datetime.fromtimestamp(1_700_000_000, UTC),
        end=datetime.fromtimestamp(1_700_000_120, UTC),
    )
    client = new_client(handler)
    try:
        await client.get_candles(candle_request)
    finally:
        await client.aclose()
    output = stream.getvalue()
    assert "resolution=" not in output
    assert "XBTUSD_TEST" not in output
    assert "1700000120" not in output


def test_client_has_no_raw_request_private_or_write_surface() -> None:
    client_type = public_api().client.DeltaPublicClient
    for forbidden in {
        "request",
        "send",
        "place_order",
        "cancel_order",
        "modify_order",
        "get_balance",
        "get_positions",
    }:
        assert not hasattr(client_type, forbidden)
