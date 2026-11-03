"""Synthetic, deterministic fixtures for Delta contract-sizing tests."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.domain import (
    Asset,
    ContractType,
    NotionalType,
    Product,
    ProductState,
    Ticker,
    TradingStatus,
)
from hmt_crypto.exchange.delta.catalog import CatalogSnapshot, CatalogSource

BASE_TIME = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)


def sizing_api() -> SimpleNamespace:
    """Load the wished-for sizing surface with one explicit missing-API failure."""
    try:
        module = importlib.import_module("hmt_crypto.exchange.delta.sizing")
        errors = importlib.import_module("hmt_crypto.errors")
    except (AttributeError, ImportError, ModuleNotFoundError):
        pytest.fail("Delta contract-sizing API is missing", pytrace=False)
    names = (
        "ContractConversion",
        "ContractRounding",
        "ContractSizerRegistry",
        "LinearContractSizer",
        "PriceBasis",
        "PriceQuantization",
        "SizingUnit",
    )
    if any(not hasattr(module, name) for name in names):
        pytest.fail("Delta contract-sizing API is missing", pytrace=False)
    return SimpleNamespace(
        **{name: getattr(module, name) for name in names},
        ContractSizingError=getattr(errors, "ContractSizingError", None),
        StaleCatalogError=getattr(errors, "StaleCatalogError", None),
        UnsupportedProductError=getattr(errors, "UnsupportedProductError", None),
    )


def make_product(**updates: Any) -> Product:
    """Build one fully explicit synthetic linear perpetual product."""
    underlying = Asset(id=1, symbol="XBT", precision=8, minimum_precision=1)
    quote = Asset(id=2, symbol="USD", precision=2, minimum_precision=1)
    values: dict[str, Any] = {
        "id": 1001,
        "symbol": "XBTUSD_TEST",
        "contract_type": ContractType.PERPETUAL_FUTURES,
        "notional_type": NotionalType.VANILLA,
        "contract_value": Decimal("0.001"),
        "contract_unit_currency": "XBT",
        "tick_size": Decimal("0.5"),
        "state": ProductState.LIVE,
        "trading_status": TradingStatus.OPERATIONAL,
        "underlying_asset": underlying,
        "quoting_asset": quote,
        "settling_asset": quote,
        "is_quanto": False,
        "only_reduce_only_orders_allowed": False,
        "position_size_limit": Decimal("5000"),
        "position_notional_limit": Decimal("250000"),
        "default_leverage": Decimal("50"),
        "max_leverage_notional": Decimal("100000"),
    }
    values.update(updates)
    return Product.model_validate(values, strict=True)


def make_snapshot(
    product: Product | None = None,
    *,
    region: DeltaRegion = DeltaRegion.INDIA,
    environment: DeltaEnvironment = DeltaEnvironment.TESTNET,
    validated_at: datetime = BASE_TIME,
    ttl: timedelta = timedelta(minutes=5),
) -> CatalogSnapshot:
    """Publish one real immutable snapshot using synthetic metadata only."""
    return CatalogSnapshot(
        source=CatalogSource(region=region, environment=environment),
        revision=7,
        fetched_at=validated_at,
        validated_at=validated_at,
        ttl=ttl,
        products=(product or make_product(),),
    )


def make_ticker(**updates: Any) -> Ticker:
    """Build one exact ticker containing every selectable price basis."""
    values: dict[str, Any] = {
        "product_id": 1001,
        "symbol": "XBTUSD_TEST",
        "timestamp": BASE_TIME,
        "contract_type": ContractType.PERPETUAL_FUTURES,
        "product_trading_status": TradingStatus.UNKNOWN,
        "mark_price": Decimal("50000"),
        "close": Decimal("49990"),
        "best_bid": Decimal("49999.5"),
        "best_ask": Decimal("50000.5"),
    }
    values.update(updates)
    return Ticker.model_validate(values, strict=True)


def make_registry(
    *,
    region: DeltaRegion = DeltaRegion.INDIA,
    environment: DeltaEnvironment = DeltaEnvironment.TESTNET,
) -> Any:
    """Create a source-bound registry through the public sizing API."""
    api = sizing_api()
    return api.ContractSizerRegistry(source=CatalogSource(region=region, environment=environment))


def resolve(
    product: Product | None = None,
    *,
    snapshot: CatalogSnapshot | None = None,
    checked_at: datetime = BASE_TIME,
    risk_increasing: bool = True,
) -> Any:
    """Resolve the common source-bound linear sizer."""
    active = snapshot or make_snapshot(product)
    return make_registry().resolve(
        snapshot=active,
        product_id=active.products[0].id,
        checked_at=checked_at,
        risk_increasing=risk_increasing,
    )


__all__ = [
    "BASE_TIME",
    "make_product",
    "make_registry",
    "make_snapshot",
    "make_ticker",
    "resolve",
    "sizing_api",
]
