"""Validated exact primitives shared by public market observations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

_CHANNEL = re.compile(
    r"(?:ticker|ob_l1|ob_l2|ob_updates|trades|mark_price|spot_price|"
    r"funding_rate|product_updates|system_status|candlestick_(?:1m|3m|5m|15m|"
    r"30m|1h|2h|4h|6h|12h|1d|1w))\Z"
)


def exact_utc(value: object, *, name: str) -> datetime:
    """Return an exact aware value normalized to UTC."""
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def exact_decimal(
    value: object,
    *,
    name: str,
    positive: bool = False,
    nonnegative: bool = False,
) -> Decimal:
    """Validate one finite Decimal without binary-float coercion."""
    if type(value) is not Decimal or not value.is_finite():
        raise TypeError(f"{name} must be an exact finite Decimal")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    if nonnegative and value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return value


def optional_decimal(
    value: object, *, name: str, positive: bool = False, nonnegative: bool = False
) -> Decimal | None:
    """Validate one optional exact Decimal."""
    if value is None:
        return None
    return exact_decimal(value, name=name, positive=positive, nonnegative=nonnegative)


def positive_int(value: object, *, name: str) -> int:
    """Validate a strict positive integer."""
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def nonnegative_int(value: object, *, name: str) -> int:
    """Validate a strict nonnegative integer."""
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def require_channel(source: MarketSource, *channels: str) -> None:
    """Bind an observation type to its exact admitted channel."""
    if source.channel not in channels:
        raise ValueError("observation source channel conflicts with its type")


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketSource:
    """Exact source identity for one accepted public observation."""

    region: str
    environment: str
    connection_generation: UUID
    local_sequence: int
    catalogue_snapshot_id: UUID
    channel: str
    symbol: str | None
    venue_sequence: int | None
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.region != "india" or self.environment not in {"testnet", "prod"}:
            raise ValueError("market source must be an admitted India environment")
        if (
            type(self.connection_generation) is not UUID
            or type(self.catalogue_snapshot_id) is not UUID
        ):
            raise TypeError("market source identifiers must be UUIDs")
        positive_int(self.local_sequence, name="local_sequence")
        if self.venue_sequence is not None:
            nonnegative_int(self.venue_sequence, name="venue_sequence")
        if type(self.channel) is not str or _CHANNEL.fullmatch(self.channel) is None:
            raise ValueError("market source channel is not admitted")
        if self.symbol is not None and (type(self.symbol) is not str or not self.symbol):
            raise ValueError("market source symbol is invalid")
        object.__setattr__(self, "observed_at", exact_utc(self.observed_at, name="observed_at"))


@dataclass(frozen=True, slots=True, kw_only=True)
class ProductObservation:
    """Shared exact catalogue identity for a product observation."""

    source: MarketSource
    product_id: int
    symbol: str

    def __post_init__(self) -> None:
        if type(self.source) is not MarketSource:
            raise TypeError("observation source is invalid")
        positive_int(self.product_id, name="product_id")
        if type(self.symbol) is not str or not self.symbol or self.source.symbol != self.symbol:
            raise ValueError("observation identity does not match its source")


@dataclass(frozen=True, slots=True, kw_only=True)
class BookLevel:
    """One exact level retaining numeric value and checksum wire spelling."""

    price: Decimal
    size: int
    price_lexeme: str
    size_lexeme: str

    def __post_init__(self) -> None:
        exact_decimal(self.price, name="price", positive=True)
        nonnegative_int(self.size, name="size")
        if (
            type(self.price_lexeme) is not str
            or not self.price_lexeme
            or type(self.size_lexeme) is not str
            or not self.size_lexeme
            or len(self.price_lexeme) > 1024
            or len(self.size_lexeme) > 1024
        ):
            raise ValueError("book level lexemes are invalid")
        try:
            wire_price = Decimal(self.price_lexeme)
            wire_size = Decimal(self.size_lexeme)
        except Exception:
            raise ValueError("book level lexemes are invalid") from None
        if wire_price != self.price or not wire_price.is_finite() or not wire_size.is_finite():
            raise ValueError("book level lexemes conflict with parsed values")
        if wire_size != self.size:
            raise ValueError("book size must be integral and match its lexeme")


__all__ = ["BookLevel", "MarketSource", "ProductObservation"]
