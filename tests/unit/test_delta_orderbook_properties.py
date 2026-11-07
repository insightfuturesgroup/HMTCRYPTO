"""Property checks for Delta order-book copy-on-write equivalence."""

from __future__ import annotations

import zlib
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from hypothesis import given
from hypothesis import strategies as st

from hmt_crypto.market_data.contracts import (
    BookDeltaObservation,
    BookLevel,
    BookSnapshotObservation,
    MarketSource,
)


def _source(sequence: int) -> MarketSource:
    return MarketSource(
        region="india",
        environment="testnet",
        connection_generation=UUID("10000000-0000-0000-0000-000000000001"),
        local_sequence=sequence,
        catalogue_snapshot_id=UUID("20000000-0000-0000-0000-000000000002"),
        channel="ob_updates",
        symbol="XBTUSD_CAT",
        venue_sequence=sequence,
        observed_at=datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
    )


def _level(price: str, size: int) -> BookLevel:
    return BookLevel(
        price=Decimal(price),
        size=size,
        price_lexeme=price,
        size_lexeme=str(size),
    )


def _checksum(asks: dict[Decimal, BookLevel], bids: dict[Decimal, BookLevel]) -> int:
    ask_text = ",".join(
        f"{level.price_lexeme}:{level.size_lexeme}" for _, level in sorted(asks.items())[:10]
    )
    bid_text = ",".join(
        f"{level.price_lexeme}:{level.size_lexeme}"
        for _, level in sorted(bids.items(), reverse=True)[:10]
    )
    return zlib.crc32(f"{ask_text}|{bid_text}".encode("ascii")) & 0xFFFFFFFF


@given(index=st.integers(min_value=0, max_value=11), size=st.integers(min_value=0, max_value=20))
def test_random_valid_update_matches_independent_reference_map(index: int, size: int) -> None:
    from hmt_crypto.market_data.orderbook import BookApplyStatus, DeltaOrderBookEngine

    asks = {
        _level(f"{101 + offset}.00", 1).price: _level(f"{101 + offset}.00", 1)
        for offset in range(12)
    }
    bids = {_level(str(99 - offset), 1).price: _level(str(99 - offset), 1) for offset in range(12)}
    snapshot_checksum = _checksum(asks, bids)
    snapshot = BookSnapshotObservation(
        source=_source(1),
        product_id=1101,
        symbol="XBTUSD_CAT",
        asks=tuple(level for _, level in sorted(asks.items())),
        bids=tuple(level for _, level in sorted(bids.items(), reverse=True)),
        checksum=snapshot_checksum,
        checksum_lexeme=str(snapshot_checksum),
    )
    engine = DeltaOrderBookEngine()
    assert engine.apply(snapshot).status is BookApplyStatus.ACCEPTED

    changed = _level(f"{101 + index}.00", size)
    expected_asks = dict(asks)
    if size == 0:
        del expected_asks[changed.price]
    else:
        expected_asks[changed.price] = changed
    update_checksum = _checksum(expected_asks, bids)
    update = BookDeltaObservation(
        source=_source(2),
        product_id=1101,
        symbol="XBTUSD_CAT",
        asks=(changed,),
        bids=(),
        checksum=update_checksum,
        checksum_lexeme=str(update_checksum),
    )
    result = engine.apply(update)
    assert result.status is BookApplyStatus.ACCEPTED
    assert result.publication is not None
    assert result.publication.asks == tuple(level for _, level in sorted(expected_asks.items()))


@given(bad_checksum=st.integers(min_value=0, max_value=0xFFFFFFFF))
def test_bad_snapshot_checksum_never_publishes_partial_state(bad_checksum: int) -> None:
    from hmt_crypto.market_data.orderbook import BookApplyStatus, DeltaOrderBookEngine

    asks = {Decimal("100.00"): _level("100.00", 2)}
    good = _checksum(asks, {})
    if bad_checksum == good:
        return
    snapshot = BookSnapshotObservation(
        source=_source(1),
        product_id=1101,
        symbol="XBTUSD_CAT",
        asks=tuple(asks.values()),
        bids=(),
        checksum=bad_checksum,
        checksum_lexeme=str(bad_checksum),
    )
    result = DeltaOrderBookEngine().apply(snapshot)
    assert result.status is BookApplyStatus.INVALIDATED
    assert result.publication is None
