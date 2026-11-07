"""Canonical wallet/funding evidence rejects untyped or additive claims."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from hmt_crypto.persistence.accounting_evidence import (
    decode_accounting_evidence,
    encode_accounting_evidence,
    validate_accounting_evidence,
)
from hmt_crypto.persistence.errors import PersistenceValueError

WALLET = {
    "amount": Decimal("-1.2500"),
    "asset_id": 1,
    "asset_symbol": "USDT",
    "effective_at": datetime(2026, 11, 4, 0, 0, 0, 123456, tzinfo=UTC),
    "exchange_transaction_id": "tx-1",
    "post_balance": Decimal("10.500"),
    "product_id": 1101,
    "transaction_type": "funding",
}
WALLET_PARENT = UUID("11111111-1111-4111-8111-111111111111")
POSITION_PARENT = UUID("22222222-2222-4222-8222-222222222222")
FUNDING = {
    "accounting_role": "NON_ADDITIVE_OBSERVATION",
    "amount": Decimal("-1.2500"),
    "asset_id": 1,
    "asset_symbol": "USDT",
    "effective_at": None,
    "position_snapshot_id": None,
    "product_id": 1101,
    "rate": None,
    "source_kind": "WALLET_TRANSACTION",
    "wallet_transaction_id": WALLET_PARENT,
}


class _AlwaysEqual:
    def __eq__(self, other):
        return True


def test_literal_wallet_payload_and_hash_vector():
    assert encode_accounting_evidence("wallet_transaction", 1, WALLET) == (
        '{"amount":"-1.25","asset_id":1,"asset_symbol":"USDT",'
        '"effective_at":"2026-11-04T00:00:00.123456+00:00",'
        '"exchange_transaction_id":"tx-1","post_balance":"10.5",'
        '"product_id":1101,"transaction_type":"funding"}',
        "f2ea55cb176995ac2e9e8a99fd0f29658fa56880412a7f55fd5aba22b148e8cc",
    )


def test_literal_funding_payload_and_hash_vector():
    assert encode_accounting_evidence("funding_snapshot", 1, FUNDING) == (
        '{"accounting_role":"NON_ADDITIVE_OBSERVATION","amount":"-1.25",'
        '"asset_id":1,"asset_symbol":"USDT","effective_at":null,'
        '"position_snapshot_id":null,"product_id":1101,"rate":null,'
        '"source_kind":"WALLET_TRANSACTION",'
        '"wallet_transaction_id":"11111111-1111-4111-8111-111111111111"}',
        "187eadbc59282b3e222dceb8fa53fa013068c72996931fa2a538ca72211b8cc3",
    )


def test_decimal_and_timezone_equivalence_is_canonical():
    equivalent = WALLET | {
        "amount": Decimal("-1.25"),
        "post_balance": Decimal("10.500000000000000000"),
        "effective_at": datetime(
            2026,
            11,
            4,
            5,
            30,
            0,
            123456,
            tzinfo=timezone(timedelta(hours=5, minutes=30)),
        ),
    }
    assert encode_accounting_evidence("wallet_transaction", 1, equivalent) == (
        encode_accounting_evidence("wallet_transaction", 1, WALLET)
    )


@pytest.mark.parametrize("kind", [None, "", "unknown", 1, True])
def test_kind_is_exact_and_errors_never_echo_input(kind):
    marker = "SECRET-ACCOUNTING-MARKER"
    with pytest.raises(PersistenceValueError) as exc:
        encode_accounting_evidence(kind, 1, WALLET | {"api_secret": marker})
    assert marker not in str(exc.value) and marker not in repr(exc.value)


@pytest.mark.parametrize("version", [None, True, 0, 2, 1.0, "1"])
def test_schema_version_is_exactly_one(version):
    with pytest.raises(PersistenceValueError):
        encode_accounting_evidence("wallet_transaction", version, WALLET)


@pytest.mark.parametrize("field", sorted(WALLET))
def test_wallet_requires_every_exact_semantic_field(field):
    with pytest.raises(PersistenceValueError):
        encode_accounting_evidence(
            "wallet_transaction", 1, {k: v for k, v in WALLET.items() if k != field}
        )


@pytest.mark.parametrize("field", sorted(FUNDING))
def test_funding_requires_every_exact_semantic_field(field):
    with pytest.raises(PersistenceValueError):
        encode_accounting_evidence(
            "funding_snapshot", 1, {k: v for k, v in FUNDING.items() if k != field}
        )


def test_extra_arbitrary_and_secret_fields_are_not_admitted():
    for kind, values in (("wallet_transaction", WALLET), ("funding_snapshot", FUNDING)):
        with pytest.raises(PersistenceValueError):
            encode_accounting_evidence(kind, 1, values | {"metadata": {"api_key": "SECRET"}})


@pytest.mark.parametrize("field", ["exchange_transaction_id", "transaction_type", "asset_symbol"])
@pytest.mark.parametrize("value", [None, "", " ", "\t", "x" * 129, "bad\x00text", 1, True])
def test_wallet_text_fields_are_bounded_nonblank_exact_strings(field, value):
    with pytest.raises(PersistenceValueError):
        encode_accounting_evidence("wallet_transaction", 1, WALLET | {field: value})


@pytest.mark.parametrize("field", ["asset_id", "product_id"])
@pytest.mark.parametrize("value", [True, False, 0, -1, 1.0, "1", 2**63])
def test_wallet_ids_are_exact_positive_bigints(field, value):
    with pytest.raises(PersistenceValueError):
        encode_accounting_evidence("wallet_transaction", 1, WALLET | {field: value})


def test_wallet_nullable_fields_preserve_explicit_unknown():
    payload, _ = encode_accounting_evidence(
        "wallet_transaction",
        1,
        WALLET | {"product_id": None, "post_balance": None, "effective_at": None},
    )
    assert '"effective_at":null' in payload
    assert '"post_balance":null' in payload
    assert '"product_id":null' in payload


@pytest.mark.parametrize(
    "value",
    [True, 1.25, "1.25", Decimal("NaN"), Decimal("Infinity"), Decimal("1e20"), Decimal("1e-19")],
)
@pytest.mark.parametrize("field", ["amount", "post_balance"])
def test_wallet_decimal_fields_are_exact_and_bounded(field, value):
    with pytest.raises(PersistenceValueError):
        encode_accounting_evidence("wallet_transaction", 1, WALLET | {field: value})


@pytest.mark.parametrize("value", [datetime(2026, 11, 4), "2026-11-04T00:00:00+00:00", 1, True])
def test_wallet_effective_time_is_null_or_exact_aware_datetime(value):
    with pytest.raises(PersistenceValueError):
        encode_accounting_evidence("wallet_transaction", 1, WALLET | {"effective_at": value})


@pytest.mark.parametrize(
    "source,changes",
    [
        ("WALLET_TRANSACTION", {}),
        (
            "POSITION_AGGREGATE",
            {
                "wallet_transaction_id": None,
                "position_snapshot_id": POSITION_PARENT,
                "asset_id": None,
                "asset_symbol": None,
            },
        ),
        (
            "MARKET_RATE",
            {
                "wallet_transaction_id": None,
                "position_snapshot_id": None,
                "asset_id": None,
                "asset_symbol": None,
                "amount": None,
                "rate": Decimal("0.0001"),
                "effective_at": datetime(2026, 11, 4, tzinfo=UTC),
            },
        ),
    ],
)
def test_exact_funding_source_shapes_are_admitted(source, changes):
    payload, digest = encode_accounting_evidence(
        "funding_snapshot", 1, FUNDING | {"source_kind": source} | changes
    )
    assert validate_accounting_evidence("funding_snapshot", 1, payload, digest) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"source_kind": "UNKNOWN"},
        {"accounting_role": "ADDITIVE"},
        {"wallet_transaction_id": None},
        {"position_snapshot_id": POSITION_PARENT},
        {"amount": None},
        {"rate": Decimal("0.1")},
        {"asset_id": None},
        {"asset_symbol": None},
        {"asset_id": 2, "asset_symbol": None},
        {"wallet_transaction_id": str(WALLET_PARENT)},
    ],
)
def test_wallet_funding_shape_rejects_additive_or_incomplete_claims(changes):
    with pytest.raises(PersistenceValueError):
        encode_accounting_evidence("funding_snapshot", 1, FUNDING | changes)


@pytest.mark.parametrize("value", [True, 1, _AlwaysEqual()])
def test_accounting_role_requires_the_exact_string_type(value):
    with pytest.raises(PersistenceValueError):
        encode_accounting_evidence("funding_snapshot", 1, FUNDING | {"accounting_role": value})


@pytest.mark.parametrize(
    "changes",
    [
        {"wallet_transaction_id": WALLET_PARENT},
        {"position_snapshot_id": None},
        {"product_id": None},
        {"amount": None},
        {"rate": Decimal("0.1")},
    ],
)
def test_position_funding_shape_requires_one_parent_product_and_amount(changes):
    values = FUNDING | {
        "source_kind": "POSITION_AGGREGATE",
        "wallet_transaction_id": None,
        "position_snapshot_id": POSITION_PARENT,
        "asset_id": None,
        "asset_symbol": None,
    }
    with pytest.raises(PersistenceValueError):
        encode_accounting_evidence("funding_snapshot", 1, values | changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"wallet_transaction_id": WALLET_PARENT},
        {"position_snapshot_id": POSITION_PARENT},
        {"product_id": None},
        {"amount": Decimal("1")},
        {"rate": None},
        {"effective_at": None},
    ],
)
def test_market_rate_shape_requires_product_rate_time_and_no_parent_or_amount(changes):
    values = FUNDING | {
        "source_kind": "MARKET_RATE",
        "wallet_transaction_id": None,
        "position_snapshot_id": None,
        "asset_id": None,
        "asset_symbol": None,
        "amount": None,
        "rate": Decimal("0.0001"),
        "effective_at": datetime(2026, 11, 4, tzinfo=UTC),
    }
    with pytest.raises(PersistenceValueError):
        encode_accounting_evidence("funding_snapshot", 1, values | changes)


@pytest.mark.parametrize(
    "payload",
    [
        "{}",
        "[]",
        "null",
        '{"amount":"-1.2500","asset_id":1}',
        '{"api_secret":"SECRET"}',
        "x" * 2049,
        None,
        True,
    ],
)
def test_stored_payload_must_be_exact_complete_and_canonical(payload):
    with pytest.raises(PersistenceValueError):
        decode_accounting_evidence("wallet_transaction", payload)


def test_stored_payload_round_trip_restores_typed_values():
    payload, digest = encode_accounting_evidence("funding_snapshot", 1, FUNDING)
    restored = decode_accounting_evidence("funding_snapshot", payload)
    assert restored["amount"] == Decimal("-1.25")
    assert restored["wallet_transaction_id"] == WALLET_PARENT
    assert restored["effective_at"] is None
    assert validate_accounting_evidence("funding_snapshot", 1, payload, digest) is None


@pytest.mark.parametrize("digest", [None, True, "A" * 64, "a" * 63, "b" * 64])
def test_validation_requires_exact_matching_lowercase_digest(digest):
    payload, _ = encode_accounting_evidence("wallet_transaction", 1, WALLET)
    with pytest.raises(PersistenceValueError):
        validate_accounting_evidence("wallet_transaction", 1, payload, digest)


def test_caller_containers_are_not_retained():
    values = dict(WALLET)
    payload, digest = encode_accounting_evidence("wallet_transaction", 1, values)
    values["amount"] = Decimal("999")
    assert validate_accounting_evidence("wallet_transaction", 1, payload, digest) is None
    assert uuid4() != WALLET_PARENT
