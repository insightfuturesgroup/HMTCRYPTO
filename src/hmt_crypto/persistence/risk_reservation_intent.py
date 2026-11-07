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
    """Own and cross-check immutable D9a copies against current locked rows."""
    values = _owned_mapping(value)
    for owner in ("decision", "current"):
        version_field = f"{owner}_command_evidence_schema_version"
        payload_field = f"{owner}_command_evidence_payload"
        digest_field = f"{owner}_command_evidence_sha256"
        version = _integer(values[version_field])
        if version != 1:
            _fail()
        values[version_field] = version
        values[payload_field] = _text(values[payload_field], maximum=2048)
        values[digest_field] = _hash(values[digest_field])
    values["decision_approved_contracts"] = _integer(values["decision_approved_contracts"])
    values["decision_approved_side"] = _enum(values["decision_approved_side"], ("buy", "sell"))
    values["decision_risk_effect"] = _enum(
        values["decision_risk_effect"], ("RISK_INCREASE", "REDUCE_ONLY")
    )
    for owner in ("decision", "current"):
        values[f"{owner}_order_id"] = _uuid(values[f"{owner}_order_id"])
        values[f"{owner}_order_product_id"] = _integer(values[f"{owner}_order_product_id"])
        values[f"{owner}_order_origin_command_id"] = _uuid(
            values[f"{owner}_order_origin_command_id"]
        )
        values[f"{owner}_order_requested_contracts"] = _integer(
            values[f"{owner}_order_requested_contracts"]
        )
        values[f"{owner}_order_requested_side"] = _enum(
            values[f"{owner}_order_requested_side"], ("buy", "sell")
        )
        if type(values[f"{owner}_order_requested_reduce_only"]) is not bool:
            _fail()
    values["current_order_requested_order_type"] = _enum(
        values["current_order_requested_order_type"], ("limit", "market")
    )
    values["current_order_requested_limit_price"] = _optional_positive_decimal(
        values["current_order_requested_limit_price"]
    )
    values["current_order_requested_stop_price"] = _optional_positive_decimal(
        values["current_order_requested_stop_price"]
    )

    operation = _enum(lineage["command_operation_kind"], ("PLACE_ORDER", "MODIFY_ORDER"))
    command_id = _uuid(lineage["command_id"])
    order_id = _uuid(lineage["order_id"])
    product_id = _integer(lineage["product_id"])
    assert command_id is not None and order_id is not None and product_id is not None
    for owner in ("decision", "current"):
        validate_stored_evidence(
            "intent",
            operation,
            cast(int, values[f"{owner}_command_evidence_schema_version"]),
            cast(str, values[f"{owner}_command_evidence_payload"]),
            cast(str, values[f"{owner}_command_evidence_sha256"]),
        )
    command_fields = (
        "command_evidence_schema_version",
        "command_evidence_payload",
        "command_evidence_sha256",
    )
    if any(values[f"decision_{field}"] != values[f"current_{field}"] for field in command_fields):
        _fail()
    order_fields = (
        "order_id",
        "order_product_id",
        "order_origin_command_id",
        "order_requested_contracts",
        "order_requested_side",
        "order_requested_reduce_only",
    )
    if any(values[f"decision_{field}"] != values[f"current_{field}"] for field in order_fields):
        _fail()
    intent = json.loads(cast(str, values["current_command_evidence_payload"]))
    order_type = values["current_order_requested_order_type"]
    limit_price = values["current_order_requested_limit_price"]
    if (
        type(intent) is not dict
        or intent.get("product_id") != product_id
        or values["current_order_id"] != order_id
        or values["current_order_product_id"] != product_id
        or values["decision_approved_side"] != values["current_order_requested_side"]
        or (values["current_order_requested_reduce_only"] is True)
        != (values["decision_risk_effect"] == "REDUCE_ONLY")
        or values["current_order_requested_stop_price"] is not None
        or (order_type == "market" and limit_price is not None)
        or (order_type == "limit" and limit_price is None)
    ):
        _fail()
    _compare_release_plan(values, lineage)

    if operation == "PLACE_ORDER":
        if (
            values["current_order_origin_command_id"] != command_id
            or values["decision_approved_contracts"] != values["current_order_requested_contracts"]
            or intent.get("requested_contracts") != values["decision_approved_contracts"]
            or intent.get("side") != values["decision_approved_side"]
            or intent.get("order_type") != order_type
            or intent.get("reduce_only") is not values["current_order_requested_reduce_only"]
        ):
            _fail()
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
