"""Immutable allowlist for Delta Phase 1 public REST routes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.base import ProductRef
from hmt_crypto.exchange.delta.rate_limiter import RateLimitRoute


class PublicRouteId(StrEnum):
    """Stable identifiers for the complete Phase 1 public route set."""

    PRODUCTS = "products"
    PRODUCT = "product"
    TICKERS = "tickers"
    TICKER = "ticker"
    ORDERBOOK = "orderbook"
    CANDLES = "candles"


class ResponseSchema(StrEnum):
    """Expected response result shape used by the private parser layer."""

    PRODUCTS = "products"
    PRODUCT = "product"
    TICKERS = "tickers"
    TICKER = "ticker"
    ORDERBOOK = "orderbook"
    CANDLES = "candles"


class PaginationPolicy(StrEnum):
    """Pagination ownership for one route."""

    NONE = "none"
    CURSOR = "cursor"


@dataclass(frozen=True, slots=True)
class PublicRouteSpec:
    """Transport and parsing policy for one allowlisted public route."""

    route_id: PublicRouteId
    method: str
    path_template: str
    rate_route: RateLimitRoute
    response_schema: ResponseSchema
    max_response_bytes: int
    pagination: PaginationPolicy = PaginationPolicy.NONE
    is_public: bool = True
    retry_safe: bool = True


_SPECS = MappingProxyType(
    {
        PublicRouteId.PRODUCTS: PublicRouteSpec(
            route_id=PublicRouteId.PRODUCTS,
            method="GET",
            path_template="/v2/products",
            rate_route=RateLimitRoute.PRODUCTS,
            response_schema=ResponseSchema.PRODUCTS,
            max_response_bytes=8 * 1024 * 1024,
            pagination=PaginationPolicy.CURSOR,
        ),
        PublicRouteId.PRODUCT: PublicRouteSpec(
            route_id=PublicRouteId.PRODUCT,
            method="GET",
            path_template="/v2/products/{symbol}",
            rate_route=RateLimitRoute.PRODUCTS,
            response_schema=ResponseSchema.PRODUCT,
            max_response_bytes=512 * 1024,
        ),
        PublicRouteId.TICKERS: PublicRouteSpec(
            route_id=PublicRouteId.TICKERS,
            method="GET",
            path_template="/v2/tickers",
            rate_route=RateLimitRoute.TICKERS,
            response_schema=ResponseSchema.TICKERS,
            max_response_bytes=4 * 1024 * 1024,
        ),
        PublicRouteId.TICKER: PublicRouteSpec(
            route_id=PublicRouteId.TICKER,
            method="GET",
            path_template="/v2/tickers/{symbols}",
            rate_route=RateLimitRoute.TICKERS,
            response_schema=ResponseSchema.TICKER,
            max_response_bytes=1024 * 1024,
        ),
        PublicRouteId.ORDERBOOK: PublicRouteSpec(
            route_id=PublicRouteId.ORDERBOOK,
            method="GET",
            path_template="/v2/l2orderbook/{symbol}",
            rate_route=RateLimitRoute.ORDERBOOK,
            response_schema=ResponseSchema.ORDERBOOK,
            max_response_bytes=4 * 1024 * 1024,
        ),
        PublicRouteId.CANDLES: PublicRouteSpec(
            route_id=PublicRouteId.CANDLES,
            method="GET",
            path_template="/v2/history/candles",
            rate_route=RateLimitRoute.CANDLES,
            response_schema=ResponseSchema.CANDLES,
            max_response_bytes=4 * 1024 * 1024,
        ),
    }
)


def _configuration_error() -> ConfigurationError:
    return ConfigurationError(context={"operation": "resolve_public_route"})


def _symbol(value: str) -> bool:
    try:
        ProductRef(symbol=value)
    except ValueError:
        return False
    return True


def resolve_public_request(method: object, path: object) -> PublicRouteSpec:
    """Resolve only a documented GET path; reject unknown/write/fund routes locally."""
    if type(method) is not str or method != "GET" or type(path) is not str:
        raise _configuration_error()
    if not path or any(not character.isprintable() for character in path):
        raise _configuration_error()
    if any(token in path for token in ("?", "#", "\\", "%", "//")):
        raise _configuration_error()
    if path == "/v2/products":
        return _SPECS[PublicRouteId.PRODUCTS]
    if path == "/v2/tickers":
        return _SPECS[PublicRouteId.TICKERS]
    if path == "/v2/history/candles":
        return _SPECS[PublicRouteId.CANDLES]

    product_prefix = "/v2/products/"
    ticker_prefix = "/v2/tickers/"
    orderbook_prefix = "/v2/l2orderbook/"
    if path.startswith(product_prefix):
        symbol = path.removeprefix(product_prefix)
        if _symbol(symbol):
            return _SPECS[PublicRouteId.PRODUCT]
    elif path.startswith(ticker_prefix):
        symbols = path.removeprefix(ticker_prefix).split(",")
        if 1 <= len(symbols) <= 10 and len(set(symbols)) == len(symbols):
            if all(_symbol(symbol) for symbol in symbols):
                return _SPECS[PublicRouteId.TICKER]
    elif path.startswith(orderbook_prefix):
        symbol = path.removeprefix(orderbook_prefix)
        if _symbol(symbol):
            return _SPECS[PublicRouteId.ORDERBOOK]
    raise _configuration_error()


__all__ = [
    "PaginationPolicy",
    "PublicRouteId",
    "PublicRouteSpec",
    "ResponseSchema",
    "resolve_public_request",
]
