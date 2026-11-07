"""Typed Phase 1 Delta public market-data client."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from decimal import Decimal
from enum import StrEnum
from typing import Self

import httpx

from hmt_crypto.domain import Product, Ticker
from hmt_crypto.errors import ConfigurationError, ExchangeProtocolError, PaginationLimitError
from hmt_crypto.exchange.base import (
    CandleRequest,
    CandleResult,
    OrderBookRequest,
    OrderBookSnapshot,
    ProductQuery,
    ProductRef,
)
from hmt_crypto.exchange.delta.candle_policy import normalize_candles
from hmt_crypto.exchange.delta.endpoints import DeltaEndpointSet, _validated_endpoint_snapshot
from hmt_crypto.exchange.delta.http_headers import validate_user_agent
from hmt_crypto.exchange.delta.paginator import (
    MISSING_AFTER,
    CursorPage,
    PaginationResult,
    collect_cursor_pages,
)
from hmt_crypto.exchange.delta.rate_limiter import (
    DEFAULT_RETRY_POLICY,
    RetryPolicy,
    ScopeKind,
    WeightedRateLimiter,
)
from hmt_crypto.exchange.delta.routes import resolve_public_request
from hmt_crypto.exchange.delta.transport import _DeltaPublicTransport, _EnvelopeResult
from hmt_crypto.exchange.delta.wire_schemas import (
    parse_candles,
    parse_order_book,
    parse_product,
    parse_ticker,
    parse_ticker_collection,
)

_PRODUCT_PAGE_SIZE_CAP = 100
_DEFAULT_MAX_PRODUCT_PAGES = 100
_DEFAULT_MAX_PRODUCTS = 10_000
_MAX_TICKER_SYMBOLS = 10
_MAX_CANDLES = 2_000


class L2TimestampPolicy(StrEnum):
    """Explicit policy for Delta's undocumented REST L2 timestamp unit."""

    DISABLED = "disabled"
    INFER_MICROSECONDS_FROM_OFFICIAL_EXAMPLE = "infer_microseconds_from_official_example"


def _config(operation: str) -> ConfigurationError:
    return ConfigurationError(context={"operation": operation})


def _response_protocol(response: _EnvelopeResult) -> ExchangeProtocolError:
    return ExchangeProtocolError(context=response.context)


class DeltaPublicClient:
    """Owned async Delta client exposing public, read-only typed operations only."""

    def __init__(
        self,
        *,
        endpoints: DeltaEndpointSet,
        limiter: WeightedRateLimiter,
        user_agent: str,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter_source: Callable[[], Decimal] | None = None,
        l2_timestamp_policy: L2TimestampPolicy = L2TimestampPolicy.DISABLED,
    ) -> None:
        endpoints = _validated_endpoint_snapshot(endpoints)
        if not isinstance(limiter, WeightedRateLimiter):
            raise _config("public_client_limiter")
        scope = limiter.scope
        if (
            scope.region != endpoints.region
            or scope.environment != endpoints.environment
            or scope.kind is not ScopeKind.IP
        ):
            raise _config("public_client_limiter_scope")
        checked_user_agent = validate_user_agent(user_agent)
        if not isinstance(retry_policy, RetryPolicy):
            raise _config("public_client_retry_policy")
        if not callable(sleeper) or not callable(jitter_source or random.random):
            raise _config("public_client_retry_hooks")
        if not isinstance(l2_timestamp_policy, L2TimestampPolicy):
            raise _config("public_client_l2_timestamp")
        if transport is not None and not isinstance(transport, httpx.AsyncBaseTransport):
            raise _config("public_client_transport")

        jitter = jitter_source or (lambda: Decimal(str(random.random())))
        self._region = endpoints.region
        self._l2_timestamp_policy = l2_timestamp_policy
        self._transport = _DeltaPublicTransport(
            endpoints=endpoints,
            limiter=limiter,
            user_agent=checked_user_agent,
            retry_policy=retry_policy,
            transport=transport,
            sleeper=sleeper,
            jitter_source=jitter,
        )

    async def get_products(
        self,
        *,
        query: ProductQuery | None = None,
        page_size: int = _PRODUCT_PAGE_SIZE_CAP,
        max_pages: int = _DEFAULT_MAX_PRODUCT_PAGES,
        max_items: int = _DEFAULT_MAX_PRODUCTS,
    ) -> PaginationResult[Product]:
        """Load a bounded complete catalogue with explicit cursor evidence."""
        selected_query = query or ProductQuery()
        if not isinstance(selected_query, ProductQuery):
            raise _config("products_query")
        spec = resolve_public_request("GET", "/v2/products")
        last_page_context: dict[str, object] | None = None

        async def fetch_page(params: dict[str, str | int]) -> CursorPage[Product]:
            nonlocal last_page_context
            response = await self._transport._get_json(  # noqa: SLF001
                spec,
                path="/v2/products",
                params=tuple(params.items()),
            )
            page_context = dict(response.context)
            page_context["operation"] = "products"
            page_context["region"] = self._region
            last_page_context = page_context
            try:
                if not isinstance(response.result, list):
                    raise ExchangeProtocolError(context=page_context)
                items = tuple(
                    parse_product(item, endpoint_id="products") for item in response.result
                )
                if response.meta is None:
                    after: object = MISSING_AFTER
                elif isinstance(response.meta, dict) and all(
                    type(name) is str for name in response.meta
                ):
                    before = response.meta.get("before")
                    if before is not None and (
                        type(before) is not str
                        or not 0 < len(before) <= 4096
                        or any(
                            not character.isprintable() or character.isspace()
                            for character in before
                        )
                    ):
                        raise ExchangeProtocolError(context=page_context)
                    after = response.meta.get("after", MISSING_AFTER)
                else:
                    raise ExchangeProtocolError(context=page_context)
                return CursorPage(items=items, after=after)
            except (TypeError, ValueError, ExchangeProtocolError):
                raise ExchangeProtocolError(context=page_context) from None

        try:
            return await collect_cursor_pages(
                fetch_page,
                filters=selected_query.to_filters(),
                page_size=page_size,
                page_size_cap=_PRODUCT_PAGE_SIZE_CAP,
                max_pages=max_pages,
                max_items=max_items,
            )
        except (ExchangeProtocolError, PaginationLimitError) as error:
            if error.context.get("operation") != "cursor_pagination" or last_page_context is None:
                raise
            if isinstance(error, PaginationLimitError):
                raise PaginationLimitError(context=last_page_context) from None
            raise ExchangeProtocolError(context=last_page_context) from None

    async def get_product(self, product: ProductRef) -> Product:
        """Return one product selected by an exact validated symbol."""
        if not isinstance(product, ProductRef):
            raise _config("product_reference")
        path = f"/v2/products/{product.symbol}"
        response = await self._transport._get_json(  # noqa: SLF001
            resolve_public_request("GET", path), path=path
        )
        try:
            parsed = parse_product(response.result, endpoint_id="product")
        except ExchangeProtocolError:
            raise _response_protocol(response) from None
        if parsed.symbol != product.symbol:
            raise _response_protocol(response)
        return parsed

    async def get_ticker(self, product: ProductRef) -> Ticker:
        """Return one ticker without silently substituting any price field."""
        if not isinstance(product, ProductRef):
            raise _config("ticker_reference")
        path = f"/v2/tickers/{product.symbol}"
        response = await self._transport._get_json(  # noqa: SLF001
            resolve_public_request("GET", path), path=path
        )
        try:
            ticker = parse_ticker(response.result, endpoint_id="ticker")
        except ExchangeProtocolError:
            raise _response_protocol(response) from None
        if ticker.symbol != product.symbol:
            raise _response_protocol(response)
        return ticker

    async def get_tickers(
        self, products: tuple[ProductRef, ...] | None = None
    ) -> tuple[Ticker, ...]:
        """Return all tickers, or an ordered subset of at most ten symbols."""
        if products is None:
            path = "/v2/tickers"
            response = await self._transport._get_json(  # noqa: SLF001
                resolve_public_request("GET", path), path=path
            )
            try:
                if not isinstance(response.result, list):
                    raise _response_protocol(response)
                return parse_ticker_collection(response.result, endpoint_id="tickers")
            except ExchangeProtocolError:
                raise _response_protocol(response) from None
        if (
            not isinstance(products, tuple)
            or not 1 <= len(products) <= _MAX_TICKER_SYMBOLS
            or any(not isinstance(product, ProductRef) for product in products)
        ):
            raise _config("ticker_references")
        symbols = tuple(product.symbol for product in products)
        if len(set(symbols)) != len(symbols):
            raise _config("ticker_references")
        path = f"/v2/tickers/{','.join(symbols)}"
        response = await self._transport._get_json(  # noqa: SLF001
            resolve_public_request("GET", path), path=path
        )
        try:
            tickers = parse_ticker_collection(response.result, endpoint_id="ticker")
        except ExchangeProtocolError:
            raise _response_protocol(response) from None
        by_symbol = {ticker.symbol: ticker for ticker in tickers}
        if len(by_symbol) != len(tickers) or set(by_symbol) != set(symbols):
            raise _response_protocol(response)
        return tuple(by_symbol[symbol] for symbol in symbols)

    async def get_order_book(self, request: OrderBookRequest) -> OrderBookSnapshot:
        """Return a normalized L2 snapshot only under explicit timestamp inference."""
        if not isinstance(request, OrderBookRequest):
            raise _config("orderbook_request")
        if self._l2_timestamp_policy is not (
            L2TimestampPolicy.INFER_MICROSECONDS_FROM_OFFICIAL_EXAMPLE
        ):
            raise _config("orderbook_timestamp_unit")
        path = f"/v2/l2orderbook/{request.product.symbol}"
        params = () if request.depth is None else (("depth", request.depth),)
        response = await self._transport._get_json(  # noqa: SLF001
            resolve_public_request("GET", path), path=path, params=params
        )
        try:
            book = parse_order_book(
                response.result,
                endpoint_id="orderbook",
                requested=request.product,
            )
        except ExchangeProtocolError:
            raise _response_protocol(response) from None
        if request.depth is not None and (
            len(book.bids) > request.depth or len(book.asks) > request.depth
        ):
            raise _response_protocol(response)
        return book

    async def get_candles(self, request: CandleRequest) -> CandleResult:
        """Return up to 2,000 candles and expose possible venue truncation."""
        if not isinstance(request, CandleRequest):
            raise _config("candle_request")
        path = "/v2/history/candles"
        params = (
            ("resolution", request.resolution.value),
            ("symbol", request.product.symbol),
            ("start", request.start_epoch_seconds),
            ("end", request.end_epoch_seconds),
        )
        response = await self._transport._get_json(  # noqa: SLF001
            resolve_public_request("GET", path), path=path, params=params
        )
        try:
            candles = parse_candles(response.result, endpoint_id="candles")
            candles = normalize_candles(
                candles,
                request=request,
                maximum=_MAX_CANDLES,
                context=response.context,
            )
        except ExchangeProtocolError:
            raise _response_protocol(response) from None
        return CandleResult(
            items=candles,
            possibly_truncated=len(candles) == _MAX_CANDLES,
        )

    async def aclose(self) -> None:
        """Close the owned HTTP client idempotently."""
        await self._transport.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()


__all__ = ["DeltaPublicClient", "L2TimestampPolicy", "RetryPolicy"]
