"""Strict call-local command, decision, and order intent ownership."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import cast
from uuid import UUID

from hmt_crypto.persistence.execution_evidence import validate_stored_evidence
from hmt_crypto.persistence.risk_reservation_contracts import (
    _decimal,
    _enum,
    _fail,
    _hash,
    _integer,
    _text,
    _uuid,
)
from hmt_crypto.persistence.risk_reservation_fields import (
    CURRENT_COMMAND_DECISION_ORDER_FIELDS,
)
from hmt_crypto.persistence.types import _decimal_text


def _owned_mapping(value: object) -> dict[str, object]:
    if type(value) is not dict or set(value) != CURRENT_COMMAND_DECISION_ORDER_FIELDS:
        _fail()
    return dict(value)


def _optional_positive_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    result = _decimal(value)
    if result <= 0:
        _fail()
    return result


def _compare_release_plan(values: dict[str, object], lineage: dict[str, object]) -> None:
    pairs = (
        ("decision_approved_contracts", "approved_contracts"),
        ("decision_approved_side", "approved_side"),
        ("decision_risk_effect", "risk_effect"),
    )
    for snapshot_field, release_field in pairs:
        if release_field in lineage and values[snapshot_field] != lineage[release_field]:
            _fail()


def validate_current_intent_snapshot(
    value: object, lineage: dict[str, object]
) -> dict[str, object]:
    """Own and cross-check one current command/decision/order semantic snapshot."""
    values = _owned_mapping(value)
    schema_version = _integer(values["command_evidence_schema_version"])
    if schema_version != 1:
        _fail()
    values["command_evidence_schema_version"] = schema_version
    values["command_evidence_payload"] = _text(values["command_evidence_payload"], maximum=2048)
    values["command_evidence_sha256"] = _hash(values["command_evidence_sha256"])
    values["decision_approved_contracts"] = _integer(values["decision_approved_contracts"])
    values["decision_approved_side"] = _enum(values["decision_approved_side"], ("buy", "sell"))
    values["decision_risk_effect"] = _enum(
        values["decision_risk_effect"], ("RISK_INCREASE", "REDUCE_ONLY")
    )
    values["order_origin_command_id"] = _uuid(values["order_origin_command_id"])
    values["order_product_id"] = _integer(values["order_product_id"])
    values["order_requested_contracts"] = _integer(values["order_requested_contracts"])
    values["order_requested_side"] = _enum(values["order_requested_side"], ("buy", "sell"))
    values["order_requested_order_type"] = _enum(
        values["order_requested_order_type"], ("limit", "market")
    )
    values["order_requested_limit_price"] = _optional_positive_decimal(
        values["order_requested_limit_price"]
    )
    values["order_requested_stop_price"] = _optional_positive_decimal(
        values["order_requested_stop_price"]
    )
    if type(values["order_requested_reduce_only"]) is not bool:
        _fail()

    operation = _enum(lineage["command_operation_kind"], ("PLACE_ORDER", "MODIFY_ORDER"))
    command_id = _uuid(lineage["command_id"])
    order_id = _uuid(lineage["order_id"])
    product_id = _integer(lineage["product_id"])
    assert command_id is not None and order_id is not None and product_id is not None
    validate_stored_evidence(
        "intent",
        operation,
        schema_version,
        cast(str, values["command_evidence_payload"]),
        cast(str, values["command_evidence_sha256"]),
    )
    intent = json.loads(cast(str, values["command_evidence_payload"]))
    if (
        type(intent) is not dict
        or intent.get("product_id") != product_id
        or values["order_product_id"] != product_id
        or values["decision_approved_side"] != values["order_requested_side"]
        or (values["order_requested_reduce_only"] is True)
        != (values["decision_risk_effect"] == "REDUCE_ONLY")
        or values["order_requested_stop_price"] is not None
    ):
        _fail()
    _compare_release_plan(values, lineage)

    if operation == "PLACE_ORDER":
        if (
            values["order_origin_command_id"] != command_id
            or values["decision_approved_contracts"] != values["order_requested_contracts"]
            or intent.get("requested_contracts") != values["decision_approved_contracts"]
            or intent.get("side") != values["decision_approved_side"]
            or intent.get("order_type") != values["order_requested_order_type"]
            or intent.get("reduce_only") is not values["order_requested_reduce_only"]
        ):
            _fail()
        limit_price = values["order_requested_limit_price"]
        if limit_price is None:
            if "limit_price" in intent:
                _fail()
        elif intent.get("limit_price") != _decimal_text(limit_price, 38, 18):
            _fail()
    else:
        try:
            target_order_id = UUID(intent["target_order_id"])
        except Exception:
            _fail()
        if (
            target_order_id != order_id
            or intent.get("requested_contracts") != values["decision_approved_contracts"]
        ):
            _fail()
    return values
