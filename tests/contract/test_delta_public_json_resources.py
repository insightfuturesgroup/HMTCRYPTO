"""Strict JSON decoding has an explicit, string-aware nesting resource bound."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from hmt_crypto.errors import ExchangeProtocolError
from hmt_crypto.exchange.delta import wire_json

pytestmark = pytest.mark.contract

_MAX_DEPTH = 64


def _array_document(depth: int, leaf: str = "0") -> bytes:
    value = leaf
    for _ in range(depth - 1):
        value = f"[{value}]"
    return f'{{"result":{value}}}'.encode()


def _object_document(depth: int, leaf: str = "0") -> bytes:
    value = leaf
    for _ in range(depth):
        value = f'{{"result":{value}}}'
    return value.encode()


@pytest.mark.parametrize("depth", [_MAX_DEPTH - 1, _MAX_DEPTH])
@pytest.mark.parametrize("document", [_array_document, _object_document])
def test_json_container_depth_at_or_below_limit_is_accepted(
    depth: int, document: Callable[[int, str], bytes]
) -> None:
    decoded = wire_json.decode_json_object(document(depth), endpoint_id="ticker")
    assert isinstance(decoded, dict)


@pytest.mark.parametrize("document", [_array_document, _object_document])
def test_json_container_depth_above_limit_is_safe_protocol_error(
    document: Callable[[int, str], bytes],
) -> None:
    body = document(_MAX_DEPTH + 1, '"fixture-secret-depth"')
    with pytest.raises(ExchangeProtocolError) as raised:
        wire_json.decode_json_object(body, endpoint_id="ticker")
    assert "fixture-secret-depth" not in str(raised.value)
    assert "fixture-secret-depth" not in json.dumps(raised.value.to_dict())


def test_brackets_braces_and_escaped_quotes_inside_strings_do_not_consume_depth() -> None:
    body = json.dumps({"result": '[{"nested":[]}]' * 500}).encode()
    decoded = wire_json.decode_json_object(body, endpoint_id="ticker")
    assert isinstance(decoded["result"], str)


def test_raw_decoder_recursion_error_is_normalized_without_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def recurse(*_args: object, **_kwargs: object) -> object:
        raise RecursionError("fixture-secret-recursion")

    monkeypatch.setattr(wire_json.json, "loads", recurse)
    with pytest.raises(ExchangeProtocolError) as raised:
        wire_json.decode_json_object(b'{"result":{}}', endpoint_id="ticker")
    rendered = str(raised.value) + repr(raised.value) + json.dumps(raised.value.to_dict())
    assert "fixture-secret-recursion" not in rendered
