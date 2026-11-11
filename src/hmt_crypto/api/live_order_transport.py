"""Canonical Delta Live order wire evidence without network authority."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256

from hmt_crypto.domain.private_orders import OrderSide, OrderType, TimeInForce
from hmt_crypto.errors import OrderRejected
from hmt_crypto.exchange.delta.private_mutation_parsers import parse_place_order_response
from hmt_crypto.exchange.private_requests import PlaceOrderRequest


class LiveOrderPreparationError(Exception):
    """Safe local rejection for an invalid wire preparation request."""


@dataclass(frozen=True, slots=True)
class PreparedLiveOrder:
    request: PlaceOrderRequest
    body: bytes
    body_sha256: str


@dataclass(frozen=True, slots=True)
class LiveOrderResponseOutcome:
    kind: str
    exchange_order_id: int | None


def prepare_live_order(
    *,
    product_id: int,
    contracts: int,
    side: str,
    order_type: str,
    limit_price: Decimal | None,
    reduce_only: bool,
    client_order_id: str,
) -> PreparedLiveOrder:
    """Build the exact documented Delta order body and stable request evidence."""
    try:
        request = PlaceOrderRequest(
            product_id=product_id,
            size=contracts,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            order_type=(OrderType.LIMIT_ORDER if order_type == "limit" else OrderType.MARKET_ORDER),
            limit_price=limit_price,
            time_in_force=TimeInForce.GTC,
            reduce_only=reduce_only,
            client_order_id=client_order_id,
        )
        if side not in {"buy", "sell"} or order_type not in {"limit", "market"}:
            raise ValueError
        if request.time_in_force is None:
            raise ValueError
        body: dict[str, object] = {
            "client_order_id": request.client_order_id,
            "order_type": request.order_type.value,
            "product_id": request.product_id,
            "reduce_only": request.reduce_only,
            "side": request.side.value,
            "size": request.size,
            "time_in_force": request.time_in_force.value,
        }
        if request.limit_price is not None:
            body["limit_price"] = format(request.limit_price, "f")
        encoded = json.dumps(
            body, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True
        ).encode("ascii")
        if len(encoded) > 4096:
            raise ValueError
        return PreparedLiveOrder(
            request=request,
            body=encoded,
            body_sha256=sha256(encoded).hexdigest(),
        )
    except Exception:
        raise LiveOrderPreparationError from None


def classify_live_order_response(
    *,
    prepared: PreparedLiveOrder,
    status_code: int,
    body: bytes,
    external_user_id: str,
) -> LiveOrderResponseOutcome:
    """Classify only proven acceptance/rejection; every other result is uncertain."""
    if (
        type(prepared) is not PreparedLiveOrder
        or type(status_code) is not int
        or type(body) is not bytes
        or len(body) > 1_048_576
        or type(external_user_id) is not str
    ):
        return LiveOrderResponseOutcome("UNCERTAIN", None)
    try:
        order = parse_place_order_response(
            body,
            status_code=status_code,
            request=prepared.request,
            external_user_id=external_user_id,
        )
    except OrderRejected:
        return LiveOrderResponseOutcome("REJECTED", None)
    except Exception:
        return LiveOrderResponseOutcome("UNCERTAIN", None)
    return LiveOrderResponseOutcome("ACCEPTED", order.id)


__all__ = [
    "LiveOrderPreparationError",
    "LiveOrderResponseOutcome",
    "PreparedLiveOrder",
    "classify_live_order_response",
    "prepare_live_order",
]
