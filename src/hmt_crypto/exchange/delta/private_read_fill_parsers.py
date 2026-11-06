"""Strict Delta India fill-page parser and reviewed metadata profiles."""

from __future__ import annotations

from math import isfinite
from typing import cast

from pydantic import TypeAdapter

from hmt_crypto.domain import private_orders as orders
from hmt_crypto.domain.private_primitives import (
    PrivateId,
    exact_enum_member_for_literal,
    parse_position_iso_datetime,
    parse_private_decimal_text,
)
from hmt_crypto.exchange.delta.private_read_parsers import (
    _exact_object,
    _nonnegative_integer,
    _object_with_optional,
    _observed,
    _parse_bounded_list,
    _parse_cursor_meta,
    _private_id,
    _protocol,
    _text,
)
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId

_FILL_FIELDS = (
    "id",
    "size",
    "notional",
    "product",
    "fill_type",
    "side",
    "price",
    "role",
    "commission",
    "created_at",
    "product_id",
    "product_symbol",
    "order_id",
    "settling_asset_id",
    "settling_asset_symbol",
)
_PRODUCT_FIELDS = (
    "id",
    "symbol",
    "contract_type",
    "tick_size",
    "contract_value",
    "contract_unit_currency",
    "notional_type",
    "quoting_asset",
    "underlying_asset",
    "settling_asset",
    "spot_index",
)
_ASSET_FIELDS = ("symbol", "precision", "minimum_precision")
_META_TEXT_FIELDS = frozenset(
    {
        "commission_deto",
        "commission_deto_in_settling_asset",
        "effective_commission_rate",
        "liquidation_fee_deto",
        "liquidation_fee_deto_in_settling_asset",
        "order_price",
        "order_size",
        "order_type",
        "order_unfilled_size",
        "tfc_used_for_commission",
        "tfc_used_for_liquidation_fee",
        "total_commission_in_settling_asset",
        "total_liquidation_fee_in_settling_asset",
        "liquidation_fee_in_settling_asset",
        "mark",
        "rack_commision",
        "spot",
        "trading_fee_credits_used",
        "original_referrer_commission",
    }
)
_META_ALL_FIELDS = _META_TEXT_FIELDS | frozenset(
    {"ip", "source", "is_commission_on_notional", "cut", "new_position"}
)
_META_ORDER_FIELDS = frozenset({"order_price", "order_size", "order_type", "order_unfilled_size"})
_META_COMMISSION_FIELDS = frozenset(
    {
        "commission_deto",
        "commission_deto_in_settling_asset",
        "effective_commission_rate",
        "tfc_used_for_commission",
        "total_commission_in_settling_asset",
        "trading_fee_credits_used",
        "original_referrer_commission",
    }
)
_SETTLEMENT_FIELDS = _META_ORDER_FIELDS | _META_COMMISSION_FIELDS | {"new_position"}
_OTC_FIELDS = frozenset(
    {
        "order_price",
        "order_size",
        "order_type",
        "commission_deto",
        "commission_deto_in_settling_asset",
        "effective_commission_rate",
        "original_referrer_commission",
        "trading_fee_credits_used",
        "cut",
    }
)
_NEW_POSITION_FIELDS = frozenset(
    {
        "size",
        "margin",
        "entry_price",
        "liquidation_price",
        "bankruptcy_price",
        "commission",
        "total_commission_paid",
        "realized_pnl",
        "realized_cashflow",
        "realized_funding",
        "last_fill_uuid",
    }
)


def _asset(value: object) -> orders.FillAssetSummary:
    row = _exact_object(value, _ASSET_FIELDS)
    return orders.FillAssetSummary(
        symbol=_text(row["symbol"]),
        precision=_nonnegative_integer(row["precision"]),
        minimum_precision=_nonnegative_integer(row["minimum_precision"]),
    )


def _product(value: object) -> orders.FillProductSummary:
    row = _exact_object(value, _PRODUCT_FIELDS)
    spot = _exact_object(row["spot_index"], ("symbol",))
    return orders.FillProductSummary(
        id=_private_id(row["id"]),
        symbol=_text(row["symbol"]),
        contract_type=_observed(
            row["contract_type"],
            orders.FillContractType,
            orders.ObservedFillContractType,
        ),
        tick_size=parse_private_decimal_text(row["tick_size"]),
        contract_value=parse_private_decimal_text(row["contract_value"]),
        contract_unit_currency=_text(row["contract_unit_currency"]),
        notional_type=_observed(
            row["notional_type"],
            orders.FillNotionalType,
            orders.ObservedFillNotionalType,
        ),
        quoting_asset=_asset(row["quoting_asset"]),
        underlying_asset=_asset(row["underlying_asset"]),
        settling_asset=_asset(row["settling_asset"]),
        spot_index=orders.FillSpotIndexSummary(symbol=_text(spot["symbol"])),
    )


def _validate_cut(value: object) -> None:
    row = _exact_object(value, ("equity", "api_trader"))
    for item in row.values():
        if type(item) is int:
            continue
        if type(item) is not float or not isfinite(item):
            raise ValueError


def _validate_new_position(value: object) -> None:
    if (
        type(value) is not dict
        or not value
        or any(type(key) is not str or key not in _NEW_POSITION_FIELDS for key in value)
    ):
        raise ValueError
    for key, item in value.items():
        if key == "size":
            if type(item) is not int:
                raise ValueError
        elif key == "last_fill_uuid":
            orders.parse_compact_fill_uuid(item)
        elif key in ("liquidation_price", "bankruptcy_price") and item is None:
            continue
        else:
            parse_private_decimal_text(item)


def _metadata(value: object, fill_type: orders.FillType) -> orders.FillMetadataValue:
    if type(value) is not dict or not value or any(type(key) is not str for key in value):
        raise ValueError
    keys = frozenset(value)
    if fill_type in (orders.FillType.NORMAL, orders.FillType.LIQUIDATION):
        allowed = _META_ALL_FIELDS
        required = _META_ALL_FIELDS
        profile = (
            orders.FillMetadataProfile.NORMAL_V1
            if fill_type is orders.FillType.NORMAL
            else orders.FillMetadataProfile.LIQUIDATION_V1
        )
    elif fill_type is orders.FillType.ADL:
        allowed = _META_ALL_FIELDS
        required = _META_ALL_FIELDS - {"cut"}
        profile = orders.FillMetadataProfile.ADL_V1
    elif fill_type is orders.FillType.SETTLEMENT:
        allowed = _SETTLEMENT_FIELDS
        required = _META_ORDER_FIELDS | _META_COMMISSION_FIELDS | {"new_position"}
        profile = orders.FillMetadataProfile.SETTLEMENT_V1
    elif fill_type is orders.FillType.OTC:
        allowed = _OTC_FIELDS
        required = _OTC_FIELDS - {"cut"}
        profile = orders.FillMetadataProfile.OTC_V1
    else:
        raise ValueError
    if not required <= keys or not keys <= allowed:
        raise ValueError
    if fill_type is orders.FillType.ADL and value.get("order_type") != "adl_order":
        raise ValueError
    for key, item in value.items():
        if key in _META_TEXT_FIELDS:
            if type(item) is not str:
                raise ValueError
        elif key in ("ip", "source"):
            if (fill_type is orders.FillType.ADL and item is not None) or (
                fill_type is not orders.FillType.ADL and type(item) is not str
            ):
                raise ValueError
        elif key == "is_commission_on_notional":
            if type(item) is not bool:
                raise ValueError
        elif key == "cut":
            _validate_cut(item)
        elif key == "new_position":
            _validate_new_position(item)
        else:
            raise ValueError
    return orders.FillMetadataValue(profile=profile)


def _row(
    value: object,
    allowed_product_ids: tuple[PrivateId, ...] | None,
    allowed_contract_types: tuple[str, ...] | None,
) -> orders.Fill:
    row = _object_with_optional(value, _FILL_FIELDS, ("meta_data",))
    product_id = _private_id(row["product_id"])
    if allowed_product_ids is not None and product_id not in allowed_product_ids:
        raise ValueError
    product = _product(row["product"])
    if (
        allowed_contract_types is not None
        and product.contract_type.raw not in allowed_contract_types
    ):
        raise ValueError
    fill_type = exact_enum_member_for_literal(orders.FillType, row["fill_type"])
    if fill_type is None:
        raise ValueError
    order_id: orders.NumericFillOrderRef | orders.SettlementFillOrderRef
    if fill_type is orders.FillType.SETTLEMENT:
        order_id = orders.SettlementFillOrderRef(
            kind="SETTLEMENT",
            profile=orders.SettlementFillOrderProfile.COMPACT_UUID_V1,
            order_uuid=orders.parse_compact_fill_uuid(row["order_id"]),
        )
    else:
        order_id = orders.parse_numeric_fill_order_ref(row["order_id"])
    if "meta_data" not in row:
        metadata_presence = TypeAdapter(orders.FillMetadataPresence).validate_python(
            {"kind": "MISSING"}
        )
    elif row["meta_data"] is None:
        metadata_presence = TypeAdapter(orders.FillMetadataPresence).validate_python(
            {"kind": "NULL"}
        )
    else:
        metadata_presence = cast(
            orders.FillMetadataPresence,
            orders.FillMetadataValueField(
                kind="VALUE", value=_metadata(row["meta_data"], fill_type)
            ),
        )
    return orders.Fill(
        id=orders.parse_compact_fill_uuid(row["id"]),
        size=parse_private_decimal_text(row["size"]),
        notional=parse_private_decimal_text(row["notional"]),
        product=product,
        fill_type=orders.ObservedFillType(raw=fill_type.value, known=fill_type),
        side=_observed(row["side"], orders.OrderSide, orders.ObservedOrderSide),
        price=parse_private_decimal_text(row["price"]),
        role=_observed(row["role"], orders.FillRole, orders.ObservedFillRole),
        commission=parse_private_decimal_text(row["commission"]),
        created_at=parse_position_iso_datetime(row["created_at"]),
        product_id=product_id,
        product_symbol=_text(row["product_symbol"]),
        order_id=order_id,
        settling_asset_id=_private_id(row["settling_asset_id"]),
        settling_asset_symbol=_text(row["settling_asset_symbol"]),
        meta_data=metadata_presence,
    )


def _parse_fill_page_payload(
    result: object,
    meta: object,
    *,
    allowed_product_ids: tuple[PrivateId, ...] | None,
    allowed_contract_types: tuple[str, ...] | None,
    page_size: int,
) -> tuple[tuple[orders.Fill, ...], str | None, str | None]:
    """Parse one documented fill cursor page with type-specific metadata caps."""
    endpoint_id = PrivateRouteId.FILLS
    items = _parse_bounded_list(
        result,
        page_size=page_size,
        endpoint_id=endpoint_id,
        parser=lambda item: _row(item, allowed_product_ids, allowed_contract_types),
    )
    after, before = _parse_cursor_meta(meta, endpoint_id=endpoint_id)
    if not items and (after is not None or before is not None):
        raise _protocol(endpoint_id)
    return items, after, before


__all__: list[str] = []
