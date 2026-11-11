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
_ROUTES = (
    ("balances", "/v2/wallet/balances", b""),
    ("positions", "/v2/positions/margined", b""),
    (
        "orders",
        "/v2/orders",
        b"?states=open%2Cpending&contract_types=futures%2Cperpetual_futures&page_size=50",
    ),
)


class ReconciliationReadError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PrivateReconciliationEvidence:
    balance_count: int
    position_count: int
    open_order_count: int
    foreign_product_ids: tuple[int, ...]
    complete: bool
    content_sha256: str


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
                if name == "orders" and isinstance(meta, dict) and meta.get("after") is not None:
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
            if name != "balances":
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
        foreign_product_ids=tuple(sorted(foreign)),
        complete=complete,
        content_sha256=hashlib.sha256(canonical.encode("ascii")).hexdigest(),
    )


__all__ = [
    "PrivateReconciliationEvidence",
    "ReconciliationReadError",
    "read_private_reconciliation",
]
