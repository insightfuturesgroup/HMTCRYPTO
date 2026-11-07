"""Single-product private-channel snapshot, identity and sequence ledger."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from hmt_crypto.exchange.delta.private_ws_models import (
    MarginUpdate,
    OrderSnapshot,
    OrderUpdate,
    PositionSnapshot,
    PositionUpdate,
    PrivateWsObservation,
    UserTradeUpdate,
)


class PrivateWsSequenceError(ValueError):
    """A private identity/sequence/snapshot invariant made a generation unsafe."""


class PrivateWsReadiness(StrEnum):
    """Truthful authority state for one physical private connection generation."""

    AUTHENTICATING = "authenticating"
    SUBSCRIBING = "subscribing"
    AWAITING_SNAPSHOTS = "awaiting_snapshots"
    RECONCILING = "reconciling"
    READY = "ready"
    INVALID = "invalid"


@dataclass(slots=True)
class PrivateWsGenerationLedger:
    """Own all stream-local baselines; no evidence survives a reconnect."""

    generation: UUID
    symbol: str
    product_id: int
    external_user_id: str
    readiness: PrivateWsReadiness = PrivateWsReadiness.AUTHENTICATING
    _order_sequence: int | None = field(default=None, init=False, repr=False)
    _trade_sequence: int | None = field(default=None, init=False, repr=False)
    _position_snapshot_seen: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.generation) is not UUID
            or self.generation.int == 0
            or type(self.symbol) is not str
            or not self.symbol
            or type(self.product_id) is not int
            or not 1 <= self.product_id < 2**31
            or type(self.external_user_id) is not str
            or not self.external_user_id.isdigit()
            or int(self.external_user_id) <= 0
            or type(self.readiness) is not PrivateWsReadiness
        ):
            raise PrivateWsSequenceError("invalid generation identity")

    @property
    def snapshots_ready(self) -> bool:
        """Return whether the one-product order/position baselines both exist."""
        return self._position_snapshot_seen and self._order_sequence is not None

    @property
    def authoritative(self) -> bool:
        """Return whether final post-baseline REST reconciliation completed."""
        return self.readiness is PrivateWsReadiness.READY

    def authenticated(self) -> None:
        self._require(PrivateWsReadiness.AUTHENTICATING)
        self.readiness = PrivateWsReadiness.SUBSCRIBING

    def subscriptions_accepted(self) -> None:
        self._require(PrivateWsReadiness.SUBSCRIBING)
        self.readiness = PrivateWsReadiness.AWAITING_SNAPSHOTS

    def begin_reconciliation(self) -> None:
        """Freeze observation processing only after both baselines are present."""
        self._require(PrivateWsReadiness.AWAITING_SNAPSHOTS)
        if not self.snapshots_ready:
            self.invalidate()
            raise PrivateWsSequenceError("reconciliation before snapshots")
        self.readiness = PrivateWsReadiness.RECONCILING

    def reconciliation_completed(self, *, generation: UUID) -> None:
        self._require(PrivateWsReadiness.RECONCILING)
        if type(generation) is not UUID or generation != self.generation:
            self.invalidate()
            raise PrivateWsSequenceError("foreign reconciliation generation")
        self.readiness = PrivateWsReadiness.READY

    def observe(self, observation: PrivateWsObservation) -> None:
        """Apply one parsed observation or fail closed on identity/gap/reset."""
        if self.readiness not in {
            PrivateWsReadiness.AWAITING_SNAPSHOTS,
            PrivateWsReadiness.RECONCILING,
            PrivateWsReadiness.READY,
        }:
            self.invalidate()
            raise PrivateWsSequenceError("observation outside receive phase")
        try:
            expected_user = int(self.external_user_id)
            if isinstance(observation, OrderSnapshot):
                self._require_symbol(observation.symbol)
                if self._order_sequence is not None:
                    raise PrivateWsSequenceError("duplicate order snapshot")
                for order_row in observation.rows:
                    self._require_product(order_row.symbol, order_row.product_id)
                    if order_row.user_id != expected_user:
                        raise PrivateWsSequenceError("foreign order user")
                self._order_sequence = observation.sequence
            elif isinstance(observation, OrderUpdate):
                self._require_product(observation.row.symbol, observation.row.product_id)
                if self._order_sequence is None or observation.sequence != self._order_sequence + 1:
                    raise PrivateWsSequenceError("order sequence gap")
                self._order_sequence = observation.sequence
            elif isinstance(observation, UserTradeUpdate):
                self._require_symbol(observation.symbol)
                if observation.user_id != expected_user:
                    raise PrivateWsSequenceError("foreign fill user")
                if (
                    self._trade_sequence is not None
                    and observation.sequence != self._trade_sequence + 1
                ):
                    raise PrivateWsSequenceError("fill sequence gap or reset")
                self._trade_sequence = observation.sequence
            elif isinstance(observation, PositionSnapshot):
                if self._position_snapshot_seen:
                    raise PrivateWsSequenceError("duplicate position snapshot")
                for position_row in observation.rows:
                    self._require_product(position_row.symbol, position_row.product_id)
                    if position_row.user_id != expected_user:
                        raise PrivateWsSequenceError("foreign position user")
                self._position_snapshot_seen = True
            elif isinstance(observation, PositionUpdate):
                if not self._position_snapshot_seen:
                    raise PrivateWsSequenceError("position update before snapshot")
                self._require_product(observation.row.symbol, observation.row.product_id)
            elif isinstance(observation, MarginUpdate):
                if observation.user_id != expected_user:
                    raise PrivateWsSequenceError("foreign margin user")
            else:
                raise PrivateWsSequenceError("unknown private observation")
        except PrivateWsSequenceError:
            self.invalidate()
            raise

    def invalidate(self) -> None:
        self.readiness = PrivateWsReadiness.INVALID

    def _require(self, expected: PrivateWsReadiness) -> None:
        if self.readiness is not expected:
            self.invalidate()
            raise PrivateWsSequenceError("invalid readiness transition")

    def _require_symbol(self, symbol: str) -> None:
        if type(symbol) is not str or symbol != self.symbol:
            raise PrivateWsSequenceError("foreign symbol")

    def _require_product(self, symbol: str, product_id: int) -> None:
        self._require_symbol(symbol)
        if type(product_id) is not int or product_id != self.product_id:
            raise PrivateWsSequenceError("catalogue product mismatch")


__all__ = [
    "PrivateWsGenerationLedger",
    "PrivateWsReadiness",
    "PrivateWsSequenceError",
]
