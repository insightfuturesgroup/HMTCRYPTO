"""Exact scalar, timestamp, identity, and level parsing for public frames."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from uuid import UUID

from hmt_crypto.domain import Product
from hmt_crypto.errors import ExchangeProtocolError
from hmt_crypto.exchange.delta.catalog_models import CatalogSnapshot
from hmt_crypto.exchange.delta.websocket_codec import WsNumberToken
from hmt_crypto.market_data.contracts import BookLevel, MarketSource

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")


def protocol_error() -> ExchangeProtocolError:
    """Return one redacted stable public parser error."""
    return ExchangeProtocolError(context={"operation": "parse_delta_public_ws", "scope": "public"})


@dataclass(frozen=True, slots=True, kw_only=True)
class PublicParseContext:
    """Immutable authoritative inputs for parsing one received public frame."""

    catalogue: CatalogSnapshot
    catalogue_snapshot_id: UUID
    connection_generation: UUID
    local_sequence_start: int
    received_at: datetime
    spot_asset_ids: Mapping[str, int]

    def __post_init__(self) -> None:
        if type(self.catalogue) is not CatalogSnapshot:
            raise ValueError("public parse catalogue is invalid")
        if (
            type(self.catalogue_snapshot_id) is not UUID
            or type(self.connection_generation) is not UUID
        ):
            raise ValueError("public parse identity is invalid")
        if type(self.local_sequence_start) is not int or self.local_sequence_start <= 0:
            raise ValueError("public parse sequence is invalid")
        if (
            type(self.received_at) is not datetime
            or self.received_at.tzinfo is None
            or self.received_at.utcoffset() is None
        ):
            raise ValueError("public receive time is invalid")
        owned = dict(self.spot_asset_ids)
        for symbol, asset_id in owned.items():
            if (
                type(symbol) is not str
                or not symbol
                or type(asset_id) is not int
                or asset_id <= 0
                or asset_id not in self.catalogue.assets_by_id
            ):
                raise ValueError("public spot binding is invalid")
        object.__setattr__(self, "spot_asset_ids", MappingProxyType(owned))
        object.__setattr__(self, "received_at", self.received_at.astimezone(UTC))

    def source(
        self,
        *,
        channel: str,
        symbol: str | None,
        offset: int,
        venue_sequence: int | None = None,
    ) -> MarketSource:
        """Create one source identity without minting catalogue evidence."""
        return MarketSource(
            region=self.catalogue.source.region.value,
            environment=self.catalogue.source.environment.value,
            connection_generation=self.connection_generation,
            local_sequence=self.local_sequence_start + offset,
            catalogue_snapshot_id=self.catalogue_snapshot_id,
            channel=channel,
            symbol=symbol,
            venue_sequence=venue_sequence,
            observed_at=self.received_at,
        )


def keys(
    value: object,
    required: set[str],
    optional: set[str] | None = None,
) -> dict[str, object]:
    """Validate exact required and admitted optional object keys."""
    admitted = set() if optional is None else optional
    if type(value) is not dict or not required <= set(value) <= required | admitted:
        raise ValueError("schema keys")
    return value


def text(value: object) -> str:
    """Parse one bounded nonempty protocol text scalar."""
    if type(value) is not str or not value or len(value) > 256:
        raise ValueError("text field")
    return value


def number(value: object) -> tuple[Decimal, str]:
    """Parse one exact number while retaining its original lexeme."""
    if type(value) is WsNumberToken:
        return value.value, value.lexeme
    if type(value) is str and len(value) <= 1024 and _NUMBER.fullmatch(value) is not None:
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            raise ValueError("decimal field") from None
        if parsed.is_finite():
            return parsed, value
    raise ValueError("decimal field")


def integer(value: object, *, nonnegative: bool = True) -> tuple[int, str]:
    """Parse one integral exact number and preserve its lexeme."""
    parsed, lexeme = number(value)
    integral = parsed.to_integral_value()
    if parsed != integral or (nonnegative and parsed < 0):
        raise ValueError("integer field")
    return int(integral), lexeme


def microseconds(value: object) -> datetime:
    """Parse an exact epoch-microsecond field without binary floats."""
    parsed, _ = integer(value)
    try:
        return _EPOCH + timedelta(microseconds=parsed)
    except OverflowError:
        raise ValueError("microsecond timestamp") from None


def iso_microseconds(value: object) -> datetime:
    """Parse an aware ISO timestamp carrying exactly six fractional digits."""
    raw = text(value)
    if "." not in raw or len(raw.rsplit(".", 1)[1].split("+")[0].rstrip("Z")) != 6:
        raise ValueError("ISO timestamp precision")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("ISO timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("ISO timestamp zone")
    return parsed.astimezone(UTC)


def product(context: PublicParseContext, symbol: str, product_id: int | None = None) -> Product:
    """Resolve one exact catalogue symbol/id pair without inference."""
    item = context.catalogue.by_symbol.get(symbol)
    if item is None or (product_id is not None and item.id != product_id):
        raise ValueError("catalogue identity")
    return item


def resolve_product(
    context: PublicParseContext,
    symbol: str,
    product_id: int | None = None,
) -> tuple[Product | None, int | None]:
    """Resolve an exact pair or return only a safe authoritative affected ID."""
    by_symbol = context.catalogue.by_symbol.get(symbol)
    by_id = context.catalogue.by_id.get(product_id) if product_id is not None else None
    if by_symbol is not None and (product_id is None or by_symbol.id == product_id):
        return by_symbol, None
    affected = by_symbol.id if by_symbol is not None else None
    if affected is None and by_id is not None:
        affected = by_id.id
    return None, affected


def levels(value: object, *, descending: bool = False) -> tuple[BookLevel, ...]:
    """Parse and own one already-ordered book side without coercive sorting."""
    if type(value) is not list:
        raise ValueError("book side")
    result: list[BookLevel] = []
    for row in value:
        if type(row) is not list or len(row) != 2:
            raise ValueError("book level shape")
        price, price_lexeme = number(row[0])
        size, size_lexeme = integer(row[1])
        result.append(
            BookLevel(
                price=price,
                size=size,
                price_lexeme=price_lexeme,
                size_lexeme=size_lexeme,
            )
        )
    prices = tuple(level.price for level in result)
    expected = tuple(sorted(prices, reverse=descending))
    if prices != expected or len(set(prices)) != len(prices):
        raise ValueError("book side order")
    return tuple(result)


def array_numbers(value: object, length: int) -> tuple[Decimal, ...]:
    """Parse an exact-length compact decimal array."""
    if type(value) is not list or len(value) != length:
        raise ValueError("compact array shape")
    return tuple(number(item)[0] for item in value)


def array_optional_numbers(
    value: object,
    length: int,
    *,
    nullable: frozenset[int],
) -> tuple[Decimal | None, ...]:
    """Parse an exact compact array with null admitted only at named positions."""
    if type(value) is not list or len(value) != length:
        raise ValueError("compact array shape")
    result: list[Decimal | None] = []
    for index, item in enumerate(value):
        if item is None:
            if index not in nullable:
                raise ValueError("compact array null")
            result.append(None)
        else:
            result.append(number(item)[0])
    return tuple(result)
