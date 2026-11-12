from decimal import Decimal

import pytest
from fastapi import HTTPException

from hmt_crypto.api.platform_api import _page_payload_rows, _payload_decimal, _payload_list


def test_payload_helpers_fail_closed_on_malformed_reconciliation_data() -> None:
    assert _payload_list(None) == []
    assert _payload_list("not-json") == []
    assert _payload_list('{"balance":"10"}') == []
    assert _payload_list('[{"balance":"10"},null,"bad"]') == [{"balance": "10"}]
    assert _payload_decimal({"balance": "NaN"}, "balance") == Decimal(0)
    assert _payload_decimal({"balance": "10.25"}, "balance") == Decimal("10.25")


def test_normalized_rows_filter_and_paginate_without_mutating_payload() -> None:
    rows = [
        {"symbol": "BTCUSD", "status": "open", "order_type": "limit", "id": 1},
        {"symbol": "BTCUSD", "status": "open", "order_type": "limit", "id": 2},
        {"symbol": "ETHUSD", "status": "closed", "order_type": "market", "id": 3},
    ]
    result = _page_payload_rows(
        rows,
        symbol="btcusd",
        status="OPEN",
        order_type="LIMIT",
        date_from=None,
        date_to=None,
        sort="newest",
        page=2,
        page_size=1,
    )
    assert result == {"items": [rows[1]], "total": 2, "page": 2, "pageSize": 1}
    assert len(rows) == 3


def test_normalized_rows_reject_non_list_account_state() -> None:
    with pytest.raises(HTTPException) as raised:
        _page_payload_rows(
            {},
            symbol=None,
            status=None,
            order_type=None,
            date_from=None,
            date_to=None,
            sort="newest",
            page=1,
            page_size=25,
        )
    assert raised.value.status_code == 409
    assert raised.value.detail == "account_state_not_ready"
