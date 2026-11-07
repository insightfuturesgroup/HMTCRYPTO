"""Sealed single-product private subscription and provenance contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import NoReturn, Self, SupportsIndex
from uuid import UUID
from weakref import WeakKeyDictionary

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.domain.account import AccountRef
from hmt_crypto.errors import ConfigurationError, ExchangeProtocolError
from hmt_crypto.exchange.delta.catalog_models import (
    CatalogSnapshot,
    _has_valid_catalog_snapshot_publication,
)
from hmt_crypto.exchange.delta.private_ws_models import (
    MarginUpdate,
    OrderSnapshot,
    OrderUpdate,
    PositionSnapshot,
    PositionUpdate,
    PrivateWsObservation,
    UserTradeUpdate,
)
from hmt_crypto.exchange.delta.websocket_codec import encode_subscribe
from hmt_crypto.exchange.delta.websocket_contracts import WsChannel
from hmt_crypto.exchange.delta.websocket_subscriptions import (
    DesiredSubscriptions,
    SubscriptionsAccepted,
    WsSubscription,
    desired_subscriptions,
    parse_subscriptions_ack,
)


def _configuration_error() -> ConfigurationError:
    return ConfigurationError(context={"operation": "configure_delta_private_ws"})


@dataclass(slots=True)
class _PlanState:
    symbol: str
    product_id: int
    catalogue_snapshot_id: UUID
    catalogue_revision: int
    catalogue_content_sha256: str
    desired: DesiredSubscriptions


_PLAN_STATES: WeakKeyDictionary[PrivateWsSubscriptionPlan, _PlanState]
_CLASSES_CLOSED = False


class PrivateWsSubscriptionPlan:
    """Module-issued exact four-channel plan for one catalogue-bound product."""

    __slots__ = ("__weakref__",)

    def __new__(cls, *_args: object, **_kwargs: object) -> Self:
        raise TypeError("private WebSocket subscription plan cannot be constructed")

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if _CLASSES_CLOSED:
            raise TypeError("private WebSocket subscription plan cannot be subclassed")

    def __copy__(self) -> NoReturn:
        raise TypeError("private WebSocket subscription plan cannot be copied")

    def __deepcopy__(self, _memo: dict[int, object]) -> NoReturn:
        raise TypeError("private WebSocket subscription plan cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("private WebSocket subscription plan cannot be serialized")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError("private WebSocket subscription plan cannot be serialized")

    @property
    def symbol(self) -> str:
        """Return the sole explicit subscribed symbol."""
        return _plan_state(self).symbol

    @property
    def product_id(self) -> int:
        """Return the catalogue-bound numeric product identifier."""
        return _plan_state(self).product_id

    @property
    def catalogue_snapshot_id(self) -> UUID:
        """Return the catalogue revision identity used for the binding."""
        return UUID(int=_plan_state(self).catalogue_snapshot_id.int)

    @property
    def catalogue_revision(self) -> int:
        """Return the published catalogue revision used for the binding."""
        return _plan_state(self).catalogue_revision

    @property
    def catalogue_content_sha256(self) -> str:
        """Return the published catalogue content identity used for the binding."""
        return _plan_state(self).catalogue_content_sha256

    @property
    def desired(self) -> DesiredSubscriptions:
        """Return the immutable exact subscription set."""
        return _plan_state(self).desired

    @property
    def encoded_subscribe(self) -> str:
        """Return the deterministic current-name subscription frame."""
        return encode_subscribe(_plan_state(self).desired.entries)

    def parse_ack(self, message: object) -> SubscriptionsAccepted:
        """Accept only an exact complete acknowledgement of this plan."""
        if type(message) is not dict:
            raise ExchangeProtocolError(
                context={"operation": "parse_ws_subscriptions_ack", "scope": "private"}
            )
        return parse_subscriptions_ack(message, expected=_plan_state(self).desired)


_CLASSES_CLOSED = True
_PLAN_STATES = WeakKeyDictionary()


def _plan_state(value: object) -> _PlanState:
    if type(value) is not PrivateWsSubscriptionPlan:
        raise _configuration_error()
    state = _PLAN_STATES.get(value)
    if state is None:
        raise _configuration_error()
    return state


def private_ws_subscription_plan(
    *, symbol: str, catalogue: CatalogSnapshot, catalogue_snapshot_id: UUID
) -> PrivateWsSubscriptionPlan:
    """Issue the only admitted private plan: four channels and one explicit symbol."""
    try:
        if (
            type(symbol) is not str
            or not symbol
            or symbol.casefold() == "all"
            or len(symbol) > 64
            or type(catalogue) is not CatalogSnapshot
            or not _has_valid_catalog_snapshot_publication(catalogue)
            or catalogue.source.region is not DeltaRegion.INDIA
            or catalogue.source.environment is not DeltaEnvironment.TESTNET
            or type(catalogue_snapshot_id) is not UUID
            or catalogue_snapshot_id.int == 0
        ):
            raise _configuration_error()
        product = catalogue.require_symbol(symbol)
        product_id = product.id
        if (
            type(product_id) is not int
            or not 1 <= product_id < 2**31
            or catalogue.require_product_id(product_id).symbol != symbol
        ):
            raise _configuration_error()
        entries = (
            WsSubscription.for_channel(WsChannel.MARGINS, symbols=()),
            WsSubscription.for_channel(WsChannel.ORDERS, symbols=(symbol,)),
            WsSubscription.for_channel(WsChannel.POSITIONS, symbols=(symbol,)),
            WsSubscription.for_channel(WsChannel.V2_USER_TRADES, symbols=(symbol,)),
        )
        desired = desired_subscriptions(revision=1, entries=entries)
    except Exception:
        raise _configuration_error() from None
    plan = object.__new__(PrivateWsSubscriptionPlan)
    _PLAN_STATES[plan] = _PlanState(
        symbol=symbol,
        product_id=product_id,
        catalogue_snapshot_id=UUID(int=catalogue_snapshot_id.int),
        catalogue_revision=catalogue.revision,
        catalogue_content_sha256=catalogue.content_sha256,
        desired=desired,
    )
    return plan


class PrivateWsAuthorityClass(StrEnum):
    """Whether an observation may mutate state without another REST read."""

    SEQUENCED = "sequenced"
    PROVISIONAL = "provisional"


class PrivateWsInvalidationReason(StrEnum):
    """Closed reasons that synchronously revoke one generation lease."""

    AUTH = "auth"
    CANCELLED = "cancelled"
    DISCONNECTED = "disconnected"
    PROTOCOL = "protocol"
    SEQUENCE = "sequence"
    STOPPED = "stopped"


class _OpaqueGenerationValue:
    __slots__ = ("__weakref__",)

    def __new__(cls, *_args: object, **_kwargs: object) -> Self:
        raise TypeError("private WebSocket generation values cannot be constructed")

    def __copy__(self) -> NoReturn:
        raise TypeError("private WebSocket generation values cannot be copied")

    def __deepcopy__(self, _memo: dict[int, object]) -> NoReturn:
        raise TypeError("private WebSocket generation values cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("private WebSocket generation values cannot be serialized")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError("private WebSocket generation values cannot be serialized")


class PrivateWsGenerationLease(_OpaqueGenerationValue):
    """Opaque currentness capability shared by every event in one generation."""

    __slots__ = ()


class PrivateWsSource(_OpaqueGenerationValue):
    """Opaque complete provenance whose access requires a current generation."""

    __slots__ = ()

    @property
    def account_id(self) -> UUID:
        return UUID(int=_source_state(self).account_id.int)

    @property
    def external_user_id(self) -> str:
        return _source_state(self).external_user_id

    @property
    def region(self) -> DeltaRegion:
        return _source_state(self).region

    @property
    def environment(self) -> DeltaEnvironment:
        return _source_state(self).environment

    @property
    def mode(self) -> ExecutionMode:
        return _source_state(self).mode

    @property
    def connection_generation(self) -> UUID:
        return UUID(int=_source_state(self).generation.int)

    @property
    def local_sequence(self) -> int:
        return _source_state(self).local_sequence

    @property
    def catalogue_snapshot_id(self) -> UUID:
        return UUID(int=_source_state(self).catalogue_snapshot_id.int)

    @property
    def catalogue_revision(self) -> int:
        return _source_state(self).catalogue_revision

    @property
    def catalogue_content_sha256(self) -> str:
        return _source_state(self).catalogue_content_sha256

    @property
    def received_at(self) -> datetime:
        return _source_state(self).received_at

    @property
    def reconciliation_evidence_sha256(self) -> str:
        return _source_state(self).reconciliation_evidence_sha256


class PrivateWsDelivery(_OpaqueGenerationValue):
    """Opaque current-generation observation; held stale values revoke in place."""

    __slots__ = ()

    @property
    def source(self) -> PrivateWsSource:
        return _delivery_state(self).source

    @property
    def authority(self) -> PrivateWsAuthorityClass:
        return _delivery_state(self).authority

    @property
    def observation(self) -> PrivateWsObservation:
        return _delivery_state(self).observation


class PrivateWsGenerationInvalidated(_OpaqueGenerationValue):
    """Sealed terminal control event that remains readable after lease revocation."""

    __slots__ = ()

    @property
    def connection_generation(self) -> UUID:
        return UUID(int=_invalidation_state(self).generation.int)

    @property
    def account_id(self) -> UUID:
        return UUID(int=_invalidation_state(self).account_id.int)

    @property
    def reason(self) -> PrivateWsInvalidationReason:
        return _invalidation_state(self).reason

    @property
    def invalidated_at(self) -> datetime:
        return _invalidation_state(self).invalidated_at


@dataclass(slots=True)
class _GenerationState:
    account: AccountRef
    plan: PrivateWsSubscriptionPlan
    generation: UUID
    active: bool = True
    next_sequence: int = 1


@dataclass(slots=True)
class _SourceState:
    lease: PrivateWsGenerationLease
    account_id: UUID
    external_user_id: str
    region: DeltaRegion
    environment: DeltaEnvironment
    mode: ExecutionMode
    generation: UUID
    local_sequence: int
    catalogue_snapshot_id: UUID
    catalogue_revision: int
    catalogue_content_sha256: str
    received_at: datetime
    reconciliation_evidence_sha256: str


@dataclass(slots=True)
class _DeliveryState:
    lease: PrivateWsGenerationLease
    source: PrivateWsSource
    authority: PrivateWsAuthorityClass
    observation: PrivateWsObservation


@dataclass(slots=True)
class _InvalidationState:
    generation: UUID
    account_id: UUID
    reason: PrivateWsInvalidationReason
    invalidated_at: datetime


_GENERATIONS: WeakKeyDictionary[PrivateWsGenerationLease, _GenerationState] = WeakKeyDictionary()
_SOURCES: WeakKeyDictionary[PrivateWsSource, _SourceState] = WeakKeyDictionary()
_DELIVERIES: WeakKeyDictionary[PrivateWsDelivery, _DeliveryState] = WeakKeyDictionary()
_INVALIDATIONS: WeakKeyDictionary[PrivateWsGenerationInvalidated, _InvalidationState] = (
    WeakKeyDictionary()
)


def _event_error(operation: str) -> ExchangeProtocolError:
    return ExchangeProtocolError(context={"operation": operation, "scope": "private"})


def _generation_state(value: object, *, active: bool = True) -> _GenerationState:
    if type(value) is not PrivateWsGenerationLease:
        raise _event_error("attest_delta_private_ws_generation")
    state = _GENERATIONS.get(value)
    if state is None or (active and not state.active):
        raise _event_error("attest_delta_private_ws_generation")
    return state


def _source_state(value: object) -> _SourceState:
    if type(value) is not PrivateWsSource:
        raise _event_error("attest_delta_private_ws_delivery")
    state = _SOURCES.get(value)
    if state is None:
        raise _event_error("attest_delta_private_ws_delivery")
    _generation_state(state.lease)
    return state


def _delivery_state(value: object) -> _DeliveryState:
    if type(value) is not PrivateWsDelivery:
        raise _event_error("attest_delta_private_ws_delivery")
    state = _DELIVERIES.get(value)
    if state is None:
        raise _event_error("attest_delta_private_ws_delivery")
    _generation_state(state.lease)
    _source_state(state.source)
    return state


def _invalidation_state(value: object) -> _InvalidationState:
    if type(value) is not PrivateWsGenerationInvalidated:
        raise _event_error("attest_delta_private_ws_invalidation")
    state = _INVALIDATIONS.get(value)
    if state is None:
        raise _event_error("attest_delta_private_ws_invalidation")
    return state


def _issue_private_ws_generation(
    *, account: AccountRef, plan: PrivateWsSubscriptionPlan, generation: UUID
) -> PrivateWsGenerationLease:
    if (
        type(account) is not AccountRef
        or type(plan) is not PrivateWsSubscriptionPlan
        or type(generation) is not UUID
        or generation.int == 0
        or account.region is not DeltaRegion.INDIA
        or account.environment is not DeltaEnvironment.TESTNET
    ):
        raise _event_error("issue_delta_private_ws_generation")
    lease = object.__new__(PrivateWsGenerationLease)
    _GENERATIONS[lease] = _GenerationState(
        account=account.model_copy(), plan=plan, generation=UUID(int=generation.int)
    )
    return lease


def _issue_private_ws_source(
    *,
    lease: PrivateWsGenerationLease,
    received_at: datetime,
    reconciliation_evidence_sha256: str,
) -> PrivateWsSource:
    state = _generation_state(lease)
    if (
        type(received_at) is not datetime
        or received_at.tzinfo is not UTC
        or received_at.fold != 0
        or type(reconciliation_evidence_sha256) is not str
        or len(reconciliation_evidence_sha256) != 64
        or any(character not in "0123456789abcdef" for character in reconciliation_evidence_sha256)
        or state.next_sequence >= 2**63
    ):
        raise _event_error("issue_delta_private_ws_source")
    plan = _plan_state(state.plan)
    account = state.account
    source = object.__new__(PrivateWsSource)
    _SOURCES[source] = _SourceState(
        lease=lease,
        account_id=account.account_id,
        external_user_id=account.external_user_id,
        region=account.region,
        environment=account.environment,
        mode=account.mode,
        generation=state.generation,
        local_sequence=state.next_sequence,
        catalogue_snapshot_id=plan.catalogue_snapshot_id,
        catalogue_revision=plan.catalogue_revision,
        catalogue_content_sha256=plan.catalogue_content_sha256,
        received_at=received_at,
        reconciliation_evidence_sha256=reconciliation_evidence_sha256,
    )
    state.next_sequence += 1
    return source


def _issue_private_ws_delivery(
    *,
    lease: PrivateWsGenerationLease,
    authority: PrivateWsAuthorityClass,
    observation: PrivateWsObservation,
    received_at: datetime,
    reconciliation_evidence_sha256: str,
) -> PrivateWsDelivery:
    if type(authority) is not PrivateWsAuthorityClass or type(observation) not in {
        MarginUpdate,
        OrderSnapshot,
        OrderUpdate,
        PositionSnapshot,
        PositionUpdate,
        UserTradeUpdate,
    }:
        raise _event_error("issue_delta_private_ws_delivery")
    source = _issue_private_ws_source(
        lease=lease,
        received_at=received_at,
        reconciliation_evidence_sha256=reconciliation_evidence_sha256,
    )
    delivery = object.__new__(PrivateWsDelivery)
    _DELIVERIES[delivery] = _DeliveryState(
        lease=lease,
        source=source,
        authority=authority,
        observation=observation,
    )
    return delivery


def _invalidate_private_ws_generation(
    *,
    lease: PrivateWsGenerationLease,
    reason: PrivateWsInvalidationReason,
    invalidated_at: datetime,
) -> PrivateWsGenerationInvalidated:
    state = _generation_state(lease)
    if (
        type(reason) is not PrivateWsInvalidationReason
        or type(invalidated_at) is not datetime
        or invalidated_at.tzinfo is not UTC
        or invalidated_at.fold != 0
    ):
        raise _event_error("invalidate_delta_private_ws_generation")
    state.active = False
    invalidation = object.__new__(PrivateWsGenerationInvalidated)
    _INVALIDATIONS[invalidation] = _InvalidationState(
        generation=state.generation,
        account_id=state.account.account_id,
        reason=reason,
        invalidated_at=invalidated_at,
    )
    return invalidation


def attest_private_ws_delivery(value: object) -> PrivateWsDelivery:
    """Return the same delivery only while its exact generation remains current."""
    _delivery_state(value)
    assert isinstance(value, PrivateWsDelivery)
    return value


def attest_private_ws_invalidation(value: object) -> PrivateWsGenerationInvalidated:
    """Attest one sealed terminal control event."""
    _invalidation_state(value)
    assert isinstance(value, PrivateWsGenerationInvalidated)
    return value


__all__ = [
    "PrivateWsAuthorityClass",
    "PrivateWsDelivery",
    "PrivateWsGenerationInvalidated",
    "PrivateWsInvalidationReason",
    "PrivateWsSource",
    "PrivateWsSubscriptionPlan",
    "attest_private_ws_delivery",
    "attest_private_ws_invalidation",
    "private_ws_subscription_plan",
]
