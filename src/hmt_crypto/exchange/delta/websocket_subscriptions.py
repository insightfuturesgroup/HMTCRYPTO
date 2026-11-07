"""Immutable WebSocket subscriptions and an in-memory owner-refcount registry."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from hmt_crypto.errors import ConfigurationError, ExchangeProtocolError
from hmt_crypto.exchange.delta.websocket_contracts import (
    WsCandleResolution,
    WsChannel,
    WsPlane,
    candle_channel,
)

_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9_.:/-]{0,63}\Z")
_SYMBOL_LIMIT = 100
_SYMBOLLESS_CHANNELS = frozenset(
    {WsChannel.PRODUCT_UPDATES.value, WsChannel.SYSTEM_STATUS.value, WsChannel.MARGINS.value}
)
_PUBLIC_CHANNELS = frozenset(
    {
        WsChannel.TICKER.value,
        WsChannel.OB_L1.value,
        WsChannel.OB_L2.value,
        WsChannel.OB_UPDATES.value,
        WsChannel.TRADES.value,
        WsChannel.MARK_PRICE.value,
        WsChannel.SPOT_PRICE.value,
        WsChannel.FUNDING_RATE.value,
        WsChannel.PRODUCT_UPDATES.value,
        WsChannel.SYSTEM_STATUS.value,
    }
)
_PRIVATE_CHANNELS = frozenset(
    {
        WsChannel.ORDERS.value,
        WsChannel.POSITIONS.value,
        WsChannel.MARGINS.value,
        WsChannel.V2_USER_TRADES.value,
    }
)
_CANDLE_CHANNELS = frozenset(candle_channel(value) for value in WsCandleResolution)


def _configuration_error() -> ConfigurationError:
    return ConfigurationError(context={"operation": "configure_ws_subscriptions"})


def _protocol_error() -> ExchangeProtocolError:
    return ExchangeProtocolError(context={"operation": "parse_ws_subscriptions_ack"})


def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


@dataclass(frozen=True, slots=True, order=True)
class WsSubscription:
    """One canonical current-name channel subscription."""

    channel: str
    symbols: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.channel) is not str or (
            self.channel not in _PUBLIC_CHANNELS
            and self.channel not in _PRIVATE_CHANNELS
            and self.channel not in _CANDLE_CHANNELS
        ):
            raise _configuration_error()
        if type(self.symbols) is not tuple or len(self.symbols) > _SYMBOL_LIMIT:
            raise _configuration_error()
        admitted: list[str] = []
        for symbol in self.symbols:
            if (
                type(symbol) is not str
                or _SYMBOL.fullmatch(symbol) is None
                or symbol.casefold() == "all"
                or symbol in admitted
            ):
                raise _configuration_error()
            admitted.append(symbol)
        if (self.channel in _SYMBOLLESS_CHANNELS) != (not admitted):
            raise _configuration_error()
        if self.channel == WsChannel.MARK_PRICE.value and any(
            not symbol.startswith("MARK:") or len(symbol) == 5 for symbol in admitted
        ):
            raise _configuration_error()
        object.__setattr__(self, "symbols", tuple(sorted(admitted)))

    @classmethod
    def for_channel(
        cls,
        channel: WsChannel,
        *,
        symbols: tuple[str, ...],
    ) -> WsSubscription:
        """Create one exact current channel, applying mark-symbol notation."""
        if type(channel) is not WsChannel or type(symbols) is not tuple:
            raise _configuration_error()
        normalized = symbols
        if channel is WsChannel.MARK_PRICE:
            normalized = tuple(f"MARK:{symbol}" for symbol in symbols)
        return cls(channel=channel.value, symbols=normalized)

    @classmethod
    def for_candles(
        cls,
        resolution: WsCandleResolution,
        *,
        symbols: tuple[str, ...],
    ) -> WsSubscription:
        """Create one streaming-candle subscription with no finality claim."""
        return cls(channel=candle_channel(resolution), symbols=symbols)


def _evidence(entries: tuple[WsSubscription, ...]) -> str:
    body = json.dumps(
        [{"channel": entry.channel, "symbols": list(entry.symbols)} for entry in entries],
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("ascii")
    return sha256(body).hexdigest()


@dataclass(frozen=True, slots=True)
class DesiredSubscriptions:
    """A revisioned canonical desired set, independent of server acknowledgement."""

    revision: int
    entries: tuple[WsSubscription, ...]
    evidence_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.revision) is not int
            or self.revision < 0
            or type(self.entries) is not tuple
            or any(type(entry) is not WsSubscription for entry in self.entries)
            or self.entries != tuple(sorted(self.entries))
            or len({entry.channel for entry in self.entries}) != len(self.entries)
            or not _is_sha256(self.evidence_sha256)
            or self.evidence_sha256 != _evidence(self.entries)
        ):
            raise _configuration_error()


def desired_subscriptions(
    *, revision: int, entries: tuple[WsSubscription, ...]
) -> DesiredSubscriptions:
    """Build one sorted desired-set snapshot with reproducible evidence."""
    if type(entries) is not tuple or any(type(entry) is not WsSubscription for entry in entries):
        raise _configuration_error()
    ordered = tuple(sorted(entries))
    if len({entry.channel for entry in ordered}) != len(ordered):
        raise _configuration_error()
    return DesiredSubscriptions(
        revision=revision,
        entries=ordered,
        evidence_sha256=_evidence(ordered),
    )


@dataclass(frozen=True, slots=True)
class SubscriptionsAccepted:
    """Evidence that one complete venue acknowledgement equals the desired set."""

    desired_revision: int
    evidence_sha256: str
    entries: tuple[WsSubscription, ...]


def _ack_entry(value: object) -> WsSubscription:
    if type(value) is not dict or "error" in value:
        raise _protocol_error()
    if set(value) not in ({"name"}, {"name", "symbols"}):
        raise _protocol_error()
    name = value.get("name")
    raw_symbols = value.get("symbols", [])
    if type(name) is not str or type(raw_symbols) is not list:
        raise _protocol_error()
    if any(type(symbol) is not str for symbol in raw_symbols):
        raise _protocol_error()
    try:
        return WsSubscription(channel=name, symbols=tuple(raw_symbols))
    except ConfigurationError:
        raise _protocol_error() from None


def parse_subscriptions_ack(
    message: Mapping[str, object],
    *,
    expected: DesiredSubscriptions,
) -> SubscriptionsAccepted:
    """Accept only the exact complete current set for one desired revision."""
    if (
        type(message) is not dict
        or set(message) != {"type", "channels"}
        or message.get("type") != "subscriptions"
        or type(expected) is not DesiredSubscriptions
    ):
        raise _protocol_error()
    channels = message.get("channels")
    if type(channels) is not list:
        raise _protocol_error()
    entries = tuple(sorted(_ack_entry(value) for value in channels))
    if entries != expected.entries or _evidence(entries) != expected.evidence_sha256:
        raise _protocol_error()
    return SubscriptionsAccepted(
        desired_revision=expected.revision,
        evidence_sha256=expected.evidence_sha256,
        entries=expected.entries,
    )


class StreamInvalidationKind(StrEnum):
    """Closed local reasons for invalidating one stream generation."""

    SUBSCRIPTION_CHANGE = "subscription_change"
    BOOK_GAP = "book_gap"
    CATALOGUE_MISMATCH = "catalogue_mismatch"
    QUEUE_OVERFLOW = "queue_overflow"
    PROTOCOL_DRIFT = "protocol_drift"


def _validate_generation_fields(
    plane: object,
    generation: object,
    sequence: object,
    evidence_sha256: object,
) -> None:
    if (
        type(plane) is not WsPlane
        or type(generation) is not UUID
        or type(sequence) is not int
        or sequence < 0
        or not _is_sha256(evidence_sha256)
    ):
        raise ValueError("invalid stream generation evidence")


@dataclass(frozen=True, slots=True)
class GenerationSubscriptionReceipt:
    """A full-set acknowledgement bound to one connection and control revision."""

    plane: WsPlane
    connection_generation: UUID
    control_revision: int
    desired_revision: int
    evidence_sha256: str
    accepted_at_monotonic_ns: int

    def __post_init__(self) -> None:
        _validate_generation_fields(
            self.plane,
            self.connection_generation,
            self.control_revision,
            self.evidence_sha256,
        )
        if (
            type(self.desired_revision) is not int
            or self.desired_revision < 0
            or type(self.accepted_at_monotonic_ns) is not int
            or self.accepted_at_monotonic_ns < 0
        ):
            raise ValueError("invalid subscription receipt")


@dataclass(frozen=True, slots=True)
class GenerationControlInvalidation:
    """A typed actor-to-supervisor generation invalidation."""

    plane: WsPlane
    connection_generation: UUID
    local_sequence: int
    kind: StreamInvalidationKind
    evidence_sha256: str

    def __post_init__(self) -> None:
        _validate_generation_fields(
            self.plane,
            self.connection_generation,
            self.local_sequence,
            self.evidence_sha256,
        )
        if type(self.kind) is not StreamInvalidationKind:
            raise ValueError("invalid stream invalidation kind")


class SubscriptionLease(Protocol):
    """Idempotent ownership handle for one desired subscription claim."""

    async def aclose(self) -> None:
        """Release the owner's complete claim once."""
        ...


class _RegistryLease:
    __slots__ = ("_closed", "_owner_id", "_registry")

    def __init__(self, registry: SubscriptionRegistry, owner_id: UUID) -> None:
        self._registry = registry
        self._owner_id = owner_id
        self._closed = False

    async def aclose(self) -> None:
        if not self._closed:
            await self._registry._release(self._owner_id)
            self._closed = True


class SubscriptionRegistry:
    """Credential-free desired-set union with owner refcounts and revisions."""

    def __init__(
        self,
        *,
        plane: WsPlane,
        allow_testnet_assumption: bool = False,
    ) -> None:
        if type(plane) is not WsPlane or type(allow_testnet_assumption) is not bool:
            raise _configuration_error()
        self._plane = plane
        self._allow_testnet_assumption = allow_testnet_assumption
        self._lock = asyncio.Lock()
        self._owners: dict[UUID, tuple[WsSubscription, ...]] = {}
        self._revision = 0
        self._entries: tuple[WsSubscription, ...] = ()

    def _validate_claim(
        self,
        owner_id: UUID,
        entries: tuple[WsSubscription, ...],
    ) -> tuple[WsSubscription, ...]:
        if (
            type(owner_id) is not UUID
            or type(entries) is not tuple
            or not entries
            or any(type(entry) is not WsSubscription for entry in entries)
        ):
            raise _configuration_error()
        ordered = tuple(sorted(entries))
        if len({entry.channel for entry in ordered}) != len(ordered):
            raise _configuration_error()
        allowed = _PUBLIC_CHANNELS | _CANDLE_CHANNELS
        if self._plane is WsPlane.PRIVATE:
            allowed = _PRIVATE_CHANNELS
        if any(entry.channel not in allowed for entry in ordered):
            raise _configuration_error()
        if not self._allow_testnet_assumption and any(
            entry.channel == WsChannel.PRODUCT_UPDATES.value for entry in ordered
        ):
            raise _configuration_error()
        return ordered

    @staticmethod
    def _union(
        owners: Mapping[UUID, tuple[WsSubscription, ...]],
    ) -> tuple[WsSubscription, ...]:
        symbols_by_channel: dict[str, set[str]] = {}
        for entries in owners.values():
            for entry in entries:
                symbols_by_channel.setdefault(entry.channel, set()).update(entry.symbols)
        return tuple(
            sorted(
                WsSubscription(channel=channel, symbols=tuple(symbols))
                for channel, symbols in symbols_by_channel.items()
            )
        )

    def _replace_entries(self, entries: tuple[WsSubscription, ...]) -> None:
        if entries != self._entries:
            self._entries = entries
            self._revision += 1

    async def acquire(
        self,
        *,
        owner_id: UUID,
        entries: tuple[WsSubscription, ...],
    ) -> SubscriptionLease:
        """Add one owner's complete claim atomically."""
        claim = self._validate_claim(owner_id, entries)
        async with self._lock:
            if owner_id in self._owners:
                raise _configuration_error()
            candidate = dict(self._owners)
            candidate[owner_id] = claim
            union = self._union(candidate)
            self._owners = candidate
            self._replace_entries(union)
        return _RegistryLease(self, owner_id)

    async def _release(self, owner_id: UUID) -> None:
        async with self._lock:
            if owner_id not in self._owners:
                return
            candidate = dict(self._owners)
            del candidate[owner_id]
            self._owners = candidate
            self._replace_entries(self._union(candidate))

    async def snapshot(self) -> DesiredSubscriptions:
        """Return an independently owned stable reconnect snapshot."""
        async with self._lock:
            return desired_subscriptions(revision=self._revision, entries=self._entries)


__all__ = [
    "DesiredSubscriptions",
    "GenerationControlInvalidation",
    "GenerationSubscriptionReceipt",
    "StreamInvalidationKind",
    "SubscriptionLease",
    "SubscriptionRegistry",
    "SubscriptionsAccepted",
    "WsSubscription",
    "desired_subscriptions",
    "parse_subscriptions_ack",
]
