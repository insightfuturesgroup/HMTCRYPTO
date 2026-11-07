"""Credential-free public generation values and narrow dependency protocols."""

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol
from uuid import UUID

from hmt_crypto.exchange.delta.catalog_models import CatalogSnapshot
from hmt_crypto.exchange.delta.websocket_contracts import WsCandleResolution

_REFRESH_CHANNELS = frozenset(
    {
        "catalogue_binding",
        "funding_rate",
        "mark_price",
        "ob_l1",
        "ob_l2",
        "ob_updates",
        "product_updates",
        "spot_price",
        "system_status",
        "ticker",
        "trades",
        *(f"candlestick_{resolution.value}" for resolution in WsCandleResolution),
    }
)


class PublicGenerationStatus(StrEnum):
    """Typed terminal outcome for one supervisor-opened public generation."""

    RECEIVER_ENDED = "receiver_ended"
    CONTROL_TIMEOUT = "control_timeout"
    PROTOCOL_DRIFT = "protocol_drift"
    CATALOGUE_MISMATCH = "catalogue_mismatch"


@dataclass(frozen=True, slots=True, kw_only=True)
class PublicGenerationResult:
    """Bounded terminal evidence returned to the reconnect supervisor."""

    status: PublicGenerationStatus
    connection_generation: UUID
    observations_applied: int
    desired_revision: int

    def __post_init__(self) -> None:
        if (
            type(self.status) is not PublicGenerationStatus
            or type(self.connection_generation) is not UUID
        ):
            raise TypeError("public generation result identity is invalid")
        if type(self.observations_applied) is not int or self.observations_applied < 0:
            raise ValueError("public generation observation count is invalid")
        if type(self.desired_revision) is not int or self.desired_revision < 0:
            raise ValueError("public generation desired revision is invalid")


@dataclass(frozen=True, slots=True, kw_only=True)
class PublicConnectionGeneration:
    """Supervisor-issued identity and bounded local control timings."""

    connection_generation: UUID
    region: str
    environment: str
    control_timeout_ns: int
    registry_poll_ns: int
    repair_delay_ns: int
    finalization_timeout_ns: int
    max_frame_bytes: int

    def __post_init__(self) -> None:
        if type(self.connection_generation) is not UUID:
            raise TypeError("public connection generation is invalid")
        if self.region != "india" or self.environment not in {"testnet", "prod"}:
            raise ValueError("public connection source is invalid")
        for name in ("control_timeout_ns", "registry_poll_ns", "finalization_timeout_ns"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError("public generation deadline is invalid")
        if type(self.repair_delay_ns) is not int or self.repair_delay_ns < 5_000_000_000:
            raise ValueError("public book repair delay is below local policy")
        if (
            type(self.max_frame_bytes) is not int
            or not 1 <= self.max_frame_bytes <= 16 * 1024 * 1024
        ):
            raise ValueError("public frame bound is invalid")


@dataclass(frozen=True, slots=True, kw_only=True)
class PublicCatalogueBinding:
    """Authoritative catalogue publication ID plus explicit spot-index bindings."""

    catalogue: CatalogSnapshot
    catalogue_snapshot_id: UUID
    spot_asset_ids: Mapping[str, int]

    def __post_init__(self) -> None:
        if (
            type(self.catalogue) is not CatalogSnapshot
            or type(self.catalogue_snapshot_id) is not UUID
        ):
            raise TypeError("public catalogue binding is invalid")
        owned = dict(self.spot_asset_ids)
        if any(
            type(symbol) is not str
            or not symbol
            or type(asset_id) is not int
            or asset_id <= 0
            or asset_id not in self.catalogue.assets_by_id
            for symbol, asset_id in owned.items()
        ):
            raise ValueError("public spot catalogue binding is invalid")
        object.__setattr__(self, "spot_asset_ids", MappingProxyType(owned))


@dataclass(frozen=True, slots=True, kw_only=True)
class CatalogueRefreshRequest:
    """Sanitized generation-bound request to refresh product identity."""

    connection_generation: UUID
    catalogue_snapshot_id: UUID
    channel: str

    def __post_init__(self) -> None:
        if (
            type(self.connection_generation) is not UUID
            or type(self.catalogue_snapshot_id) is not UUID
        ):
            raise TypeError("catalogue refresh identity is invalid")
        if self.channel not in _REFRESH_CHANNELS:
            raise ValueError("catalogue refresh channel is invalid")


class PublicCatalogueProvider(Protocol):
    """Read-only provider for the current accepted catalogue publication."""

    def current(self) -> PublicCatalogueBinding: ...


class CatalogueRefreshPort(Protocol):
    """Narrow scheduling capability; it performs no catalogue I/O in the actor."""

    async def request_catalogue_refresh(self, request: CatalogueRefreshRequest) -> None: ...


class CatalogueRefreshMailbox:
    """Bounded non-blocking handoff for sanitized catalogue refresh requests."""

    __slots__ = ("_max_pending", "_requests")

    def __init__(self, *, max_pending: int = 256) -> None:
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError("catalogue refresh bound is invalid")
        self._max_pending = max_pending
        self._requests: deque[CatalogueRefreshRequest] = deque()

    async def request_catalogue_refresh(self, request: CatalogueRefreshRequest) -> None:
        """Enqueue one exact request without awaiting an external dependency."""
        if type(request) is not CatalogueRefreshRequest or len(self._requests) >= self._max_pending:
            del request
            raise RuntimeError("catalogue refresh mailbox is unavailable")
        self._requests.append(request)

    @property
    def requests(self) -> tuple[CatalogueRefreshRequest, ...]:
        """Return an immutable snapshot of pending refresh requests."""
        return tuple(self._requests)


MonotonicNs = Callable[[], int]
SleepNs = Callable[[int], Awaitable[None]]


__all__ = [
    "CatalogueRefreshPort",
    "CatalogueRefreshMailbox",
    "CatalogueRefreshRequest",
    "MonotonicNs",
    "PublicCatalogueBinding",
    "PublicCatalogueProvider",
    "PublicConnectionGeneration",
    "PublicGenerationResult",
    "PublicGenerationStatus",
    "SleepNs",
]
