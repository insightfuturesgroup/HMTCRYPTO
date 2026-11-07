"""Consumer-specific fail-closed market readiness tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from hmt_crypto.exchange.base import MarketComponent
from hmt_crypto.market_data.contracts import (
    BookLevel,
    BookSnapshotObservation,
    MarketSource,
    MarkObservation,
    ProductStatusObservation,
    SpotObservation,
    SystemStatusObservation,
    TickerObservation,
)

GENERATION = UUID("10000000-0000-0000-0000-000000000001")
CATALOGUE = UUID("20000000-0000-0000-0000-000000000002")
NEW_CATALOGUE = UUID("30000000-0000-0000-0000-000000000003")
OTHER_GENERATION = UUID("40000000-0000-0000-0000-000000000004")


class Clock:
    def __init__(self) -> None:
        self.value = 1_000_000_000

    def __call__(self) -> int:
        return self.value


def _source(
    channel: str,
    sequence: int,
    *,
    symbol: str | None = "XBTUSD_CAT",
    catalogue: UUID = CATALOGUE,
    generation: UUID = GENERATION,
    venue_sequence: int | None = None,
) -> MarketSource:
    return MarketSource(
        region="india",
        environment="testnet",
        connection_generation=generation,
        local_sequence=sequence,
        catalogue_snapshot_id=catalogue,
        channel=channel,
        symbol=symbol,
        venue_sequence=venue_sequence,
        observed_at=datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
    )


def _evidence(*, catalogue: UUID = CATALOGUE, fresh: bool = True, barrier: bool = True):
    from hmt_crypto.market_data.readiness import MarketReadinessEvidence

    return MarketReadinessEvidence(
        connection_generation=GENERATION,
        catalogue_snapshot_id=catalogue,
        catalogue_fresh=fresh,
        supervisor_barrier_current=barrier,
    )


def _requirements(
    *components: MarketComponent,
    max_age: timedelta | None = None,
    require_system_status: bool = False,
    require_product_status: bool = False,
    spot_asset_ids: tuple[int, ...] = (),
):
    from hmt_crypto.market_data.readiness import FreshnessRequirement, MarketRequirements

    return MarketRequirements(
        product_ids=(1101,) if components or require_product_status else (),
        components=tuple(
            FreshnessRequirement(component=component, max_age=max_age) for component in components
        ),
        require_system_status=require_system_status,
        require_product_status=require_product_status,
        spot_asset_ids=spot_asset_ids,
    )


def _ticker(sequence: int = 1) -> TickerObservation:
    return TickerObservation(
        source=_source("ticker", sequence),
        product_id=1101,
        symbol="XBTUSD_CAT",
        mark_price=Decimal("100"),
        close=Decimal("100"),
    )


def test_component_ages_are_independent_and_requirement_specific() -> None:
    from hmt_crypto.market_data.readiness import MarketFreshnessPolicy
    from hmt_crypto.market_data.state import MarketStateStore

    clock = Clock()
    policy = MarketFreshnessPolicy(
        ticker_max_age=timedelta(seconds=10),
        mark_max_age=timedelta(seconds=3),
    )
    store = MarketStateStore(freshness_policy=policy, monotonic_ns=clock)
    store.apply(_ticker())
    clock.value += 11_000_000_000
    store.apply(
        MarkObservation(
            source=_source("mark_price", 2),
            product_id=1101,
            symbol="XBTUSD_CAT",
            price=Decimal("101"),
        )
    )
    assert store.snapshot(_requirements(MarketComponent.MARK), evidence=_evidence()).usable
    ticker = store.snapshot(_requirements(MarketComponent.TICKER), evidence=_evidence())
    assert ticker.usable is False
    assert "ticker_stale" in ticker.reasons


def test_catalogue_and_supervisor_barrier_are_both_explicit_readiness_gates() -> None:
    from hmt_crypto.market_data.readiness import MarketFreshnessPolicy
    from hmt_crypto.market_data.state import MarketStateStore

    store = MarketStateStore(freshness_policy=MarketFreshnessPolicy(), monotonic_ns=Clock())
    store.apply(_ticker())
    requirements = _requirements(MarketComponent.TICKER)
    assert not store.snapshot(requirements, evidence=_evidence(fresh=False)).usable
    assert not store.snapshot(requirements, evidence=_evidence(barrier=False)).usable
    assert not store.snapshot(requirements, evidence=None).usable
    assert store.snapshot(requirements, evidence=_evidence()).usable


def test_full_book_can_remain_current_without_updates_but_heartbeat_cannot_refresh_ticker() -> None:
    from hmt_crypto.market_data.readiness import MarketFreshnessPolicy
    from hmt_crypto.market_data.state import MarketStateStore

    clock = Clock()
    store = MarketStateStore(freshness_policy=MarketFreshnessPolicy(), monotonic_ns=clock)
    level_ask = BookLevel(price=Decimal("100"), size=1, price_lexeme="100", size_lexeme="1")
    level_bid = BookLevel(price=Decimal("99"), size=1, price_lexeme="99", size_lexeme="1")
    store.apply(
        BookSnapshotObservation(
            source=_source("ob_updates", 1, venue_sequence=1),
            product_id=1101,
            symbol="XBTUSD_CAT",
            asks=(level_ask,),
            bids=(level_bid,),
            checksum=3_804_137_074,
            checksum_lexeme="3804137074",
        )
    )
    store.apply(_ticker(sequence=2))
    clock.value += 600_000_000_000
    assert store.snapshot(_requirements(MarketComponent.FULL_BOOK), evidence=_evidence()).usable
    assert not store.snapshot(_requirements(MarketComponent.TICKER), evidence=_evidence()).usable


def test_positive_system_event_cannot_clear_maintenance_without_new_catalogue_barrier() -> None:
    from hmt_crypto.market_data.readiness import MarketFreshnessPolicy
    from hmt_crypto.market_data.state import MarketStateStore

    clock = Clock()
    store = MarketStateStore(freshness_policy=MarketFreshnessPolicy(), monotonic_ns=clock)
    store.apply(
        SystemStatusObservation(
            source=_source("system_status", 1, symbol=None),
            event="maintenance_started",
            status="maintenance",
        )
    )
    store.apply(
        SystemStatusObservation(
            source=_source("system_status", 2, symbol=None),
            event="maintenance_finished",
            status="live",
        )
    )
    requirements = _requirements(require_system_status=True)
    blocked = store.snapshot(requirements, evidence=_evidence())
    assert blocked.usable is False
    assert "catalogue_refresh_required_after_system_disruption" in blocked.reasons

    store.apply(
        SystemStatusObservation(
            source=_source("system_status", 3, symbol=None, catalogue=NEW_CATALOGUE),
            event="snapshot",
            status="live",
        )
    )
    assert store.snapshot(requirements, evidence=_evidence(catalogue=NEW_CATALOGUE)).usable


def test_required_product_status_enforces_catalogue_generation_and_age() -> None:
    from hmt_crypto.market_data.readiness import MarketFreshnessPolicy
    from hmt_crypto.market_data.state import MarketStateStore

    clock = Clock()
    store = MarketStateStore(freshness_policy=MarketFreshnessPolicy(), monotonic_ns=clock)
    store.apply(
        ProductStatusObservation(
            source=_source("product_updates", 1, catalogue=NEW_CATALOGUE),
            product_id=1101,
            symbol="XBTUSD_CAT",
            event="finish_auction",
            trading_status="operational",
        )
    )
    requirements = _requirements(require_product_status=True)
    wrong_catalogue = store.snapshot(requirements, evidence=_evidence())
    assert "product_status_wrong_catalogue" in wrong_catalogue.reasons

    wrong_generation_store = MarketStateStore(
        freshness_policy=MarketFreshnessPolicy(), monotonic_ns=Clock()
    )
    wrong_generation_store.apply(
        ProductStatusObservation(
            source=_source("product_updates", 1, generation=OTHER_GENERATION),
            product_id=1101,
            symbol="XBTUSD_CAT",
            event="finish_auction",
            trading_status="operational",
        )
    )
    wrong_generation = wrong_generation_store.snapshot(requirements, evidence=_evidence())
    assert "product_status_wrong_generation" in wrong_generation.reasons

    current = MarketStateStore(freshness_policy=MarketFreshnessPolicy(), monotonic_ns=clock)
    current.apply(
        ProductStatusObservation(
            source=_source("product_updates", 1),
            product_id=1101,
            symbol="XBTUSD_CAT",
            event="finish_auction",
            trading_status="operational",
        )
    )
    clock.value += 61_000_000_000
    stale = current.snapshot(requirements, evidence=_evidence())
    assert "product_status_stale" in stale.reasons


def test_spot_readiness_uses_explicit_asset_ids_not_product_ids() -> None:
    from hmt_crypto.market_data.readiness import MarketFreshnessPolicy
    from hmt_crypto.market_data.state import MarketStateStore

    store = MarketStateStore(freshness_policy=MarketFreshnessPolicy(), monotonic_ns=Clock())
    store.apply(
        SpotObservation(
            source=_source("spot_price", 1, symbol=".DEUSDTUSD"),
            asset_id=12,
            index_symbol=".DEUSDTUSD",
            price=Decimal("1.01"),
        )
    )
    explicit = _requirements(spot_asset_ids=(12,))
    assert store.snapshot(explicit, evidence=_evidence()).usable

    missing = _requirements(spot_asset_ids=(13,))
    snapshot = store.snapshot(missing, evidence=_evidence())
    assert not snapshot.usable
    assert "spot_missing" in snapshot.reasons
