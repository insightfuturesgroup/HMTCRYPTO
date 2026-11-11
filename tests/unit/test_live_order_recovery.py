"""Focused uncertainty lookup classification tests."""

import pytest

from hmt_crypto.api.live_order_recovery import RecoveryObservation

pytestmark = pytest.mark.unit


def test_positive_lookup_requires_exchange_order_id() -> None:
    found = RecoveryObservation("FOUND", 123)

    assert found.exchange_order_id == 123
    with pytest.raises(ValueError):
        RecoveryObservation("FOUND", None)


def test_missing_lookup_remains_nonterminal_evidence() -> None:
    missing = RecoveryObservation("NOT_FOUND", None)

    assert missing.result == "NOT_FOUND"
    assert missing.exchange_order_id is None
    with pytest.raises(ValueError):
        RecoveryObservation("NOT_FOUND", 123)
