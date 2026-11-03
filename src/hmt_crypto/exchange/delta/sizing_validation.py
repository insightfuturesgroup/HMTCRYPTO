"""Fail-closed catalogue provenance and product validation for sizing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.domain import ContractType, NotionalType, Product, ProductState, TradingStatus
from hmt_crypto.errors import (
    ContractSizingError,
    ProductNotFoundError,
    StaleCatalogError,
    UnsupportedProductError,
)
from hmt_crypto.exchange.delta.catalog import CatalogSnapshot, CatalogSource
from hmt_crypto.exchange.delta.catalog_models import _has_valid_catalog_snapshot_publication
from hmt_crypto.exchange.delta.sizing_math import NumericContractError, positive_decimal

_REQUIRED_PRODUCT_FIELDS = frozenset(
    {
        "id",
        "symbol",
        "contract_type",
        "notional_type",
        "contract_value",
        "contract_unit_currency",
        "tick_size",
        "state",
        "trading_status",
        "underlying_asset",
        "is_quanto",
        "only_reduce_only_orders_allowed",
    }
)


def sizing_error(operation: str, product_id: int | None = None) -> ContractSizingError:
    """Build one safe sizing failure without retaining raw inputs."""
    context: dict[str, object] = {"operation": operation}
    if type(product_id) is int and product_id > 0:
        context["product_id"] = product_id
    return ContractSizingError(context=context)


def unsupported(operation: str, product_id: int | None = None) -> UnsupportedProductError:
    """Build one safe unsupported-product failure."""
    context: dict[str, object] = {"operation": operation}
    if type(product_id) is int and product_id > 0:
        context["product_id"] = product_id
    return UnsupportedProductError(context=context)


def utc_datetime(value: object) -> datetime:
    """Normalize one aware datetime to UTC without accepting strings or epochs."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError
    try:
        if value.utcoffset() is None:
            raise ValueError
        return value.astimezone(UTC)
    except Exception:
        raise ValueError from None


def clone_source(source: object) -> CatalogSource:
    """Clone an exact typed source identity."""
    if type(source) is not CatalogSource:
        raise ValueError
    if type(source.region) is not DeltaRegion or type(source.environment) is not DeltaEnvironment:
        raise ValueError
    return CatalogSource(region=source.region, environment=source.environment)


def clone_product(product: object) -> Product:
    """Revalidate and own a Product while preserving explicit-field provenance."""
    if type(product) is not Product:
        raise ValueError
    fields_set = frozenset(product.model_fields_set)
    if not fields_set <= Product.model_fields.keys():
        raise ValueError
    payload = product.model_dump(mode="python", include=set(fields_set), warnings="none")
    cloned = Product.model_validate(payload, strict=True)
    if frozenset(cloned.model_fields_set) != fields_set:
        raise ValueError
    return cloned


def validate_supported_product(product: Product, *, risk_increasing: bool) -> None:
    """Validate only the documented live linear vanilla derivatives subset."""
    fields_set = frozenset(product.model_fields_set)
    if not _REQUIRED_PRODUCT_FIELDS <= fields_set:
        raise unsupported("contract_sizer_resolve", product.id)
    # Discriminator checks intentionally precede decimal resource validation.
    if product.contract_type not in {
        ContractType.PERPETUAL_FUTURES,
        ContractType.FUTURES,
    }:
        raise unsupported("contract_sizer_resolve", product.id)
    if product.notional_type is not NotionalType.VANILLA or product.is_quanto is not False:
        raise unsupported("contract_sizer_resolve", product.id)
    if (
        product.state is not ProductState.LIVE
        or product.trading_status is not TradingStatus.OPERATIONAL
    ):
        raise unsupported("contract_sizer_resolve", product.id)
    if type(product.only_reduce_only_orders_allowed) is not bool:
        raise unsupported("contract_sizer_resolve", product.id)
    if risk_increasing and product.only_reduce_only_orders_allowed:
        raise unsupported("contract_sizer_resolve", product.id)
    if (
        type(product.contract_unit_currency) is not str
        or type(product.underlying_asset.symbol) is not str
        or product.contract_unit_currency != product.underlying_asset.symbol
    ):
        raise unsupported("contract_sizer_resolve", product.id)
    try:
        positive_decimal(product.contract_value)
        positive_decimal(product.tick_size)
    except NumericContractError:
        raise unsupported("contract_sizer_resolve", product.id) from None


@dataclass(frozen=True, slots=True, repr=False)
class ResolvedSizingProduct:
    """Internal owned proof produced only after snapshot/product validation."""

    product: Product
    source: CatalogSource
    catalog_revision: int
    catalog_content_sha256: str
    risk_increasing: bool
    _issuance_evidence: object | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )


def _build_resolved_sizing_capability() -> tuple[
    Callable[[ResolvedSizingProduct], ResolvedSizingProduct],
    Callable[[object], bool],
]:
    seal = object()

    def issue(value: ResolvedSizingProduct) -> ResolvedSizingProduct:
        if type(value) is not ResolvedSizingProduct or value._issuance_evidence is not None:
            raise TypeError
        object.__setattr__(value, "_issuance_evidence", seal)
        return value

    def verify(value: object) -> bool:
        return type(value) is ResolvedSizingProduct and value._issuance_evidence is seal

    return issue, verify


_issue_resolved_sizing_product, _has_valid_resolved_sizing_product = (
    _build_resolved_sizing_capability()
)


def resolve_sizing_product(
    *,
    snapshot: object,
    expected_source: CatalogSource,
    product_id: int,
    checked_at: datetime,
    risk_increasing: bool,
) -> ResolvedSizingProduct:
    """Verify a real snapshot and return independently owned sizing evidence."""
    if type(snapshot) is not CatalogSnapshot:
        raise sizing_error("contract_sizer_resolve")
    try:
        if not _has_valid_catalog_snapshot_publication(snapshot):
            raise unsupported("contract_sizer_snapshot_publication", product_id)
        source = clone_source(snapshot.source)
        revision = snapshot.revision
        content_hash = snapshot.content_sha256
        if type(revision) is not int or revision <= 0:
            raise ValueError
        if (
            type(content_hash) is not str
            or len(content_hash) != 64
            or any(character not in "0123456789abcdef" for character in content_hash)
        ):
            raise ValueError
        if (
            source.region is not expected_source.region
            or source.environment is not expected_source.environment
        ):
            raise unsupported("contract_sizer_source")
        if not snapshot.is_fresh(checked_at):
            raise StaleCatalogError(
                context={
                    "operation": "contract_sizer_resolve",
                    "region": source.region.value,
                    "environment": source.environment.value,
                }
            )
        owned_snapshot = CatalogSnapshot(
            source=source,
            revision=revision,
            fetched_at=snapshot.fetched_at,
            validated_at=snapshot.validated_at,
            ttl=snapshot.fresh_until - snapshot.validated_at,
            products=tuple(snapshot.products),
        )
        if owned_snapshot.content_sha256 != content_hash:
            raise ValueError
        product = owned_snapshot.require_product_id(product_id)
        validate_supported_product(product, risk_increasing=risk_increasing)
        owned_product = clone_product(product)
        return _issue_resolved_sizing_product(
            ResolvedSizingProduct(
                product=owned_product,
                source=source,
                catalog_revision=revision,
                catalog_content_sha256=content_hash,
                risk_increasing=risk_increasing,
            )
        )
    except (ProductNotFoundError, StaleCatalogError, UnsupportedProductError):
        raise
    except Exception:
        raise unsupported("contract_sizer_snapshot", product_id) from None


__all__ = [
    "clone_product",
    "clone_source",
    "resolve_sizing_product",
    "sizing_error",
    "unsupported",
    "utc_datetime",
    "validate_supported_product",
]
