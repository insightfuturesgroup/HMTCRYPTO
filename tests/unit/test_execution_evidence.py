"""Execution evidence accepts typed safe values, never arbitrary redacted metadata."""

import importlib
import json
from hashlib import sha256

import pytest

from hmt_crypto.persistence.errors import PersistenceError


def evidence_api():
    return importlib.import_module("hmt_crypto.persistence.execution_evidence")


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
    with pytest.raises(PersistenceError) as caught:
        api.encode_evidence(kind, "PLACE_ORDER", 1, values)
    assert "SECRET-MARKER" not in str(caught.value) + repr(caught.value)
    assert caught.value.__cause__ is None


def test_evidence_owns_caller_values_and_hash_binds_operation_and_schema():
    api = evidence_api()
    supplied = {"requested_contracts": 2, "side": "buy"}
    payload, digest = api.encode_evidence("intent", "PLACE_ORDER", 1, supplied)
    supplied["requested_contracts"] = 99
    assert payload == '{"requested_contracts":2,"side":"buy"}'
    semantic = (
        '{"operation":"PLACE_ORDER","payload":{"requested_contracts":2,"side":"buy"},'
        '"schema_version":1}'
    )
    assert digest == sha256(semantic.encode()).hexdigest()
    assert api.encode_evidence("intent", "MODIFY_ORDER", 1, json.loads(payload))[1] != digest
    with pytest.raises(PersistenceError):
        api.encode_evidence("intent", "PLACE_ORDER", 2, {})
    typed = api.IntentEvidence(requested_contracts=2, side="buy")
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
    typed = api.IntentEvidence(side="buy")
    object.__setattr__(typed, "side", "SECRET-MARKER")
    with pytest.raises(PersistenceError):
        api.encode_evidence("intent", "PLACE_ORDER", 1, typed)
