"""Bounded HTTP response framing, metadata, and body handling."""

from __future__ import annotations

import asyncio
import re
import zlib
from collections.abc import Mapping

import httpx

from hmt_crypto.errors import ExchangeProtocolError
from hmt_crypto.exchange.delta.routes import PublicRouteSpec

_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")


def response_context(
    response: httpx.Response,
    *,
    spec: PublicRouteSpec,
    correlation_id: str,
) -> dict[str, object]:
    """Build allowlisted response metadata exactly once."""
    context: dict[str, object] = {
        "operation": spec.route_id.value,
        "http_status": response.status_code,
        "correlation_id": correlation_id,
    }
    values = response.headers.get_list("x-request-id")
    if len(values) == 1 and _REQUEST_ID.fullmatch(values[0]) is not None:
        context["request_id"] = values[0]
    return context


def protocol(context: Mapping[str, object]) -> ExchangeProtocolError:
    """Return a response-safe protocol error."""
    return ExchangeProtocolError(context=context)


def _single_header(
    response: httpx.Response,
    name: str,
    context: Mapping[str, object],
) -> str | None:
    values = response.headers.get_list(name)
    if not values:
        return None
    if len(values) != 1 or "," in values[0]:
        raise protocol(context)
    return values[0]


def _validate_media_type(
    response: httpx.Response,
    context: Mapping[str, object],
) -> None:
    value = _single_header(response, "content-type", context)
    if value is None:
        raise protocol(context)
    media_type = value.split(";", 1)[0].strip().casefold()
    parts = media_type.split("/")
    if (
        len(parts) != 2
        or not all(parts)
        or not media_type.isascii()
        or any(not character.isprintable() or character.isspace() for character in media_type)
        or (media_type != "application/json" and not parts[1].endswith("+json"))
    ):
        raise protocol(context)


def _framing(
    response: httpx.Response,
    *,
    maximum: int,
    context: Mapping[str, object],
) -> tuple[int | None, str]:
    transfer = _single_header(response, "transfer-encoding", context)
    length_value = _single_header(response, "content-length", context)
    if transfer is not None and (transfer.casefold() != "chunked" or length_value is not None):
        raise protocol(context)

    declared: int | None = None
    if length_value is not None:
        if (
            not length_value
            or len(length_value) > len(str(maximum))
            or not length_value.isascii()
            or not length_value.isdecimal()
        ):
            raise protocol(context)
        try:
            declared = int(length_value)
        except (ValueError, OverflowError):
            raise protocol(context) from None
        if declared > maximum:
            raise protocol(context)

    encoding_value = _single_header(response, "content-encoding", context)
    encoding = "identity" if encoding_value is None else encoding_value.casefold()
    if encoding not in {"identity", "gzip"}:
        raise protocol(context)
    return declared, encoding


def _decode_gzip(
    chunks: list[bytes],
    *,
    maximum: int,
    context: Mapping[str, object],
) -> bytes:
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    decoded: list[bytes] = []
    decoded_size = 0
    try:
        for chunk in chunks:
            pending = chunk
            while pending:
                piece = decoder.decompress(pending, maximum - decoded_size + 1)
                pending = decoder.unconsumed_tail
                decoded_size += len(piece)
                if decoded_size > maximum:
                    raise protocol(context)
                decoded.append(piece)
        tail = decoder.flush(maximum - decoded_size + 1)
    except zlib.error:
        raise protocol(context) from None
    decoded_size += len(tail)
    if decoded_size > maximum or not decoder.eof or decoder.unused_data:
        raise protocol(context)
    decoded.append(tail)
    return b"".join(decoded)


async def read_json_body(
    response: httpx.Response,
    *,
    spec: PublicRouteSpec,
    context: Mapping[str, object],
) -> bytes:
    """Read one JSON body with strict encoded and decoded byte ceilings."""
    _validate_media_type(response, context)
    declared, encoding = _framing(
        response,
        maximum=spec.max_response_bytes,
        context=context,
    )
    if response.is_stream_consumed:
        body = response.content
        if len(body) > spec.max_response_bytes:
            raise protocol(context)
        if encoding == "identity" and declared is not None and len(body) != declared:
            raise protocol(context)
        return body

    chunks: list[bytes] = []
    encoded_size = 0
    try:
        async for chunk in response.aiter_raw():
            encoded_size += len(chunk)
            if encoded_size > spec.max_response_bytes:
                raise protocol(context)
            chunks.append(chunk)
    except asyncio.CancelledError:
        raise
    except httpx.TransportError:
        raise
    except ExchangeProtocolError:
        raise
    except (httpx.HTTPError, UnicodeError, ValueError):
        raise protocol(context) from None

    if declared is not None and encoded_size != declared:
        raise protocol(context)
    if encoding == "gzip":
        return _decode_gzip(chunks, maximum=spec.max_response_bytes, context=context)
    return b"".join(chunks)


async def drain_status_body(response: httpx.Response, *, maximum: int) -> None:
    """Best-effort bounded drain for a status whose body cannot affect classification."""
    if response.is_stream_consumed:
        return
    size = 0
    try:
        async for chunk in response.aiter_raw():
            size += len(chunk)
            if size > maximum:
                break
    except asyncio.CancelledError:
        raise
    except Exception:  # The already-known retryable status remains authoritative.
        return


__all__: list[str] = []
