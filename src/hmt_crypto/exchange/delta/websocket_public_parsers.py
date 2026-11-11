"""Strict, catalogue-bound parsers for current Delta India public channels."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from hmt_crypto.domain import Product
from hmt_crypto.errors import ExchangeProtocolError
from hmt_crypto.exchange.delta.websocket_public_parser_support import (
    PublicParseContext,
)
from hmt_crypto.exchange.delta.websocket_public_parser_support import (
    array_numbers as _array_numbers,
)
from hmt_crypto.exchange.delta.websocket_public_parser_support import (
    array_optional_numbers as _array_optional_numbers,
)
from hmt_crypto.exchange.delta.websocket_public_parser_support import (
    integer as _integer,
)
from hmt_crypto.exchange.delta.websocket_public_parser_support import (
    keys as _keys,
)
from hmt_crypto.exchange.delta.websocket_public_parser_support import (
    levels as _levels,
)
from hmt_crypto.exchange.delta.websocket_public_parser_support import (
    microseconds as _microseconds,
)
from hmt_crypto.exchange.delta.websocket_public_parser_support import (
    number as _number,
)
from hmt_crypto.exchange.delta.websocket_public_parser_support import (
    protocol_error as _protocol_error,
)
from hmt_crypto.exchange.delta.websocket_public_parser_support import (
    resolve_product as _resolve_product,
)
from hmt_crypto.exchange.delta.websocket_public_parser_support import (
    text as _text,
)
from hmt_crypto.market_data.contracts import (
    BookDeltaObservation,
    BookSnapshotObservation,
    BookStreamError,
    CandleObservation,
    CatalogueMismatchSignal,
    FundingRateObservation,
    L1Observation,
    L2Observation,
    MarkObservation,
    ProductStatusObservation,
    PublicParseOutcome,
    PublicTradeObservation,
    SpotObservation,
    SystemStatusObservation,
    TickerObservation,
)

_CANDLE_CHANNELS = frozenset(
    {
        "candlestick_1m",
        "candlestick_3m",
        "candlestick_5m",
        "candlestick_15m",
        "candlestick_30m",
        "candlestick_1h",
        "candlestick_2h",
        "candlestick_4h",
        "candlestick_6h",
        "candlestick_12h",
        "candlestick_1d",
        "candlestick_1w",
    }
)


def _binding(
    context: PublicParseContext,
    *,
    channel: str,
    symbol: str,
    product_id: int | None = None,
    offset: int = 0,
) -> Product | CatalogueMismatchSignal:
    product, affected_id = _resolve_product(context, symbol, product_id)
    if product is not None:
        return product
    return CatalogueMismatchSignal(
        source=context.source(channel=channel, symbol=None, offset=offset),
        product_id=affected_id,
    )


def _ticker_row(
    row: object,
    *,
    outer_symbol: str,
    spot_price: Decimal,
    context: PublicParseContext,
    offset: int,
) -> PublicParseOutcome:
    fields = _keys(
        row,
        {"i", "s", "m", "m24hc", "ohlc", "oi", "pb", "q", "qiv", "to", "g"},
    )
    product_id, _ = _integer(fields["i"])
    symbol = _text(fields["s"])
    mark = _number(fields["m"])[0]
    _number(fields["m24hc"])
    ohlc = _array_numbers(fields["ohlc"], 4)
    oi = _array_numbers(fields["oi"], 2)
    bands = _array_numbers(fields["pb"], 2)
    quotes = _array_optional_numbers(fields["q"], 5, nullable=frozenset({4}))
    _array_optional_numbers(fields["qiv"], 3, nullable=frozenset(range(3)))
    turnover = _array_numbers(fields["to"], 2)
    _array_optional_numbers(fields["g"], 5, nullable=frozenset(range(5)))
    product = _binding(
        context,
        channel="ticker",
        symbol=symbol,
        product_id=product_id,
        offset=offset,
    )
    if isinstance(product, CatalogueMismatchSignal) or symbol != outer_symbol:
        affected_id = (
            product.product_id if isinstance(product, CatalogueMismatchSignal) else product.id
        )
        return CatalogueMismatchSignal(
            source=context.source(channel="ticker", symbol=None, offset=offset),
            product_id=affected_id,
        )
    open_interest = int(oi[0].to_integral_value())
    if oi[0] != open_interest or open_interest < 0:
        raise ValueError("open interest")
    if quotes[0] is None or quotes[2] is None:
        raise ValueError("ticker quote")
    return TickerObservation(
        source=context.source(channel="ticker", symbol=product.symbol, offset=offset),
        product_id=product.id,
        symbol=product.symbol,
        spot_price=spot_price,
        mark_price=mark,
        close=ohlc[3],
        best_bid=quotes[2],
        best_ask=quotes[0],
        lower_price_limit=bands[0],
        upper_price_limit=bands[1],
        open_interest=open_interest,
        turnover=turnover[0],
    )


def _parse_ticker(
    message: Mapping[str, object], context: PublicParseContext
) -> tuple[PublicParseOutcome, ...]:
    fields = _keys(message, {"type", "ts", "sy", "sp", "d"})
    _microseconds(fields["ts"])
    outer_symbol = _text(fields["sy"])
    spot_price = _number(fields["sp"])[0]
    rows = fields["d"]
    if type(rows) is not list or not rows or len(rows) > 100:
        raise ValueError("ticker rows")
    return tuple(
        _ticker_row(
            row,
            outer_symbol=outer_symbol,
            spot_price=spot_price,
            context=context,
            offset=index,
        )
        for index, row in enumerate(rows)
    )


def _parse_l1(
    message: Mapping[str, object], context: PublicParseContext
) -> L1Observation | CatalogueMismatchSignal:
    fields = _keys(message, {"type", "sy", "ts", "lts", "ap", "as", "bp", "bs"})
    _microseconds(fields["ts"])
    symbol = _text(fields["sy"])
    product = _binding(context, channel="ob_l1", symbol=symbol)
    if isinstance(product, CatalogueMismatchSignal):
        return product
    return L1Observation(
        source=context.source(channel="ob_l1", symbol=symbol, offset=0),
        product_id=product.id,
        symbol=symbol,
        ask_price=_number(fields["ap"])[0],
        ask_size=_integer(fields["as"])[0],
        bid_price=_number(fields["bp"])[0],
        bid_size=_integer(fields["bs"])[0],
        last_update_at=_microseconds(fields["lts"]),
    )


def _parse_l2(
    message: Mapping[str, object], context: PublicParseContext
) -> L2Observation | CatalogueMismatchSignal:
    fields = _keys(message, {"type", "sy", "ts", "lts", "a", "b"})
    _microseconds(fields["ts"])
    symbol = _text(fields["sy"])
    product = _binding(context, channel="ob_l2", symbol=symbol)
    if isinstance(product, CatalogueMismatchSignal):
        return product
    return L2Observation(
        source=context.source(channel="ob_l2", symbol=symbol, offset=0),
        product_id=product.id,
        symbol=symbol,
        asks=_levels(fields["a"]),
        bids=_levels(fields["b"], descending=True),
        last_update_at=_microseconds(fields["lts"]),
    )


def _parse_book(
    message: Mapping[str, object], context: PublicParseContext
) -> BookSnapshotObservation | BookDeltaObservation | BookStreamError | CatalogueMismatchSignal:
    action = _text(message.get("action"))
    if action == "error":
        error_fields = _keys(message, {"type", "action", "symbol", "msg"})
        symbol = _text(error_fields["symbol"])
        _text(error_fields["msg"])
        product = _binding(context, channel="ob_updates", symbol=symbol)
        if isinstance(product, CatalogueMismatchSignal):
            return product
        return BookStreamError(
            source=context.source(channel="ob_updates", symbol=symbol, offset=0),
            product_id=product.id,
            symbol=symbol,
        )
    fields = _keys(message, {"type", "action", "a", "b", "ts", "seq", "sy", "cs"})
    _microseconds(fields["ts"])
    symbol = _text(fields["sy"])
    product = _binding(context, channel="ob_updates", symbol=symbol)
    if isinstance(product, CatalogueMismatchSignal):
        return product
    sequence, _ = _integer(fields["seq"])
    checksum, checksum_lexeme = _integer(fields["cs"])
    source = context.source(
        channel="ob_updates",
        symbol=symbol,
        offset=0,
        venue_sequence=sequence,
    )
    asks = _levels(fields["a"])
    bids = _levels(fields["b"], descending=True)
    if action == "snapshot":
        return BookSnapshotObservation(
            source=source,
            product_id=product.id,
            symbol=symbol,
            asks=asks,
            bids=bids,
            checksum=checksum,
            checksum_lexeme=checksum_lexeme,
        )
    if action == "update":
        return BookDeltaObservation(
            source=source,
            product_id=product.id,
            symbol=symbol,
            asks=asks,
            bids=bids,
            checksum=checksum,
            checksum_lexeme=checksum_lexeme,
        )
    raise ValueError("book action")


def _parse_trade(
    message: Mapping[str, object], context: PublicParseContext
) -> PublicTradeObservation | CatalogueMismatchSignal:
    fields = _keys(message, {"type", "sy", "ts", "t", "p", "s", "r"})
    _microseconds(fields["ts"])
    symbol = _text(fields["sy"])
    product = _binding(context, channel="trades", symbol=symbol)
    if isinstance(product, CatalogueMismatchSignal):
        return product
    return PublicTradeObservation(
        source=context.source(channel="trades", symbol=symbol, offset=0),
        product_id=product.id,
        symbol=symbol,
        price=_number(fields["p"])[0],
        size=_integer(fields["s"])[0],
        buyer_role=_text(fields["r"]),
        trade_at=_microseconds(fields["t"]),
    )


def _strip_mark_symbol(value: object) -> str:
    symbol = _text(value)
    if not symbol.startswith("MARK:") or len(symbol) == 5:
        raise ValueError("mark symbol")
    return symbol[5:]


def _parse_mark(
    message: Mapping[str, object], context: PublicParseContext
) -> MarkObservation | CatalogueMismatchSignal:
    fields = _keys(message, {"type", "sy", "ts", "p"})
    _microseconds(fields["ts"])
    symbol = _strip_mark_symbol(fields["sy"])
    product = _binding(context, channel="mark_price", symbol=symbol)
    if isinstance(product, CatalogueMismatchSignal):
        return product
    return MarkObservation(
        source=context.source(channel="mark_price", symbol=symbol, offset=0),
        product_id=product.id,
        symbol=symbol,
        price=_number(fields["p"])[0],
    )


def _parse_candle(
    message: Mapping[str, object], context: PublicParseContext, channel: str
) -> CandleObservation | CatalogueMismatchSignal:
    raw_symbol = _text(message.get("sy"))
    is_mark = raw_symbol.startswith("MARK:")
    required = {"type", "sy", "ts", "res", "o", "h", "l", "c"}
    if not is_mark:
        required.add("v")
    fields = _keys(message, required, {"cst"})
    symbol = _strip_mark_symbol(raw_symbol) if raw_symbol.startswith("MARK:") else raw_symbol
    product = _binding(context, channel=channel, symbol=symbol)
    if isinstance(product, CatalogueMismatchSignal):
        return product
    resolution = _text(fields["res"])
    if channel != f"candlestick_{resolution}":
        raise ValueError("candle resolution")
    volume = None if is_mark else _number(fields["v"])[0]
    # The current public endpoint includes an undocumented, minute-aligned candle
    # start (`cst`) in addition to the publish timestamp (`ts`). Prefer the start
    # when present while retaining compatibility with the documented frame.
    _microseconds(fields["ts"])
    candle_timestamp = _microseconds(fields.get("cst", fields["ts"]))
    return CandleObservation(
        source=context.source(channel=channel, symbol=symbol, offset=0),
        product_id=product.id,
        symbol=symbol,
        resolution=resolution,
        timestamp=candle_timestamp,
        open=_number(fields["o"])[0],
        high=_number(fields["h"])[0],
        low=_number(fields["l"])[0],
        close=_number(fields["c"])[0],
        volume=volume,
    )


def _parse_spot(
    message: Mapping[str, object], context: PublicParseContext
) -> SpotObservation | CatalogueMismatchSignal:
    fields = _keys(message, {"type", "sy", "ts", "p"})
    _microseconds(fields["ts"])
    symbol = _text(fields["sy"])
    asset_id = context.spot_asset_ids.get(symbol)
    if type(asset_id) is not int:
        return CatalogueMismatchSignal(
            source=context.source(channel="spot_price", symbol=None, offset=0)
        )
    return SpotObservation(
        source=context.source(channel="spot_price", symbol=symbol, offset=0),
        asset_id=asset_id,
        index_symbol=symbol,
        price=_number(fields["p"])[0],
    )


def _parse_funding(
    message: Mapping[str, object], context: PublicParseContext
) -> FundingRateObservation | CatalogueMismatchSignal:
    fields = _keys(message, {"type", "sy", "ts", "fi", "fr", "nfr"})
    _microseconds(fields["ts"])
    symbol = _text(fields["sy"])
    product = _binding(context, channel="funding_rate", symbol=symbol)
    if isinstance(product, CatalogueMismatchSignal):
        return product
    return FundingRateObservation(
        source=context.source(channel="funding_rate", symbol=symbol, offset=0),
        product_id=product.id,
        symbol=symbol,
        interval_seconds=_integer(fields["fi"])[0],
        rate=_number(fields["fr"])[0],
        next_funding_at=_microseconds(fields["nfr"]),
    )


def _parse_product_status(
    message: Mapping[str, object], context: PublicParseContext
) -> ProductStatusObservation | CatalogueMismatchSignal:
    fields = _keys(message, {"type", "event", "product", "timestamp"})
    _microseconds(fields["timestamp"])
    product_fields = _keys(fields["product"], {"id", "symbol", "trading_status"})
    product_id, _ = _integer(product_fields["id"])
    symbol = _text(product_fields["symbol"])
    product = _binding(
        context,
        channel="product_updates",
        symbol=symbol,
        product_id=product_id,
    )
    if isinstance(product, CatalogueMismatchSignal):
        return product
    return ProductStatusObservation(
        source=context.source(channel="product_updates", symbol=symbol, offset=0),
        product_id=product.id,
        symbol=symbol,
        event=_text(fields["event"]),
        trading_status=_text(product_fields["trading_status"]),
    )


def _optional_microseconds(value: object) -> datetime | None:
    return None if value is None else _microseconds(value)


def _parse_system_status(
    message: Mapping[str, object], context: PublicParseContext
) -> SystemStatusObservation:
    fields = _keys(
        message,
        {
            "type",
            "status",
            "event",
            "maintenance_announcement_time",
            "maintenance_start_time",
            "maintenance_finish_time",
            "timestamp",
        },
    )
    _microseconds(fields["timestamp"])
    return SystemStatusObservation(
        source=context.source(channel="system_status", symbol=None, offset=0),
        event=_text(fields["event"]),
        status=_text(fields["status"]),
        announcement_at=_optional_microseconds(fields["maintenance_announcement_time"]),
        start_at=_optional_microseconds(fields["maintenance_start_time"]),
        finish_at=_optional_microseconds(fields["maintenance_finish_time"]),
    )


class DeltaPublicFrameParser:
    """Parse one already-decoded public frame without I/O or publication."""

    def parse(
        self,
        message: Mapping[str, object],
        *,
        context: PublicParseContext,
    ) -> tuple[PublicParseOutcome, ...]:
        """Return catalogue-bound observations or narrow typed repair signals."""
        if type(context) is not PublicParseContext or type(message) is not dict:
            raise _protocol_error()
        try:
            channel = _text(message.get("type"))
            if channel == "ticker":
                return _parse_ticker(message, context)
            if channel == "ob_l1":
                return (_parse_l1(message, context),)
            if channel == "ob_l2":
                return (_parse_l2(message, context),)
            if channel == "ob_updates":
                return (_parse_book(message, context),)
            if channel == "trades":
                return (_parse_trade(message, context),)
            if channel == "mark_price":
                return (_parse_mark(message, context),)
            if channel in _CANDLE_CHANNELS:
                return (_parse_candle(message, context, channel),)
            if channel == "spot_price":
                return (_parse_spot(message, context),)
            if channel == "funding_rate":
                return (_parse_funding(message, context),)
            if channel == "product_updates":
                return (_parse_product_status(message, context),)
            if channel == "system_status":
                return (_parse_system_status(message, context),)
            raise ValueError("public channel")
        except ExchangeProtocolError:
            raise
        except Exception:
            raise _protocol_error() from None


__all__ = ["DeltaPublicFrameParser", "PublicParseContext"]
