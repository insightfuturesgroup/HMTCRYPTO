"""Consumer-specific market freshness policy and external readiness evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from hmt_crypto.exchange.base import MarketComponent


def _positive_duration(value: object, *, optional: bool = False) -> timedelta | None:
    if optional and value is None:
        return None
    if type(value) is not timedelta or value <= timedelta(0):
        raise ValueError("market freshness duration must be positive")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketFreshnessPolicy:
    """Documented local defaults; full books rely on sequence plus barrier state."""

    ticker_max_age: timedelta = timedelta(seconds=10)
    l1_max_age: timedelta = timedelta(seconds=10)
    l2_max_age: timedelta = timedelta(seconds=15)
    full_book_max_age: timedelta | None = None
    trades_max_age: timedelta = timedelta(seconds=10)
    mark_max_age: timedelta = timedelta(seconds=5)
    candle_max_age: timedelta = timedelta(minutes=2)
    spot_max_age: timedelta = timedelta(seconds=5)
    funding_max_age: timedelta = timedelta(minutes=10)
    product_status_max_age: timedelta = timedelta(minutes=1)
    system_status_max_age: timedelta = timedelta(minutes=1)

    def __post_init__(self) -> None:
        for name in (
            "ticker_max_age",
            "l1_max_age",
            "l2_max_age",
            "trades_max_age",
            "mark_max_age",
            "candle_max_age",
            "spot_max_age",
            "funding_max_age",
            "product_status_max_age",
            "system_status_max_age",
        ):
            _positive_duration(getattr(self, name))
        _positive_duration(self.full_book_max_age, optional=True)

    def max_age(self, component: MarketComponent) -> timedelta | None:
        """Return the configured default for one exact component."""
        if type(component) is not MarketComponent:
            raise TypeError("market component is invalid")
        return {
            MarketComponent.TICKER: self.ticker_max_age,
            MarketComponent.L1: self.l1_max_age,
            MarketComponent.L2: self.l2_max_age,
            MarketComponent.FULL_BOOK: self.full_book_max_age,
            MarketComponent.TRADES: self.trades_max_age,
            MarketComponent.MARK: self.mark_max_age,
            MarketComponent.CANDLE: self.candle_max_age,
            MarketComponent.SPOT: self.spot_max_age,
            MarketComponent.FUNDING: self.funding_max_age,
            MarketComponent.PRODUCT_STATUS: self.product_status_max_age,
            MarketComponent.SYSTEM_STATUS: self.system_status_max_age,
        }[component]


@dataclass(frozen=True, slots=True, kw_only=True)
class FreshnessRequirement:
    """One consumer-required component and optional stricter maximum age."""

    component: MarketComponent
    max_age: timedelta | None = None

    def __post_init__(self) -> None:
        if type(self.component) is not MarketComponent:
            raise TypeError("market requirement component is invalid")
        if self.max_age is not None:
            _positive_duration(self.max_age)


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketRequirements:
    """Exact product/component subset needed by one consumer."""

    product_ids: tuple[int, ...]
    components: tuple[FreshnessRequirement, ...]
    spot_asset_ids: tuple[int, ...] = ()
    require_system_status: bool = False
    require_product_status: bool = False

    def __post_init__(self) -> None:
        if type(self.product_ids) is not tuple or any(
            type(product_id) is not int or product_id <= 0 for product_id in self.product_ids
        ):
            raise ValueError("market requirement products are invalid")
        if len(set(self.product_ids)) != len(self.product_ids):
            raise ValueError("market requirement products must be unique")
        if type(self.spot_asset_ids) is not tuple or any(
            type(asset_id) is not int or asset_id <= 0 for asset_id in self.spot_asset_ids
        ):
            raise ValueError("market requirement spot assets are invalid")
        if len(set(self.spot_asset_ids)) != len(self.spot_asset_ids):
            raise ValueError("market requirement spot assets must be unique")
        if type(self.components) is not tuple or any(
            type(requirement) is not FreshnessRequirement for requirement in self.components
        ):
            raise ValueError("market requirements are invalid")
        component_names = tuple(requirement.component for requirement in self.components)
        if len(set(component_names)) != len(component_names):
            raise ValueError("market requirement components must be unique")
        if self.components and not self.product_ids:
            raise ValueError("component requirements need product identities")
        if MarketComponent.SPOT in component_names:
            raise ValueError("spot requirements need explicit asset identities")
        if (
            type(self.require_system_status) is not bool
            or type(self.require_product_status) is not bool
        ):
            raise TypeError("market readiness flags are invalid")


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketReadinessEvidence:
    """Supervisor-owned catalogue and reconciliation barrier evidence."""

    connection_generation: UUID
    catalogue_snapshot_id: UUID
    catalogue_fresh: bool
    supervisor_barrier_current: bool

    def __post_init__(self) -> None:
        if (
            type(self.connection_generation) is not UUID
            or type(self.catalogue_snapshot_id) is not UUID
        ):
            raise TypeError("market readiness identities are invalid")
        if (
            type(self.catalogue_fresh) is not bool
            or type(self.supervisor_barrier_current) is not bool
        ):
            raise TypeError("market readiness gates are invalid")


def duration_ns(value: timedelta | None) -> int | None:
    """Convert an exact duration to integer nanoseconds."""
    if value is None:
        return None
    return (value.days * 86_400 + value.seconds) * 1_000_000_000 + value.microseconds * 1_000


__all__ = [
    "FreshnessRequirement",
    "MarketFreshnessPolicy",
    "MarketReadinessEvidence",
    "MarketRequirements",
]
