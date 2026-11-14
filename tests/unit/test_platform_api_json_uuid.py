from uuid import UUID

import pytest
from asyncpg.pgproto.pgproto import UUID as AsyncpgUUID
from pydantic import ValidationError

from hmt_crypto.api.live_submission import make_live_client_order_id
from hmt_crypto.api.platform_api import OrderIntentBody, OrderPreviewBody, _database_uuid

ACCOUNT_ID = "32468e34-b2a5-4d5e-9a82-64aa3e8ab148"


def test_order_preview_accepts_uuid_as_json_string_without_relaxing_other_fields() -> None:
    body = OrderPreviewBody.model_validate_json(
        """
        {
          "account_id": "32468e34-b2a5-4d5e-9a82-64aa3e8ab148",
          "product_id": 3136,
          "side": "buy",
          "contracts": 1,
          "order_type": "market",
          "limit_price": null,
          "reduce_only": false
        }
        """
    )

    assert type(body.account_id) is UUID
    assert str(body.account_id) == ACCOUNT_ID
    assert type(body.contracts) is int


def test_json_uuid_rejects_malformed_values() -> None:
    with pytest.raises(ValidationError):
        OrderIntentBody.model_validate_json('{"preview_id":"not-a-uuid"}')


def test_json_uuid_conversion_does_not_coerce_strict_numeric_fields() -> None:
    with pytest.raises(ValidationError):
        OrderPreviewBody.model_validate(
            {
                "account_id": ACCOUNT_ID,
                "product_id": 3136,
                "side": "buy",
                "contracts": "1",
                "order_type": "market",
                "limit_price": None,
                "reduce_only": False,
            }
        )


def test_database_uuid_is_canonicalized_before_strict_live_submission_values() -> None:
    account_id = _database_uuid(AsyncpgUUID(ACCOUNT_ID))
    intent_id = UUID("f76c06a4-36c7-4fd4-970d-e34609fba27f")

    assert type(account_id) is UUID
    assert make_live_client_order_id(account_id, intent_id).startswith("HML1_")
