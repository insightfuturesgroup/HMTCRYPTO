"""Immutable product-catalogue evidence and candidate validation."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from types import MappingProxyType

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.domain import Asset, Product, ProductState, TradingStatus
from hmt_crypto.errors import ProductNotFoundError


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("catalogue timestamps must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True, repr=False)
class CatalogSource:
    """Region/environment identity for one isolated Delta product universe."""

    region: DeltaRegion
    environment: DeltaEnvironment

    def __post_init__(self) -> None:
        if not isinstance(self.region, DeltaRegion) or not isinstance(
            self.environment, DeltaEnvironment
        ):
            raise TypeError("catalogue source requires typed Delta selectors")

    def __repr__(self) -> str:
        return (
            f"CatalogSource(region={self.region.value!r}, environment={self.environment.value!r})"
        )


def venue_allows_new_orders(product: Product) -> bool:
    """Return only the venue-state gate, not overall sizing/risk eligibility."""
    if not isinstance(product, Product):
        raise TypeError("venue state requires a Product")
    return (
        product.state is ProductState.LIVE
        and product.trading_status is TradingStatus.OPERATIONAL
        and not product.only_reduce_only_orders_allowed
    )


@dataclass(frozen=True, slots=True, init=False, repr=False)
class CatalogSnapshot:
    """One complete immutable, source-scoped product-catalogue snapshot."""

    source: CatalogSource
    revision: int
    content_sha256: str
    fetched_at: datetime
    validated_at: datetime
    fresh_until: datetime
    products: tuple[Product, ...]
    by_id: Mapping[int, Product]
    by_symbol: Mapping[str, Product]
    assets_by_id: Mapping[int, Asset]

    def __init__(
        self,
        *,
        source: CatalogSource,
        revision: int,
        content_sha256: str,
        fetched_at: datetime,
        validated_at: datetime,
        fresh_until: datetime,
        products: Sequence[Product],
        by_id: Mapping[int, Product],
        by_symbol: Mapping[str, Product],
        assets_by_id: Mapping[int, Asset],
    ) -> None:
        if not isinstance(source, CatalogSource):
            raise TypeError("snapshot source is invalid")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision <= 0:
            raise ValueError("snapshot revision must be positive")
        if (
            type(content_sha256) is not str
            or len(content_sha256) != 64
            or any(character not in "0123456789abcdef" for character in content_sha256)
        ):
            raise ValueError("snapshot content hash is invalid")
        fetched = _utc(fetched_at)
        validated = _utc(validated_at)
        fresh = _utc(fresh_until)
        if fetched > validated or fresh <= validated:
            raise ValueError("snapshot timestamps are inconsistent")
        copied_products = tuple(products)
        id_map = dict(by_id)
        symbol_map = dict(by_symbol)
        asset_map = dict(assets_by_id)
        if not copied_products or any(not isinstance(item, Product) for item in copied_products):
            raise ValueError("snapshot products are invalid")
        if set(id_map) != {item.id for item in copied_products} or set(symbol_map) != {
            item.symbol for item in copied_products
        }:
            raise ValueError("snapshot indexes are inconsistent")
        if any(id_map[item.id] is not item for item in copied_products) or any(
            symbol_map[item.symbol] is not item for item in copied_products
        ):
            raise ValueError("snapshot indexes must reference snapshot products")
        if any(not isinstance(asset, Asset) or key != asset.id for key, asset in asset_map.items()):
            raise ValueError("snapshot asset index is inconsistent")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "content_sha256", content_sha256)
        object.__setattr__(self, "fetched_at", fetched)
        object.__setattr__(self, "validated_at", validated)
        object.__setattr__(self, "fresh_until", fresh)
        object.__setattr__(self, "products", copied_products)
        object.__setattr__(self, "by_id", MappingProxyType(id_map))
        object.__setattr__(self, "by_symbol", MappingProxyType(symbol_map))
        object.__setattr__(self, "assets_by_id", MappingProxyType(asset_map))

    def __repr__(self) -> str:
        return (
            "CatalogSnapshot("
            f"source={self.source!r}, revision={self.revision}, "
            f"product_count={len(self.products)})"
        )

    def is_fresh(self, at: datetime) -> bool:
        """Return whether this snapshot is fresh at an aware UTC instant."""
        return _utc(at) <= self.fresh_until

    def require_product_id(self, product_id: int) -> Product:
        """Return a product by numeric ID or raise a safe typed error."""
        if isinstance(product_id, bool) or not isinstance(product_id, int) or product_id <= 0:
            raise ProductNotFoundError(context={"operation": "catalog_lookup"})
        try:
            return self.by_id[product_id]
        except KeyError:
            raise ProductNotFoundError(
                context={"operation": "catalog_lookup", "product_id": product_id}
            ) from None

    def require_symbol(self, symbol: str) -> Product:
        """Return a product by exact symbol or raise a safe typed error."""
        if type(symbol) is not str or not symbol or not symbol.isprintable():
            raise ProductNotFoundError(context={"operation": "catalog_lookup"})
        try:
            return self.by_symbol[symbol]
        except KeyError:
            raise ProductNotFoundError(context={"operation": "catalog_lookup"}) from None

    def venue_allows_new_orders(self, product_id: int) -> bool:
        """Apply only the narrow venue-state gate to one indexed product."""
        return venue_allows_new_orders(self.require_product_id(product_id))


def _canonical_value(value: object) -> object:
    if isinstance(value, Decimal):
        if not value:
            return {"decimal": [0, "0", 0]}
        sign, digits, exponent = value.as_tuple()
        if not isinstance(exponent, int):
            raise TypeError("canonical product decimals must be finite")
        canonical_digits = list(digits)
        while len(canonical_digits) > 1 and canonical_digits[-1] == 0:
            canonical_digits.pop()
            exponent += 1
        return {"decimal": [sign, "".join(str(digit) for digit in canonical_digits), exponent]}
    if isinstance(value, Enum):
        return _canonical_value(value.value)
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("canonical product mappings require string keys")
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if value is None or type(value) in {bool, int, str}:
        return value
    raise TypeError("unsupported canonical product value")


def _canonical_hash(source: CatalogSource, products: Sequence[Product]) -> str:
    payload = {
        "source": {
            "region": source.region.value,
            "environment": source.environment.value,
        },
        "products": [
            _canonical_value(product.model_dump(mode="python", warnings="error"))
            for product in products
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def build_snapshot(
    *,
    source: CatalogSource,
    revision: int,
    products: Sequence[Product],
    fetched_at: datetime,
    validated_at: datetime,
    fresh_until: datetime,
) -> CatalogSnapshot:
    """Validate a full candidate and return an immutable snapshot."""
    if not isinstance(source, CatalogSource):
        raise ValueError("catalogue source is invalid")
    copied = tuple(products)
    if not copied or any(not isinstance(product, Product) for product in copied):
        raise ValueError("catalogue requires products")
    ordered = tuple(sorted(copied, key=lambda product: product.id))
    by_id: dict[int, Product] = {}
    by_symbol: dict[str, Product] = {}
    symbols_casefold: set[str] = set()
    assets_by_id: dict[int, Asset] = {}
    asset_symbol_ids: dict[str, int] = {}

    for product in ordered:
        Product.model_validate(
            product.model_dump(mode="python", warnings="none"),
            strict=True,
        )
        if product.id in by_id or product.symbol in by_symbol:
            raise ValueError("duplicate product identity")
        folded_symbol = product.symbol.casefold()
        if folded_symbol in symbols_casefold:
            raise ValueError("ambiguous product symbol")
        if (
            product.initial_margin is not None
            and product.maintenance_margin is not None
            and product.maintenance_margin > product.initial_margin
        ):
            raise ValueError("maintenance margin exceeds initial margin")
        by_id[product.id] = product
        by_symbol[product.symbol] = product
        symbols_casefold.add(folded_symbol)
        for asset in (
            product.underlying_asset,
            product.quoting_asset,
            product.settling_asset,
        ):
            known = assets_by_id.get(asset.id)
            if known is not None and known != asset:
                raise ValueError("conflicting asset metadata")
            folded_asset = asset.symbol.casefold()
            known_id = asset_symbol_ids.get(folded_asset)
            if known_id is not None and known_id != asset.id:
                raise ValueError("ambiguous asset symbol")
            assets_by_id[asset.id] = asset
            asset_symbol_ids[folded_asset] = asset.id

    return CatalogSnapshot(
        source=source,
        revision=revision,
        content_sha256=_canonical_hash(source, ordered),
        fetched_at=fetched_at,
        validated_at=validated_at,
        fresh_until=fresh_until,
        products=ordered,
        by_id=by_id,
        by_symbol=by_symbol,
        assets_by_id=assets_by_id,
    )


__all__ = [
    "CatalogSnapshot",
    "CatalogSource",
    "build_snapshot",
    "venue_allows_new_orders",
]
