"""Immutable product-catalogue evidence and candidate validation."""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from secrets import token_bytes
from types import MappingProxyType

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.domain import Asset, Product, ProductState, TradingStatus
from hmt_crypto.errors import ProductNotFoundError
from hmt_crypto.exchange.delta.paginator import PaginationResult


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
        if type(self.region) is not DeltaRegion or type(self.environment) is not DeltaEnvironment:
            raise TypeError("catalogue source requires typed Delta selectors")

    def __repr__(self) -> str:
        return (
            f"CatalogSource(region={self.region.value!r}, environment={self.environment.value!r})"
        )


def _is_exact_source(value: object) -> bool:
    return (
        type(value) is CatalogSource
        and type(value.region) is DeltaRegion
        and type(value.environment) is DeltaEnvironment
    )


def _clone_source(value: object) -> CatalogSource:
    if type(value) is not CatalogSource:
        raise TypeError("catalogue source is invalid")
    region = value.region
    environment = value.environment
    if type(region) is not DeltaRegion or type(environment) is not DeltaEnvironment:
        raise TypeError("catalogue source is invalid")
    return CatalogSource(region=region, environment=environment)


def _clone_product(value: object) -> Product:
    if type(value) is not Product:
        raise ValueError("catalogue item is not a Product")
    fields_set = frozenset(value.model_fields_set)
    if not fields_set <= Product.model_fields.keys():
        raise ValueError("catalogue product provenance is invalid")
    payload = value.model_dump(
        mode="python",
        include=set(fields_set),
        warnings="none",
    )
    cloned = Product.model_validate(payload, strict=True)
    if frozenset(cloned.model_fields_set) != fields_set:
        raise ValueError("catalogue product provenance changed during cloning")
    return cloned


@dataclass(frozen=True, slots=True, init=False, repr=False)
class CatalogProductLoad:
    """Immutable source provenance plus explicit paginator completion evidence."""

    source: CatalogSource
    pagination: PaginationResult[Product]

    def __init__(
        self,
        *,
        source: CatalogSource,
        pagination: PaginationResult[Product],
    ) -> None:
        owned_source = _clone_source(source)
        if type(pagination) is not PaginationResult:
            raise TypeError("catalogue load pagination is invalid")
        owned = PaginationResult[Product](
            items=tuple(_clone_product(item) for item in pagination.items),
            page_count=pagination.page_count,
            complete=pagination.complete,
            truncated=pagination.truncated,
            terminal_after=pagination.terminal_after,
        )
        object.__setattr__(self, "source", owned_source)
        object.__setattr__(self, "pagination", owned)

    def __repr__(self) -> str:
        return (
            "CatalogProductLoad("
            f"source={self.source!r}, item_count={len(self.pagination.items)}, "
            f"page_count={self.pagination.page_count}, complete={self.pagination.complete})"
        )


def venue_allows_new_orders(product: Product) -> bool:
    """Return only the venue-state gate, not overall sizing/risk eligibility."""
    if not isinstance(product, Product):
        raise TypeError("venue state requires a Product")
    return (
        "only_reduce_only_orders_allowed" in product.model_fields_set
        and product.state is ProductState.LIVE
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
    _publication_evidence: bytes | None

    def __init__(
        self,
        *,
        source: CatalogSource,
        revision: int,
        fetched_at: datetime,
        validated_at: datetime,
        ttl: timedelta,
        products: Sequence[Product],
    ) -> None:
        owned_source = _clone_source(source)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision <= 0:
            raise ValueError("snapshot revision must be positive")
        if not isinstance(ttl, timedelta) or ttl <= timedelta(0):
            raise ValueError("snapshot TTL must be positive")
        fetched = _utc(fetched_at)
        validated = _utc(validated_at)
        if fetched > validated:
            raise ValueError("snapshot timestamps are inconsistent")
        try:
            fresh = validated + ttl
        except OverflowError:
            raise ValueError("snapshot freshness overflows UTC time") from None
        (
            copied_products,
            id_map,
            symbol_map,
            asset_map,
            content_sha256,
        ) = _validated_candidate(source=owned_source, products=products)
        object.__setattr__(self, "source", owned_source)
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "content_sha256", content_sha256)
        object.__setattr__(self, "fetched_at", fetched)
        object.__setattr__(self, "validated_at", validated)
        object.__setattr__(self, "fresh_until", fresh)
        object.__setattr__(self, "products", copied_products)
        object.__setattr__(self, "by_id", MappingProxyType(id_map))
        object.__setattr__(self, "by_symbol", MappingProxyType(symbol_map))
        object.__setattr__(self, "assets_by_id", MappingProxyType(asset_map))
        object.__setattr__(self, "_publication_evidence", None)

    def __repr__(self) -> str:
        return (
            "CatalogSnapshot("
            f"source={self.source!r}, revision={self.revision}, "
            f"product_count={len(self.products)})"
        )

    def is_fresh(self, at: datetime) -> bool:
        """Return whether this snapshot is fresh at an aware UTC instant."""
        checked_at = _utc(at)
        return self.validated_at <= checked_at <= self.fresh_until

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


def _build_catalog_publication_capability() -> tuple[
    Callable[[CatalogSnapshot], CatalogSnapshot],
    Callable[[object], bool],
]:
    signing_key = token_bytes(32)

    def digest(snapshot: CatalogSnapshot) -> bytes:
        payload = json.dumps(
            {
                "content_sha256": snapshot.content_sha256,
                "environment": snapshot.source.environment.value,
                "fetched_at": snapshot.fetched_at.isoformat(timespec="microseconds"),
                "fresh_until": snapshot.fresh_until.isoformat(timespec="microseconds"),
                "region": snapshot.source.region.value,
                "revision": snapshot.revision,
                "validated_at": snapshot.validated_at.isoformat(timespec="microseconds"),
            },
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return hmac.digest(signing_key, payload, "sha256")

    def issue(snapshot: CatalogSnapshot) -> CatalogSnapshot:
        if type(snapshot) is not CatalogSnapshot or snapshot._publication_evidence is not None:
            raise TypeError("catalogue publication candidate is invalid")
        object.__setattr__(snapshot, "_publication_evidence", digest(snapshot))
        return snapshot

    def verify(value: object) -> bool:
        if type(value) is not CatalogSnapshot:
            return False
        try:
            evidence = value._publication_evidence
            return (
                type(evidence) is bytes
                and len(evidence) == sha256().digest_size
                and type(value.source) is CatalogSource
                and hmac.compare_digest(evidence, digest(value))
            )
        except Exception:
            return False

    return issue, verify


_issue_catalog_snapshot_publication, _has_valid_catalog_snapshot_publication = (
    _build_catalog_publication_capability()
)


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


def _validated_candidate(
    *,
    source: CatalogSource,
    products: Sequence[Product],
) -> tuple[
    tuple[Product, ...],
    dict[int, Product],
    dict[str, Product],
    dict[int, Asset],
    str,
]:
    if type(source) is not CatalogSource:
        raise ValueError("catalogue source is invalid")
    supplied = tuple(products)
    if not supplied:
        raise ValueError("catalogue requires products")
    copied = tuple(_clone_product(product) for product in supplied)
    for product in copied:
        if "only_reduce_only_orders_allowed" not in product.model_fields_set:
            raise ValueError("catalogue product lacks reduce-only provenance")
    ordered = tuple(sorted(copied, key=lambda product: product.id))
    by_id: dict[int, Product] = {}
    by_symbol: dict[str, Product] = {}
    symbols_casefold: set[str] = set()
    assets_by_id: dict[int, Asset] = {}
    asset_symbol_ids: dict[str, int] = {}

    for product in ordered:
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
            if asset is None:
                continue
            known = assets_by_id.get(asset.id)
            if known is not None and known != asset:
                raise ValueError("conflicting asset metadata")
            folded_asset = asset.symbol.casefold()
            known_id = asset_symbol_ids.get(folded_asset)
            if known_id is not None and known_id != asset.id:
                raise ValueError("ambiguous asset symbol")
            assets_by_id[asset.id] = asset
            asset_symbol_ids[folded_asset] = asset.id

    return ordered, by_id, by_symbol, assets_by_id, _canonical_hash(source, ordered)


__all__ = [
    "CatalogProductLoad",
    "CatalogSnapshot",
    "CatalogSource",
    "venue_allows_new_orders",
]
