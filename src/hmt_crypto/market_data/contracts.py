"""Immutable exchange-neutral public market observations and subscriptions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, TypeAlias

from hmt_crypto.exchange.delta.websocket_contracts import WsCandleResolution
from hmt_crypto.market_data.primitives import (
    BookLevel,
    MarketSource,
    ProductObservation,
)
from hmt_crypto.market_data.primitives import (
    exact_decimal as _decimal,
)
from hmt_crypto.market_data.primitives import (
    exact_utc as _utc,
)
from hmt_crypto.market_data.primitives import (
    nonnegative_int as _nonnegative_int,
)
from hmt_crypto.market_data.primitives import (
    optional_decimal as _optional_decimal,
)
from hmt_crypto.market_data.primitives import (
    positive_int as _positive_int,
)
from hmt_crypto.market_data.primitives import (
    require_channel as _require_channel,
)

_RESOLUTIONS = frozenset(resolution.value for resolution in WsCandleResolution)


def _validate_checksum_lexeme(checksum: int, lexeme: object) -> None:
    if type(lexeme) is not str or not lexeme or len(lexeme) > 1024:
        raise ValueError("book checksum lexeme is invalid")
    try:
        value = Decimal(lexeme)
    except Exception:
        raise ValueError("book checksum lexeme is invalid") from None
    if not value.is_finite() or value != checksum:
        raise ValueError("book checksum lexeme conflicts with parsed value")


@dataclass(frozen=True, slots=True, kw_only=True)
class TickerObservation(ProductObservation):
    """One compact ticker row bound to the catalogue."""

    spot_price: Decimal | None = None
    mark_price: Decimal | None = None
    close: Decimal | None = None
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    lower_price_limit: Decimal | None = None
    upper_price_limit: Decimal | None = None
    open_interest: int | None = None
    turnover: Decimal | None = None

    def __post_init__(self) -> None:
        super(TickerObservation, self).__post_init__()
        _require_channel(self.source, "ticker")
        for name in (
            "spot_price",
            "mark_price",
            "close",
            "best_bid",
            "best_ask",
            "lower_price_limit",
            "upper_price_limit",
        ):
            _optional_decimal(getattr(self, name), name=name, positive=True)
        _optional_decimal(self.turnover, name="turnover", nonnegative=True)
        if self.open_interest is not None:
            _nonnegative_int(self.open_interest, name="open_interest")


@dataclass(frozen=True, slots=True, kw_only=True)
class L1Observation(ProductObservation):
    """Best bid/ask observation."""

    ask_price: Decimal
    ask_size: int
    bid_price: Decimal
    bid_size: int
    last_update_at: datetime

    def __post_init__(self) -> None:
        super(L1Observation, self).__post_init__()
        _require_channel(self.source, "ob_l1")
        _decimal(self.ask_price, name="ask_price", positive=True)
        _decimal(self.bid_price, name="bid_price", positive=True)
        _nonnegative_int(self.ask_size, name="ask_size")
        _nonnegative_int(self.bid_size, name="bid_size")
        object.__setattr__(self, "last_update_at", _utc(self.last_update_at, name="last_update_at"))


@dataclass(frozen=True, slots=True, kw_only=True)
class L2Observation(ProductObservation):
    """Immutable top-of-book depth observation."""

    asks: tuple[BookLevel, ...]
    bids: tuple[BookLevel, ...]
    last_update_at: datetime

    def __post_init__(self) -> None:
        super(L2Observation, self).__post_init__()
        _require_channel(self.source, "ob_l2")
        object.__setattr__(self, "asks", _levels(self.asks, ascending=True, allow_zero=False))
        object.__setattr__(self, "bids", _levels(self.bids, ascending=False, allow_zero=False))
        object.__setattr__(self, "last_update_at", _utc(self.last_update_at, name="last_update_at"))


def _levels(value: object, *, ascending: bool, allow_zero: bool) -> tuple[BookLevel, ...]:
    if not isinstance(value, (tuple, list)):
        raise TypeError("book levels must be an ordered sequence")
    levels = tuple(value)
    if any(type(level) is not BookLevel for level in levels):
        raise TypeError("book level is invalid")
    if not allow_zero and any(level.size == 0 for level in levels):
        raise ValueError("published book levels cannot be empty")
    prices = tuple(level.price for level in levels)
    if prices != tuple(sorted(prices, reverse=not ascending)) or len(set(prices)) != len(prices):
        raise ValueError("book levels are not normalized")
    return levels


@dataclass(frozen=True, slots=True, kw_only=True)
class BookSnapshotObservation(ProductObservation):
    """Candidate full-depth snapshot with an unsigned venue checksum."""

    asks: tuple[BookLevel, ...]
    bids: tuple[BookLevel, ...]
    checksum: int
    checksum_lexeme: str

    def __post_init__(self) -> None:
        super(BookSnapshotObservation, self).__post_init__()
        _require_channel(self.source, "ob_updates")
        if self.source.venue_sequence is None:
            raise ValueError("book snapshot requires a venue sequence")
        object.__setattr__(self, "asks", _levels(self.asks, ascending=True, allow_zero=False))
        object.__setattr__(self, "bids", _levels(self.bids, ascending=False, allow_zero=False))
        if type(self.checksum) is not int or not 0 <= self.checksum <= 0xFFFFFFFF:
            raise ValueError("book checksum must be unsigned 32-bit")
        _validate_checksum_lexeme(self.checksum, self.checksum_lexeme)

    @property
    def sequence(self) -> int:
        """Return the validated venue sequence."""
        assert self.source.venue_sequence is not None
        return self.source.venue_sequence


@dataclass(frozen=True, slots=True, kw_only=True)
class BookDeltaObservation(ProductObservation):
    """Candidate sequenced full-depth changes with checksum evidence."""

    asks: tuple[BookLevel, ...]
    bids: tuple[BookLevel, ...]
    checksum: int
    checksum_lexeme: str

    def __post_init__(self) -> None:
        super(BookDeltaObservation, self).__post_init__()
        _require_channel(self.source, "ob_updates")
        if self.source.venue_sequence is None:
            raise ValueError("book update requires a venue sequence")
        object.__setattr__(self, "asks", _levels(self.asks, ascending=True, allow_zero=True))
        object.__setattr__(self, "bids", _levels(self.bids, ascending=False, allow_zero=True))
        if type(self.checksum) is not int or not 0 <= self.checksum <= 0xFFFFFFFF:
            raise ValueError("book checksum must be unsigned 32-bit")
        _validate_checksum_lexeme(self.checksum, self.checksum_lexeme)

    @property
    def sequence(self) -> int:
        """Return the validated venue sequence."""
        assert self.source.venue_sequence is not None
        return self.source.venue_sequence


@dataclass(frozen=True, slots=True, kw_only=True)
class BookStreamError(ProductObservation):
    """Validated venue action=error repair signal for one exact book symbol."""

    reason: Literal["venue_error"] = "venue_error"

    def __post_init__(self) -> None:
        super(BookStreamError, self).__post_init__()
        if self.source.channel != "ob_updates" or self.reason != "venue_error":
            raise ValueError("book stream repair signal is invalid")


@dataclass(frozen=True, slots=True, kw_only=True)
class CatalogueMismatchSignal:
    """Sanitized request to refresh catalogue identity after one poisoned row."""

    source: MarketSource
    product_id: int | None = None
    reason: Literal["catalogue_identity_mismatch"] = "catalogue_identity_mismatch"

    def __post_init__(self) -> None:
        if (
            type(self.source) is not MarketSource
            or self.source.symbol is not None
            or self.reason != "catalogue_identity_mismatch"
        ):
            raise ValueError("catalogue mismatch signal is invalid")
        if self.product_id is not None:
            _positive_int(self.product_id, name="product_id")


@dataclass(frozen=True, slots=True, kw_only=True)
class PublicTradeObservation(ProductObservation):
    """Non-authoritative public execution without account/fill identity."""

    price: Decimal
    size: int
    buyer_role: str
    trade_at: datetime

    def __post_init__(self) -> None:
        super(PublicTradeObservation, self).__post_init__()
        _require_channel(self.source, "trades")
        _decimal(self.price, name="price", positive=True)
        _positive_int(self.size, name="size")
        if self.buyer_role not in {"m", "t"}:
            raise ValueError("public trade role is invalid")
        object.__setattr__(self, "trade_at", _utc(self.trade_at, name="trade_at"))


@dataclass(frozen=True, slots=True, kw_only=True)
class MarkObservation(ProductObservation):
    """Exact mark price observation."""

    price: Decimal

    def __post_init__(self) -> None:
        super(MarkObservation, self).__post_init__()
        _require_channel(self.source, "mark_price")
        _decimal(self.price, name="price", positive=True)


@dataclass(frozen=True, slots=True, kw_only=True)
class CandleObservation(ProductObservation):
    """Provisional streaming OHLCV observation with no finality claim."""

    resolution: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None
    complete: Literal[False] = False

    def __post_init__(self) -> None:
        super(CandleObservation, self).__post_init__()
        if type(self.resolution) is not str or self.resolution not in _RESOLUTIONS:
            raise ValueError("candle resolution is invalid")
        if self.source.channel != f"candlestick_{self.resolution}":
            raise ValueError("candle channel and resolution conflict")
        object.__setattr__(self, "timestamp", _utc(self.timestamp, name="timestamp"))
        for name in ("open", "high", "low", "close"):
            _decimal(getattr(self, name), name=name, positive=True)
        _optional_decimal(self.volume, name="volume", nonnegative=True)
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("candle OHLC range is invalid")
        if self.complete is not False:
            raise ValueError("streaming candles are provisional")


@dataclass(frozen=True, slots=True, kw_only=True)
class SpotObservation:
    """Exact spot/index price with an injected authoritative asset binding."""

    source: MarketSource
    asset_id: int
    index_symbol: str
    price: Decimal

    def __post_init__(self) -> None:
        if type(self.source) is not MarketSource:
            raise TypeError("spot source is invalid")
        _require_channel(self.source, "spot_price")
        _positive_int(self.asset_id, name="asset_id")
        if (
            type(self.index_symbol) is not str
            or not self.index_symbol
            or self.index_symbol != self.source.symbol
        ):
            raise ValueError("spot index identity is invalid")
        _decimal(self.price, name="price", positive=True)


@dataclass(frozen=True, slots=True, kw_only=True)
class FundingRateObservation(ProductObservation):
    """Current and next funding evidence for one perpetual product."""

    interval_seconds: int
    rate: Decimal
    next_funding_at: datetime

    def __post_init__(self) -> None:
        super(FundingRateObservation, self).__post_init__()
        _require_channel(self.source, "funding_rate")
        _positive_int(self.interval_seconds, name="interval_seconds")
        _decimal(self.rate, name="rate")
        object.__setattr__(
            self,
            "next_funding_at",
            _utc(self.next_funding_at, name="next_funding_at"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ProductStatusObservation(ProductObservation):
    """Gated product disruption/auction status observation."""

    event: str
    trading_status: str

    def __post_init__(self) -> None:
        super(ProductStatusObservation, self).__post_init__()
        _require_channel(self.source, "product_updates")
        if self.event not in {"market_disruption", "start_auction", "finish_auction"}:
            raise ValueError("product status event is invalid")
        if self.trading_status not in {
            "disrupted_cancel_only",
            "disrupted_post_only",
            "operational",
        }:
            raise ValueError("product trading status is invalid")


@dataclass(frozen=True, slots=True, kw_only=True)
class SystemStatusObservation:
    """System-wide status observation with optional maintenance instants."""

    source: MarketSource
    event: str
    status: str
    announcement_at: datetime | None = None
    start_at: datetime | None = None
    finish_at: datetime | None = None

    def __post_init__(self) -> None:
        if type(self.source) is not MarketSource or self.source.symbol is not None:
            raise ValueError("system status source must be symbol-less")
        if self.source.channel != "system_status":
            raise ValueError("system status channel is invalid")
        if self.event not in {
            "snapshot",
            "maintenance_scheduled",
            "maintenance_started",
            "maintenance_cancelled",
            "maintenance_finished",
            "app_status_update",
        }:
            raise ValueError("system status event is invalid")
        if self.status not in {"live", "maintenance", "api_fallback", "degraded_mode"}:
            raise ValueError("system status is invalid")
        for name in ("announcement_at", "start_at", "finish_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _utc(value, name=name))


MarketObservation: TypeAlias = (
    TickerObservation
    | L1Observation
    | L2Observation
    | BookSnapshotObservation
    | BookDeltaObservation
    | PublicTradeObservation
    | MarkObservation
    | CandleObservation
    | SpotObservation
    | FundingRateObservation
    | ProductStatusObservation
    | SystemStatusObservation
)
PublicParseOutcome: TypeAlias = MarketObservation | BookStreamError | CatalogueMismatchSignal

# Re-export the neutral subscription boundary from the exchange abstraction.
from hmt_crypto.exchange.base import (  # noqa: E402
    MarketComponent,
    MarketDataSubscriptionService,
    MarketSubscription,
    MarketSubscriptionRequest,
)

__all__ = [
    "BookDeltaObservation",
    "BookLevel",
    "BookSnapshotObservation",
    "BookStreamError",
    "CatalogueMismatchSignal",
    "CandleObservation",
    "FundingRateObservation",
    "L1Observation",
    "L2Observation",
    "MarketComponent",
    "MarketDataSubscriptionService",
    "MarketObservation",
    "MarketSource",
    "MarketSubscription",
    "MarketSubscriptionRequest",
    "MarkObservation",
    "ProductObservation",
    "ProductStatusObservation",
    "PublicParseOutcome",
    "PublicTradeObservation",
    "SpotObservation",
    "SystemStatusObservation",
    "TickerObservation",
]
