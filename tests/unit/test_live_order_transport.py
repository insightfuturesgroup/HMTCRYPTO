"""Canonical non-network Delta Live order wire tests."""

import json
from decimal import Decimal

import pytest

from hmt_crypto.api.live_order_transport import (
    LiveOrderPreparationError,
    classify_live_order_response,
    prepare_live_order,
)

pytestmark = pytest.mark.unit


def test_limit_order_body_is_canonical_and_documented() -> None:
    prepared = prepare_live_order(
        product_id=27,
        contracts=3,
        side="buy",
        order_type="limit",
        limit_price=Decimal("50000.50"),
        reduce_only=False,
        client_order_id="HML1_ABCDEFGHIJKLMNOPQRSTUVWXYZA",
    )

    assert json.loads(prepared.body) == {
        "client_order_id": "HML1_ABCDEFGHIJKLMNOPQRSTUVWXYZA",
        "limit_price": "50000.5",
        "order_type": "limit_order",
        "product_id": 27,
        "reduce_only": False,
        "side": "buy",
        "size": 3,
        "time_in_force": "gtc",
    }
    assert len(prepared.body_sha256) == 64


def test_market_order_omits_limit_price() -> None:
    prepared = prepare_live_order(
        product_id=27,
        contracts=1,
        side="sell",
        order_type="market",
        limit_price=None,
        reduce_only=True,
        client_order_id="HML1_ABCDEFGHIJKLMNOPQRSTUVWXYZA",
    )

    assert "limit_price" not in json.loads(prepared.body)


@pytest.mark.parametrize(
    ("order_type", "limit_price"),
    [("limit", None), ("market", Decimal("1")), ("stop", Decimal("1"))],
)
def test_invalid_order_shape_fails_closed(order_type: str, limit_price: Decimal | None) -> None:
    with pytest.raises(LiveOrderPreparationError):
        prepare_live_order(
            product_id=27,
            contracts=1,
            side="buy",
            order_type=order_type,
            limit_price=limit_price,
            reduce_only=False,
            client_order_id="HML1_ABCDEFGHIJKLMNOPQRSTUVWXYZA",
        )


def test_documented_rejection_is_definitive_but_malformed_response_is_uncertain() -> None:
    prepared = prepare_live_order(
        product_id=27,
        contracts=1,
        side="buy",
        order_type="market",
        limit_price=None,
        reduce_only=False,
        client_order_id="HML1_ABCDEFGHIJKLMNOPQRSTUVWXYZA",
    )
    rejected = classify_live_order_response(
        prepared=prepared,
        status_code=400,
        body=b'{"success":false,"error":{"code":"insufficient_margin"}}',
        external_user_id="12345",
    )
    uncertain = classify_live_order_response(
        prepared=prepared,
        status_code=502,
        body=b"upstream timeout",
        external_user_id="12345",
    )

    assert rejected.kind == "REJECTED"
    assert rejected.exchange_order_id is None
    assert uncertain.kind == "UNCERTAIN"
