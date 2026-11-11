"""Bounded, read-only Delta Live reconciliation snapshot acquisition."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

import httpx
from pydantic import SecretStr

from hmt_crypto.exchange.delta.signer import DeltaSigner

_ORIGIN = "https://api.india.delta.exchange"
_MAX_BODY = 4 * 1024 * 1024
_MAX_PROJECTION = 1024 * 1024
_ROUTES = (
    ("balances", "/v2/wallet/balances", b""),
    ("positions", "/v2/positions/margined", b""),
    (
        "orders",
        "/v2/orders",
        b"?states=open%2Cpending&contract_types=futures%2Cperpetual_futures&page_size=50",
    ),
    ("fills", "/v2/fills", b"?page_size=50"),
    ("transactions", "/v2/wallet/transactions", b"?page_size=50"),
)


class ReconciliationReadError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PrivateReconciliationEvidence:
    balance_count: int
    position_count: int
    open_order_count: int
    fill_count: int
    wallet_transaction_count: int
    foreign_product_ids: tuple[int, ...]
    complete: bool
    content_sha256: str
    balances_payload: str
    positions_payload: str
    open_orders_payload: str
    fills_payload: str
    wallet_transactions_payload: str


_PROJECTION_FIELDS = {
    "balances": frozenset(
        {"asset_id", "asset_symbol", "available_balance", "balance", "blocked_margin"}
    ),
    "positions": frozenset(
        {
            "product_id",
            "product_symbol",
            "size",
            "entry_price",
            "mark_price",
            "liquidation_price",
            "margin",
            "realized_pnl",
            "realized_funding",
            "unrealized_pnl",
            "margin_mode",
            "updated_at",
        }
    ),
    "orders": frozenset(
        {
            "id",
            "client_order_id",
            "product_id",
            "product_symbol",
            "side",
            "state",
            "order_type",
            "size",
            "unfilled_size",
            "limit_price",
            "stop_price",
            "reduce_only",
            "created_at",
            "updated_at",
        }
    ),
    "fills": frozenset(
        {
            "id",
            "order_id",
            "product_id",
            "product_symbol",
            "side",
            "size",
            "price",
            "commission",
            "fill_type",
            "role",
            "settling_asset_id",
            "settling_asset_symbol",
            "created_at",
        }
    ),
    "transactions": frozenset(
        {
            "id",
            "transaction_type",
            "product_id",
            "asset_id",
            "asset_symbol",
            "amount",
            "balance",
            "created_at",
        }
    ),
}


def _projection_payload(name: str, items: list[object]) -> str:
    fields = _PROJECTION_FIELDS[name]
    projected: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ReconciliationReadError()
        row: dict[str, object] = {}
        for key in fields:
            value = item.get(key)
            if value is not None and type(value) not in {str, int, bool}:
                raise ReconciliationReadError()
            if key in item:
                row[key] = value
        projected.append(row)
    payload = json.dumps(
        projected, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True
    )
    if not 2 <= len(payload.encode("ascii")) <= _MAX_PROJECTION:
        raise ReconciliationReadError()
    return payload


def _items(payload: object) -> tuple[list[object], object]:
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ReconciliationReadError()
    result = payload.get("result")
    if not isinstance(result, list):
        raise ReconciliationReadError()
    return result, payload.get("meta")


async def read_private_reconciliation(
    *,
    api_key: str,
    api_secret: str,
    expected_user_id: str,
    allowed_product_ids: frozenset[int],
    transport: httpx.AsyncBaseTransport | None = None,
) -> PrivateReconciliationEvidence:
    signer = DeltaSigner(
        api_key=SecretStr(api_key),
        api_secret=SecretStr(api_secret),
        user_agent="hmt-crypto-reconciler/1.0",
        clock=lambda: int(time.time()),
    )
    resources: dict[str, list[object]] = {}
    complete = True
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5, read=10, write=5, pool=5),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            for name, path, query in _ROUTES:
                headers = signer.sign_now("GET", path, query_string=query).as_headers()
                headers["Accept"] = "application/json"
                try:
                    response = await client.get(f"{_ORIGIN}{path}{query.decode()}", headers=headers)
                finally:
                    headers.clear()
                body = await response.aread()
                if response.status_code != 200 or len(body) > _MAX_BODY:
                    raise ReconciliationReadError()
                try:
                    payload = json.loads(body)
                except (UnicodeDecodeError, ValueError):
                    raise ReconciliationReadError() from None
                items, meta = _items(payload)
                if name in {"orders", "fills", "transactions"} and (
                    not isinstance(meta, dict) or meta.get("after") is not None
                ):
                    complete = False
                resources[name] = items
    except httpx.HTTPError:
        raise ReconciliationReadError() from None

    foreign: set[int] = set()
    try:
        expected = int(expected_user_id)
    except (TypeError, ValueError):
        raise ReconciliationReadError() from None
    for name, items in resources.items():
        for item in items:
            if not isinstance(item, dict):
                raise ReconciliationReadError()
            user_id = item.get("user_id")
            if user_id is not None and user_id != expected:
                raise ReconciliationReadError()
            if name not in {"balances", "transactions"}:
                product_id = item.get("product_id")
                if type(product_id) is not int or product_id <= 0:
                    raise ReconciliationReadError()
                if product_id not in allowed_product_ids:
                    foreign.add(product_id)
            elif name == "transactions" and item.get("product_id") is not None:
                product_id = item.get("product_id")
                if type(product_id) is not int or product_id <= 0:
                    raise ReconciliationReadError()
                if product_id not in allowed_product_ids:
                    foreign.add(product_id)
    canonical = json.dumps(
        resources, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True
    )
    return PrivateReconciliationEvidence(
        balance_count=len(resources["balances"]),
        position_count=len(resources["positions"]),
        open_order_count=len(resources["orders"]),
        fill_count=len(resources["fills"]),
        wallet_transaction_count=len(resources["transactions"]),
        foreign_product_ids=tuple(sorted(foreign)),
        complete=complete,
        content_sha256=hashlib.sha256(canonical.encode("ascii")).hexdigest(),
        balances_payload=_projection_payload("balances", resources["balances"]),
        positions_payload=_projection_payload("positions", resources["positions"]),
        open_orders_payload=_projection_payload("orders", resources["orders"]),
        fills_payload=_projection_payload("fills", resources["fills"]),
        wallet_transactions_payload=_projection_payload("transactions", resources["transactions"]),
    )


__all__ = [
    "PrivateReconciliationEvidence",
    "ReconciliationReadError",
    "read_private_reconciliation",
]
