"""Exact, inert semantic values for private exchange requests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BeforeValidator, model_validator

from hmt_crypto.domain.account import ExactBool
from hmt_crypto.domain.private_orders import (
    MmpLevel,
    OrderSide,
    OrderType,
    StopTriggerMethod,
    TimeInForce,
)
from hmt_crypto.domain.private_primitives import (
    Contracts,
    ExactAwareUtcDatetime,
    ExactStrEnum,
    ExternalUserText,
    NonZeroPrivateDecimal,
    ObservedDeltaClientOrderId,
    OmsClientOrderId,
    OpaqueCursor,
    PositivePrivateDecimal,
    PrivateBoundaryModel,
    PrivateId,
)
from hmt_crypto.domain.private_wallet import WalletTransactionType

_MAX_PRIVATE_ID = 2**63 - 1


class PrivateContractTypeFilter(ExactStrEnum):
    """Exact contract categories accepted by private order/fill filters."""

    FUTURES = "futures"
    PERPETUAL_FUTURES = "perpetual_futures"
    CALL_OPTIONS = "call_options"
    PUT_OPTIONS = "put_options"


class MarginedPositionContractTypeFilter(ExactStrEnum):
    """Exact contract categories accepted by margined-position filters."""

    PERPETUAL_FUTURES = "perpetual_futures"
    CALL_OPTIONS = "call_options"
    PUT_OPTIONS = "put_options"


class OpenOrderStateFilter(ExactStrEnum):
    """Exact states accepted by the active-order endpoint."""

    OPEN = "open"
    PENDING = "pending"


class OrderTypeFilter(ExactStrEnum):
    """Exact order-type vocabulary accepted by read filters."""

    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"
    ALL_STOP = "all_stop"


def _private_page_size(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 50:
        raise ValueError("page size must be an exact integer from 1 through 50") from None
    return value


PrivatePageSize = Annotated[int, BeforeValidator(_private_page_size)]


def _product_id_tuple(value: object) -> tuple[int, ...]:
    if type(value) is not tuple or tuple.__len__(value) > 10:
        raise ValueError("product IDs require a bounded exact tuple") from None
    owned: list[int] = []
    for index in range(tuple.__len__(value)):
        item = tuple.__getitem__(value, index)
        if type(item) is not int or not 1 <= item <= _MAX_PRIVATE_ID:
            del item, value
            raise ValueError("product IDs require a bounded exact tuple") from None
        if any(item == prior for prior in owned):
            del item, value
            raise ValueError("product IDs require a bounded exact tuple") from None
        owned.append(item)
    del value
    return tuple(item for item in owned)


_ProductIdTuple = Annotated[tuple[PrivateId, ...], BeforeValidator(_product_id_tuple)]


def _enum_tuple_validator(enum_type: type[StrEnum]) -> Callable[[object], tuple[StrEnum, ...]]:
    members = tuple(enum_type)

    def validate(value: object) -> tuple[StrEnum, ...]:
        if type(value) is not tuple:
            raise ValueError("enum filters require an exact tuple") from None
        owned: list[StrEnum] = []
        for index in range(tuple.__len__(value)):
            item = tuple.__getitem__(value, index)
            if type(item) is not enum_type or not any(item is member for member in members):
                del item, value
                raise ValueError("enum filters require canonical members") from None
            if any(item is prior for prior in owned):
                del item, value
                raise ValueError("enum filters require unique members") from None
            owned.append(item)
        del value
        return tuple(item for item in owned)

    return validate


_WalletTransactionTypes = Annotated[
    tuple[WalletTransactionType, ...],
    BeforeValidator(_enum_tuple_validator(WalletTransactionType)),
]
_MarginedContractTypes = Annotated[
    tuple[MarginedPositionContractTypeFilter, ...],
    BeforeValidator(_enum_tuple_validator(MarginedPositionContractTypeFilter)),
]
_OpenOrderStates = Annotated[
    tuple[OpenOrderStateFilter, ...],
    BeforeValidator(_enum_tuple_validator(OpenOrderStateFilter)),
]
_PrivateContractTypes = Annotated[
    tuple[PrivateContractTypeFilter, ...],
    BeforeValidator(_enum_tuple_validator(PrivateContractTypeFilter)),
]
_OrderTypes = Annotated[
    tuple[OrderTypeFilter, ...],
    BeforeValidator(_enum_tuple_validator(OrderTypeFilter)),
]


def _require_cursor_time_window(
    model: PrivateBoundaryModel,
    *,
    start_time: datetime | None,
    end_time: datetime | None,
    after: str | None,
    before: str | None,
) -> None:
    if (after is not None and before is not None) or (
        start_time is not None and end_time is not None and start_time > end_time
    ):
        raise type(model)._safe_error(
            "private_query_window", "invalid private query window"
        ) from None


class WalletTransactionQuery(PrivateBoundaryModel):
    """One bounded wallet-transaction page request."""

    asset_id: PrivateId | None = None
    transaction_types: _WalletTransactionTypes = ()
    start_time: ExactAwareUtcDatetime | None = None
    end_time: ExactAwareUtcDatetime | None = None
    after: OpaqueCursor | None = None
    before: OpaqueCursor | None = None
    page_size: PrivatePageSize = 10

    @model_validator(mode="after")
    def require_reviewed_wallet_filters(self) -> WalletTransactionQuery:
        _require_cursor_time_window(
            self,
            start_time=self.start_time,
            end_time=self.end_time,
            after=self.after,
            before=self.before,
        )
        return self


class MarginedPositionQuery(PrivateBoundaryModel):
    """Exact product and contract filters for current margined positions."""

    product_ids: _ProductIdTuple = ()
    contract_types: _MarginedContractTypes = ()


class LatestPositionQuery(PrivateBoundaryModel):
    """Exact numeric selector for the latest product position."""

    product_id: PrivateId


class OpenOrderQuery(PrivateBoundaryModel):
    """One bounded active-order page request."""

    product_ids: _ProductIdTuple = ()
    states: _OpenOrderStates = ()
    contract_types: _PrivateContractTypes = ()
    order_types: _OrderTypes = ()
    start_time: ExactAwareUtcDatetime | None = None
    end_time: ExactAwareUtcDatetime | None = None
    after: OpaqueCursor | None = None
    before: OpaqueCursor | None = None
    page_size: PrivatePageSize = 10

    @model_validator(mode="after")
    def require_valid_window(self) -> OpenOrderQuery:
        _require_cursor_time_window(
            self,
            start_time=self.start_time,
            end_time=self.end_time,
            after=self.after,
            before=self.before,
        )
        return self


class OrderHistoryQuery(PrivateBoundaryModel):
    """One bounded historical-order page request."""

    product_ids: _ProductIdTuple = ()
    contract_types: _PrivateContractTypes = ()
    order_types: _OrderTypes = ()
    start_time: ExactAwareUtcDatetime | None = None
    end_time: ExactAwareUtcDatetime | None = None
    after: OpaqueCursor | None = None
    before: OpaqueCursor | None = None
    page_size: PrivatePageSize = 10

    @model_validator(mode="after")
    def require_valid_window(self) -> OrderHistoryQuery:
        _require_cursor_time_window(
            self,
            start_time=self.start_time,
            end_time=self.end_time,
            after=self.after,
            before=self.before,
        )
        return self


class FillQuery(PrivateBoundaryModel):
    """One bounded user-fill page request."""

    product_ids: _ProductIdTuple = ()
    contract_types: _PrivateContractTypes = ()
    start_time: ExactAwareUtcDatetime | None = None
    end_time: ExactAwareUtcDatetime | None = None
    after: OpaqueCursor | None = None
    before: OpaqueCursor | None = None
    page_size: PrivatePageSize = 10

    @model_validator(mode="after")
    def require_valid_window(self) -> FillQuery:
        _require_cursor_time_window(
            self,
            start_time=self.start_time,
            end_time=self.end_time,
            after=self.after,
            before=self.before,
        )
        return self


class OrderByIdQuery(PrivateBoundaryModel):
    """Exact numeric order lookup."""

    order_id: PrivateId


class OrderByClientIdQuery(PrivateBoundaryModel):
    """Exact observed-client-ID order lookup."""

    client_order_id: ObservedDeltaClientOrderId


class LeverageQuery(PrivateBoundaryModel):
    """Exact product selector for leverage observation."""

    product_id: PrivateId


class RequestStopOrderType(ExactStrEnum):
    """Request-safe stop categories; system-only response values are absent."""

    STOP_LOSS_ORDER = "stop_loss_order"
    TAKE_PROFIT_ORDER = "take_profit_order"


class RequestMarginMode(ExactStrEnum):
    """Exact margin-mode values admitted by the account mutation request."""

    ISOLATED = "isolated"
    PORTFOLIO = "portfolio"


def _invalid_request(model: PrivateBoundaryModel) -> ValueError:
    return type(model)._safe_error("private_request_value", "invalid private request")


class PlaceOrderRequest(PrivateBoundaryModel):
    """Inert, exact order-placement intent with no dispatch authority."""

    product_id: PrivateId
    size: Contracts
    side: OrderSide
    order_type: OrderType
    limit_price: PositivePrivateDecimal | None = None
    stop_order_type: RequestStopOrderType | None = None
    stop_price: PositivePrivateDecimal | None = None
    trail_amount: PositivePrivateDecimal | None = None
    stop_trigger_method: StopTriggerMethod | None = None
    bracket_stop_trigger_method: StopTriggerMethod | None = None
    bracket_stop_loss_limit_price: PositivePrivateDecimal | None = None
    bracket_stop_loss_price: PositivePrivateDecimal | None = None
    bracket_trail_amount: PositivePrivateDecimal | None = None
    bracket_take_profit_limit_price: PositivePrivateDecimal | None = None
    bracket_take_profit_price: PositivePrivateDecimal | None = None
    time_in_force: TimeInForce | None = None
    mmp: MmpLevel | None = None
    post_only: ExactBool | None = None
    reduce_only: ExactBool | None = None
    client_order_id: OmsClientOrderId | None = None

    @model_validator(mode="after")
    def require_complete_order_intent(self) -> PlaceOrderRequest:
        is_limit = self.order_type is OrderType.LIMIT_ORDER
        if (is_limit and self.limit_price is None) or (
            self.order_type is OrderType.MARKET_ORDER and self.limit_price is not None
        ):
            raise _invalid_request(self) from None

        stop_active = any(
            value is not None
            for value in (
                self.stop_order_type,
                self.stop_price,
                self.trail_amount,
                self.stop_trigger_method,
            )
        )
        stop_complete = (
            self.stop_order_type is not None
            and self.stop_trigger_method is not None
            and (self.stop_price is None) is not (self.trail_amount is None)
        )
        if stop_active and not stop_complete:
            raise _invalid_request(self) from None

        stop_loss_active = any(
            value is not None
            for value in (
                self.bracket_stop_loss_limit_price,
                self.bracket_stop_loss_price,
                self.bracket_trail_amount,
            )
        )
        stop_loss_complete = stop_loss_active and (self.bracket_stop_loss_price is None) is not (
            self.bracket_trail_amount is None
        )
        take_profit_active = (
            self.bracket_take_profit_limit_price is not None
            or self.bracket_take_profit_price is not None
        )
        take_profit_complete = take_profit_active and self.bracket_take_profit_price is not None
        bracket_active = (
            stop_loss_active or take_profit_active or self.bracket_stop_trigger_method is not None
        )
        bracket_complete = (
            bracket_active
            and self.bracket_stop_trigger_method is not None
            and (stop_loss_complete or take_profit_complete)
            and (not stop_loss_active or stop_loss_complete)
            and (not take_profit_active or take_profit_complete)
        )
        if bracket_active and not bracket_complete:
            raise _invalid_request(self) from None
        if stop_active and bracket_active:
            raise _invalid_request(self) from None

        plain_limit_gtc = (
            is_limit
            and self.time_in_force is TimeInForce.GTC
            and not stop_active
            and not bracket_active
        )
        if (self.post_only is not None or self.mmp is not None) and not plain_limit_gtc:
            raise _invalid_request(self) from None
        if self.reduce_only is True and bracket_active:
            raise _invalid_request(self) from None
        return self


class ModifyOrderRequest(PrivateBoundaryModel):
    """Inert exact total-size order modification intent."""

    id: PrivateId
    product_id: PrivateId
    size: Contracts
    limit_price: PositivePrivateDecimal | None = None
    mmp: MmpLevel | None = None
    post_only: ExactBool | None = None
    stop_price: PositivePrivateDecimal | None = None
    trail_amount: PositivePrivateDecimal | None = None

    @model_validator(mode="after")
    def require_one_stop_shape(self) -> ModifyOrderRequest:
        if self.stop_price is not None and self.trail_amount is not None:
            raise _invalid_request(self) from None
        return self


class CancelOrderRequest(PrivateBoundaryModel):
    """Inert numeric order cancellation selector."""

    id: PrivateId
    product_id: PrivateId


class CancelAllRequest(PrivateBoundaryModel):
    """Inert product-scoped cancellation filter with explicit exact flags."""

    product_id: PrivateId
    cancel_limit_orders: ExactBool
    cancel_stop_orders: ExactBool
    cancel_reduce_only_orders: ExactBool

    @model_validator(mode="after")
    def require_selected_order_family(self) -> CancelAllRequest:
        if not (
            self.cancel_limit_orders is True
            or self.cancel_stop_orders is True
            or self.cancel_reduce_only_orders is True
        ):
            raise _invalid_request(self) from None
        return self


class SetLeverageRequest(PrivateBoundaryModel):
    """Inert positive-decimal leverage intent."""

    product_id: PrivateId
    leverage: PositivePrivateDecimal


class SetMarginModeRequest(PrivateBoundaryModel):
    """Inert exact account margin-mode intent."""

    margin_mode: RequestMarginMode
    subaccount_user_id: ExternalUserText


class ChangePositionMarginRequest(PrivateBoundaryModel):
    """Inert signed nonzero isolated-margin adjustment intent."""

    product_id: PrivateId
    delta_margin: NonZeroPrivateDecimal


class BracketStopLossOrder(PrivateBoundaryModel):
    """Exact nested stop-loss leg for an inert bracket intent."""

    order_type: OrderType
    stop_price: PositivePrivateDecimal | None = None
    trail_amount: PositivePrivateDecimal | None = None
    limit_price: PositivePrivateDecimal | None = None

    @model_validator(mode="after")
    def require_complete_stop_loss_leg(self) -> BracketStopLossOrder:
        if (self.stop_price is None) is (self.trail_amount is None):
            raise _invalid_request(self) from None
        if self.order_type is OrderType.LIMIT_ORDER:
            if self.limit_price is None:
                raise _invalid_request(self) from None
        elif self.order_type is OrderType.MARKET_ORDER and self.limit_price is not None:
            raise _invalid_request(self) from None
        return self


class BracketTakeProfitOrder(PrivateBoundaryModel):
    """Exact nested take-profit leg for an inert bracket intent."""

    order_type: OrderType
    stop_price: PositivePrivateDecimal
    limit_price: PositivePrivateDecimal | None = None

    @model_validator(mode="after")
    def require_complete_take_profit_leg(self) -> BracketTakeProfitOrder:
        if self.order_type is OrderType.LIMIT_ORDER:
            if self.limit_price is None:
                raise _invalid_request(self) from None
        elif self.order_type is OrderType.MARKET_ORDER and self.limit_price is not None:
            raise _invalid_request(self) from None
        return self


class CreateBracketRequest(PrivateBoundaryModel):
    """Inert explicit create-bracket intent."""

    product_id: PrivateId
    stop_loss_order: BracketStopLossOrder | None = None
    take_profit_order: BracketTakeProfitOrder | None = None
    bracket_stop_trigger_method: StopTriggerMethod

    @model_validator(mode="after")
    def require_at_least_one_leg(self) -> CreateBracketRequest:
        if self.stop_loss_order is None and self.take_profit_order is None:
            raise _invalid_request(self) from None
        return self


class EditBracketRequest(PrivateBoundaryModel):
    """Inert positive-replacement bracket edit intent."""

    id: PrivateId
    product_id: PrivateId
    bracket_stop_loss_limit_price: PositivePrivateDecimal | None = None
    bracket_stop_loss_price: PositivePrivateDecimal | None = None
    bracket_take_profit_limit_price: PositivePrivateDecimal | None = None
    bracket_take_profit_price: PositivePrivateDecimal | None = None
    bracket_trail_amount: PositivePrivateDecimal | None = None
    bracket_stop_trigger_method: StopTriggerMethod | None = None

    @model_validator(mode="after")
    def require_actual_nonconflicting_edit(self) -> EditBracketRequest:
        values = (
            self.bracket_stop_loss_limit_price,
            self.bracket_stop_loss_price,
            self.bracket_take_profit_limit_price,
            self.bracket_take_profit_price,
            self.bracket_trail_amount,
            self.bracket_stop_trigger_method,
        )
        if not any(value is not None for value in values) or (
            self.bracket_stop_loss_price is not None and self.bracket_trail_amount is not None
        ):
            raise _invalid_request(self) from None
        return self


__all__ = [
    "BracketStopLossOrder",
    "BracketTakeProfitOrder",
    "CancelAllRequest",
    "CancelOrderRequest",
    "ChangePositionMarginRequest",
    "CreateBracketRequest",
    "EditBracketRequest",
    "FillQuery",
    "LatestPositionQuery",
    "LeverageQuery",
    "MarginedPositionContractTypeFilter",
    "MarginedPositionQuery",
    "OpenOrderQuery",
    "OpenOrderStateFilter",
    "ModifyOrderRequest",
    "OrderByClientIdQuery",
    "OrderByIdQuery",
    "OrderHistoryQuery",
    "OrderTypeFilter",
    "PlaceOrderRequest",
    "PrivateContractTypeFilter",
    "RequestMarginMode",
    "RequestStopOrderType",
    "SetLeverageRequest",
    "SetMarginModeRequest",
    "WalletTransactionQuery",
]
