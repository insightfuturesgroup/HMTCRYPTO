"""Private TradingView storage contracts."""

import pytest

from hmt_crypto.api.platform_api import CreateAccountBody, TradingViewSignalBody
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


def test_tradingview_signal_accepts_native_iso_timenow_placeholder_value() -> None:
    signal = TradingViewSignalBody.model_validate_json(
        '{"signal_id":"tv-1","side":"buy","timestamp":"2026-11-11T17:00:00Z","limit_price":null}'
    )

    assert signal.timestamp == "2026-11-11T17:00:00Z"


def test_account_creation_assigns_authenticated_admin_without_owner_input() -> None:
    account = CreateAccountBody.model_validate_json(
        '{"profile_name":"delta-primary","environment":"prod"}'
    )

    assert account.profile_name == "delta-primary"


def test_account_creation_accepts_human_readable_profile_name() -> None:
    account = CreateAccountBody.model_validate_json(
        '{"profile_name":"Hitashi Garg","environment":"prod"}'
    )

    assert account.profile_name == "Hitashi Garg"
