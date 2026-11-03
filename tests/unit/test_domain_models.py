"""Strict domain boundaries protect numeric, time, identity, and error data."""

from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError


def domain_api() -> SimpleNamespace:
    """Load the wished-for public API without turning initial RED into collection errors."""
    try:
        money = importlib.import_module("hmt_crypto.domain.money")
        enums = importlib.import_module("hmt_crypto.domain.enums")
        models = importlib.import_module("hmt_crypto.domain.models")
        errors = importlib.import_module("hmt_crypto.errors")
    except (AttributeError, ImportError, ModuleNotFoundError):
        pytest.fail("domain and safe-error APIs are missing")
    return SimpleNamespace(
        parse_decimal=money.parse_decimal,
        parse_utc_datetime=money.parse_utc_datetime,
        ContractType=enums.ContractType,
        NotionalType=enums.NotionalType,
        Asset=models.Asset,
        Product=models.Product,
        Ticker=models.Ticker,
        Candle=models.Candle,
        RequestReference=models.RequestReference,
        AuthError=errors.AuthError,
        SignatureExpiredError=errors.SignatureExpiredError,
        UnauthorizedApiAccessError=errors.UnauthorizedApiAccessError,
        RateLimitError=errors.RateLimitError,
        ExchangeDownError=errors.ExchangeDownError,
        OrderRejected=errors.OrderRejected,
        ExchangeProtocolError=errors.ExchangeProtocolError,
        AmbiguousExchangeResult=errors.AmbiguousExchangeResult,
        UnsupportedProductError=errors.UnsupportedProductError,
        ConfigurationError=getattr(errors, "ConfigurationError", None),
        ExchangeTimeoutError=getattr(errors, "ExchangeTimeoutError", None),
        ExchangeError=errors.ExchangeError,
        HmtCryptoError=errors.HmtCryptoError,
    )


def asset(api: SimpleNamespace, identifier: int, symbol: str) -> Any:
    return api.Asset(id=identifier, symbol=symbol, precision=8, minimum_precision=2)


def product_values(api: SimpleNamespace) -> dict[str, Any]:
    return {
        "id": 27,
        "symbol": "BTCUSD",
        "contract_type": "perpetual_futures",
        "notional_type": "vanilla",
        "contract_value": "0.001",
        "contract_unit_currency": "BTC",
        "tick_size": "0.5",
        "state": "live",
        "trading_status": "operational",
        "underlying_asset": asset(api, 1, "BTC"),
        "quoting_asset": asset(api, 2, "USD"),
        "settling_asset": asset(api, 2, "USD"),
        "is_quanto": False,
        "only_reduce_only_orders_allowed": False,
        "position_size_limit": "10000",
        "position_notional_limit": "5000000",
        "initial_margin": "0.005",
        "maintenance_margin": "0.0025",
        "default_leverage": "200",
        "max_leverage_notional": "100000",
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (Decimal("0.100"), Decimal("0.100")),
        (17, Decimal("17")),
        ("-1.2500", Decimal("-1.2500")),
        (" 42.5 ", Decimal("42.5")),
    ],
)
def test_parse_decimal_preserves_exact_values(raw: object, expected: Decimal) -> None:
    api = domain_api()
    assert api.parse_decimal(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        0.1,
        True,
        False,
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        "NaN",
        "Infinity",
        "",
        "  ",
        "1,000",
        object(),
    ],
)
def test_parse_decimal_rejects_ambiguous_or_non_finite_values(raw: object) -> None:
    api = domain_api()
    with pytest.raises((TypeError, ValueError), match="finite decimal"):
        api.parse_decimal(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            datetime(2026, 1, 2, 8, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))),
            datetime(2026, 1, 2, 3, 0, tzinfo=UTC),
        ),
        ("2026-01-02T08:30:00+05:30", datetime(2026, 1, 2, 3, 0, tzinfo=UTC)),
        (1_735_786_800, datetime(2025, 1, 2, 3, 0, tzinfo=UTC)),
    ],
)
def test_parse_utc_datetime_normalizes_explicit_time_inputs(
    raw: object, expected: datetime
) -> None:
    api = domain_api()
    assert api.parse_utc_datetime(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        datetime(2026, 1, 2, 3, 0),
        "2026-01-02T03:00:00",
        1_735_786_800_000,
        -1,
        1_735_786_800.0,
        True,
    ],
)
def test_parse_utc_datetime_rejects_naive_millisecond_and_ambiguous_inputs(raw: object) -> None:
    api = domain_api()
    with pytest.raises((TypeError, ValueError), match="UTC-aware"):
        api.parse_utc_datetime(raw)


def test_asset_and_product_preserve_exact_typed_metadata_and_are_immutable() -> None:
    api = domain_api()
    product = api.Product(**product_values(api))

    assert product.id == 27
    assert product.contract_type is api.ContractType.PERPETUAL_FUTURES
    assert product.notional_type is api.NotionalType.VANILLA
    assert product.contract_value == Decimal("0.001")
    assert product.tick_size == Decimal("0.5")
    assert product.position_notional_limit == Decimal("5000000")
    assert product.default_leverage == Decimal("200")
    assert product.underlying_asset.id == 1
    assert product.model_dump(mode="json")["contract_value"] == "0.001"

    with pytest.raises(ValidationError, match="frozen"):
        product.id = 99
    with pytest.raises(ValidationError, match="frozen"):
        product.underlying_asset.id = 99


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("id", 0),
        ("symbol", "  "),
        ("contract_type", "mystery_swap"),
        ("notional_type", "magic"),
        ("contract_value", 0.001),
        ("tick_size", True),
        ("position_size_limit", Decimal("NaN")),
        ("default_leverage", "0"),
        ("is_quanto", 1),
    ],
)
def test_product_rejects_invalid_identity_enum_numeric_and_boolean_inputs(
    path: str, value: object
) -> None:
    api = domain_api()
    values = product_values(api)
    values[path] = value
    with pytest.raises(ValidationError):
        api.Product(**values)


def test_product_rejects_nested_float_metadata() -> None:
    api = domain_api()
    values = product_values(api)
    values["underlying_asset"] = {
        "id": 1,
        "symbol": "BTC",
        "precision": 8.0,
        "minimum_precision": 2,
    }
    with pytest.raises(ValidationError):
        api.Product(**values)


@pytest.mark.parametrize(
    ("changes", "secret"),
    [
        ({"contract_value": "fixture-secret-contract-value"}, "fixture-secret-contract-value"),
        (
            {
                "underlying_asset": {
                    "id": "fixture-secret-nested-id",
                    "symbol": "BTC",
                    "precision": 8,
                    "minimum_precision": 2,
                }
            },
            "fixture-secret-nested-id",
        ),
        ({"unexpected_field": "fixture-secret-extra-value"}, "fixture-secret-extra-value"),
    ],
)
def test_domain_validation_errors_remove_inputs_from_every_serialization_surface(
    changes: dict[str, object], secret: str
) -> None:
    api = domain_api()
    values = product_values(api)
    values.update(changes)
    with pytest.raises(ValidationError) as caught:
        api.Product(**values)
    rendered = "\n".join([str(caught.value), repr(caught.value.errors()), caught.value.json()])
    assert secret not in rendered
    assert all(detail.get("input") is None for detail in caught.value.errors())


def test_ticker_normalizes_utc_and_keeps_decimal_prices() -> None:
    api = domain_api()
    ticker = api.Ticker(
        product_id=27,
        symbol="BTCUSD",
        timestamp="2026-01-02T08:30:00+05:30",
        product_trading_status="operational",
        mark_price="100.25",
        spot_price="100.20",
        close="99.50",
        best_bid="100.10",
        best_ask="100.30",
        lower_price_limit="80",
        upper_price_limit="120",
    )
    assert ticker.timestamp == datetime(2026, 1, 2, 3, 0, tzinfo=UTC)
    assert ticker.mark_price == Decimal("100.25")
    assert ticker.best_bid == Decimal("100.10")


@pytest.mark.parametrize(
    "changes",
    [
        {"best_bid": "101", "best_ask": "100"},
        {"lower_price_limit": "120", "upper_price_limit": "80"},
        {"mark_price": "0"},
        {"spot_price": "-1"},
        {"best_bid": 100.0},
        {"timestamp": datetime(2026, 1, 2, 3, 0)},
    ],
)
def test_ticker_fails_closed_on_inconsistent_or_ambiguous_market_data(
    changes: dict[str, object],
) -> None:
    api = domain_api()
    values: dict[str, object] = {
        "product_id": 27,
        "symbol": "BTCUSD",
        "timestamp": datetime(2026, 1, 2, 3, 0, tzinfo=UTC),
        "product_trading_status": "operational",
        "mark_price": "100",
        "best_bid": "99",
        "best_ask": "101",
        "lower_price_limit": "80",
        "upper_price_limit": "120",
    }
    values.update(changes)
    with pytest.raises(ValidationError):
        api.Ticker(**values)


def test_candle_preserves_exact_ohlcv_and_utc_timestamp() -> None:
    api = domain_api()
    candle = api.Candle(
        timestamp=1_735_786_800,
        open="100.0",
        high="110.0",
        low="90.0",
        close="105.0",
        volume="12.5",
    )
    assert candle.timestamp == datetime(2025, 1, 2, 3, 0, tzinfo=UTC)
    assert candle.close == Decimal("105.0")
    assert candle.volume == Decimal("12.5")


@pytest.mark.parametrize(
    "changes",
    [
        {"high": "99"},
        {"low": "101"},
        {"high": "89", "low": "90"},
        {"volume": "-0.01"},
        {"open": 100.0},
        {"timestamp": "2026-01-02T03:00:00"},
    ],
)
def test_candle_rejects_invalid_ranges_volume_float_and_naive_time(
    changes: dict[str, object],
) -> None:
    api = domain_api()
    values: dict[str, object] = {
        "timestamp": datetime(2026, 1, 2, 3, 0, tzinfo=UTC),
        "open": "100",
        "high": "110",
        "low": "90",
        "close": "105",
        "volume": "12.5",
    }
    values.update(changes)
    with pytest.raises(ValidationError):
        api.Candle(**values)


def test_request_reference_is_typed_and_immutable() -> None:
    api = domain_api()
    reference = api.RequestReference(
        correlation_id="12345678-1234-5678-1234-567812345678",
        exchange_request_id=" req-42 ",
    )
    assert reference.correlation_id == UUID("12345678-1234-5678-1234-567812345678")
    assert reference.exchange_request_id == "req-42"
    with pytest.raises(ValidationError, match="frozen"):
        reference.exchange_request_id = "changed"


@pytest.mark.parametrize(
    ("error_name", "expected_code", "expected_message"),
    [
        ("AuthError", "auth_error", "Exchange authentication failed"),
        ("SignatureExpiredError", "signature_expired", "Exchange signature expired"),
        (
            "UnauthorizedApiAccessError",
            "unauthorized_api_access",
            "Exchange API access is unauthorized",
        ),
        ("RateLimitError", "rate_limit", "Exchange rate limit exceeded"),
        ("ExchangeDownError", "exchange_down", "Exchange is unavailable"),
        ("ExchangeTimeoutError", "exchange_timeout", "Exchange request timed out"),
        ("ConfigurationError", "configuration_error", "Configuration is invalid"),
        ("OrderRejected", "order_rejected", "Exchange rejected the order"),
        ("ExchangeProtocolError", "exchange_protocol_error", "Exchange protocol error"),
        (
            "AmbiguousExchangeResult",
            "ambiguous_exchange_result",
            "Exchange result is ambiguous",
        ),
        ("UnsupportedProductError", "unsupported_product", "Product model is unsupported"),
    ],
)
def test_error_subclasses_have_stable_safe_codes_and_messages(
    error_name: str, expected_code: str, expected_message: str
) -> None:
    api = domain_api()
    error_type = getattr(api, error_name)
    assert error_type is not None, f"{error_name} is missing"
    error = error_type()
    assert error.code == expected_code
    assert error.message == expected_message
    assert str(error) == f"{expected_code}: {expected_message}"
    assert error.to_dict() == {
        "code": expected_code,
        "message": expected_message,
        "context": {},
    }


def test_error_sanitizes_before_retaining_or_rendering_context() -> None:
    api = domain_api()
    secrets = {
        "api_key": "fixture-api-key-9fd3",
        "authorization": "Bearer fixture-auth-401a",
        "signature": "fixture-signature-2ce0",
        "raw_body": "fixture-body-b718",
        "exception": "fixture-exception-6fa4",
        "raw_response": "fixture-raw-response-68e2",
        "raw_request": "fixture-raw-request-19b5",
    }
    original: dict[str, object] = {
        "apiKey": secrets["api_key"],
        "headers": {"Authorization": secrets["authorization"], "x-request-id": "safe-id"},
        "signature": secrets["signature"],
        "raw_body": secrets["raw_body"],
        "response_body": secrets["raw_body"],
        "raw_response": secrets["raw_response"],
        "raw_request": secrets["raw_request"],
        "cause": RuntimeError(secrets["exception"]),
        "request": httpx.Request(
            "GET",
            "https://example.invalid/private?apiKey=fixture-query-secret",
            headers={"Authorization": "Bearer fixture-request-secret"},
        ),
        "correlation_id": "corr-safe-42",
        "status_code": 401,
        "product_id": 27,
        "symbol": "BTCUSD",
        "path": "/v2/orders?apiKey=fixture-path-secret",
    }
    error = api.AuthError(context=original)
    original["correlation_id"] = "changed-after-construction"

    surfaces = [
        str(error),
        repr(error),
        repr(vars(error)),
        json.dumps(error.to_dict()),
        json.dumps(error.context),
    ]
    rendered = "\n".join(surfaces)
    for secret in [
        *secrets.values(),
        "fixture-query-secret",
        "fixture-request-secret",
        "fixture-path-secret",
    ]:
        assert secret not in rendered
    assert error.context == {
        "correlation_id": "corr-safe-42",
        "status_code": 401,
        "product_id": 27,
        "symbol": "BTCUSD",
        "path": "/v2/orders",
    }
    assert error.to_dict()["context"] == error.context


def test_planned_configuration_and_timeout_errors_have_distinct_inheritance() -> None:
    api = domain_api()
    assert api.ConfigurationError is not None, "ConfigurationError is missing"
    assert api.ExchangeTimeoutError is not None, "ExchangeTimeoutError is missing"
    assert issubclass(api.ConfigurationError, api.HmtCryptoError)
    assert not issubclass(api.ConfigurationError, api.ExchangeError)
    assert issubclass(api.ExchangeTimeoutError, api.ExchangeError)
    assert api.ConfigurationError().to_dict() == {
        "code": "configuration_error",
        "message": "Configuration is invalid",
        "context": {},
    }
    assert api.ExchangeTimeoutError().to_dict() == {
        "code": "exchange_timeout",
        "message": "Exchange request timed out",
        "context": {},
    }


def test_error_public_identity_and_context_cannot_be_mutated_into_a_secret_leak() -> None:
    api = domain_api()
    error = api.AuthError(context={"safe": "original"})
    for name, value in (
        ("code", "fixture-secret-code"),
        ("message", "fixture-secret-message"),
        ("context", {"safe": "fixture-secret-context"}),
        ("retry_after_ms", "fixture-secret-retry"),
    ):
        with pytest.raises(AttributeError):
            setattr(error, name, value)
    rendered = repr(error) + str(error) + json.dumps(error.to_dict())
    assert "fixture-secret" not in rendered


def test_rate_limit_retry_metadata_is_strict_nonnegative_and_serializable() -> None:
    api = domain_api()
    error = api.RateLimitError(retry_after_ms=2500, context={"scope": "user"})
    assert error.retry_after_ms == 2500
    assert error.to_dict() == {
        "code": "rate_limit",
        "message": "Exchange rate limit exceeded",
        "context": {"scope": "user"},
        "retry_after_ms": 2500,
    }
    for invalid in (-1, True, 1.5, "2500"):
        with pytest.raises((TypeError, ValueError), match="retry_after_ms"):
            api.RateLimitError(retry_after_ms=invalid)
