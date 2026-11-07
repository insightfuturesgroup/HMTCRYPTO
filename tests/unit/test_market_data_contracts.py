"""Exchange-neutral public market observation contract tests."""

from __future__ import annotations

import dataclasses
import importlib
import sys
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest


def _contracts():
    return importlib.import_module("hmt_crypto.market_data.contracts")


def _source(
    *,
    channel: str = "ticker",
    symbol: str | None = "BTCUSD",
    venue_sequence: int | None = None,
):
    contracts = _contracts()
    return contracts.MarketSource(
        region="india",
        environment="testnet",
        connection_generation=UUID("10000000-0000-0000-0000-000000000001"),
        local_sequence=1,
        catalogue_snapshot_id=UUID("20000000-0000-0000-0000-000000000002"),
        channel=channel,
        symbol=symbol,
        venue_sequence=venue_sequence,
        observed_at=datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
    )


def test_market_source_rejects_non_utc_and_invalid_sequences() -> None:
    contracts = _contracts()
    valid = dataclasses.asdict(_source())
    for field, value in (
        ("local_sequence", 0),
        ("local_sequence", True),
        ("venue_sequence", -1),
        ("observed_at", datetime(2026, 11, 5, 10, 0)),
    ):
        with pytest.raises((TypeError, ValueError)):
            contracts.MarketSource(**{**valid, field: value})


def test_ticker_is_frozen_and_retains_exact_decimal_and_product_binding() -> None:
    contracts = _contracts()
    ticker = contracts.TickerObservation(
        source=_source(),
        product_id=27,
        symbol="BTCUSD",
        spot_price=Decimal("100.00"),
        mark_price=Decimal("100.0100"),
        close=Decimal("99.75"),
        best_bid=Decimal("99.90"),
        best_ask=Decimal("100.10"),
        open_interest=17,
    )
    assert ticker.product_id == 27
    assert ticker.source.symbol == "BTCUSD"
    assert ticker.spot_price.as_tuple().exponent == -2
    assert ticker.mark_price.as_tuple().exponent == -4
    with pytest.raises(dataclasses.FrozenInstanceError):
        ticker.product_id = 28


def test_product_observation_rejects_symbol_identity_mismatch() -> None:
    contracts = _contracts()
    with pytest.raises(ValueError, match="identity"):
        contracts.MarkObservation(
            source=_source(channel="mark_price", symbol="BTCUSD"),
            product_id=27,
            symbol="ETHUSD",
            price=Decimal("100"),
        )


def test_book_levels_copy_input_and_require_integral_contracts() -> None:
    contracts = _contracts()
    levels = [
        contracts.BookLevel(price=Decimal("100.00"), size=3, price_lexeme="100.00", size_lexeme="3")
    ]
    snapshot = contracts.BookSnapshotObservation(
        source=_source(channel="ob_updates", symbol="BTCUSD", venue_sequence=1),
        product_id=27,
        symbol="BTCUSD",
        asks=levels,
        bids=(),
        checksum=123,
        checksum_lexeme="123",
    )
    levels.clear()
    assert snapshot.asks[0].price_lexeme == "100.00"
    assert snapshot.asks[0].size == 3
    with pytest.raises((TypeError, ValueError)):
        contracts.BookLevel(
            price=Decimal("100"),
            size=Decimal("1.5"),
            price_lexeme="100",
            size_lexeme="1.5",
        )


def test_binary_float_never_enters_exact_observations() -> None:
    contracts = _contracts()
    with pytest.raises((TypeError, ValueError)):
        contracts.SpotObservation(
            source=_source(channel="spot_price", symbol=".DEUSDTUSD"),
            asset_id=12,
            index_symbol=".DEUSDTUSD",
            price=100.25,
        )


def test_checksum_lexeme_must_remain_exact_and_integral() -> None:
    contracts = _contracts()
    values = dict(
        source=_source(channel="ob_updates", symbol="BTCUSD", venue_sequence=1),
        product_id=27,
        symbol="BTCUSD",
        asks=(),
        bids=(),
        checksum=123,
    )
    assert contracts.BookSnapshotObservation(**values, checksum_lexeme="1.23e2").checksum == 123
    with pytest.raises(ValueError, match="checksum"):
        contracts.BookSnapshotObservation(**values, checksum_lexeme="123.5")


def test_funding_time_trade_role_and_product_status_are_source_bound() -> None:
    contracts = _contracts()
    next_funding_at = datetime(2026, 11, 5, 16, 0, tzinfo=UTC)
    funding = contracts.FundingRateObservation(
        source=_source(channel="funding_rate"),
        product_id=27,
        symbol="BTCUSD",
        interval_seconds=28_800,
        rate=Decimal("-0.0001"),
        next_funding_at=next_funding_at,
    )
    assert funding.next_funding_at == next_funding_at
    trade = contracts.PublicTradeObservation(
        source=_source(channel="trades"),
        product_id=27,
        symbol="BTCUSD",
        price=Decimal("100"),
        size=2,
        buyer_role="m",
        trade_at=datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
    )
    assert trade.buyer_role == "m"
    with pytest.raises(ValueError, match="trading status"):
        contracts.ProductStatusObservation(
            source=_source(channel="product_updates"),
            product_id=27,
            symbol="BTCUSD",
            event="finish_auction",
            trading_status="new_unknown_status",
        )


def test_streaming_candles_are_always_provisional() -> None:
    contracts = _contracts()
    values = dict(
        source=_source(channel="candlestick_1m"),
        product_id=27,
        symbol="BTCUSD",
        resolution="1m",
        timestamp=datetime(2026, 11, 5, 9, 59, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
        volume=Decimal("12.5"),
    )
    assert contracts.CandleObservation(**values).complete is False
    with pytest.raises((TypeError, ValueError)):
        contracts.CandleObservation(**values, complete=True)


def test_contract_module_has_no_private_or_credential_imports() -> None:
    before = set(sys.modules)
    module = _contracts()
    imported = set(sys.modules) - before
    forbidden = ("private", "credential", "signer", "persistence", "oms")
    assert module.__name__ == "hmt_crypto.market_data.contracts"
    assert not any(any(token in name.casefold() for token in forbidden) for name in imported)


def test_each_observation_type_rejects_a_conflicting_source_channel() -> None:
    contracts = _contracts()
    with pytest.raises(ValueError, match="channel"):
        contracts.MarkObservation(
            source=_source(channel="ticker"),
            product_id=27,
            symbol="BTCUSD",
            price=Decimal("100"),
        )


def test_neutral_subscription_boundary_is_exported_and_caps_products() -> None:
    from hmt_crypto.exchange.base import (
        MarketComponent,
        MarketDataSubscriptionService,
        MarketSubscriptionRequest,
        ProductRef,
    )

    assert MarketDataSubscriptionService is not None
    products = tuple(ProductRef(symbol=f"P{index}") for index in range(101))
    with pytest.raises(ValueError):
        MarketSubscriptionRequest(products=products, components=(MarketComponent.TICKER,))


def test_observations_expose_no_raw_payload_or_secret_fields() -> None:
    contracts = _contracts()
    public_types = (
        contracts.TickerObservation,
        contracts.L1Observation,
        contracts.L2Observation,
        contracts.BookSnapshotObservation,
        contracts.BookDeltaObservation,
        contracts.PublicTradeObservation,
        contracts.MarkObservation,
        contracts.CandleObservation,
        contracts.SpotObservation,
        contracts.FundingRateObservation,
        contracts.ProductStatusObservation,
        contracts.SystemStatusObservation,
    )
    forbidden = {"raw", "payload", "api_key", "secret", "signature", "credential"}
    for observation_type in public_types:
        assert forbidden.isdisjoint(field.name for field in dataclasses.fields(observation_type))
