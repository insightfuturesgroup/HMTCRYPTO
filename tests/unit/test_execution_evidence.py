"""Execution evidence accepts typed safe values, never arbitrary redacted metadata."""

import importlib
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

import pytest

from hmt_crypto.persistence.errors import PersistenceError


def evidence_api():
    return importlib.import_module("hmt_crypto.persistence.execution_evidence")


TARGET = UUID("00000000-0000-0000-0000-000000000001")
MARKET = {
    "product_id": 1101,
    "requested_contracts": 2,
    "side": "buy",
    "order_type": "market",
    "reduce_only": False,
}
INTENTS = [
    ("PLACE_ORDER", MARKET),
    ("PLACE_ORDER", MARKET | {"order_type": "limit", "limit_price": Decimal("10")}),
    ("MODIFY_ORDER", {"product_id": 1101, "target_order_id": TARGET, "requested_contracts": 3}),
    ("MODIFY_ORDER", {"product_id": 1101, "target_order_id": TARGET, "limit_price": Decimal("11")}),
    ("CANCEL_ORDER", {"product_id": 1101, "target_order_id": TARGET}),
    ("SET_LEVERAGE", {"product_id": 1101, "leverage": Decimal("2.5")}),
    ("SET_MARGIN_MODE", {"margin_mode": "isolated"}),
    ("CHANGE_POSITION_MARGIN", {"product_id": 1101, "margin_delta": Decimal("-2.5")}),
]


@pytest.mark.parametrize(
    "kind,values",
    [
        ("intent", {"api_secret": "SECRET-MARKER"}),
        ("intent", {"headers": {"Authorization": "SECRET-MARKER"}}),
        ("intent", {"signature": "SECRET-MARKER"}),
        ("intent", {"raw_error": "SECRET-MARKER"}),
        ("intent", {"side": "SECRET-MARKER"}),
        ("intent", {"requested_contracts": True}),
        ("intent", {"requested_contracts": "2"}),
        ("intent", {"requested_contracts": 1.5}),
        ("intent", {"requested_contracts": 0}),
        ("intent", {"requested_contracts": 2**63}),
        ("intent", {"limit_price": 1.5}),
        ("event", {"outcome": "SECRET-MARKER"}),
        ("event", {"message": "SECRET-MARKER"}),
        ("transition", {"reason": "SECRET-MARKER"}),
    ],
)
def test_unsupported_or_secret_fields_are_rejected_without_retaining_inputs(kind, values):
    api = evidence_api()
    if kind == "intent":
        values = MARKET | values
    with pytest.raises(PersistenceError) as caught:
        api.encode_evidence(kind, "PLACE_ORDER", 1, values)
    assert "SECRET-MARKER" not in str(caught.value) + repr(caught.value)
    assert caught.value.__cause__ is None


def test_evidence_owns_caller_values_and_hash_binds_operation_and_schema():
    api = evidence_api()
    supplied = dict(MARKET)
    payload, digest = api.encode_evidence("intent", "PLACE_ORDER", 1, supplied)
    supplied["requested_contracts"] = 99
    assert payload == (
        '{"order_type":"market","product_id":1101,"reduce_only":false,'
        '"requested_contracts":2,"side":"buy"}'
    )
    semantic = (
        '{"operation":"PLACE_ORDER","payload":{"order_type":"market","product_id":1101,'
        '"reduce_only":false,"requested_contracts":2,"side":"buy"},'
        '"schema_version":1}'
    )
    assert digest == sha256(semantic.encode()).hexdigest()
    with pytest.raises(PersistenceError):
        api.encode_evidence("intent", "PLACE_ORDER", 2, {})
    typed = api.IntentEvidence(**MARKET)
    assert api.encode_evidence("intent", "PLACE_ORDER", 1, typed) == (payload, digest)


@pytest.mark.parametrize(
    "kind,operation",
    [
        ("intent", "WITHDRAW"),
        ("event", "PLACE_ORDER"),
        ("transition", "DISPATCH_STARTED"),
    ],
)
def test_evidence_operation_vocabulary_is_allowlisted(kind, operation):
    with pytest.raises(PersistenceError):
        evidence_api().encode_evidence(kind, operation, 1, {})


def test_typed_evidence_is_revalidated_after_caller_bypasses_freezing():
    api = evidence_api()
    typed = api.IntentEvidence(**MARKET)
    object.__setattr__(typed, "side", "SECRET-MARKER")
    with pytest.raises(PersistenceError):
        api.encode_evidence("intent", "PLACE_ORDER", 1, typed)


@pytest.mark.parametrize("operation", ["SET_LEVERAGE", "CHANGE_POSITION_MARGIN"])
def test_product_only_receipt_cannot_omit_operation_defining_value(operation):
    with pytest.raises(PersistenceError):
        evidence_api().encode_evidence("intent", operation, 1, {"product_id": 1101})


@pytest.mark.parametrize("operation", ["CANCEL_ALL", "CREATE_BRACKET", "EDIT_BRACKET"])
def test_unrepresented_advanced_operation_is_not_admitted(operation):
    with pytest.raises(PersistenceError):
        evidence_api().encode_evidence("intent", operation, 1, {"product_id": 1101})


@pytest.mark.parametrize("operation,values", INTENTS)
def test_complete_basic_intent_round_trips_typed_and_stored(operation, values):
    api = evidence_api()
    payload, digest = api.encode_evidence("intent", operation, 1, values)
    api.validate_stored_evidence("intent", operation, 1, payload, digest)
    assert api.encode_evidence("intent", operation, 1, api.IntentEvidence(**values)) == (
        payload,
        digest,
    )


@pytest.mark.parametrize(
    "operation,values,field",
    [
        (op, values, field)
        for op, values in INTENTS
        for field in values
        if field not in {"limit_price", "requested_contracts"}
    ]
    + [
        ("PLACE_ORDER", MARKET, "requested_contracts"),
        (
            "PLACE_ORDER",
            MARKET | {"order_type": "limit", "limit_price": Decimal("10")},
            "limit_price",
        ),
    ],
)
def test_each_operation_requires_its_defining_fields(operation, values, field):
    incomplete = dict(values)
    incomplete.pop(field)
    with pytest.raises(PersistenceError):
        evidence_api().encode_evidence("intent", operation, 1, incomplete)


@pytest.mark.parametrize("operation,values", INTENTS)
def test_operation_forbids_cross_variant_fields_even_when_null(operation, values):
    field = "margin_mode" if operation != "SET_MARGIN_MODE" else "product_id"
    with pytest.raises(PersistenceError):
        evidence_api().encode_evidence("intent", operation, 1, values | {field: None})


@pytest.mark.parametrize(
    "operation,values",
    [
        ("PLACE_ORDER", MARKET | {"limit_price": Decimal("10")}),
        ("PLACE_ORDER", MARKET | {"stop_price": Decimal("10")}),
        ("MODIFY_ORDER", {"product_id": 1101, "target_order_id": TARGET}),
        (
            "MODIFY_ORDER",
            {"product_id": 1101, "target_order_id": TARGET, "requested_contracts": None},
        ),
    ],
)
def test_incomplete_or_unsupported_basic_combination_is_rejected(operation, values):
    with pytest.raises(PersistenceError):
        evidence_api().encode_evidence("intent", operation, 1, values)


@pytest.mark.parametrize(
    "operation,field",
    [
        ("SET_LEVERAGE", "leverage"),
        ("CHANGE_POSITION_MARGIN", "margin_delta"),
    ],
)
@pytest.mark.parametrize(
    "invalid",
    [
        True,
        1.5,
        "2",
        Decimal("0"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1e20"),
        Decimal("1e-19"),
    ],
)
def test_leverage_and_margin_delta_are_exact_finite_nonzero_values(operation, field, invalid):
    with pytest.raises(PersistenceError):
        evidence_api().encode_evidence("intent", operation, 1, {"product_id": 1101, field: invalid})


@pytest.mark.parametrize(
    "operation,field,left,right",
    [
        ("PLACE_ORDER", "product_id", 1101, 1102),
        ("PLACE_ORDER", "requested_contracts", 2, 3),
        ("PLACE_ORDER", "side", "buy", "sell"),
        ("PLACE_ORDER", "reduce_only", False, True),
        ("SET_LEVERAGE", "leverage", Decimal("2"), Decimal("3")),
        ("SET_LEVERAGE", "product_id", 1101, 1102),
        ("CHANGE_POSITION_MARGIN", "margin_delta", Decimal("-2"), Decimal("2")),
        ("CHANGE_POSITION_MARGIN", "product_id", 1101, 1102),
        ("SET_MARGIN_MODE", "margin_mode", "isolated", "portfolio"),
        ("CANCEL_ORDER", "target_order_id", TARGET, UUID(int=2)),
        ("CANCEL_ORDER", "product_id", 1101, 1102),
        ("MODIFY_ORDER", "target_order_id", TARGET, UUID(int=2)),
        ("MODIFY_ORDER", "product_id", 1101, 1102),
        ("MODIFY_ORDER", "requested_contracts", 3, 4),
    ],
)
def test_hash_distinguishes_every_admitted_defining_value(operation, field, left, right):
    values = next(values for op, values in INTENTS if op == operation)
    encode = evidence_api().encode_evidence
    assert (
        encode("intent", operation, 1, values | {field: left})[1]
        != encode("intent", operation, 1, values | {field: right})[1]
    )


def test_price_and_order_type_variants_have_distinct_hashes():
    encode = evidence_api().encode_evidence
    market = encode("intent", "PLACE_ORDER", 1, MARKET)[1]
    first_limit = encode(
        "intent", "PLACE_ORDER", 1, MARKET | {"order_type": "limit", "limit_price": Decimal("10")}
    )[1]
    second_limit = encode(
        "intent", "PLACE_ORDER", 1, MARKET | {"order_type": "limit", "limit_price": Decimal("11")}
    )[1]
    modify = {"product_id": 1101, "target_order_id": TARGET, "limit_price": Decimal("10")}
    assert len({market, first_limit, second_limit}) == 3
    assert (
        encode("intent", "MODIFY_ORDER", 1, modify)[1]
        != encode("intent", "MODIFY_ORDER", 1, modify | {"limit_price": Decimal("11")})[1]
    )


def test_negative_leverage_is_rejected_but_negative_margin_delta_is_retained():
    api = evidence_api()
    with pytest.raises(PersistenceError):
        api.encode_evidence(
            "intent", "SET_LEVERAGE", 1, {"product_id": 1101, "leverage": Decimal("-2")}
        )
    payload, _ = api.encode_evidence(
        "intent",
        "CHANGE_POSITION_MARGIN",
        1,
        {"product_id": 1101, "margin_delta": Decimal("-2.500")},
    )
    assert payload == '{"margin_delta":"-2.5","product_id":1101}'


def test_modify_optional_fields_are_exact_values_when_supplied():
    api = evidence_api()
    values = {"product_id": 1101, "target_order_id": TARGET, "requested_contracts": 3}
    with pytest.raises(PersistenceError):
        api.encode_evidence("intent", "MODIFY_ORDER", 1, values | {"limit_price": None})
    payload, digest = api.encode_evidence(
        "intent", "MODIFY_ORDER", 1, values | {"limit_price": Decimal("11")}
    )
    api.validate_stored_evidence("intent", "MODIFY_ORDER", 1, payload, digest)
