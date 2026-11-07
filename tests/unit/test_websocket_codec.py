"""Bounded WebSocket frame codec contracts (manifest F03/F08/F11/F23)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from hmt_crypto.errors import ExchangeProtocolError
from hmt_crypto.exchange.delta.websocket_subscriptions import WsSubscription

FIXTURES = Path(__file__).parents[1] / "fixtures/delta/websocket/control"


def codec() -> Any:
    try:
        return import_module("hmt_crypto.exchange.delta.websocket_codec")
    except ModuleNotFoundError:
        pytest.fail("WebSocket codec is missing", pytrace=False)


@pytest.mark.parametrize("name", ("subscriptions", "heartbeat", "pong"))
def test_documented_control_fixtures_decode_as_text_objects(name: str) -> None:
    frame = (FIXTURES / f"{name}.json").read_text().strip()
    decoded = codec().decode_ws_object(frame, max_bytes=4096, stream_label="public")
    assert isinstance(decoded, dict)
    assert decoded["type"] == name


def test_number_tokens_preserve_decimal_value_and_exact_wire_lexeme() -> None:
    api = codec()
    decoded = api.decode_ws_object(
        '{"integer":12,"trailing":12.3400,"exponent":1.20E+3}',
        max_bytes=4096,
        stream_label="public",
    )

    assert decoded == {
        "integer": api.WsNumberToken(Decimal("12"), "12"),
        "trailing": api.WsNumberToken(Decimal("12.3400"), "12.3400"),
        "exponent": api.WsNumberToken(Decimal("1.20E+3"), "1.20E+3"),
    }


@pytest.mark.parametrize(
    "frame",
    (
        b'{"type":"pong"}',
        b"\xff",
        '{"type":"pong","type":"heartbeat"}',
        "[]",
        '{"value":NaN}',
        '{"value":Infinity}',
        '{"value":-Infinity}',
        "{" * 65 + "}" * 65,
        '"not-an-object"',
    ),
)
def test_binary_duplicate_deep_nonfinite_or_nonobject_frames_fail_closed(
    frame: str | bytes,
) -> None:
    with pytest.raises(ExchangeProtocolError) as error:
        codec().decode_ws_object(frame, max_bytes=4096, stream_label="public")
    assert error.value.context == {"operation": "decode_ws_frame", "scope": "public"}


def test_utf8_byte_limit_is_enforced_before_json_decode() -> None:
    frame = '{"value":"€"}'
    assert len(frame) < len(frame.encode("utf-8"))
    with pytest.raises(ExchangeProtocolError):
        codec().decode_ws_object(
            frame,
            max_bytes=len(frame.encode("utf-8")) - 1,
            stream_label="public",
        )


def test_failures_retain_no_raw_frame_or_untrusted_label() -> None:
    secret = "fixture-secret-private-frame"
    with pytest.raises(ExchangeProtocolError) as error:
        codec().decode_ws_object(
            f'{{"{secret}":',
            max_bytes=4096,
            stream_label=secret,
        )
    rendered = str(error.value) + repr(error.value) + str(error.value.to_dict())
    assert secret not in rendered


@dataclass(frozen=True, slots=True)
class FixtureSubscription:
    channel: str
    symbols: tuple[str, ...]


def test_control_encoders_are_deterministic_compact_and_mapping_free() -> None:
    api = codec()
    subscriptions = (
        WsSubscription(channel="system_status", symbols=()),
        WsSubscription(channel="ticker", symbols=("BTCUSD", "ETHUSD")),
    )
    expected_payload = (
        '{"type":"subscribe","payload":{"channels":['
        '{"name":"system_status"},'
        '{"name":"ticker","symbols":["BTCUSD","ETHUSD"]}]}}'
    )

    assert api.encode_subscribe(subscriptions) == expected_payload
    assert api.encode_unsubscribe(subscriptions) == expected_payload.replace(
        '"subscribe"', '"unsubscribe"', 1
    )
    assert api.encode_enable_heartbeat() == '{"type":"enable_heartbeat"}'
    assert api.encode_ping() == '{"type":"ping"}'
    with pytest.raises(ExchangeProtocolError):
        api.encode_subscribe(({"channel": "ticker", "symbols": ("BTCUSD",)},))
    with pytest.raises(ExchangeProtocolError):
        api.encode_subscribe((FixtureSubscription(channel="ticker", symbols=("BTCUSD",)),))


@pytest.mark.parametrize(
    ("frame", "max_bytes", "stream_label"),
    (
        ('{"type":"pong"}', True, "public"),
        ('{"type":"pong"}', 0, "public"),
        ('{"type":"pong"}', 16 * 1024 * 1024 + 1, "public"),
        ('{"type":"pong"}', 4096, "PUBLIC/fixture-secret"),
    ),
)
def test_decoder_policy_arguments_are_exact_and_bounded(
    frame: str, max_bytes: object, stream_label: str
) -> None:
    with pytest.raises(ExchangeProtocolError):
        codec().decode_ws_object(
            frame,
            max_bytes=max_bytes,
            stream_label=stream_label,
        )
