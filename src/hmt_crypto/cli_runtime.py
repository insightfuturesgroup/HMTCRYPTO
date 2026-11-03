"""Read-only Phase 1 CLI composition over typed public exchange services."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hmt_crypto.clock import (
    LocalClockDiagnostics,
    SignedProbeEvidence,
    SignedProbeOutcome,
    assess_clock_readiness,
)
from hmt_crypto.config import Settings
from hmt_crypto.domain import Asset, Candle, Product, Ticker, parse_utc_datetime
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.base import CandleRequest, CandleResolution, ProductRef
from hmt_crypto.exchange.delta.catalog import CatalogProductLoad, CatalogSource, ProductCatalog
from hmt_crypto.exchange.delta.client import DeltaPublicClient
from hmt_crypto.exchange.delta.endpoints import get_delta_endpoints
from hmt_crypto.exchange.delta.rate_limiter import (
    RateLimitScope,
    ScopeKind,
    WeightedRateLimiter,
)
from hmt_crypto.persistence.redaction import JsonValue

_USER_AGENT = "hmt-crypto/0.1.0"
_CATALOG_TTL = timedelta(minutes=15)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _asset_payload(asset: Asset) -> dict[str, JsonValue]:
    return {
        "id": asset.id,
        "symbol": asset.symbol,
        "precision": asset.precision,
        "minimum_precision": asset.minimum_precision,
    }


def _product_payload(product: Product) -> dict[str, JsonValue]:
    return {
        "id": product.id,
        "symbol": product.symbol,
        "contract_type": product.contract_type.value,
        "notional_type": product.notional_type.value,
        "contract_value": str(product.contract_value),
        "contract_unit_currency": product.contract_unit_currency,
        "tick_size": str(product.tick_size),
        "state": product.state.value,
        "trading_status": product.trading_status.value,
        "underlying_asset": _asset_payload(product.underlying_asset),
        "quoting_asset": _asset_payload(product.quoting_asset),
        "settling_asset": (
            None if product.settling_asset is None else _asset_payload(product.settling_asset)
        ),
        "is_quanto": product.is_quanto,
        "only_reduce_only_orders_allowed": product.only_reduce_only_orders_allowed,
        "position_size_limit": _decimal(product.position_size_limit),
        "position_notional_limit": _decimal(product.position_notional_limit),
        "initial_margin": _decimal(product.initial_margin),
        "maintenance_margin": _decimal(product.maintenance_margin),
        "default_leverage": _decimal(product.default_leverage),
        "max_leverage_notional": _decimal(product.max_leverage_notional),
    }


def _ticker_payload(ticker: Ticker) -> dict[str, JsonValue]:
    return {
        "product_id": ticker.product_id,
        "symbol": ticker.symbol,
        "timestamp": _iso(ticker.timestamp),
        "contract_type": ticker.contract_type.value,
        "product_trading_status": ticker.product_trading_status.value,
        "mark_price": _decimal(ticker.mark_price),
        "spot_price": _decimal(ticker.spot_price),
        "close": _decimal(ticker.close),
        "best_bid": _decimal(ticker.best_bid),
        "best_ask": _decimal(ticker.best_ask),
        "lower_price_limit": _decimal(ticker.lower_price_limit),
        "upper_price_limit": _decimal(ticker.upper_price_limit),
    }


def _candle_payload(candle: Candle) -> dict[str, JsonValue]:
    return {
        "timestamp": _iso(candle.timestamp),
        "open": str(candle.open),
        "high": str(candle.high),
        "low": str(candle.low),
        "close": str(candle.close),
        "volume": str(candle.volume),
    }


def load_settings() -> Settings:
    """Load the typed environment/dotenv settings at command invocation time."""
    return Settings()


def config_check_payload(settings: Settings) -> dict[str, JsonValue]:
    """Return allowlisted local configuration diagnostics without I/O."""
    diagnostics = settings.safe_diagnostics()
    return {
        "execution_mode": settings.execution_mode.value,
        "region": settings.delta_region.value,
        "environment": settings.delta_env.value,
        "rest_base_url": settings.rest_base_url,
        "credential_slot": diagnostics["credential_slot"],
        "api_key_fingerprint": diagnostics["api_key_fingerprint"],
        "order_write_capable": settings.order_write_capable,
    }


def clock_check_payload(*, now: datetime | None = None) -> dict[str, JsonValue]:
    """Report absent Phase 1 clock/probe evidence without pretending to inspect NTP."""
    checked = (now or datetime.now(UTC)).astimezone(UTC)
    local = LocalClockDiagnostics(
        synchronized=None,
        offset_seconds=None,
        observed_at=checked,
        source="phase1_not_observed",
    )
    probe = SignedProbeEvidence(
        outcome=SignedProbeOutcome.NOT_ATTEMPTED,
        observed_at=checked,
    )
    readiness = assess_clock_readiness(
        local=local,
        signed_probe_evidence=probe,
        max_skew_seconds="4",
        max_evidence_age_seconds="30",
        checked_at=checked,
    )
    return {
        "checked_at": _iso(readiness.checked_at),
        "local_clock_synchronized": readiness.local_clock_diagnostics.synchronized,
        "local_clock_source": readiness.local_clock_diagnostics.source,
        "effective_skew_seconds": _decimal(readiness.effective_skew_seconds),
        "signed_probe_outcome": readiness.signed_probe_outcome.value,
        "clock_ready": readiness.clock_ready,
        "signed_probe_ready": readiness.signed_probe_ready,
        "order_write_prerequisites_met": readiness.order_write_prerequisites_met,
        "time_endpoint_documented": readiness.time_endpoint_documented,
        "reasons": [reason.value for reason in readiness.reasons],
    }


def _build_public_client(settings: Settings) -> DeltaPublicClient:
    endpoints = get_delta_endpoints(
        settings.delta_region,
        settings.delta_env,
        development_rest_base_url=settings.delta_development_rest_base_url,
        allow_development_override=settings.delta_allow_development_endpoint_override,
        live_requested=settings.execution_mode.value == "LIVE",
    )
    limiter = WeightedRateLimiter(
        RateLimitScope(
            region=settings.delta_region.value,
            environment=settings.delta_env.value,
            kind=ScopeKind.IP,
            external_id="hmt-crypto-cli-public",
        )
    )
    return DeltaPublicClient(
        endpoints=endpoints,
        limiter=limiter,
        user_agent=_USER_AGENT,
    )


def _source(settings: Settings) -> CatalogSource:
    endpoints = get_delta_endpoints(
        settings.delta_region,
        settings.delta_env,
        development_rest_base_url=settings.delta_development_rest_base_url,
        allow_development_override=settings.delta_allow_development_endpoint_override,
        live_requested=settings.execution_mode.value == "LIVE",
    )
    if endpoints.is_development_override:
        raise ConfigurationError(context={"operation": "catalog_development_override"})
    return CatalogSource(region=settings.delta_region, environment=settings.delta_env)


async def _refresh_catalog(
    settings: Settings,
    client: DeltaPublicClient,
) -> tuple[ProductCatalog, dict[str, JsonValue]]:
    source = _source(settings)

    async def fetch_products() -> CatalogProductLoad:
        return CatalogProductLoad(source=source, pagination=await client.get_products())

    catalog = ProductCatalog(
        source=source,
        fetch_products=fetch_products,
        ttl=_CATALOG_TTL,
        clock=lambda: datetime.now(UTC),
    )
    result = await catalog.refresh()
    return catalog, {
        "source": {"region": source.region.value, "environment": source.environment.value},
        "status": result.status.value,
        "attempted_at": _iso(result.attempted_at),
        "revision": result.active_revision,
        "content_sha256": result.active_content_sha256,
        "product_count": result.product_count,
        "safe_error_code": result.safe_error_code,
        "persistence": "process_local_only",
    }


async def products_sync_payload(settings: Settings) -> dict[str, JsonValue]:
    """Fetch and publish a complete process-local catalogue snapshot."""
    _source(settings)
    async with _build_public_client(settings) as client:
        catalog, evidence = await _refresh_catalog(settings, client)
    catalog.require_snapshot(at=datetime.now(UTC))
    return evidence


def _selector(product_id: str | None, symbol: str | None) -> int | str:
    if (product_id is None) == (symbol is None):
        raise ValueError("exactly one product selector is required")
    if product_id is not None:
        if not product_id.isascii() or not product_id.isdecimal():
            raise ValueError("product ID must be a positive integer")
        parsed = int(product_id, 10)
        if parsed <= 0:
            raise ValueError("product ID must be a positive integer")
        return parsed
    if symbol is None:
        raise ValueError("product symbol is required")
    return ProductRef(symbol=symbol).symbol


async def product_show_payload(
    settings: Settings,
    *,
    product_id: str | None,
    symbol: str | None,
) -> dict[str, JsonValue]:
    """Refresh then select one product from a complete catalogue."""
    selected = _selector(product_id, symbol)
    _source(settings)
    async with _build_public_client(settings) as client:
        catalog, evidence = await _refresh_catalog(settings, client)
    snapshot = catalog.require_snapshot(at=datetime.now(UTC))
    product = (
        snapshot.require_product_id(selected)
        if isinstance(selected, int)
        else snapshot.require_symbol(selected)
    )
    payload = _product_payload(product)
    payload["catalogue"] = evidence
    return payload


async def ticker_payload(settings: Settings, *, symbol: str | None) -> dict[str, JsonValue]:
    """Load one normalized public ticker."""
    if symbol is None:
        raise ValueError("ticker symbol is required")
    product = ProductRef(symbol=symbol)
    async with _build_public_client(settings) as client:
        ticker = await client.get_ticker(product)
    return _ticker_payload(ticker)


async def candles_payload(
    settings: Settings,
    *,
    symbol: str | None,
    resolution: str | None,
    start: str | None,
    end: str | None,
) -> dict[str, JsonValue]:
    """Load one strict UTC candle interval from public REST."""
    if resolution is None or start is None or end is None:
        raise ValueError("candle symbol, resolution, start, and end are required")
    if symbol is None:
        raise ValueError("candle symbol is required")
    request = CandleRequest(
        product=ProductRef(symbol=symbol),
        resolution=CandleResolution(resolution),
        start=parse_utc_datetime(start),
        end=parse_utc_datetime(end),
    )
    async with _build_public_client(settings) as client:
        result = await client.get_candles(request)
    return {
        "symbol": request.product.symbol,
        "resolution": request.resolution.value,
        "start": _iso(request.start),
        "end": _iso(request.end),
        "possibly_truncated": result.possibly_truncated,
        "items": [_candle_payload(candle) for candle in result.items],
    }


__all__ = [
    "candles_payload",
    "clock_check_payload",
    "config_check_payload",
    "load_settings",
    "product_show_payload",
    "products_sync_payload",
    "ticker_payload",
]
