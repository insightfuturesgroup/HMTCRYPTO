"""Risk-critical reduce-only product metadata is conservative and strictly typed."""

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
        ("wrong_flag", 0),
        ("wrong_flag", 1),
        ("wrong_flag", "false"),
        ("wrong_flag", None),
    ],
)
def test_reduce_only_product_metadata_fails_closed_when_malformed(
    mutation: str,
    value: object,
) -> None:
    payload = fixture_json("public_products_page1.json")
    product = payload["result"][0]
    if mutation == "missing_specs":
        product.pop("product_specs")
    elif mutation == "wrong_specs":
        product["product_specs"] = value
    else:
        product["product_specs"]["only_reduce_only_orders_allowed"] = value

    with pytest.raises(ExchangeProtocolError) as raised:
        parse_product(product, endpoint_id="products")
    rendered = str(raised.value) + repr(raised.value) + json.dumps(raised.value.to_dict())
    assert "fixture-secret-not-an-object" not in rendered


def test_missing_reduce_only_flag_is_synthesized_as_explicit_conservative_true() -> None:
    payload = fixture_json("public_products_page1.json")
    product = payload["result"][0]
    product["product_specs"].pop("only_reduce_only_orders_allowed")

    parsed = parse_product(product, endpoint_id="products")

    assert parsed.only_reduce_only_orders_allowed is True
    assert "only_reduce_only_orders_allowed" in parsed.model_fields_set


def test_spot_product_represents_absent_settling_asset_without_derivative_inference() -> None:
    payload = fixture_json("public_products_page1.json")
    product = payload["result"][0]
    product["contract_type"] = "spot"
    product["settling_asset"] = None

    parsed = parse_product(product, endpoint_id="products")

    assert parsed.settling_asset is None

    product["contract_type"] = "perpetual_futures"
    with pytest.raises(ExchangeProtocolError):
        parse_product(product, endpoint_id="products")
