"""Pure parsers for the reviewed Delta India private-read payloads."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast

from pydantic import TypeAdapter

from hmt_crypto.domain import private_orders as orders
from hmt_crypto.domain import private_positions as positions
from hmt_crypto.domain import private_wallet as wallet
from hmt_crypto.domain.private_primitives import (
    ExternalUserText,
    OpaqueCursor,
    PrivateId,
    exact_enum_member_for_literal,
    parse_position_iso_datetime,
    parse_private_decimal_text,
)
from hmt_crypto.errors import ExchangeProtocolError
from hmt_crypto.exchange.delta.private_payload_parsers import (
    _parse_leverage_payload,
    _parse_margined_position_payload,
    _parse_order_payload,
)
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId

T = TypeVar("T")
_USER = TypeAdapter(ExternalUserText)
_PRIVATE_ID = TypeAdapter(PrivateId)
_CURSOR = TypeAdapter(OpaqueCursor)

_WALLET_TRANSACTION_FIELDS = (
    "id",
    "amount",
    "balance",
    "transaction_type",
    "meta_data",
    "product_id",
    "asset_id",
    "asset_symbol",
    "created_at",
)
_BALANCE_FIELDS = (
    "asset_id",
    "asset_symbol",
    "available_balance",
    "available_balance_for_robo",
    "balance",
    "blocked_margin",
    "commission",
    "cross_asset_liability",
    "cross_commission",
    "cross_locked_collateral",
    "cross_order_margin",
    "cross_position_margin",
    "id",
    "interest_credit",
    "order_margin",
    "pending_referral_bonus",
    "pending_trading_fee_credit",
    "portfolio_margin",
    "position_margin",
    "trading_fee_credit",
    "unvested_amount",
    "user_id",
)
_BALANCE_DECIMALS = (
    "available_balance",
    "available_balance_for_robo",
    "balance",
    "blocked_margin",
    "commission",
    "cross_asset_liability",
    "cross_commission",
    "cross_locked_collateral",
    "cross_order_margin",
    "cross_position_margin",
    "interest_credit",
    "order_margin",
    "pending_referral_bonus",
    "pending_trading_fee_credit",
    "portfolio_margin",
    "position_margin",
    "trading_fee_credit",
    "unvested_amount",
)


def _protocol(endpoint_id: PrivateRouteId) -> ExchangeProtocolError:
    return ExchangeProtocolError(context={"operation": endpoint_id.value})


def _exact_object(value: object, fields: tuple[str, ...]) -> dict[str, object]:
    if (
        type(value) is not dict
        or len(value) != len(fields)
        or any(field not in value for field in fields)
    ):
        raise ValueError
    return value


def _object_with_optional(
    value: object,
    required: tuple[str, ...],
    optional: tuple[str, ...],
) -> dict[str, object]:
    if type(value) is not dict or any(field not in value for field in required):
        raise ValueError
    allowed = required + optional
    if any(type(key) is not str or key not in allowed for key in value):
        raise ValueError
    return value


def _wire_user(value: object, expected_user_id: object) -> str:
    expected = _USER.validate_python(expected_user_id)
    if type(value) is not int or not 1 <= value <= 2**63 - 1 or str(value) != expected:
        raise ValueError
    return expected


def _private_id(value: object) -> int:
    if type(value) is not int:
        raise ValueError
    return _PRIVATE_ID.validate_python(value)


def _text(value: object) -> str:
    if type(value) is not str:
        raise ValueError
    return value


def _observed(value: object, enum: Any, model: Any) -> Any:
    if type(value) is not str:
        raise ValueError
    return model(raw=value, known=exact_enum_member_for_literal(enum, value))


def _nullable_private_id(value: object) -> int | None:
    return None if value is None else _private_id(value)


def _nonnegative_integer(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError
    return value


def _parse_wallet_transaction_row(
    value: object,
    *,
    allowed_asset_id: PrivateId | None,
    allowed_transaction_types: tuple[str, ...] | None,
) -> wallet.WalletTransaction:
    required = tuple(field for field in _WALLET_TRANSACTION_FIELDS if field != "meta_data")
    row = _object_with_optional(value, required, ("meta_data",))
    asset_id = _private_id(row["asset_id"])
    if allowed_asset_id is not None and asset_id != _private_id(allowed_asset_id):
        raise ValueError
    transaction_type = _observed(
        row["transaction_type"],
        wallet.WalletTransactionType,
        wallet.ObservedWalletTransactionType,
    )
    if (
        allowed_transaction_types is not None
        and transaction_type.raw not in allowed_transaction_types
    ):
        raise ValueError
    if "meta_data" not in row:
        metadata_presence: wallet.WalletTransactionMetadataPresence = TypeAdapter(
            wallet.WalletTransactionMetadataPresence
        ).validate_python({"kind": "MISSING"})
    elif row["meta_data"] is None:
        metadata_presence = TypeAdapter(wallet.WalletTransactionMetadataPresence).validate_python(
            {"kind": "NULL"}
        )
    else:
        metadata = row["meta_data"]
        if type(metadata) is not dict or metadata:
            raise ValueError
        metadata_presence = cast(
            wallet.WalletTransactionMetadataPresence,
            wallet.WalletTransactionMetadataValueField(
                kind="VALUE",
                value=wallet.WalletTransactionMetadataValue(
                    profile=wallet.WalletTransactionMetadataProfile.EMPTY_OBJECT
                ),
            ),
        )
    return wallet.WalletTransaction(
        id=_private_id(row["id"]),
        amount=parse_private_decimal_text(row["amount"]),
        balance=parse_private_decimal_text(row["balance"]),
        transaction_type=transaction_type,
        meta_data=metadata_presence,
        product_id=_nullable_private_id(row["product_id"]),
        asset_id=asset_id,
        asset_symbol=_text(row["asset_symbol"]),
        created_at=parse_position_iso_datetime(row["created_at"]),
    )


def _parse_wallet_transaction_page_payload(
    result: object,
    meta: object,
    *,
    allowed_asset_id: PrivateId | None,
    allowed_transaction_types: tuple[str, ...] | None,
    page_size: int,
) -> tuple[tuple[wallet.WalletTransaction, ...], str | None, str | None]:
    """Parse one documented wallet-transaction cursor page."""
    endpoint_id = PrivateRouteId.TRANSACTIONS
    items = _parse_bounded_list(
        result,
        page_size=page_size,
        endpoint_id=endpoint_id,
        parser=lambda item: _parse_wallet_transaction_row(
            item,
            allowed_asset_id=allowed_asset_id,
            allowed_transaction_types=allowed_transaction_types,
        ),
    )
    after, before = _parse_cursor_meta(meta, endpoint_id=endpoint_id)
    if not items and (after is not None or before is not None):
        raise _protocol(endpoint_id)
    return items, after, before


def _parse_balance_row(value: object, *, expected_user_id: object) -> wallet.Balance:
    row = _exact_object(value, _BALANCE_FIELDS)
    payload: dict[str, object] = {
        "asset_id": _private_id(row["asset_id"]),
        "asset_symbol": row["asset_symbol"],
        "id": _private_id(row["id"]),
        "user_id": _wire_user(row["user_id"], expected_user_id),
    }
    for name in _BALANCE_DECIMALS:
        payload[name] = parse_private_decimal_text(row[name])
    return wallet.Balance.model_validate(payload)


def _parse_balance_snapshot_payload(
    result: object,
    meta: object,
    *,
    expected_user_id: ExternalUserText,
) -> wallet.BalanceSnapshot:
    """Parse the documented balances array plus signed equity metadata."""
    try:
        if type(result) is not list or len(result) > 256:
            raise ValueError
        equity_meta = _exact_object(meta, ("net_equity", "robo_trading_equity"))
        balances = tuple(
            _parse_balance_row(item, expected_user_id=expected_user_id) for item in result
        )
        return wallet.BalanceSnapshot(
            balances=balances,
            net_equity=parse_private_decimal_text(equity_meta["net_equity"]),
            robo_trading_equity=parse_private_decimal_text(equity_meta["robo_trading_equity"]),
        )
    except Exception:
        raise _protocol(PrivateRouteId.BALANCES) from None


def _parse_latest_position_payload(
    result: object,
    *,
    expected_product_id: PrivateId,
) -> positions.LatestPosition:
    """Parse the two-field latest-position result and bind its requested product."""
    try:
        row = _exact_object(result, ("size", "entry_price"))
        product_id = _private_id(expected_product_id)
        if type(row["size"]) is not int:
            raise ValueError
        return positions.LatestPosition(
            product_id=product_id,
            signed_contracts=row["size"],
            entry_price=parse_private_decimal_text(row["entry_price"]),
        )
    except Exception:
        raise _protocol(PrivateRouteId.LATEST_POSITION) from None


def _cursor(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError
    return _CURSOR.validate_python(value)


def _parse_cursor_meta(
    meta: object,
    *,
    endpoint_id: PrivateRouteId,
) -> tuple[str | None, str | None]:
    """Parse both explicit cursor fields without inferring missing metadata."""
    try:
        row = _exact_object(meta, ("after", "before"))
        return _cursor(row["after"]), _cursor(row["before"])
    except Exception:
        raise _protocol(endpoint_id) from None


def _parse_bounded_list(
    result: object,
    *,
    page_size: int,
    parser: Callable[[object], T],
    endpoint_id: PrivateRouteId,
) -> tuple[T, ...]:
    try:
        if type(page_size) is not int or not 1 <= page_size <= 50:
            raise ValueError
        if type(result) is not list or len(result) > page_size:
            raise ValueError
        return tuple(parser(item) for item in result)
    except ExchangeProtocolError:
        raise
    except Exception:
        raise _protocol(endpoint_id) from None


def _parse_order_page_payload(
    result: object,
    meta: object,
    *,
    endpoint_id: PrivateRouteId,
    expected_user_id: ExternalUserText,
    allowed_product_ids: tuple[PrivateId, ...] | None,
    page_size: int,
) -> tuple[tuple[orders.Order, ...], str | None, str | None]:
    """Parse one complete reviewed order page and its explicit cursors."""
    if endpoint_id not in (PrivateRouteId.OPEN_ORDERS, PrivateRouteId.ORDER_HISTORY):
        raise _protocol(endpoint_id)
    items = _parse_bounded_list(
        result,
        page_size=page_size,
        endpoint_id=endpoint_id,
        parser=lambda item: _parse_order_payload(
            item,
            endpoint_id=endpoint_id,
            expected_user_id=expected_user_id,
            allowed_product_ids=allowed_product_ids,
        ),
    )
    after, before = _parse_cursor_meta(meta, endpoint_id=endpoint_id)
    if not items and (after is not None or before is not None):
        raise _protocol(endpoint_id)
    return items, after, before


def _parse_margined_position_list_payload(
    result: object,
    *,
    expected_user_id: ExternalUserText,
    allowed_product_ids: tuple[PrivateId, ...] | None,
) -> tuple[positions.MarginedPosition, ...]:
    """Parse the bounded potentially-delayed margined-position list."""
    endpoint_id = PrivateRouteId.MARGINED_POSITIONS
    try:
        if type(result) is not list or len(result) > 4096:
            raise ValueError
        return tuple(
            _parse_margined_position_payload(
                item,
                endpoint_id=endpoint_id,
                expected_user_id=expected_user_id,
                allowed_product_ids=allowed_product_ids,
            )
            for item in result
        )
    except ExchangeProtocolError:
        raise
    except Exception:
        raise _protocol(endpoint_id) from None


def _parse_order_lookup_payload(
    result: object,
    *,
    endpoint_id: PrivateRouteId,
    expected_user_id: ExternalUserText,
    expected_order_id: PrivateId | None = None,
    expected_client_order_id: str | None = None,
) -> orders.Order:
    """Parse one lookup row and bind the selector when the response carries it."""
    if endpoint_id not in (PrivateRouteId.ORDER_BY_ID, PrivateRouteId.ORDER_BY_CLIENT_ID):
        raise _protocol(endpoint_id)
    parsed = _parse_order_payload(
        result,
        endpoint_id=endpoint_id,
        expected_user_id=expected_user_id,
        allowed_product_ids=None,
    )
    if expected_order_id is not None and parsed.id != expected_order_id:
        raise _protocol(endpoint_id)
    if expected_client_order_id is not None:
        client_id: object = parsed.client_order_id
        if (
            type(client_id) is not orders.ObservedDeltaClientOrderIdValueField
            or object.__getattribute__(client_id, "kind") != "VALUE"
            or object.__getattribute__(client_id, "value") != expected_client_order_id
        ):
            raise _protocol(endpoint_id)
    return parsed


def _parse_leverage_read_payload(
    result: object,
    *,
    expected_product_id: PrivateId,
) -> orders.Leverage:
    """Parse one GET leverage result using the shared reviewed row grammar."""
    return _parse_leverage_payload(
        result,
        endpoint_id=PrivateRouteId.GET_LEVERAGE,
        expected_product_id=expected_product_id,
    )


__all__: list[str] = []
