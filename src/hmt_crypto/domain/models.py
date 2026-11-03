"""Immutable, exact domain models for Phase 1 public exchange data."""

from __future__ import annotations

from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, model_validator

from hmt_crypto.domain.enums import ContractType, NotionalType, ProductState, TradingStatus
from hmt_crypto.domain.money import NonNegativeDecimal, PositiveDecimal, UtcDateTime

PositiveId = Annotated[int, Field(strict=True, gt=0)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]


class DomainModel(BaseModel):
    """Shared fail-closed configuration for immutable public domain values."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class Asset(DomainModel):
    """An exchange asset identified by its environment-specific numeric ID."""

    id: PositiveId
    symbol: NonEmptyText
    precision: NonNegativeInt | None = None
    minimum_precision: NonNegativeInt | None = None


class Product(DomainModel):
    """Catalogue metadata retained without inferring contract rules from symbols."""

    id: PositiveId
    symbol: NonEmptyText
    contract_type: ContractType
    notional_type: NotionalType
    contract_value: PositiveDecimal
    contract_unit_currency: NonEmptyText
    tick_size: PositiveDecimal
    state: ProductState
    trading_status: TradingStatus
    underlying_asset: Asset
    quoting_asset: Asset
    settling_asset: Asset
    is_quanto: StrictBool
    only_reduce_only_orders_allowed: StrictBool = False
    position_size_limit: PositiveDecimal | None = None
    position_notional_limit: PositiveDecimal | None = None
    initial_margin: PositiveDecimal | None = None
    maintenance_margin: PositiveDecimal | None = None
    default_leverage: PositiveDecimal | None = None
    max_leverage_notional: PositiveDecimal | None = None


class Ticker(DomainModel):
    """A normalized ticker snapshot with a UTC observation timestamp."""

    product_id: PositiveId
    symbol: NonEmptyText
    timestamp: UtcDateTime
    product_trading_status: TradingStatus
    mark_price: PositiveDecimal | None = None
    spot_price: PositiveDecimal | None = None
    close: PositiveDecimal | None = None
    best_bid: PositiveDecimal | None = None
    best_ask: PositiveDecimal | None = None
    lower_price_limit: PositiveDecimal | None = None
    upper_price_limit: PositiveDecimal | None = None

    @model_validator(mode="after")
    def validate_price_ordering(self) -> Self:
        """Reject internally contradictory bid/ask and price-band snapshots."""
        if (
            self.best_bid is not None
            and self.best_ask is not None
            and self.best_bid > self.best_ask
        ):
            raise ValueError("best bid cannot exceed best ask")
        if (
            self.lower_price_limit is not None
            and self.upper_price_limit is not None
            and self.lower_price_limit > self.upper_price_limit
        ):
            raise ValueError("lower price limit cannot exceed upper price limit")
        return self


class Candle(DomainModel):
    """An exact OHLCV candle anchored to a timezone-aware UTC timestamp."""

    timestamp: UtcDateTime
    open: PositiveDecimal
    high: PositiveDecimal
    low: PositiveDecimal
    close: PositiveDecimal
    volume: NonNegativeDecimal

    @model_validator(mode="after")
    def validate_ohlc_range(self) -> Self:
        """Ensure the high/low bound both opening and closing prices."""
        if self.high < self.low:
            raise ValueError("candle high cannot be below low")
        if self.high < max(self.open, self.close):
            raise ValueError("candle high must bound open and close")
        if self.low > min(self.open, self.close):
            raise ValueError("candle low must bound open and close")
        return self


class RequestReference(DomainModel):
    """Immutable local correlation and optional exchange request identifiers."""

    correlation_id: UUID
    exchange_request_id: NonEmptyText | None = None
