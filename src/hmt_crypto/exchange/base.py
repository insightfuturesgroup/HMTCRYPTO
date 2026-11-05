"""Exchange-neutral public market-data request and result boundaries."""

from __future__ import annotations

import calendar
import re
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Protocol, Self

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from hmt_crypto.domain.models import Candle, DomainModel, Product, Ticker
from hmt_crypto.domain.money import PositiveDecimal, UtcDateTime

if TYPE_CHECKING:
    from hmt_crypto.exchange.delta.paginator import PaginationResult
    from hmt_crypto.market_data.contracts import MarketObservation

_SYMBOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}\Z")
_FILTER_TOKEN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_EXPIRY = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_MAX_FILTER_VALUES = 32


class ProductRef(DomainModel):
    """A validated exchange product symbol safe for one URL path segment."""

    symbol: str

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: object) -> str:
        if type(value) is not str or _SYMBOL.fullmatch(value) is None:
            raise ValueError("symbol is not a safe exchange identifier")
        return value


class MarketComponent(StrEnum):
    """Exchange-neutral live market-data components."""

    TICKER = "ticker"
    L1 = "l1"
    L2 = "l2"
    FULL_BOOK = "full_book"
    TRADES = "trades"
    MARK = "mark"
    CANDLE = "candle"
    SPOT = "spot"
    FUNDING = "funding"
    PRODUCT_STATUS = "product_status"
    SYSTEM_STATUS = "system_status"


class MarketSubscriptionRequest(DomainModel):
    """One bounded exchange-neutral live market-data claim."""

    products: tuple[ProductRef, ...]
    components: tuple[MarketComponent, ...]

    @field_validator("products", mode="before")
    @classmethod
    def validate_products(cls, value: object) -> tuple[ProductRef, ...]:
        if type(value) is not tuple or len(value) > 100:
            raise ValueError("market subscription products must be a bounded tuple")
        if any(type(product) is not ProductRef for product in value):
            raise ValueError("market subscription product is invalid")
        if len({product.symbol for product in value}) != len(value):
            raise ValueError("market subscription products must be unique")
        return value

    @field_validator("components", mode="before")
    @classmethod
    def validate_components(cls, value: object) -> tuple[MarketComponent, ...]:
        if type(value) is not tuple or not value:
            raise ValueError("market subscription components must be a nonempty tuple")
        if any(type(component) is not MarketComponent for component in value):
            raise ValueError("market subscription component is invalid")
        if len(set(value)) != len(value):
            raise ValueError("market subscription components must be unique")
        return value

    @model_validator(mode="after")
    def validate_product_scope(self) -> Self:
        symbol_less = {MarketComponent.PRODUCT_STATUS, MarketComponent.SYSTEM_STATUS}
        if not self.products and any(component not in symbol_less for component in self.components):
            raise ValueError("product-scoped market data requires products")
        return self


class MarketSubscription(Protocol):
    """Read-only observation stream returned to one consumer."""

    def __aiter__(self) -> AsyncIterator[MarketObservation]: ...


class MarketDataSubscriptionService(Protocol):
    """Exchange-neutral live market subscription interface."""

    async def subscribe_market_data(
        self,
        request: MarketSubscriptionRequest,
    ) -> AbstractAsyncContextManager[MarketSubscription]: ...


class ProductQuery(DomainModel):
    """Immutable ordered product-catalogue filters documented by Delta."""

    contract_types: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    expiry: str | None = None

    @field_validator("contract_types", "states", mode="before")
    @classmethod
    def snapshot_tokens(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("product filters must be an ordered sequence")
        copied = tuple(value)
        if len(copied) > _MAX_FILTER_VALUES or len(set(copied)) != len(copied):
            raise ValueError("product filters must be unique and bounded")
        if any(type(item) is not str or _FILTER_TOKEN.fullmatch(item) is None for item in copied):
            raise ValueError("product filter contains an unsafe token")
        return copied

    @field_validator("expiry")
    @classmethod
    def validate_expiry(cls, value: object) -> str | None:
        if value is None:
            return None
        if type(value) is not str or _EXPIRY.fullmatch(value) is None:
            raise ValueError("expiry must be YYYY-MM-DD")
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError("expiry must be a calendar date") from None
        return value

    def to_filters(self) -> dict[str, str]:
        """Return a fresh, insertion-ordered Delta filter mapping."""
        filters: dict[str, str] = {}
        if self.contract_types:
            filters["contract_types"] = ",".join(self.contract_types)
        if self.states:
            filters["states"] = ",".join(self.states)
        if self.expiry is not None:
            filters["expiry"] = self.expiry
        return filters


class CandleResolution(StrEnum):
    """Current Delta India resolutions; stale long intervals fail closed."""

    ONE_MINUTE = "1m"
    THREE_MINUTES = "3m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    THIRTY_MINUTES = "30m"
    ONE_HOUR = "1h"
    TWO_HOURS = "2h"
    FOUR_HOURS = "4h"
    SIX_HOURS = "6h"
    ONE_DAY = "1d"
    ONE_WEEK = "1w"


class CandleRequest(DomainModel):
    """Bounded UTC candle interval expressed as exact epoch seconds on the wire."""

    product: ProductRef
    resolution: CandleResolution
    start: UtcDateTime
    end: UtcDateTime

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.start.microsecond or self.end.microsecond or self.start >= self.end:
            raise ValueError("candle interval must be ordered whole UTC seconds")
        return self

    @staticmethod
    def _epoch_seconds(value: datetime) -> int:
        return calendar.timegm(value.astimezone(UTC).timetuple())

    @property
    def start_epoch_seconds(self) -> int:
        return self._epoch_seconds(self.start)

    @property
    def end_epoch_seconds(self) -> int:
        return self._epoch_seconds(self.end)


class OrderBookRequest(DomainModel):
    """L2 snapshot request with a conservative local depth bound."""

    product: ProductRef
    depth: Annotated[StrictInt, Field(ge=1, le=1000)] | None = None


class OrderBookLevel(DomainModel):
    """One normalized L2 price level measured in contracts."""

    price: PositiveDecimal
    size: Annotated[StrictInt, Field(ge=0)]


class OrderBookSnapshot(DomainModel):
    """A normalized L2 snapshot; auction overlap is deliberately representable."""

    product: ProductRef
    timestamp: UtcDateTime
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]

    @model_validator(mode="after")
    def validate_normalized_levels(self) -> Self:
        bid_prices = tuple(level.price for level in self.bids)
        ask_prices = tuple(level.price for level in self.asks)
        if bid_prices != tuple(sorted(bid_prices, reverse=True)):
            raise ValueError("bids must be normalized in descending price order")
        if ask_prices != tuple(sorted(ask_prices)):
            raise ValueError("asks must be normalized in ascending price order")
        if len(set(bid_prices)) != len(bid_prices) or len(set(ask_prices)) != len(ask_prices):
            raise ValueError("order-book levels must have unique side prices")
        return self

    @property
    def best_bid(self) -> PositiveDecimal | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> PositiveDecimal | None:
        return self.asks[0].price if self.asks else None


class CandleResult(DomainModel):
    """Candle rows plus explicit evidence that the venue cap may have truncated them."""

    items: tuple[Candle, ...]
    possibly_truncated: StrictBool

    @model_validator(mode="after")
    def validate_truncation_evidence(self) -> Self:
        if self.possibly_truncated and not self.items:
            raise ValueError("empty candle results cannot be venue-truncated")
        return self

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> Candle:
        return self.items[index]


class MarketDataClient(Protocol):
    """Typed exchange-neutral Phase 1 public market-data interface."""

    async def get_products(
        self,
        *,
        query: ProductQuery | None = None,
        page_size: int = 100,
        max_pages: int = 100,
        max_items: int = 10_000,
    ) -> PaginationResult[Product]: ...

    async def get_product(self, product: ProductRef) -> Product: ...

    async def get_ticker(self, product: ProductRef) -> Ticker: ...

    async def get_tickers(
        self, products: tuple[ProductRef, ...] | None = None
    ) -> tuple[Ticker, ...]: ...

    async def get_order_book(self, request: OrderBookRequest) -> OrderBookSnapshot: ...

    async def get_candles(self, request: CandleRequest) -> CandleResult: ...

    async def aclose(self) -> None: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None: ...


__all__ = [
    "CandleRequest",
    "CandleResolution",
    "CandleResult",
    "MarketComponent",
    "MarketDataClient",
    "MarketDataSubscriptionService",
    "MarketSubscription",
    "MarketSubscriptionRequest",
    "OrderBookLevel",
    "OrderBookRequest",
    "OrderBookSnapshot",
    "ProductQuery",
    "ProductRef",
]
