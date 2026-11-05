"""Single-writer immutable market-state tests."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from hmt_crypto.exchange.base import MarketComponent
from hmt_crypto.market_data.contracts import MarketSource, MarkObservation, TickerObservation

NEW_GENERATION = UUID("30000000-0000-0000-0000-000000000003")

GENERATION = UUID("10000000-0000-0000-0000-000000000001")
CATALOGUE = UUID("20000000-0000-0000-0000-000000000002")


class Clock:
    def __init__(self) -> None:
        self.value = 1_000_000_000

    def __call__(self) -> int:
        return self.value


def _source(
    channel: str,
    sequence: int,
    *,
    generation: UUID = GENERATION,
    catalogue: UUID = CATALOGUE,
    venue_sequence: int | None = None,
) -> MarketSource:
    return MarketSource(
        region="india",
        environment="testnet",
        connection_generation=generation,
        local_sequence=sequence,
        catalogue_snapshot_id=catalogue,
        channel=channel,
        symbol="XBTUSD_CAT",
        venue_sequence=venue_sequence,
        observed_at=datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
    )


def _ticker(sequence: int = 1, price: str = "100") -> TickerObservation:
    return TickerObservation(
        source=_source("ticker", sequence),
        product_id=1101,
        symbol="XBTUSD_CAT",
        mark_price=Decimal(price),
        close=Decimal(price),
    )


def _requirements(*components: MarketComponent):
    from hmt_crypto.market_data.readiness import FreshnessRequirement, MarketRequirements

    return MarketRequirements(
        product_ids=(1101,),
        components=tuple(FreshnessRequirement(component=component) for component in components),
    )


def _evidence(*, generation: UUID = GENERATION, catalogue: UUID = CATALOGUE):
    from hmt_crypto.market_data.readiness import MarketReadinessEvidence

    return MarketReadinessEvidence(
        connection_generation=generation,
        catalogue_snapshot_id=catalogue,
        catalogue_fresh=True,
        supervisor_barrier_current=True,
    )


def test_exact_duplicate_returns_same_publication_without_revision_increment() -> None:
    from hmt_crypto.market_data.readiness import MarketFreshnessPolicy
    from hmt_crypto.market_data.state import MarketStateStore

    store = MarketStateStore(freshness_policy=MarketFreshnessPolicy(), monotonic_ns=Clock())
    first = store.apply(_ticker())
    duplicate = store.apply(_ticker())
    assert duplicate is first
    assert duplicate.revision == 1
    assert duplicate.duplicate is False


def test_conflicting_duplicate_invalidates_only_the_affected_component() -> None:
    from hmt_crypto.market_data.readiness import MarketFreshnessPolicy
    from hmt_crypto.market_data.state import MarketStateStore

    clock = Clock()
    store = MarketStateStore(freshness_policy=MarketFreshnessPolicy(), monotonic_ns=clock)
    store.apply(_ticker())
    mark = MarkObservation(
        source=_source("mark_price", 2),
        product_id=1101,
        symbol="XBTUSD_CAT",
        price=Decimal("101"),
    )
    store.apply(mark)
    conflict = dataclasses.replace(_ticker(price="102"), source=_source("ticker", 1))
    publication = store.apply(conflict)
    repeated = store.apply(conflict)
    assert publication.conflict is True
    assert repeated is publication

    mark_only = store.snapshot(_requirements(MarketComponent.MARK), evidence=_evidence())
    ticker_only = store.snapshot(_requirements(MarketComponent.TICKER), evidence=_evidence())
    assert mark_only.usable is True
    assert ticker_only.usable is False
    assert "ticker_conflicting_source" in ticker_only.reasons


def test_dedup_ledger_is_bounded_without_reapplying_evicted_source_identities() -> None:
    from hmt_crypto.market_data.readiness import MarketFreshnessPolicy
    from hmt_crypto.market_data.state import MarketStateStore

    store = MarketStateStore(freshness_policy=MarketFreshnessPolicy(), monotonic_ns=Clock())
    first = store.apply(_ticker(sequence=1))
    for sequence in range(2, 4_200):
        store.apply(_ticker(sequence=sequence))
    replay = store.apply(_ticker(sequence=1))

    assert store.dedup_entry_count <= 4_096
    assert replay.applied is False
    assert replay.revision >= first.revision


def test_generation_invalidation_recreates_book_engine_for_next_snapshot() -> None:
    from hmt_crypto.market_data.contracts import BookLevel, BookSnapshotObservation
    from hmt_crypto.market_data.readiness import MarketFreshnessPolicy
    from hmt_crypto.market_data.state import MarketStateStore

    ask = BookLevel(price=Decimal("100"), size=1, price_lexeme="100", size_lexeme="1")
    bid = BookLevel(price=Decimal("99"), size=1, price_lexeme="99", size_lexeme="1")

    def snapshot(generation: UUID) -> BookSnapshotObservation:
        return BookSnapshotObservation(
            source=_source(
                "ob_updates",
                1,
                generation=generation,
                venue_sequence=1,
            ),
            product_id=1101,
            symbol="XBTUSD_CAT",
            asks=(ask,),
            bids=(bid,),
            checksum=3_804_137_074,
            checksum_lexeme="3804137074",
        )

    store = MarketStateStore(freshness_policy=MarketFreshnessPolicy(), monotonic_ns=Clock())
    assert store.apply(snapshot(GENERATION)).applied
    store.invalidate_generation(GENERATION, "disconnect")
    display = store.snapshot(
        _requirements(MarketComponent.FULL_BOOK),
        evidence=_evidence(generation=GENERATION),
    )
    assert not display.usable
    assert display.products[0].full_book is not None
    assert not display.products[0].full_book.usable
    assert store.apply(snapshot(NEW_GENERATION)).applied
    fresh = store.snapshot(
        _requirements(MarketComponent.FULL_BOOK),
        evidence=_evidence(generation=NEW_GENERATION),
    )
    assert fresh.usable
    assert fresh.products[0].full_book is not None
    assert fresh.products[0].full_book.source.connection_generation == NEW_GENERATION


def test_conflicting_full_book_source_identity_is_terminal_and_requests_repair() -> None:
    from hmt_crypto.market_data.contracts import BookLevel, BookSnapshotObservation
    from hmt_crypto.market_data.orderbook import BookRecoveryAction
    from hmt_crypto.market_data.readiness import MarketFreshnessPolicy
    from hmt_crypto.market_data.state import MarketStateStore

    ask = BookLevel(price=Decimal("100"), size=1, price_lexeme="100", size_lexeme="1")
    bid = BookLevel(price=Decimal("99"), size=1, price_lexeme="99", size_lexeme="1")
    snapshot = BookSnapshotObservation(
        source=_source("ob_updates", 1, venue_sequence=1),
        product_id=1101,
        symbol="XBTUSD_CAT",
        asks=(ask,),
        bids=(bid,),
        checksum=3_804_137_074,
        checksum_lexeme="3804137074",
    )
    conflicting = dataclasses.replace(snapshot, checksum=1, checksum_lexeme="1")
    store = MarketStateStore(freshness_policy=MarketFreshnessPolicy(), monotonic_ns=Clock())
    store.apply(snapshot)
    first = store.apply(conflicting)
    repeated = store.apply(conflicting)

    assert first.book_recovery_action is BookRecoveryAction.RESUBSCRIBE_SYMBOL_AFTER_5_SECONDS
    assert repeated is first
    view = store.snapshot(_requirements(MarketComponent.FULL_BOOK), evidence=_evidence())
    assert not view.usable
    assert "full_book_conflicting_source" in view.reasons


def test_generation_invalidation_preserves_last_good_display_values() -> None:
    from hmt_crypto.market_data.readiness import MarketFreshnessPolicy
    from hmt_crypto.market_data.state import MarketStateStore

    store = MarketStateStore(freshness_policy=MarketFreshnessPolicy(), monotonic_ns=Clock())
    store.apply(_ticker())
    store.invalidate_generation(GENERATION, "disconnect")
    snapshot = store.snapshot(_requirements(MarketComponent.TICKER), evidence=_evidence())
    assert snapshot.usable is False
    assert snapshot.products[0].ticker == _ticker()
    assert "generation_invalidated" in snapshot.reasons


def test_monotonic_clock_regression_poisons_state_instead_of_making_data_younger() -> None:
    from hmt_crypto.market_data.readiness import MarketFreshnessPolicy
    from hmt_crypto.market_data.state import MarketStateStore

    clock = Clock()
    store = MarketStateStore(freshness_policy=MarketFreshnessPolicy(), monotonic_ns=clock)
    store.apply(_ticker())
    clock.value -= 1
    store.apply(
        MarkObservation(
            source=_source("mark_price", 2),
            product_id=1101,
            symbol="XBTUSD_CAT",
            price=Decimal("101"),
        )
    )
    snapshot = store.snapshot(_requirements(MarketComponent.TICKER), evidence=_evidence())
    assert snapshot.usable is False
    assert "monotonic_clock_regressed" in snapshot.reasons
