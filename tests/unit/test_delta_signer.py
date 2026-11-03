"""Delta REST signing must preserve exact wire bytes without leaking auth material."""

from __future__ import annotations

import copy
import dataclasses
import io
import json
import pickle
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from hmt_crypto.errors import ConfigurationError
from hmt_crypto.logging import configure_logging, get_logger

pytestmark = pytest.mark.unit

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "delta" / "signing_vectors.json"
AUTH_KEY = "delta-fixture-key-2026"
AUTH_SECRET = "delta-fixture-secret-2026"
USER_AGENT = "hmt-crypto-signing-test/1.0"


def _api() -> Any:
    try:
        from hmt_crypto.exchange.delta import signer
    except ImportError:
        pytest.fail("Delta signer API is missing", pytrace=False)
    return signer


def _vectors() -> list[dict[str, object]]:
    value = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, list)
    return value


def _signer(*, clock: Callable[[], int] | None = None) -> Any:
    api = _api()
    return api.DeltaSigner(
        api_key=SecretStr(AUTH_KEY),
        api_secret=SecretStr(AUTH_SECRET),
        user_agent=USER_AGENT,
        clock=clock or (lambda: 1_700_000_000),
    )


@pytest.mark.parametrize("vector", _vectors(), ids=lambda vector: str(vector["name"]))
def test_known_vectors_use_literal_expected_prehash_and_hmac(
    vector: dict[str, object],
) -> None:
    signer = _signer()
    method = str(vector["method"])
    timestamp = int(str(vector["timestamp"]))
    path = str(vector["path"])
    query = str(vector["query_string"]).encode("ascii")
    payload = str(vector["payload"]).encode("utf-8")

    expected_prehash = (
        method.upper().encode("ascii")
        + str(timestamp).encode("ascii")
        + path.encode("ascii")
        + query
        + payload
    ).decode("utf-8")
    result = signer.sign_at(
        method,
        timestamp,
        path,
        query_string=query,
        payload=payload,
    )
    headers = result.as_headers()

    assert expected_prehash == vector["expected_prehash"]
    assert headers == {
        "api-key": vector["api_key"],
        "timestamp": str(timestamp),
        "signature": vector["expected_signature"],
        "User-Agent": vector["user_agent"],
    }


def test_json_whitespace_and_key_order_change_signature() -> None:
    signer = _signer()
    compact = b'{"product_id":123,"side":"buy"}'
    reordered = b'{"side":"buy", "product_id":123}'

    first = signer.sign_at("POST", 1_700_000_002, "/v2/orders", payload=compact)
    second = signer.sign_at("POST", 1_700_000_002, "/v2/orders", payload=reordered)

    assert first.as_headers()["signature"] != second.as_headers()["signature"]


def test_query_order_and_percent_encoding_are_not_normalized() -> None:
    signer = _signer()
    first = signer.sign_at("GET", 1_700_000_001, "/v2/orders", query_string=b"?a=one%20two&b=3")
    second = signer.sign_at("GET", 1_700_000_001, "/v2/orders", query_string=b"?b=3&a=one%20two")
    third = signer.sign_at("GET", 1_700_000_001, "/v2/orders", query_string=b"?a=one+two&b=3")

    signatures = {item.as_headers()["signature"] for item in (first, second, third)}
    assert len(signatures) == 3


@pytest.mark.parametrize(
    "query",
    [
        b"a=1",
        b"?",
        b"?note=raw space",
        b"?note=\xe2\x82\xb9",
        b"?value=%",
        b"?value=%2",
        b"?value=%GG",
        b"?value=ok#fragment",
        b"?value=ok\r\nInjected:true",
        bytearray(b"?a=1"),
        memoryview(b"?a=1"),
        "?a=1",
    ],
)
def test_query_boundary_rejects_ambiguous_or_mutable_values(query: object) -> None:
    with pytest.raises(ConfigurationError) as captured:
        _signer().sign_at(
            "GET",
            1_700_000_000,
            "/v2/orders",
            query_string=query,
        )

    rendered = f"{captured.value!s} {captured.value!r}"
    assert AUTH_KEY not in rendered
    assert AUTH_SECRET not in rendered
    assert "Injected:true" not in rendered


@pytest.mark.parametrize(
    "payload",
    ["fixture-payload-do-not-log", bytearray(b"mutable"), memoryview(b"mutable")],
)
def test_payload_boundary_requires_immutable_exact_bytes(payload: object) -> None:
    with pytest.raises(ConfigurationError) as captured:
        _signer().sign_at("POST", 1_700_000_000, "/v2/orders", payload=payload)

    assert "fixture-payload-do-not-log" not in f"{captured.value!s} {captured.value!r}"


@pytest.mark.parametrize(
    "path",
    [
        "v2/orders",
        "//v2/orders",
        "/v2/orders?state=open",
        "/v2/orders#fragment",
        "/v2\\orders",
        "/v2/../orders",
        "/v2/%GG",
        "/v2/orders\x00fixture-path-secret",
        "/v2/देल्टा",
    ],
)
def test_path_boundary_rejects_non_origin_or_non_encoded_paths(path: str) -> None:
    with pytest.raises(ConfigurationError) as captured:
        _signer().sign_at("GET", 1_700_000_000, path)

    assert "fixture-path-secret" not in f"{captured.value!s} {captured.value!r}"


@pytest.mark.parametrize("method", ["", " GET", "GET ", "G ET", "GET\r\nX: yes", "GÉT", 1, True])
def test_method_boundary_normalizes_only_valid_http_tokens(method: object) -> None:
    with pytest.raises(ConfigurationError):
        _signer().sign_at(method, 1_700_000_000, "/v2/profile")


@pytest.mark.parametrize("timestamp", [True, False, 0, -1, 1.5, "1700000000", 1_700_000_000_000])
def test_timestamp_must_be_positive_epoch_seconds(timestamp: object) -> None:
    with pytest.raises(ConfigurationError):
        _signer().sign_at("GET", timestamp, "/v2/profile")


@pytest.mark.parametrize(
    ("api_key", "api_secret", "user_agent"),
    [
        ("plain-key", SecretStr(AUTH_SECRET), USER_AGENT),
        (SecretStr(AUTH_KEY), "plain-secret", USER_AGENT),
        (SecretStr(""), SecretStr(AUTH_SECRET), USER_AGENT),
        (SecretStr(AUTH_KEY), SecretStr(""), USER_AGENT),
        (SecretStr(AUTH_KEY), SecretStr(AUTH_SECRET), ""),
        (SecretStr(AUTH_KEY), SecretStr(AUTH_SECRET), "agent\r\nX: secret"),
    ],
)
def test_credentials_and_user_agent_have_strict_safe_boundaries(
    api_key: object, api_secret: object, user_agent: str
) -> None:
    api = _api()
    with pytest.raises(ConfigurationError) as captured:
        api.DeltaSigner(
            api_key=api_key,
            api_secret=api_secret,
            user_agent=user_agent,
            clock=lambda: 1_700_000_000,
        )

    rendered = f"{captured.value!s} {captured.value!r}"
    assert AUTH_KEY not in rendered
    assert AUTH_SECRET not in rendered
    assert "X: secret" not in rendered


def test_clock_is_consulted_once_only_when_sign_now_is_called() -> None:
    calls: list[str] = []

    def clock() -> int:
        calls.append("clock")
        return 1_700_000_000

    signer = _signer(clock=clock)
    finalized_query = b"?state=open"
    finalized_payload = b'{"reference":"final"}'
    assert calls == []

    result = signer.sign_now(
        "POST",
        "/v2/orders",
        query_string=finalized_query,
        payload=finalized_payload,
    )

    assert calls == ["clock"]
    assert result.as_headers()["timestamp"] == "1700000000"


@pytest.mark.parametrize("clock_value", [True, 1.5, 0, 1_700_000_000_000])
def test_clock_output_uses_the_same_strict_timestamp_boundary(clock_value: object) -> None:
    signer = _signer(clock=lambda: clock_value)

    with pytest.raises(ConfigurationError) as captured:
        signer.sign_now("GET", "/v2/profile")

    assert AUTH_SECRET not in f"{captured.value!s} {captured.value!r}"


def test_auth_header_mapping_is_fresh_and_does_not_mutate_retained_state() -> None:
    signed = _signer().sign_at("GET", 1_700_000_000, "/v2/profile")
    first = signed.as_headers()
    first["api-key"] = "tampered"
    first["signature"] = "tampered"

    second = signed.as_headers()

    assert second["api-key"] == AUTH_KEY
    assert second["signature"] != "tampered"
    with pytest.raises(AttributeError):
        signed.timestamp = "tampered"


def test_signer_and_signed_header_internal_slots_are_immutable() -> None:
    signer = _signer()
    signed = signer.sign_at("GET", 1_700_000_000, "/v2/profile")

    with pytest.raises(AttributeError):
        signer._DeltaSigner__api_secret = SecretStr("replacement")
    with pytest.raises(AttributeError):
        signed._SignedAuthHeaders__timestamp = 1_700_000_001

    assert signed.as_headers()["timestamp"] == "1700000000"


def test_caller_api_key_mutation_cannot_change_existing_or_future_headers() -> None:
    api = _api()
    source_key = SecretStr(AUTH_KEY)
    source_secret = SecretStr(AUTH_SECRET)
    signer = api.DeltaSigner(
        api_key=source_key,
        api_secret=source_secret,
        user_agent=USER_AGENT,
        clock=lambda: 1_700_000_000,
    )
    existing = signer.sign_at("GET", 1_700_000_000, "/v2/profile")

    source_key._secret_value = "caller-mutated-key"

    future = signer.sign_at("GET", 1_700_000_000, "/v2/profile")
    assert existing.as_headers()["api-key"] == AUTH_KEY
    assert future.as_headers()["api-key"] == AUTH_KEY


def test_caller_api_secret_mutation_cannot_change_future_hmac() -> None:
    api = _api()
    source_key = SecretStr(AUTH_KEY)
    source_secret = SecretStr(AUTH_SECRET)
    signer = api.DeltaSigner(
        api_key=source_key,
        api_secret=source_secret,
        user_agent=USER_AGENT,
        clock=lambda: 1_700_000_000,
    )

    source_secret._secret_value = "caller-mutated-secret"
    signed = signer.sign_at("GET", 1_700_000_000, "/v2/profile")

    assert (
        signed.as_headers()["signature"]
        == "49bc03fc21400e9fe3e5ca962379907510818ff9964da29870d0510f2eeaeb74"
    )


def test_credentials_are_independent_immutable_redacting_snapshots() -> None:
    api = _api()
    source_key = SecretStr(AUTH_KEY)
    source_secret = SecretStr(AUTH_SECRET)
    signer = api.DeltaSigner(
        api_key=source_key,
        api_secret=source_secret,
        user_agent=USER_AGENT,
        clock=lambda: 1_700_000_000,
    )
    signed = signer.sign_at("GET", 1_700_000_000, "/v2/profile")
    signer_key = signer._DeltaSigner__api_key
    signer_secret = signer._DeltaSigner__api_secret
    header_key = signed._SignedAuthHeaders__api_key

    assert signer_key is not source_key
    assert signer_secret is not source_secret
    assert header_key is not signer_key
    assert AUTH_KEY not in f"{signer_key!s} {signer_key!r}"
    assert AUTH_SECRET not in f"{signer_secret!s} {signer_secret!r}"
    with pytest.raises(AttributeError):
        signer_key._SecretSnapshot__value = "replacement"
    with pytest.raises(AttributeError):
        header_key._SecretSnapshot__value = "replacement"
    with pytest.raises(TypeError):
        pickle.dumps(signer_secret)

    assert signed.as_headers()["api-key"] == AUTH_KEY


def test_signer_and_signed_headers_never_render_auth_or_prehash() -> None:
    vector = _vectors()[1]
    signer = _signer()
    signed = signer.sign_at(
        str(vector["method"]),
        int(str(vector["timestamp"])),
        str(vector["path"]),
        query_string=str(vector["query_string"]).encode("ascii"),
        payload=str(vector["payload"]).encode("utf-8"),
    )
    forbidden = {
        AUTH_KEY,
        AUTH_SECRET,
        str(vector["expected_signature"]),
        str(vector["expected_prehash"]),
    }

    for rendered in (str(signer), repr(signer), str(signed), repr(signed)):
        assert all(value not in rendered for value in forbidden)
    assert not hasattr(signer, "__dict__")
    assert not hasattr(signed, "__dict__")
    assert all("prehash" not in name and "payload" not in name for name in dir(signer))


@pytest.mark.parametrize(
    "operation",
    [
        copy.copy,
        copy.deepcopy,
        pickle.dumps,
        vars,
        dataclasses.asdict,
        json.dumps,
    ],
    ids=["copy", "deepcopy", "pickle", "vars", "asdict", "json"],
)
def test_signer_cannot_be_copied_or_serialized_accidentally(
    operation: Callable[[object], object],
) -> None:
    signer = _signer()
    with pytest.raises((TypeError, AttributeError)) as captured:
        operation(signer)

    rendered = f"{captured.value!s} {captured.value!r}"
    assert AUTH_KEY not in rendered
    assert AUTH_SECRET not in rendered


@pytest.mark.parametrize(
    "operation",
    [copy.copy, copy.deepcopy, pickle.dumps, vars, dataclasses.asdict, json.dumps],
    ids=["copy", "deepcopy", "pickle", "vars", "asdict", "json"],
)
def test_signed_headers_cannot_be_copied_or_serialized_accidentally(
    operation: Callable[[object], object],
) -> None:
    signed = _signer().sign_at("GET", 1_700_000_000, "/v2/profile")
    signature = signed.as_headers()["signature"]
    with pytest.raises((TypeError, AttributeError)) as captured:
        operation(signed)

    rendered = f"{captured.value!s} {captured.value!r}"
    assert AUTH_KEY not in rendered
    assert AUTH_SECRET not in rendered
    assert signature not in rendered


def test_explicit_header_mapping_is_redacted_by_structured_logging() -> None:
    stream = io.StringIO()
    configure_logging(json_output=True, stream=stream)
    signed = _signer().sign_at("GET", 1_700_000_000, "/v2/profile")
    signature = signed.as_headers()["signature"]

    get_logger("exchange.delta").info("signed_request", headers=signed.as_headers())
    rendered = stream.getvalue()

    assert AUTH_KEY not in rendered
    assert AUTH_SECRET not in rendered
    assert signature not in rendered
    parsed = json.loads(rendered)
    assert "api-key" not in parsed["headers"]
    assert "signature" not in parsed["headers"]
    assert parsed["headers"] == {"user-agent": USER_AGENT}


def test_invalid_request_exceptions_never_include_request_or_auth_bytes() -> None:
    unsafe_query = f"?signature={AUTH_SECRET}&note=fixture-request-secret".encode()
    with pytest.raises(ConfigurationError) as captured:
        _signer().sign_at(
            "POST",
            1_700_000_000,
            "/v2/orders?fixture-path-secret",
            query_string=unsafe_query,
            payload=f'{{"secret":"{AUTH_SECRET}"}}'.encode(),
        )

    rendered = f"{captured.value!s} {captured.value!r}"
    for forbidden in (AUTH_KEY, AUTH_SECRET, "fixture-request-secret", "fixture-path-secret"):
        assert forbidden not in rendered
