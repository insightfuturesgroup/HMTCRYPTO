"""Exchange-neutral private mutation and account-stream boundaries."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from enum import StrEnum
from typing import Protocol

from pydantic import field_validator

from hmt_crypto.domain import private_orders as orders
from hmt_crypto.domain import private_positions as positions
from hmt_crypto.domain.models import DomainModel
from hmt_crypto.exchange.private import PrivateReadClient
from hmt_crypto.exchange.private_requests import (
    CancelAllRequest,
    CancelOrderRequest,
    ChangePositionMarginRequest,
    CreateBracketRequest,
    EditBracketRequest,
    ModifyOrderRequest,
    PlaceOrderRequest,
    SetLeverageRequest,
    SetMarginModeRequest,
)


class PrivateComponent(StrEnum):
    """Exchange-neutral account-state stream components."""

    ORDERS = "orders"
    FILLS = "fills"
    POSITIONS = "positions"
    MARGINS = "margins"


class PrivateSubscriptionRequest(DomainModel):
    """One bounded account-stream request selected by numeric products."""

    product_ids: tuple[int, ...]
    components: tuple[PrivateComponent, ...]

    @field_validator("product_ids", mode="before")
    @classmethod
    def validate_products(cls, value: object) -> tuple[int, ...]:
        if (
            type(value) is not tuple
            or not 1 <= len(value) <= 10
            or any(type(item) is not int or not 1 <= item < 2**63 for item in value)
            or len(set(value)) != len(value)
        ):
            raise ValueError("private subscription products are invalid")
        return tuple(value)

    @field_validator("components", mode="before")
    @classmethod
    def validate_components(cls, value: object) -> tuple[PrivateComponent, ...]:
        if (
            type(value) is not tuple
            or not value
            or any(type(item) is not PrivateComponent for item in value)
            or len(set(value)) != len(value)
        ):
            raise ValueError("private subscription components are invalid")
        return tuple(value)


class PrivateEventStream(Protocol):
    """Read-only normalized private observation stream."""

    def __aiter__(self) -> AsyncIterator[object]: ...


class TradingClient(Protocol):
    """Mutation surface available only to the risk-gated OMS worker."""

    async def place_order(self, request: PlaceOrderRequest) -> orders.Order: ...

    async def modify_order(self, request: ModifyOrderRequest) -> orders.Order: ...

    async def cancel_order(self, request: CancelOrderRequest) -> orders.Order: ...

    async def cancel_all(self, request: CancelAllRequest) -> orders.MutationAcknowledgement: ...

    async def set_leverage(self, request: SetLeverageRequest) -> orders.Leverage: ...

    async def set_margin_mode(self, request: SetMarginModeRequest) -> orders.MarginModeResult: ...

    async def change_position_margin(
        self, request: ChangePositionMarginRequest
    ) -> positions.MarginedPosition: ...

    async def create_bracket(
        self, request: CreateBracketRequest
    ) -> orders.MutationAcknowledgement: ...

    async def edit_bracket(self, request: EditBracketRequest) -> orders.MutationAcknowledgement: ...

    async def subscribe_private(
        self,
        request: PrivateSubscriptionRequest,
    ) -> AbstractAsyncContextManager[PrivateEventStream]: ...


class AccountTradingClient(PrivateReadClient, TradingClient, Protocol):
    """Combined exchange-neutral private account client for one exact account."""


__all__ = [
    "AccountTradingClient",
    "PrivateComponent",
    "PrivateEventStream",
    "PrivateSubscriptionRequest",
    "TradingClient",
]
