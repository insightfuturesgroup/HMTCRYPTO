"""Logs and audit summaries must be useful without retaining secret material."""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

FIXTURE_SECRET = "fixture-secret-7c9e-do-not-log"
FIXTURE_SIGNATURE = "fixture-signature-a14d-do-not-log"
REDACTED = "[REDACTED]"
CAMEL_SENSITIVE_FIELDS = ("authToken", "SecretKey", "xAPIKey", "sessionToken")
HARMLESS_FIELDS = ("monkey", "hockey", "tokenizer", "secretary", "keynote")


def _redaction_api() -> Any:
    try:
        from hmt_crypto.persistence import redaction
    except ModuleNotFoundError:
        pytest.fail("logging redaction API is missing", pytrace=False)
    return redaction


def _logging_api() -> Any:
    try:
        from hmt_crypto import logging as logging_api
    except ImportError:
        pytest.fail("structured logging API is missing", pytrace=False)
    return logging_api


def _rendered(value: object) -> str:
    return json.dumps(value, sort_keys=True)


@pytest.mark.parametrize(
    "field_name",
    [
        "api-key",
        "API_SECRET",
        "x-signature",
        "Authorization",
        "cookie",
        "Set-Cookie",
        "signed_prehash",
        "signingPayload",
        "access_token",
        "refresh-token",
        "password",
        "passphrase",
    ],
)
def test_sensitive_fields_are_redacted_without_mutating_input(field_name: str) -> None:
    api = _redaction_api()
    source = {field_name: FIXTURE_SECRET, "monkey": "banana", "symbol": "BTCUSD"}

    result = api.redact(source)

    assert result[field_name] == REDACTED
    assert result["monkey"] == "banana"
    assert result["symbol"] == "BTCUSD"
    assert source[field_name] == FIXTURE_SECRET
    assert FIXTURE_SECRET not in _rendered(result)


@pytest.mark.parametrize("field_name", CAMEL_SENSITIVE_FIELDS)
def test_camel_and_acronym_sensitive_fields_are_recursively_redacted(field_name: str) -> None:
    api = _redaction_api()
    source = {
        "details": {
            field_name: FIXTURE_SECRET,
            **{name: f"safe-{name}" for name in HARMLESS_FIELDS},
        }
    }

    result = api.redact(source)

    assert result["details"][field_name] == REDACTED
    for harmless in HARMLESS_FIELDS:
        assert result["details"][harmless] == f"safe-{harmless}"
    assert FIXTURE_SECRET not in _rendered(result)


@pytest.mark.parametrize("json_output", [True, False], ids=["json", "console"])
def test_structured_log_renderers_redact_camel_and_acronym_sensitive_fields(
    json_output: bool,
) -> None:
    api = _logging_api()
    stream = io.StringIO()
    secrets = {
        field_name: f"{FIXTURE_SECRET}-{index}"
        for index, field_name in enumerate(CAMEL_SENSITIVE_FIELDS)
    }
    harmless = {name: f"safe-{name}" for name in HARMLESS_FIELDS}
    api.configure_logging(json_output=json_output, stream=stream)

    api.get_logger("exchange").info("camel_case_redaction", details={**secrets, **harmless})
    rendered = stream.getvalue()

    for secret in secrets.values():
        assert secret not in rendered
    for safe_value in harmless.values():
        assert safe_value in rendered
    assert REDACTED in rendered
    if json_output:
        event = json.loads(rendered)
        for field_name in CAMEL_SENSITIVE_FIELDS:
            assert event["details"][field_name] == REDACTED


def test_nested_values_are_json_safe_and_never_render_secret_objects() -> None:
    api = _redaction_api()

    class LeakyObject:
        def __str__(self) -> str:
            return FIXTURE_SECRET

        def __repr__(self) -> str:
            return FIXTURE_SECRET

    source = {
        "safe": ["visible", {"ApiSecret": FIXTURE_SECRET}],
        "secret_value": SecretStr(FIXTURE_SECRET),
        "binary": FIXTURE_SECRET.encode(),
        "set_value": {"beta", "alpha"},
        "unknown": LeakyObject(),
    }

    result = api.redact(source)
    rendered = _rendered(result)

    assert result["safe"] == ["visible", {"ApiSecret": REDACTED}]
    assert result["secret_value"] == REDACTED
    assert result["binary"]["byte_length"] == len(FIXTURE_SECRET)
    assert result["set_value"] == ["alpha", "beta"]
    assert result["unknown"] == "<LeakyObject>"
    assert FIXTURE_SECRET not in rendered
    assert source["safe"][1]["ApiSecret"] == FIXTURE_SECRET


def test_recursive_input_is_bounded_instead_of_crashing() -> None:
    api = _redaction_api()
    source: dict[str, object] = {"api_key": FIXTURE_SECRET}
    source["self"] = source

    result = api.redact(source)

    assert result == {"api_key": REDACTED, "self": "[CYCLE]"}


def test_url_query_and_userinfo_are_redacted_while_safe_values_remain() -> None:
    api = _redaction_api()
    url = (
        f"https://user:{FIXTURE_SECRET}@example.test/v2/orders"
        f"?symbol=BTCUSD&api_key={FIXTURE_SECRET}&signature={FIXTURE_SIGNATURE}"
    )

    result = api.redact_url(url)

    assert result.startswith("https://%5BREDACTED%5D@example.test/v2/orders?")
    assert "symbol=BTCUSD" in result
    assert "api_key=%5BREDACTED%5D" in result
    assert "signature=%5BREDACTED%5D" in result
    assert FIXTURE_SECRET not in result
    assert FIXTURE_SIGNATURE not in result


def test_header_redaction_is_case_insensitive() -> None:
    api = _redaction_api()
    headers = {
        "AUTHORIZATION": f"Bearer {FIXTURE_SECRET}",
        "Signature": FIXTURE_SIGNATURE,
        "Content-Type": "application/json",
    }

    result = api.redact_headers(headers)

    assert result == {
        "authorization": REDACTED,
        "signature": REDACTED,
        "content-type": "application/json",
    }


def test_body_summary_contains_only_size_hash_and_allowlisted_redacted_fields() -> None:
    api = _redaction_api()
    body = b'{"symbol":"BTCUSD","api_key":"body-secret","ignored":"hidden"}'

    result = api.safe_body_summary(body, allowlisted_fields={"symbol", "api_key"})

    assert result == {
        "byte_length": 62,
        "sha256": "f82b93f01d8db8db39a351b610414c57fff4b91e48f05df348ef4527e2956675",
        "fields": {"symbol": "BTCUSD", "api_key": REDACTED},
    }
    assert "body-secret" not in _rendered(result)
    assert "hidden" not in _rendered(result)


def test_request_summary_redacts_headers_query_and_buffered_json_body() -> None:
    api = _redaction_api()
    request = httpx.Request(
        "POST",
        f"https://example.test/v2/orders?api-key={FIXTURE_SECRET}&symbol=BTCUSD",
        headers={
            "Authorization": f"Bearer {FIXTURE_SECRET}",
            "Signature": FIXTURE_SIGNATURE,
            "X-Request-ID": "request-123",
            "Content-Type": "application/json",
        },
        content=json.dumps({"symbol": "BTCUSD", "api_secret": FIXTURE_SECRET}),
    )

    result = api.safe_request_summary(request, allowlisted_body_fields={"symbol", "api_secret"})
    rendered = _rendered(result)

    assert result["method"] == "POST"
    assert result["headers"]["content-type"] == "application/json"
    assert result["headers"]["x-request-id"] == "request-123"
    assert "authorization" not in result["headers"]
    assert "signature" not in result["headers"]
    assert result["body"]["fields"] == {"symbol": "BTCUSD", "api_secret": REDACTED}
    assert "symbol=BTCUSD" in result["url"]
    assert FIXTURE_SECRET not in rendered
    assert FIXTURE_SIGNATURE not in rendered


def test_response_summary_uses_only_safe_metadata_and_buffered_body() -> None:
    api = _redaction_api()
    request = httpx.Request("GET", "https://example.test/v2/tickers/BTCUSD")
    response = httpx.Response(
        429,
        headers={
            "Set-Cookie": f"session={FIXTURE_SECRET}",
            "X-Request-ID": "request-456",
            "Retry-After": "3",
        },
        json={"success": False, "api_secret": FIXTURE_SECRET},
        request=request,
    )

    result = api.safe_response_summary(response, allowlisted_body_fields={"success", "api_secret"})
    rendered = _rendered(result)

    assert result["status_code"] == 429
    assert result["headers"]["retry-after"] == "3"
    assert result["headers"]["x-request-id"] == "request-456"
    assert "set-cookie" not in result["headers"]
    assert result["body"]["fields"] == {"success": False, "api_secret": REDACTED}
    assert FIXTURE_SECRET not in rendered


def test_summaries_never_consume_streaming_bodies() -> None:
    api = _redaction_api()

    class NeverRead(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            raise AssertionError("logging consumed a streaming body")

    request = httpx.Request("POST", "https://example.test/v2/orders", stream=NeverRead())
    response = httpx.Response(200, request=request, stream=NeverRead())

    assert api.safe_request_summary(request)["body"] == {"streaming": True}
    assert api.safe_response_summary(response)["body"] == {"streaming": True}


def test_httpx_exception_summary_does_not_render_request_secrets() -> None:
    api = _redaction_api()
    request = httpx.Request(
        "GET",
        f"https://example.test/v2/positions?api_key={FIXTURE_SECRET}",
        headers={"Authorization": FIXTURE_SECRET, "Signature": FIXTURE_SIGNATURE},
    )
    response = httpx.Response(401, request=request)
    error = httpx.HTTPStatusError(FIXTURE_SECRET, request=request, response=response)

    result = api.safe_exception_summary(error)
    rendered = _rendered(result)

    assert result["type"] == "HTTPStatusError"
    assert result["request"]["method"] == "GET"
    assert result["response"]["status_code"] == 401
    assert FIXTURE_SECRET not in rendered
    assert FIXTURE_SIGNATURE not in rendered


def test_json_logging_redacts_direct_kwargs_and_binds_correlation_id() -> None:
    api = _logging_api()
    stream = io.StringIO()
    api.configure_logging(json_output=True, stream=stream)
    api.bind_correlation_id("correlation-123")

    api.get_logger("exchange").info(
        "request_sent", api_key=FIXTURE_SECRET, signature=FIXTURE_SIGNATURE, symbol="BTCUSD"
    )
    api.clear_correlation_id()
    event = json.loads(stream.getvalue())

    assert event["event"] == "request_sent"
    assert event["component"] == "exchange"
    assert event["correlation_id"] == "correlation-123"
    assert event["api_key"] == REDACTED
    assert event["signature"] == REDACTED
    assert event["symbol"] == "BTCUSD"
    assert FIXTURE_SECRET not in stream.getvalue()
    assert FIXTURE_SIGNATURE not in stream.getvalue()


def test_console_logging_is_redacted_and_configuration_is_idempotent() -> None:
    api = _logging_api()
    stream = io.StringIO()
    api.configure_logging(json_output=False, stream=stream)
    api.configure_logging(json_output=False, stream=stream)

    api.get_logger("risk").warning("risk_rejected", authorization=FIXTURE_SECRET)
    rendered = stream.getvalue()

    assert rendered.count("risk_rejected") == 1
    assert REDACTED in rendered
    assert FIXTURE_SECRET not in rendered


def test_component_log_levels_filter_only_the_selected_component() -> None:
    api = _logging_api()
    stream = io.StringIO()
    api.configure_logging(
        json_output=True,
        stream=stream,
        default_level="INFO",
        component_levels={"exchange": "WARNING"},
    )

    api.get_logger("exchange").info("filtered")
    api.get_logger("exchange").warning("exchange_warning")
    api.get_logger("risk").info("risk_info")
    events = [json.loads(line)["event"] for line in stream.getvalue().splitlines()]

    assert events == ["exchange_warning", "risk_info"]


@pytest.mark.asyncio
async def test_correlation_ids_are_context_local_between_async_tasks() -> None:
    api = _logging_api()
    stream = io.StringIO()
    api.configure_logging(json_output=True, stream=stream)

    async def emit(event: str, correlation_id: str) -> None:
        api.bind_correlation_id(correlation_id)
        await asyncio.sleep(0)
        api.get_logger("worker").info(event)
        api.clear_correlation_id()

    await asyncio.gather(emit("first", "correlation-a"), emit("second", "correlation-b"))
    events = {item["event"]: item for item in map(json.loads, stream.getvalue().splitlines())}

    assert events["first"]["correlation_id"] == "correlation-a"
    assert events["second"]["correlation_id"] == "correlation-b"


def test_logged_httpx_exception_is_safely_summarized() -> None:
    api = _logging_api()
    stream = io.StringIO()
    api.configure_logging(json_output=True, stream=stream)
    request = httpx.Request(
        "GET",
        f"https://example.test/v2/orders?signature={FIXTURE_SIGNATURE}",
        headers={"Authorization": FIXTURE_SECRET},
    )

    try:
        raise httpx.RequestError(FIXTURE_SECRET, request=request)
    except httpx.RequestError:
        api.get_logger("exchange").exception("request_failed")

    rendered = stream.getvalue()
    event = json.loads(rendered)
    assert event["error"]["type"] == "RequestError"
    assert event["error"]["request"]["method"] == "GET"
    assert FIXTURE_SECRET not in rendered
    assert FIXTURE_SIGNATURE not in rendered
