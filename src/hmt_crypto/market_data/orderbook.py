"""Atomic sequenced Delta full-depth order-book reconstruction."""

from __future__ import annotations

import zlib
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

from hmt_crypto.market_data.contracts import (
    BookDeltaObservation,
    BookLevel,
    BookSnapshotObservation,
    MarketSource,
)


class BookApplyStatus(StrEnum):
    """Outcome of applying one full-depth observation."""

    ACCEPTED = "accepted"
    INVALIDATED = "invalidated"


class BookRecoveryAction(StrEnum):
    """Bounded actor action required after book invalidation."""

    RESUBSCRIBE_SYMBOL_AFTER_5_SECONDS = "resubscribe_symbol_after_5_seconds"


class BookInvalidationReason(StrEnum):
    """Closed local reasons a reconstructed book becomes unusable."""

    FIRST_EVENT_NOT_SNAPSHOT = "first_event_not_snapshot"
    SEQUENCE = "sequence"
    CHECKSUM = "checksum"
    IDENTITY = "identity"
    UNSOLICITED_SNAPSHOT = "unsolicited_snapshot"
    VENUE_ERROR = "venue_error"
    GENERATION_INVALIDATED = "generation_invalidated"
    EXPLICIT_REPAIR = "explicit_repair"


@dataclass(frozen=True, slots=True)
class CompleteOrderBook:
    """One immutable full-depth publication, possibly display-only."""

    source: MarketSource
    product_id: int
    symbol: str
    sequence: int
    asks: tuple[BookLevel, ...]
    bids: tuple[BookLevel, ...]
    checksum: int
    usable: bool
    invalidation_reason: BookInvalidationReason | None = None

    def __post_init__(self) -> None:
        if self.usable != (self.invalidation_reason is None):
            raise ValueError("book usability evidence is inconsistent")


@dataclass(frozen=True, slots=True)
class BookApplyResult:
    """Publication plus any required symbol-scoped repair action."""

    status: BookApplyStatus
    publication: CompleteOrderBook | None
    required_action: BookRecoveryAction | None

    def __post_init__(self) -> None:
        if self.status is BookApplyStatus.ACCEPTED:
            if self.publication is None or not self.publication.usable:
                raise ValueError("accepted book result requires a usable publication")
            if self.required_action is not None:
                raise ValueError("accepted book result cannot request recovery")
        elif self.status is BookApplyStatus.INVALIDATED:
            if self.publication is not None and self.publication.usable:
                raise ValueError("invalid book result cannot publish usable state")
            if self.required_action is not BookRecoveryAction.RESUBSCRIBE_SYMBOL_AFTER_5_SECONDS:
                raise ValueError("invalid book result requires symbol recovery")
        else:
            raise ValueError("unknown book apply status")


_BookObservation = BookSnapshotObservation | BookDeltaObservation
_Identity = tuple[str, str, object, object, int, str]


def _identity(observation: _BookObservation) -> _Identity:
    source = observation.source
    return (
        source.region,
        source.environment,
        source.connection_generation,
        source.catalogue_snapshot_id,
        observation.product_id,
        observation.symbol,
    )


def _checksum(
    asks: dict[Decimal, BookLevel],
    bids: dict[Decimal, BookLevel],
) -> int:
    ask_text = ",".join(
        f"{level.price_lexeme}:{level.size_lexeme}" for _, level in sorted(asks.items())[:10]
    )
    bid_text = ",".join(
        f"{level.price_lexeme}:{level.size_lexeme}"
        for _, level in sorted(bids.items(), reverse=True)[:10]
    )
    try:
        body = f"{ask_text}|{bid_text}".encode("ascii")
    except UnicodeEncodeError:
        raise ValueError("book checksum lexeme is not ASCII") from None
    return zlib.crc32(body) & 0xFFFFFFFF


def _side(levels: tuple[BookLevel, ...]) -> dict[Decimal, BookLevel]:
    return {level.price: level for level in levels}


def _apply_side(
    candidate: dict[Decimal, BookLevel],
    changes: tuple[BookLevel, ...],
) -> None:
    for level in changes:
        if level.size == 0:
            candidate.pop(level.price, None)
        else:
            candidate[level.price] = level


class DeltaOrderBookEngine:
    """Single-symbol copy-on-write full-depth book owner."""

    def __init__(self) -> None:
        self._identity: _Identity | None = None
        self._asks: dict[Decimal, BookLevel] = {}
        self._bids: dict[Decimal, BookLevel] = {}
        self._publication: CompleteOrderBook | None = None
        self._awaiting_snapshot = True

    @property
    def publication(self) -> CompleteOrderBook | None:
        """Return the latest immutable publication, including display-only state."""
        return self._publication

    def invalidate(self, reason: BookInvalidationReason) -> None:
        """Make the last-good publication display-only and await a repair snapshot."""
        if type(reason) is not BookInvalidationReason:
            raise TypeError("book invalidation reason is invalid")
        self._awaiting_snapshot = True
        if self._publication is not None:
            self._publication = replace(
                self._publication,
                usable=False,
                invalidation_reason=reason,
            )

    def _failed(self, reason: BookInvalidationReason) -> BookApplyResult:
        self.invalidate(reason)
        return BookApplyResult(
            status=BookApplyStatus.INVALIDATED,
            publication=self._publication,
            required_action=BookRecoveryAction.RESUBSCRIBE_SYMBOL_AFTER_5_SECONDS,
        )

    def _publish(
        self,
        observation: _BookObservation,
        asks: dict[Decimal, BookLevel],
        bids: dict[Decimal, BookLevel],
    ) -> BookApplyResult:
        self._asks = asks
        self._bids = bids
        self._identity = _identity(observation)
        self._awaiting_snapshot = False
        publication = CompleteOrderBook(
            source=observation.source,
            product_id=observation.product_id,
            symbol=observation.symbol,
            sequence=observation.sequence,
            asks=tuple(level for _, level in sorted(asks.items())),
            bids=tuple(level for _, level in sorted(bids.items(), reverse=True)),
            checksum=observation.checksum,
            usable=True,
        )
        self._publication = publication
        return BookApplyResult(
            status=BookApplyStatus.ACCEPTED,
            publication=publication,
            required_action=None,
        )

    def apply(self, observation: _BookObservation) -> BookApplyResult:
        """Atomically verify and apply one snapshot or exact-next update."""
        if type(observation) not in {BookSnapshotObservation, BookDeltaObservation}:
            raise TypeError("book observation is invalid")
        if self._identity is not None and _identity(observation) != self._identity:
            return self._failed(BookInvalidationReason.IDENTITY)
        if type(observation) is BookSnapshotObservation:
            if not self._awaiting_snapshot:
                return self._failed(BookInvalidationReason.UNSOLICITED_SNAPSHOT)
            asks = _side(observation.asks)
            bids = _side(observation.bids)
            if _checksum(asks, bids) != observation.checksum:
                return self._failed(BookInvalidationReason.CHECKSUM)
            return self._publish(observation, asks, bids)
        if self._publication is None or self._awaiting_snapshot:
            return self._failed(BookInvalidationReason.FIRST_EVENT_NOT_SNAPSHOT)
        if observation.sequence != self._publication.sequence + 1:
            return self._failed(BookInvalidationReason.SEQUENCE)
        asks = dict(self._asks)
        bids = dict(self._bids)
        _apply_side(asks, observation.asks)
        _apply_side(bids, observation.bids)
        if _checksum(asks, bids) != observation.checksum:
            return self._failed(BookInvalidationReason.CHECKSUM)
        return self._publish(observation, asks, bids)


__all__ = [
    "BookApplyResult",
    "BookApplyStatus",
    "BookInvalidationReason",
    "BookRecoveryAction",
    "CompleteOrderBook",
    "DeltaOrderBookEngine",
]
