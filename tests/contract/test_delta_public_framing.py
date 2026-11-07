"""HTTP response framing and content encoding fail closed."""

from __future__ import annotations

import gzip

import httpx
import pytest
from delta_public_test_support import fixture_bytes, new_client, public_api

from hmt_crypto.errors import ExchangeProtocolError

pytestmark = pytest.mark.contract


async def _ticker_call(client: object) -> object:
    return await client.get_ticker(public_api().base.ProductRef(symbol="XBTUSD_TEST"))


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra_headers",
    [
        [(b"transfer-encoding", b"chunked"), (b"content-length", b"10")],
        [(b"transfer-encoding", b"chunked"), (b"transfer-encoding", b"chunked")],
        [(b"transfer-encoding", b"chunked, gzip")],
        [(b"transfer-encoding", b"identity")],
    ],
)
async def test_ambiguous_or_unsupported_transfer_framing_is_rejected(
    extra_headers: list[tuple[bytes, bytes]],
) -> None:
    stream = _Chunks(fixture_bytes("public_ticker.json"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers=[(b"content-type", b"application/json"), *extra_headers],
            stream=stream,
            request=request,
        )

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError):
            await _ticker_call(client)
    finally:
        await client.aclose()
    assert stream.closed is True


@pytest.mark.asyncio
async def test_single_chunked_transfer_without_content_length_is_accepted() -> None:
    body = fixture_bytes("public_ticker.json")
    stream = _Chunks(body[:23], body[23:])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/json",
                "Transfer-Encoding": "chunked",
            },
            stream=stream,
            request=request,
        )

    client = new_client(handler)
    try:
        ticker = await _ticker_call(client)
    finally:
        await client.aclose()
    assert ticker.product_id == 101
    assert stream.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "encoding_headers",
    [
        [(b"content-encoding", b"br")],
        [(b"content-encoding", b"gzip, identity")],
        [(b"content-encoding", b"gzip"), (b"content-encoding", b"gzip")],
    ],
)
async def test_unknown_or_ambiguous_content_encoding_is_rejected(
    encoding_headers: list[tuple[bytes, bytes]],
) -> None:
    stream = _Chunks(fixture_bytes("public_ticker.json"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers=[(b"content-type", b"application/json"), *encoding_headers],
            stream=stream,
            request=request,
        )

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError):
            await _ticker_call(client)
    finally:
        await client.aclose()
    assert stream.closed is True


@pytest.mark.asyncio
async def test_gzip_encoding_checks_encoded_length_and_decodes_successfully() -> None:
    encoded = gzip.compress(fixture_bytes("public_ticker.json"))
    stream = _Chunks(encoded[:17], encoded[17:])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
                "Content-Length": str(len(encoded)),
            },
            stream=stream,
            request=request,
        )

    client = new_client(handler)
    try:
        ticker = await _ticker_call(client)
    finally:
        await client.aclose()
    assert ticker.product_id == 101
    assert stream.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("declared_delta", [-1, 1])
async def test_identity_encoding_requires_exact_declared_length(declared_delta: int) -> None:
    body = fixture_bytes("public_ticker.json")
    stream = _Chunks(body)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "identity",
                "Content-Length": str(len(body) + declared_delta),
            },
            stream=stream,
            request=request,
        )

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError):
            await _ticker_call(client)
    finally:
        await client.aclose()
    assert stream.closed is True


@pytest.mark.asyncio
async def test_identity_encoding_with_exact_length_is_accepted() -> None:
    body = fixture_bytes("public_ticker.json")
    stream = _Chunks(body)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "identity",
                "Content-Length": str(len(body)),
            },
            stream=stream,
            request=request,
        )

    client = new_client(handler)
    try:
        ticker = await _ticker_call(client)
    finally:
        await client.aclose()
    assert ticker.product_id == 101
    assert stream.closed is True
