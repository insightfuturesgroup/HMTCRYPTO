"""Bounded, redacted JSON codec for Delta WebSocket text frames."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, DecimalException
from typing import TypeAlias

from hmt_crypto.errors import ExchangeProtocolError
from hmt_crypto.exchange.delta.websocket_subscriptions import WsSubscription

WsJsonObject: TypeAlias = Mapping[str, object]

_MAX_FRAME_BYTES = 16 * 1024 * 1024
_MAX_CONTAINER_DEPTH = 64
_MAX_NUMBER_CHARACTERS = 1024
_SAFE_STREAM_LABELS = frozenset({"control", "private", "public"})


class _InvalidFrame(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WsNumberToken:
    """One finite JSON number with its exact checksum-relevant wire spelling."""

    value: Decimal
    lexeme: str

    def __post_init__(self) -> None:
        if (
            type(self.value) is not Decimal
            or not self.value.is_finite()
            or type(self.lexeme) is not str
            or not self.lexeme
            or len(self.lexeme) > _MAX_NUMBER_CHARACTERS
        ):
            raise ValueError("invalid WebSocket number token")


def _protocol_error(stream_label: object) -> ExchangeProtocolError:
    scope = (
        stream_label
        if type(stream_label) is str and stream_label in _SAFE_STREAM_LABELS
        else "websocket"
    )
    return ExchangeProtocolError(context={"operation": "decode_ws_frame", "scope": scope})


def _number_token(lexeme: str) -> WsNumberToken:
    if len(lexeme) > _MAX_NUMBER_CHARACTERS:
        raise _InvalidFrame("number length")
    try:
        value = Decimal(lexeme)
    except DecimalException as error:
        raise _InvalidFrame("number grammar") from error
    if not value.is_finite():
        raise _InvalidFrame("non-finite number")
    return WsNumberToken(value=value, lexeme=lexeme)


def _reject_constant(_value: str) -> object:
    raise _InvalidFrame("non-finite number")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidFrame("duplicate key")
        result[key] = value
    return result


def _validate_container_depth(body: bytes) -> None:
    depth = 0
    in_string = False
    escaped = False
    for byte in body:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in {0x5B, 0x7B}:
            depth += 1
            if depth > _MAX_CONTAINER_DEPTH:
                raise _InvalidFrame("container depth")
        elif byte in {0x5D, 0x7D}:
            depth -= 1


def decode_ws_object(
    frame: str | bytes,
    *,
    max_bytes: int,
    stream_label: str,
) -> dict[str, object]:
    """Decode one bounded text object without retaining rejected frame material."""
    if (
        type(frame) is not str
        or type(max_bytes) is not int
        or not 1 <= max_bytes <= _MAX_FRAME_BYTES
        or type(stream_label) is not str
        or stream_label not in _SAFE_STREAM_LABELS
    ):
        raise _protocol_error(stream_label)
    try:
        body = frame.encode("utf-8", errors="strict")
        if len(body) > max_bytes:
            raise _InvalidFrame("frame size")
        _validate_container_depth(body)
        value = json.loads(
            frame,
            parse_float=_number_token,
            parse_int=_number_token,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (
        UnicodeEncodeError,
        json.JSONDecodeError,
        _InvalidFrame,
        ValueError,
        TypeError,
        RecursionError,
        MemoryError,
        DecimalException,
        OverflowError,
    ):
        raise _protocol_error(stream_label) from None
    if type(value) is not dict:
        raise _protocol_error(stream_label)
    return value


def _subscription_payload(subscriptions: tuple[WsSubscription, ...]) -> list[dict[str, object]]:
    if type(subscriptions) is not tuple or not subscriptions:
        raise _protocol_error("control")
    channels: list[dict[str, object]] = []
    for subscription in subscriptions:
        if type(subscription) is not WsSubscription:
            raise _protocol_error("control")
        row: dict[str, object] = {"name": subscription.channel}
        if subscription.symbols:
            row["symbols"] = list(subscription.symbols)
        channels.append(row)
    return channels


def _encode_control(message_type: str, subscriptions: tuple[WsSubscription, ...]) -> str:
    payload = {"type": message_type, "payload": {"channels": _subscription_payload(subscriptions)}}
    return json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def encode_subscribe(subscriptions: tuple[WsSubscription, ...]) -> str:
    """Encode one deterministic current-name subscribe request."""
    return _encode_control("subscribe", subscriptions)


def encode_unsubscribe(subscriptions: tuple[WsSubscription, ...]) -> str:
    """Encode one deterministic current-name unsubscribe request."""
    return _encode_control("unsubscribe", subscriptions)


def encode_enable_heartbeat() -> str:
    """Encode the documented server-heartbeat enable message."""
    return '{"type":"enable_heartbeat"}'


def encode_ping() -> str:
    """Encode the documented raw JSON ping message."""
    return '{"type":"ping"}'


__all__ = [
    "WsJsonObject",
    "WsNumberToken",
    "decode_ws_object",
    "encode_enable_heartbeat",
    "encode_ping",
    "encode_subscribe",
    "encode_unsubscribe",
]
