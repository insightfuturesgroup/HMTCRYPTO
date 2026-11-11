import json

import httpx
import pytest

from hmt_crypto.api.delta_reconciliation import (
    ReconciliationReadError,
    read_private_reconciliation,
)


@pytest.mark.asyncio
async def test_private_reconciliation_requires_complete_known_account_state() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("balances"):
            result = [{"user_id": 71, "asset_id": 5}]
            meta = None
        elif request.url.path.endswith("margined"):
            result = [{"user_id": 71, "product_id": 27}]
            meta = None
        elif request.url.path.endswith("orders"):
            result = [{"user_id": 71, "product_id": 27}]
            meta = {"after": None}
        elif request.url.path.endswith("fills"):
            result = [{"user_id": 71, "product_id": 27, "id": "fill-1"}]
            meta = {"after": None}
        else:
            result = [{"user_id": 71, "product_id": 27, "id": 8}]
            meta = {"after": None}
        return httpx.Response(200, json={"success": True, "result": result, "meta": meta})

    evidence = await read_private_reconciliation(
        api_key="key-12345678",
        api_secret="secret-1234567890",
        expected_user_id="71",
        allowed_product_ids=frozenset({27}),
        transport=httpx.MockTransport(handler),
    )
    assert evidence.complete
    assert evidence.foreign_product_ids == ()
    assert (evidence.balance_count, evidence.position_count, evidence.open_order_count) == (1, 1, 1)
    assert "user_id" not in evidence.balances_payload
    assert json.loads(evidence.positions_payload) == [{"product_id": 27}]
    assert evidence.fill_count == 1
    assert evidence.wallet_transaction_count == 1


@pytest.mark.asyncio
async def test_private_reconciliation_reports_foreign_products_and_pagination() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        result = [{"user_id": 71, "asset_id": 5}]
        meta = None
        if not request.url.path.endswith("balances"):
            result = [{"user_id": 71, "product_id": 99}]
        if request.url.path.endswith("orders"):
            if request.url.params.get("after") is None:
                meta = {"after": "page two"}
            else:
                result = [{"user_id": 71, "product_id": 99, "id": "second"}]
                meta = {"after": None}
        elif request.url.path.endswith(("fills", "transactions")):
            meta = {"after": None}
        return httpx.Response(200, json={"success": True, "result": result, "meta": meta})

    evidence = await read_private_reconciliation(
        api_key="key-12345678",
        api_secret="secret-1234567890",
        expected_user_id="71",
        allowed_product_ids=frozenset({27}),
        transport=httpx.MockTransport(handler),
    )
    assert evidence.complete
    assert evidence.foreign_product_ids == (99,)
    assert evidence.open_order_count == 2


@pytest.mark.asyncio
async def test_private_reconciliation_rejects_cursor_cycles() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(("orders", "fills", "transactions")):
            meta = {"after": "same-cursor"}
        else:
            meta = None
        return httpx.Response(200, json={"success": True, "result": [], "meta": meta})

    with pytest.raises(ReconciliationReadError):
        await read_private_reconciliation(
            api_key="key-12345678",
            api_secret="secret-1234567890",
            expected_user_id="71",
            allowed_product_ids=frozenset({27}),
            transport=httpx.MockTransport(handler),
        )
