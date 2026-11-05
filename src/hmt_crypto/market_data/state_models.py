"""Immutable publications and identity helpers for the market-state owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias, cast
from uuid import UUID

from hmt_crypto.exchange.base import MarketComponent
from hmt_crypto.market_data.contracts import (
    BookDeltaObservation,
    BookSnapshotObservation,
    CandleObservation,
    FundingRateObservation,
    L1Observation,
    L2Observation,
    MarketObservation,
    MarketSource,
    MarkObservation,
    ProductStatusObservation,
    PublicTradeObservation,
    SpotObservation,
    SystemStatusObservation,
    TickerObservation,
)
from hmt_crypto.market_data.orderbook import (
    BookRecoveryAction,
    CompleteOrderBook,
)

SourceIdentity: TypeAlias = tuple[UUID, int]
ComponentKey: TypeAlias = tuple[int | None, MarketComponent]


@dataclass(frozen=True, slots=True)
class MarketPublication:
    """Result of applying one source identity to the single writer."""

    revision: int
    source_identity: SourceIdentity
    component: MarketComponent
    applied: bool
    duplicate: bool
    conflict: bool
    book_recovery_action: BookRecoveryAction | None = None


@dataclass(frozen=True, slots=True)
class ProductMarketSnapshot:
    """Immutable last-good/display values for one product."""

    product_id: int
    symbol: str | None
    ticker: TickerObservation | None = None
    l1: L1Observation | None = None
    l2: L2Observation | None = None
    full_book: CompleteOrderBook | None = None
    trade: PublicTradeObservation | None = None
    mark: MarkObservation | None = None
    candle: CandleObservation | None = None
    funding: FundingRateObservation | None = None
    product_status: ProductStatusObservation | None = None


@dataclass(frozen=True, slots=True)
class MarketStateSnapshot:
    """Consumer-specific immutable market view and readiness reasons."""

    revision: int
    connection_generation: UUID
    usable: bool
    reasons: tuple[str, ...]
    products: tuple[ProductMarketSnapshot, ...]
    system_status: SystemStatusObservation | None
    spots: tuple[SpotObservation, ...]


@dataclass(slots=True)
class StateRecord:
    """Mutable single-writer metadata around an immutable display value."""

    value: object
    received_at_monotonic_ns: int
    valid: bool = True
    reason: str | None = None


def observation_source(observation: MarketObservation) -> MarketSource:
    """Return the validated source carried by an observation."""
    return observation.source


def observation_component(observation: MarketObservation) -> MarketComponent:
    """Map one closed observation type to its neutral component."""
    mapping: dict[type[object], MarketComponent] = {
        TickerObservation: MarketComponent.TICKER,
        L1Observation: MarketComponent.L1,
        L2Observation: MarketComponent.L2,
        BookSnapshotObservation: MarketComponent.FULL_BOOK,
        BookDeltaObservation: MarketComponent.FULL_BOOK,
        PublicTradeObservation: MarketComponent.TRADES,
        MarkObservation: MarketComponent.MARK,
        CandleObservation: MarketComponent.CANDLE,
        SpotObservation: MarketComponent.SPOT,
        FundingRateObservation: MarketComponent.FUNDING,
        ProductStatusObservation: MarketComponent.PRODUCT_STATUS,
        SystemStatusObservation: MarketComponent.SYSTEM_STATUS,
    }
    try:
        return mapping[type(observation)]
    except KeyError:
        raise TypeError("unsupported market observation") from None


def observation_owner_id(observation: MarketObservation) -> int | None:
    """Return the product/asset owner or None for system-wide state."""
    if type(observation) is SystemStatusObservation:
        return None
    if type(observation) is SpotObservation:
        return observation.asset_id
    return cast(
        TickerObservation
        | L1Observation
        | L2Observation
        | BookSnapshotObservation
        | BookDeltaObservation
        | PublicTradeObservation
        | MarkObservation
        | CandleObservation
        | FundingRateObservation
        | ProductStatusObservation,
        observation,
    ).product_id


def publication_symbol(value: object) -> str | None:
    """Return a safe symbol from an immutable display publication."""
    if isinstance(value, CompleteOrderBook):
        return value.symbol
    if isinstance(value, SpotObservation):
        return value.index_symbol
    candidate = getattr(value, "symbol", None)
    return candidate if type(candidate) is str else None


def build_product_snapshot(
    records: dict[ComponentKey, StateRecord], product_id: int
) -> ProductMarketSnapshot:
    """Project the last immutable display values for one product."""
    values = {component: records.get((product_id, component)) for component in MarketComponent}

    def value(component: MarketComponent) -> object | None:
        record = values.get(component)
        return None if record is None else record.value

    candidates = tuple(record.value for record in values.values() if record is not None)
    symbol = next(
        (found for item in candidates if (found := publication_symbol(item)) is not None),
        None,
    )
    return ProductMarketSnapshot(
        product_id=product_id,
        symbol=symbol,
        ticker=cast(TickerObservation | None, value(MarketComponent.TICKER)),
        l1=cast(L1Observation | None, value(MarketComponent.L1)),
        l2=cast(L2Observation | None, value(MarketComponent.L2)),
        full_book=cast(CompleteOrderBook | None, value(MarketComponent.FULL_BOOK)),
        trade=cast(PublicTradeObservation | None, value(MarketComponent.TRADES)),
        mark=cast(MarkObservation | None, value(MarketComponent.MARK)),
        candle=cast(CandleObservation | None, value(MarketComponent.CANDLE)),
        funding=cast(FundingRateObservation | None, value(MarketComponent.FUNDING)),
        product_status=cast(
            ProductStatusObservation | None,
            value(MarketComponent.PRODUCT_STATUS),
        ),
    )


__all__ = [
    "ComponentKey",
    "MarketPublication",
    "MarketStateSnapshot",
    "ProductMarketSnapshot",
    "SourceIdentity",
    "StateRecord",
    "build_product_snapshot",
    "observation_component",
    "observation_owner_id",
    "observation_source",
    "publication_symbol",
]
