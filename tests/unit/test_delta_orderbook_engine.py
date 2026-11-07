"""Copy-on-write Delta full-depth order-book engine tests."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from hmt_crypto.market_data.contracts import (
    BookDeltaObservation,
    BookLevel,
    BookSnapshotObservation,
    MarketSource,
)


def _level(price: str, size: str) -> BookLevel:
    return BookLevel(
        price=Decimal(price),
        size=int(Decimal(size)),
        price_lexeme=price,
        size_lexeme=size,
    )


def _source(sequence: int, *, local_sequence: int | None = None) -> MarketSource:
    return MarketSource(
        region="india",
        environment="testnet",
        connection_generation=UUID("10000000-0000-0000-0000-000000000001"),
        local_sequence=sequence if local_sequence is None else local_sequence,
        catalogue_snapshot_id=UUID("20000000-0000-0000-0000-000000000002"),
        channel="ob_updates",
        symbol="XBTUSD_CAT",
        venue_sequence=sequence,
        observed_at=datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
    )


def _snapshot(
    *,
    sequence: int = 5,
    checksum: int = 772_313_654,
) -> BookSnapshotObservation:
    return BookSnapshotObservation(
        source=_source(sequence),
        product_id=1101,
        symbol="XBTUSD_CAT",
        asks=(_level("100.00", "2"), _level("101", "1")),
        bids=(_level("99", "3"), _level("98.00", "4")),
        checksum=checksum,
        checksum_lexeme=str(checksum),
    )


def _delta(*, sequence: int = 6, checksum: int = 863_104_686) -> BookDeltaObservation:
    return BookDeltaObservation(
        source=_source(sequence),
        product_id=1101,
        symbol="XBTUSD_CAT",
        asks=(_level("101", "5"),),
        bids=(_level("98.00", "0"), _level("97", "1")),
        checksum=checksum,
        checksum_lexeme=str(checksum),
    )


def test_first_valid_snapshot_is_checksum_verified_and_published_immutably() -> None:
    from hmt_crypto.market_data.orderbook import BookApplyStatus, DeltaOrderBookEngine

    result = DeltaOrderBookEngine().apply(_snapshot())
    assert result.status is BookApplyStatus.ACCEPTED
    assert result.required_action is None
    assert result.publication is not None
    assert result.publication.usable is True
    assert result.publication.sequence == 5
    assert tuple(level.price_lexeme for level in result.publication.asks) == ("100.00", "101")
    try:
        result.publication.usable = False
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("publication must be frozen")


def test_update_replaces_inserts_and_zero_deletes_before_crc_publication() -> None:
    from hmt_crypto.market_data.orderbook import BookApplyStatus, DeltaOrderBookEngine

    engine = DeltaOrderBookEngine()
    engine.apply(_snapshot())
    result = engine.apply(_delta())
    assert result.status is BookApplyStatus.ACCEPTED
    assert result.publication is not None
    assert result.publication.sequence == 6
    assert tuple((level.price, level.size) for level in result.publication.asks) == (
        (Decimal("100.00"), 2),
        (Decimal("101"), 5),
    )
    assert tuple((level.price, level.size) for level in result.publication.bids) == (
        (Decimal("99"), 3),
        (Decimal("97"), 1),
    )


def test_sequence_gap_invalidates_but_preserves_last_good_display_snapshot() -> None:
    from hmt_crypto.market_data.orderbook import (
        BookApplyStatus,
        BookRecoveryAction,
        DeltaOrderBookEngine,
    )

    engine = DeltaOrderBookEngine()
    accepted = engine.apply(_snapshot()).publication
    result = engine.apply(_delta(sequence=7))
    assert result.status is BookApplyStatus.INVALIDATED
    assert result.required_action is BookRecoveryAction.RESUBSCRIBE_SYMBOL_AFTER_5_SECONDS
    assert result.publication is not None
    assert result.publication.usable is False
    assert result.publication.sequence == accepted.sequence
    assert result.publication.asks == accepted.asks
    assert result.publication.bids == accepted.bids


def test_failed_update_checksum_rolls_back_candidate_and_duplicate_is_not_ignored() -> None:
    from hmt_crypto.market_data.orderbook import BookApplyStatus, DeltaOrderBookEngine

    engine = DeltaOrderBookEngine()
    before = engine.apply(_snapshot()).publication
    failed = engine.apply(_delta(checksum=1))
    assert failed.status is BookApplyStatus.INVALIDATED
    assert failed.publication is not None
    assert failed.publication.sequence == before.sequence
    assert failed.publication.asks == before.asks
    assert failed.publication.bids == before.bids

    duplicate_engine = DeltaOrderBookEngine()
    duplicate_engine.apply(_snapshot())
    duplicate = duplicate_engine.apply(_delta(sequence=5, checksum=1))
    assert duplicate.status is BookApplyStatus.INVALIDATED


def test_unsolicited_snapshot_cannot_rebase_a_healthy_head_but_repair_snapshot_can() -> None:
    from hmt_crypto.market_data.orderbook import (
        BookApplyStatus,
        BookInvalidationReason,
        DeltaOrderBookEngine,
    )

    healthy = DeltaOrderBookEngine()
    healthy.apply(_snapshot())
    assert healthy.apply(_snapshot(sequence=8)).status is BookApplyStatus.INVALIDATED

    repairing = DeltaOrderBookEngine()
    repairing.apply(_snapshot())
    repairing.invalidate(BookInvalidationReason.EXPLICIT_REPAIR)
    repaired = repairing.apply(_snapshot(sequence=8))
    assert repaired.status is BookApplyStatus.ACCEPTED
    assert repaired.publication is not None and repaired.publication.sequence == 8


def test_wrong_first_event_or_conflicting_identity_requires_symbol_repair() -> None:
    from hmt_crypto.market_data.orderbook import BookApplyStatus, DeltaOrderBookEngine

    assert DeltaOrderBookEngine().apply(_delta()).status is BookApplyStatus.INVALIDATED

    engine = DeltaOrderBookEngine()
    engine.apply(_snapshot())
    conflicting = dataclasses.replace(
        _delta(),
        source=dataclasses.replace(
            _source(6),
            connection_generation=UUID("30000000-0000-0000-0000-000000000003"),
        ),
    )
    assert engine.apply(conflicting).status is BookApplyStatus.INVALIDATED
