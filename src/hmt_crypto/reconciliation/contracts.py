"""Immutable reconciliation inputs, results, and drift classifications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID


class ReconciliationTrigger(StrEnum):
    STARTUP = "STARTUP"
    PERIODIC = "PERIODIC"
    MANUAL = "MANUAL"
    RECOVERY = "RECOVERY"
    RECONNECT = "RECONNECT"


class ReconciliationDriftKind(StrEnum):
    EXTERNAL_OPEN_ORDER = "EXTERNAL_OPEN_ORDER"
    EXTERNAL_POSITION = "EXTERNAL_POSITION"
    LOCAL_OPEN_ORDER_MISSING = "LOCAL_OPEN_ORDER_MISSING"
    POSITION_MISMATCH = "POSITION_MISMATCH"
    UNCERTAIN_OPERATION = "UNCERTAIN_OPERATION"


class UncertainResolution(StrEnum):
    ACCEPTED = "ACCEPTED"
    NOT_SENT = "NOT_SENT"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class LocalOrderTruth:
    local_order_id: UUID
    product_id: int
    client_order_id: str | None
    exchange_order_id: int | None
    uncertain_attempt_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        if (
            type(self.local_order_id) is not UUID
            or self.local_order_id.int == 0
            or type(self.product_id) is not int
            or not 1 <= self.product_id < 2**63
            or (
                self.client_order_id is not None
                and (type(self.client_order_id) is not str or not self.client_order_id)
            )
            or (
                self.exchange_order_id is not None
                and (
                    type(self.exchange_order_id) is not int
                    or not 1 <= self.exchange_order_id < 2**63
                )
            )
            or (self.client_order_id is None and self.exchange_order_id is None)
            or type(self.uncertain_attempt_ids) is not tuple
            or any(type(item) is not UUID or item.int == 0 for item in self.uncertain_attempt_ids)
            or len(set(self.uncertain_attempt_ids)) != len(self.uncertain_attempt_ids)
        ):
            raise ValueError("invalid local order truth")

    @property
    def uncertain(self) -> bool:
        return bool(self.uncertain_attempt_ids)


@dataclass(frozen=True, slots=True)
class LocalAccountProjection:
    open_orders: tuple[LocalOrderTruth, ...]
    signed_positions: tuple[tuple[int, int], ...]
    unbound_uncertain_attempt_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        if (
            type(self.open_orders) is not tuple
            or any(type(item) is not LocalOrderTruth for item in self.open_orders)
            or len({item.local_order_id for item in self.open_orders}) != len(self.open_orders)
            or type(self.signed_positions) is not tuple
            or any(
                type(item) is not tuple
                or len(item) != 2
                or type(item[0]) is not int
                or not 1 <= item[0] < 2**63
                or type(item[1]) is not int
                for item in self.signed_positions
            )
            or len({item[0] for item in self.signed_positions}) != len(self.signed_positions)
            or type(self.unbound_uncertain_attempt_ids) is not tuple
            or any(
                type(item) is not UUID or item.int == 0
                for item in self.unbound_uncertain_attempt_ids
            )
            or len(set(self.unbound_uncertain_attempt_ids))
            != len(self.unbound_uncertain_attempt_ids)
            or len(
                {
                    attempt_id
                    for order in self.open_orders
                    for attempt_id in order.uncertain_attempt_ids
                }
                | set(self.unbound_uncertain_attempt_ids)
            )
            != sum(len(order.uncertain_attempt_ids) for order in self.open_orders)
            + len(self.unbound_uncertain_attempt_ids)
        ):
            raise ValueError("invalid local account projection")


@dataclass(frozen=True, slots=True)
class ReconciliationDrift:
    kind: ReconciliationDriftKind
    subject_sha256: str
    product_id: int | None
    external_exposure: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.kind) is not ReconciliationDriftKind
            or type(self.subject_sha256) is not str
            or len(self.subject_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.subject_sha256)
            or (
                self.product_id is not None
                and (type(self.product_id) is not int or not 1 <= self.product_id < 2**63)
            )
            or type(self.external_exposure) is not bool
        ):
            raise ValueError("invalid reconciliation drift")


@dataclass(frozen=True, slots=True)
class UncertainOrderOutcome:
    attempt_id: UUID
    local_order_id: UUID | None
    resolution: UncertainResolution
    evidence_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.attempt_id) is not UUID
            or self.attempt_id.int == 0
            or (
                self.local_order_id is not None
                and (type(self.local_order_id) is not UUID or self.local_order_id.int == 0)
            )
            or type(self.resolution) is not UncertainResolution
            or type(self.evidence_sha256) is not str
            or len(self.evidence_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.evidence_sha256)
        ):
            raise ValueError("invalid uncertain order outcome")


@dataclass(frozen=True, slots=True)
class ReconciliationReceipt:
    request_id: UUID
    trigger: ReconciliationTrigger
    ready: bool
    complete: bool
    evidence_sha256: str | None
    observed_at: datetime | None
    drifts: tuple[ReconciliationDrift, ...]
    uncertain: tuple[UncertainOrderOutcome, ...]
    replayed: bool
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.request_id) is not UUID
            or self.request_id.int == 0
            or type(self.trigger) is not ReconciliationTrigger
            or type(self.ready) is not bool
            or type(self.complete) is not bool
            or type(self.drifts) is not tuple
            or any(type(item) is not ReconciliationDrift for item in self.drifts)
            or type(self.uncertain) is not tuple
            or any(type(item) is not UncertainOrderOutcome for item in self.uncertain)
            or type(self.replayed) is not bool
            or self.evidence_sha256 is not None
            and (
                type(self.evidence_sha256) is not str
                or len(self.evidence_sha256) != 64
                or any(character not in "0123456789abcdef" for character in self.evidence_sha256)
            )
            or self.failure_code is not None
            and self.failure_code
            not in {
                "REQUEST_FAILED",
                "INVALID_RESPONSE",
                "PAGINATION_INCOMPLETE",
                "LIMIT_REACHED",
                "LOCAL_STATE_CHANGED",
                "UNKNOWN",
            }
        ):
            raise ValueError("invalid reconciliation receipt")
        if self.observed_at is not None and (
            type(self.observed_at) is not datetime
            or self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("invalid reconciliation receipt time")
        if self.complete != (
            self.evidence_sha256 is not None
            and self.observed_at is not None
            and self.failure_code is None
        ):
            raise ValueError("invalid reconciliation completion evidence")
        if not self.complete and (self.ready or self.drifts or self.uncertain):
            raise ValueError("failed reconciliation cannot publish venue evidence")
        if not self.complete and self.failure_code is None:
            raise ValueError("failed reconciliation requires a classified failure")
        if self.ready and (self.drifts or self.uncertain):
            raise ValueError("ready reconciliation must be complete and drift-free")


__all__ = [
    "LocalAccountProjection",
    "LocalOrderTruth",
    "ReconciliationDrift",
    "ReconciliationDriftKind",
    "ReconciliationReceipt",
    "ReconciliationTrigger",
    "UncertainOrderOutcome",
    "UncertainResolution",
]
