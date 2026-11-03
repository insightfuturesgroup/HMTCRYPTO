"""Risk-critical reduce-only product metadata is required and strictly typed."""

from __future__ import annotations

import json

import pytest
from delta_public_test_support import fixture_json

from hmt_crypto.errors import ExchangeProtocolError
from hmt_crypto.exchange.delta.wire_schemas import parse_product

pytestmark = pytest.mark.contract


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("missing_specs", None),
        ("wrong_specs", "fixture-secret-not-an-object"),
        ("missing_flag", None),
        ("wrong_flag", 0),
        ("wrong_flag", 1),
        ("wrong_flag", "false"),
        ("wrong_flag", None),
    ],
)
def test_reduce_only_product_metadata_fails_closed_when_missing_or_not_boolean(
    mutation: str,
    value: object,
) -> None:
    payload = fixture_json("public_products_page1.json")
    product = payload["result"][0]
    if mutation == "missing_specs":
        product.pop("product_specs")
    elif mutation == "wrong_specs":
        product["product_specs"] = value
    elif mutation == "missing_flag":
        product["product_specs"].pop("only_reduce_only_orders_allowed")
    else:
        product["product_specs"]["only_reduce_only_orders_allowed"] = value

    with pytest.raises(ExchangeProtocolError) as raised:
        parse_product(product, endpoint_id="products")
    rendered = str(raised.value) + repr(raised.value) + json.dumps(raised.value.to_dict())
    assert "fixture-secret-not-an-object" not in rendered
