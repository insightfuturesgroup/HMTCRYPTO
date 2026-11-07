"""Immutable desired-subscription and acknowledgement contracts (manifest F03/F12)."""

from __future__ import annotations

from importlib import import_module
from typing import Any
from uuid import UUID

import pytest

from hmt_crypto.errors import ConfigurationError, ExchangeProtocolError
from hmt_crypto.exchange.delta.websocket_contracts import (
    WsCandleResolution,
    WsChannel,
    WsPlane,
)


def api() -> Any:
    try:
        return import_module("hmt_crypto.exchange.delta.websocket_subscriptions")
    except ModuleNotFoundError:
        pytest.fail("WebSocket subscription contracts are missing", pytrace=False)


def test_channel_factories_normalize_symbols_and_apply_documented_transforms() -> None:
    module = api()
    ticker = module.WsSubscription.for_channel(
        WsChannel.TICKER,
        symbols=("ETHUSD", "BTCUSD"),
    )
    mark = module.WsSubscription.for_channel(
        WsChannel.MARK_PRICE,
        symbols=("ETHUSD", "BTCUSD"),
    )
    candle = module.WsSubscription.for_candles(
        WsCandleResolution.TWELVE_HOURS,
        symbols=("ETHUSD", "BTCUSD"),
    )

    assert ticker == module.WsSubscription("ticker", ("BTCUSD", "ETHUSD"))
    assert mark == module.WsSubscription("mark_price", ("MARK:BTCUSD", "MARK:ETHUSD"))
    assert candle == module.WsSubscription("candlestick_12h", ("BTCUSD", "ETHUSD"))


@pytest.mark.parametrize(
    ("channel", "symbols"),
    (
        ("ticker", ()),
        ("ticker", ("BTCUSD", "BTCUSD")),
        ("ticker", ["BTCUSD"]),
        ("system_status", ("BTCUSD",)),
        ("margins", ("BTCUSD",)),
        ("unknown", ("BTCUSD",)),
        ("candlestick_7m", ("BTCUSD",)),
    ),
)
def test_invalid_channel_symbol_or_duplicate_inputs_are_rejected(
    channel: str, symbols: object
) -> None:
    with pytest.raises(ConfigurationError):
        api().WsSubscription(channel=channel, symbols=symbols)


@pytest.mark.parametrize(
    "alias",
    ("all", "perpetual_futures", "put_options", "futures"),
)
def test_lowercase_all_and_documented_category_aliases_are_rejected(alias: str) -> None:
    with pytest.raises(ConfigurationError):
        api().WsSubscription(channel="trades", symbols=(alias,))


def test_symbol_cap_is_exactly_one_hundred() -> None:
    module = api()
    one_hundred = tuple(f"P{index:03d}" for index in range(100))
    assert len(module.WsSubscription("ob_updates", one_hundred).symbols) == 100
    with pytest.raises(ConfigurationError):
        module.WsSubscription("ob_updates", one_hundred + ("P100",))


def test_exact_complete_subscription_acknowledgement_is_accepted() -> None:
    module = api()
    entries = (
        module.WsSubscription("system_status", ()),
        module.WsSubscription("ticker", ("BTCUSD", "ETHUSD")),
    )
    expected = module.desired_subscriptions(revision=7, entries=entries)
    message = {
        "type": "subscriptions",
        "channels": [
            {"name": "ticker", "symbols": ["ETHUSD", "BTCUSD"]},
            {"name": "system_status"},
        ],
    }

    accepted = module.parse_subscriptions_ack(message, expected=expected)
    assert accepted.desired_revision == 7
    assert accepted.evidence_sha256 == expected.evidence_sha256
    assert accepted.entries == expected.entries


@pytest.mark.parametrize(
    "channels",
    (
        [{"name": "ticker", "symbols": ["BTCUSD"]}],
        [
            {"name": "ticker", "symbols": ["BTCUSD", "ETHUSD"]},
            {"name": "system_status"},
            {"name": "funding_rate", "symbols": ["BTCUSD"]},
        ],
        [
            {"name": "ticker", "symbols": ["BTCUSD", "ETHUSD"], "error": "denied"},
            {"name": "system_status"},
        ],
    ),
)
def test_partial_extra_or_channel_error_acknowledgements_fail_closed(
    channels: list[dict[str, object]],
) -> None:
    module = api()
    expected = module.desired_subscriptions(
        revision=3,
        entries=(
            module.WsSubscription("system_status", ()),
            module.WsSubscription("ticker", ("BTCUSD", "ETHUSD")),
        ),
    )
    with pytest.raises(ExchangeProtocolError) as error:
        module.parse_subscriptions_ack(
            {"type": "subscriptions", "channels": channels},
            expected=expected,
        )
    assert error.value.context == {"operation": "parse_ws_subscriptions_ack"}
    assert "denied" not in str(error.value.to_dict())


def test_generation_receipts_and_invalidations_require_exact_evidence() -> None:
    module = api()
    generation = UUID("12345678-1234-5678-1234-567812345678")
    evidence = "a" * 64
    receipt = module.GenerationSubscriptionReceipt(
        plane=WsPlane.PUBLIC,
        connection_generation=generation,
        control_revision=2,
        desired_revision=3,
        evidence_sha256=evidence,
        accepted_at_monotonic_ns=4,
    )
    invalidation = module.GenerationControlInvalidation(
        plane=WsPlane.PUBLIC,
        connection_generation=generation,
        local_sequence=1,
        kind=module.StreamInvalidationKind.BOOK_GAP,
        evidence_sha256=evidence,
    )
    assert receipt.connection_generation == invalidation.connection_generation
    with pytest.raises(ValueError):
        module.GenerationSubscriptionReceipt(
            plane=WsPlane.PUBLIC,
            connection_generation=generation,
            control_revision=True,
            desired_revision=3,
            evidence_sha256=evidence,
            accepted_at_monotonic_ns=4,
        )


def test_real_subscription_values_encode_through_the_control_codec() -> None:
    codec = import_module("hmt_crypto.exchange.delta.websocket_codec")
    module = api()
    entries = (
        module.WsSubscription("system_status", ()),
        module.WsSubscription("ticker", ("BTCUSD",)),
    )
    assert codec.encode_subscribe(entries) == (
        '{"type":"subscribe","payload":{"channels":['
        '{"name":"system_status"},{"name":"ticker","symbols":["BTCUSD"]}]}}'
    )
