"""Private Delta wire-schema validation and exchange-neutral normalization."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TypeVar

from pydantic import ValidationError

from hmt_crypto.domain import Asset, Candle, ContractType, NotionalType, Product, Ticker
from hmt_crypto.domain.enums import ProductState, TradingStatus
from hmt_crypto.domain.money import parse_decimal
from hmt_crypto.errors import ExchangeProtocolError
from hmt_crypto.exchange.base import OrderBookLevel, OrderBookSnapshot, ProductRef

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
E = TypeVar("E", bound=StrEnum)


def _protocol(endpoint_id: str) -> ExchangeProtocolError:
    return ExchangeProtocolError(context={"operation": endpoint_id})


def _object(value: object, endpoint_id: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(type(key) is not str for key in value):
        raise _protocol(endpoint_id)
    return value


def _list(value: object, endpoint_id: str) -> list[object]:
    if not isinstance(value, list):
        raise _protocol(endpoint_id)
    return value


def _text(value: object, endpoint_id: str) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > 256
        or any(not character.isprintable() for character in value)
    ):
        raise _protocol(endpoint_id)
    return value


def _integer(value: object, endpoint_id: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _protocol(endpoint_id)
    return value


def _decimal(
    value: object, endpoint_id: str, *, positive: bool = False, nonnegative: bool = False
) -> Decimal:
    try:
        parsed = parse_decimal(value)
    except (TypeError, ValueError):
        raise _protocol(endpoint_id) from None
    if (positive and parsed <= 0) or (nonnegative and parsed < 0):
        raise _protocol(endpoint_id)
    return parsed


def _optional_decimal(
    mapping: dict[str, object], name: str, endpoint_id: str, *, positive: bool = True
) -> Decimal | None:
    value = mapping.get(name)
    if value is None:
        return None
    return _decimal(value, endpoint_id, positive=positive, nonnegative=not positive)


def _enum(value: object, enum_type: type[E], endpoint_id: str) -> E:
    if (
        type(value) is not str
        or not value.isascii()
        or not 0 < len(value) <= 64
        or not value[0].islower()
        or any(
            not (character.islower() or character.isdigit() or character == "_")
            for character in value
        )
    ):
        raise _protocol(endpoint_id)
    try:
        return enum_type(value)
    except ValueError:
        return enum_type("unknown")


def _timestamp_seconds(value: object, endpoint_id: str) -> datetime:
    seconds = _integer(value, endpoint_id)
    try:
        return _EPOCH + timedelta(seconds=seconds)
    except OverflowError:
        raise _protocol(endpoint_id) from None


def _ticker_timestamp(value: object, endpoint_id: str) -> datetime:
    """Accept only documented seconds or observed microseconds, never ambiguous units."""
    raw = _integer(value, endpoint_id)
    if raw <= 253_402_300_799:
        return _timestamp_seconds(raw, endpoint_id)
    if raw < 1_000_000_000_000_000 or raw > 253_402_300_799_999_999:
        raise _protocol(endpoint_id)
    try:
        return _EPOCH + timedelta(microseconds=raw)
    except OverflowError:
        raise _protocol(endpoint_id) from None


def _asset(value: object, endpoint_id: str) -> Asset:
    item = _object(value, endpoint_id)
    precision = (
        _integer(item["precision"], endpoint_id) if item.get("precision") is not None else None
    )
    minimum_precision = (
        _integer(item["minimum_precision"], endpoint_id)
        if item.get("minimum_precision") is not None
        else None
    )
    return Asset(
        id=_integer(item.get("id"), endpoint_id, minimum=1),
        symbol=ProductRef(symbol=_text(item.get("symbol"), endpoint_id)).symbol,
        precision=precision,
        minimum_precision=minimum_precision,
    )


def parse_product(value: object, *, endpoint_id: str) -> Product:
    """Parse one product using an explicit local required-core policy."""
    try:
        item = _object(value, endpoint_id)
        specs = _object(item["product_specs"], endpoint_id)
        # Current testnet option families may omit this flag. Unknown venue permission is
        # represented as explicitly reduce-only so downstream new-risk gates remain closed.
        reduce_only = specs.get("only_reduce_only_orders_allowed", True)
        is_quanto = item.get("is_quanto")
        if not isinstance(reduce_only, bool) or not isinstance(is_quanto, bool):
            raise _protocol(endpoint_id)
        contract_type = _enum(item.get("contract_type"), ContractType, endpoint_id)
        settling_value = item.get("settling_asset")
        settling_asset = (
            None
            if contract_type is ContractType.SPOT and settling_value is None
            else _asset(settling_value, endpoint_id)
        )
        return Product(
            id=_integer(item.get("id"), endpoint_id, minimum=1),
            symbol=ProductRef(symbol=_text(item.get("symbol"), endpoint_id)).symbol,
            contract_type=contract_type,
            notional_type=_enum(item.get("notional_type"), NotionalType, endpoint_id),
            contract_value=_decimal(item.get("contract_value"), endpoint_id, positive=True),
            contract_unit_currency=ProductRef(
                symbol=_text(item.get("contract_unit_currency"), endpoint_id)
            ).symbol,
            tick_size=_decimal(item.get("tick_size"), endpoint_id, positive=True),
            state=_enum(item.get("state"), ProductState, endpoint_id),
            trading_status=_enum(item.get("trading_status"), TradingStatus, endpoint_id),
            underlying_asset=_asset(item.get("underlying_asset"), endpoint_id),
            quoting_asset=_asset(item.get("quoting_asset"), endpoint_id),
            settling_asset=settling_asset,
            is_quanto=is_quanto,
            only_reduce_only_orders_allowed=reduce_only,
            position_size_limit=_optional_decimal(
                item, "position_size_limit", endpoint_id, positive=False
            ),
            position_notional_limit=_optional_decimal(
                item, "position_notional_limit", endpoint_id, positive=False
            ),
            initial_margin=_optional_decimal(item, "initial_margin", endpoint_id, positive=False),
            maintenance_margin=_optional_decimal(
                item, "maintenance_margin", endpoint_id, positive=False
            ),
            default_leverage=_optional_decimal(item, "default_leverage", endpoint_id),
            max_leverage_notional=_optional_decimal(
                item, "max_leverage_notional", endpoint_id, positive=False
            ),
        )
    except (KeyError, TypeError, ValueError, ValidationError, ExchangeProtocolError):
        raise _protocol(endpoint_id) from None


def parse_ticker(value: object, *, endpoint_id: str) -> Ticker:
    """Parse one ticker; trading status is deliberately not inferred from REST."""
    try:
        item = _object(value, endpoint_id)
        quotes_value = item.get("quotes")
        band_value = item.get("price_band")
        quotes = {} if quotes_value is None else _object(quotes_value, endpoint_id)
        band = {} if band_value is None else _object(band_value, endpoint_id)
        return Ticker(
            product_id=_integer(item.get("product_id"), endpoint_id, minimum=1),
            symbol=ProductRef(symbol=_text(item.get("symbol"), endpoint_id)).symbol,
            timestamp=_ticker_timestamp(item.get("timestamp"), endpoint_id),
            contract_type=_enum(item.get("contract_type", "unknown"), ContractType, endpoint_id),
            product_trading_status=TradingStatus.UNKNOWN,
            mark_price=_optional_decimal(item, "mark_price", endpoint_id),
            spot_price=_optional_decimal(item, "spot_price", endpoint_id),
            close=_optional_decimal(item, "close", endpoint_id),
            best_bid=_optional_decimal(quotes, "best_bid", endpoint_id),
            best_ask=_optional_decimal(quotes, "best_ask", endpoint_id),
            lower_price_limit=_optional_decimal(band, "lower_limit", endpoint_id),
            upper_price_limit=_optional_decimal(band, "upper_limit", endpoint_id),
        )
    except (TypeError, ValueError, ValidationError, ExchangeProtocolError):
        raise _protocol(endpoint_id) from None


def parse_ticker_collection(value: object, *, endpoint_id: str) -> tuple[Ticker, ...]:
    values = value if isinstance(value, list) else [value]
    return tuple(parse_ticker(item, endpoint_id=endpoint_id) for item in values)


def _level(value: object, endpoint_id: str) -> OrderBookLevel:
    item = _object(value, endpoint_id)
    depth = item.get("depth")
    if type(depth) is not str:
        raise _protocol(endpoint_id)
    _decimal(depth, endpoint_id, nonnegative=True)
    return OrderBookLevel(
        price=_decimal(item.get("price"), endpoint_id, positive=True),
        size=_integer(item.get("size"), endpoint_id),
    )


def parse_order_book(
    value: object,
    *,
    endpoint_id: str,
    requested: ProductRef,
) -> OrderBookSnapshot:
    """Parse and sort L2 levels without rejecting documented auction overlap."""
    try:
        item = _object(value, endpoint_id)
        if _text(item.get("symbol"), endpoint_id) != requested.symbol:
            raise _protocol(endpoint_id)
        raw_timestamp = _integer(item.get("last_updated_at"), endpoint_id)
        if not 10**15 <= raw_timestamp < 10**16:
            raise _protocol(endpoint_id)
        seconds, microseconds = divmod(raw_timestamp, 1_000_000)
        timestamp = _EPOCH + timedelta(seconds=seconds, microseconds=microseconds)
        bids = tuple(
            sorted(
                (_level(level, endpoint_id) for level in _list(item.get("buy"), endpoint_id)),
                key=lambda level: level.price,
                reverse=True,
            )
        )
        asks = tuple(
            sorted(
                (_level(level, endpoint_id) for level in _list(item.get("sell"), endpoint_id)),
                key=lambda level: level.price,
            )
        )
        if len({level.price for level in bids}) != len(bids):
            raise _protocol(endpoint_id)
        if len({level.price for level in asks}) != len(asks):
            raise _protocol(endpoint_id)
        return OrderBookSnapshot(
            product=requested,
            timestamp=timestamp,
            bids=bids,
            asks=asks,
        )
    except (TypeError, ValueError, ValidationError, OverflowError, ExchangeProtocolError):
        raise _protocol(endpoint_id) from None


def parse_candles(value: object, *, endpoint_id: str) -> tuple[Candle, ...]:
    """Parse a candle list while preserving exact OHLCV decimal values."""
    try:
        values = _list(value, endpoint_id)
        candles = []
        for raw in values:
            item = _object(raw, endpoint_id)
            candles.append(
                Candle(
                    timestamp=_timestamp_seconds(item.get("time"), endpoint_id),
                    open=_decimal(item.get("open"), endpoint_id, positive=True),
                    high=_decimal(item.get("high"), endpoint_id, positive=True),
                    low=_decimal(item.get("low"), endpoint_id, positive=True),
                    close=_decimal(item.get("close"), endpoint_id, positive=True),
                    volume=_decimal(item.get("volume"), endpoint_id, nonnegative=True),
                )
            )
        return tuple(candles)
    except (TypeError, ValueError, ValidationError, ExchangeProtocolError):
        raise _protocol(endpoint_id) from None


__all__ = [
    "parse_candles",
    "parse_order_book",
    "parse_product",
    "parse_ticker",
    "parse_ticker_collection",
]
