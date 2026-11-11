"""Private TradingView storage contracts."""

import pytest

from hmt_crypto.persistence.platform_models import (
    TradingViewSignal,
    TradingViewStrategy,
    TradingViewWebhookKey,
)

pytestmark = pytest.mark.unit


def test_tradingview_secrets_are_hash_only() -> None:
    columns = set(TradingViewWebhookKey.__table__.c.keys())

    assert {"key_sha256", "key_fingerprint", "revoked_at"} <= columns
    assert "key" not in columns
    assert "secret" not in columns


def test_signal_deduplication_is_database_enforced() -> None:
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in TradingViewSignal.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert ("strategy_id", "external_signal_id") in unique_columns


def test_strategy_is_bound_to_one_account_and_product() -> None:
    assert {"account_id", "product_id", "contracts", "order_type", "reduce_only", "enabled"} <= set(
        TradingViewStrategy.__table__.c.keys()
    )
