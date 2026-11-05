"""Source-manifest F09/F10/F12/F13 Delta public WebSocket parser contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.errors import ExchangeProtocolError
from hmt_crypto.exchange.delta.catalog_models import CatalogSnapshot, CatalogSource
from hmt_crypto.exchange.delta.websocket_codec import decode_ws_object
from hmt_crypto.exchange.delta.wire_schemas import parse_product

FIXTURES = Path(__file__).parents[1] / "fixtures" / "delta"


def _catalogue() -> CatalogSnapshot:
    rows = json.loads((FIXTURES / "products_catalog_valid.json").read_text())
    products = tuple(parse_product(row, endpoint_id="parser_test") for row in rows)
    now = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
    return CatalogSnapshot(
        source=CatalogSource(
            region=DeltaRegion.INDIA,
            environment=DeltaEnvironment.TESTNET,
        ),
        revision=7,
        fetched_at=now,
        validated_at=now,
        ttl=timedelta(minutes=5),
        products=products,
    )


def _context():
    from hmt_crypto.exchange.delta.websocket_public_parsers import PublicParseContext

    return PublicParseContext(
        catalogue=_catalogue(),
        catalogue_snapshot_id=UUID("20000000-0000-0000-0000-000000000002"),
        connection_generation=UUID("10000000-0000-0000-0000-000000000001"),
        local_sequence_start=41,
        received_at=datetime(2026, 11, 5, 10, 0, 1, tzinfo=UTC),
        spot_asset_ids={".DEUSDTUSD": 12},
    )


def _parse(frame: str):
    from hmt_crypto.exchange.delta.websocket_public_parsers import DeltaPublicFrameParser

    message = decode_ws_object(frame, max_bytes=4096, stream_label="public")
    return DeltaPublicFrameParser().parse(message, context=_context())


@pytest.mark.parametrize(
    "name",
    ("F10_ob_updates_snapshot.json", "F12_mark_candle.json", "F13_system_status.json"),
)
def test_manifest_cited_sanitized_fixtures_parse(name: str) -> None:
    frame = (FIXTURES / "websocket" / "public" / name).read_text()
    assert len(_parse(frame)) == 1


@pytest.mark.parametrize(
    ("frame", "expected_type"),
    [
        (
            '{"type":"ob_l1","sy":"XBTUSD_CAT","ts":1793872800000000,'
            '"lts":1793872799000000,"ap":"100.50","as":"2","bp":"99.50","bs":"3"}',
            "L1Observation",
        ),
        (
            '{"type":"ob_l2","sy":"XBTUSD_CAT","ts":1793872800000000,'
            '"lts":1793872799000000,"a":[["100.50","2"]],"b":[["99.50","3"]]}',
            "L2Observation",
        ),
        (
            '{"type":"trades","sy":"XBTUSD_CAT","ts":1793872800000000,'
            '"t":1793872799000000,"p":"100.00","s":"2","r":"m"}',
            "PublicTradeObservation",
        ),
        (
            '{"type":"mark_price","sy":"MARK:XBTUSD_CAT","ts":1793872800000000,"p":"100.00"}',
            "MarkObservation",
        ),
        (
            '{"type":"candlestick_1m","sy":"XBTUSD_CAT",'
            '"ts":1793872740000000,"res":"1m","o":"99","h":"102",'
            '"l":"98","c":"101","v":"5.5"}',
            "CandleObservation",
        ),
        (
            '{"type":"spot_price","sy":".DEUSDTUSD","ts":1793872800000000,"p":"100.00"}',
            "SpotObservation",
        ),
        (
            '{"type":"funding_rate","sy":"XBTUSD_CAT",'
            '"ts":1793872800000000,"fi":28800,"fr":"-0.0001",'
            '"nfr":1793894400000000}',
            "FundingRateObservation",
        ),
        (
            '{"type":"product_updates","event":"market_disruption",'
            '"product":{"id":1101,"symbol":"XBTUSD_CAT",'
            '"trading_status":"disrupted_cancel_only"},'
            '"timestamp":1793872800000000}',
            "ProductStatusObservation",
        ),
        (
            '{"type":"system_status","event":"snapshot","status":"live",'
            '"maintenance_announcement_time":null,"maintenance_start_time":null,'
            '"maintenance_finish_time":null,"timestamp":1793872800000000}',
            "SystemStatusObservation",
        ),
    ],
)
def test_each_documented_public_channel_parses_to_one_bound_observation(
    frame: str, expected_type: str
) -> None:
    outcomes = _parse(frame)
    assert len(outcomes) == 1
    assert type(outcomes[0]).__name__ == expected_type
    assert outcomes[0].source.catalogue_snapshot_id == UUID("20000000-0000-0000-0000-000000000002")


def _ticker_row(*, product_id: int, symbol: str) -> str:
    return (
        f'{{"i":{product_id},"s":"{symbol}","m":"100.00","m24hc":"1.5",'
        '"ohlc":["90","110","80","100"],"oi":["3","1"],'
        '"pb":["70","130"],"q":["72101","2","72100","3","72100.5"],'
        '"qiv":["0.4","0.5","0.45"],"to":["500","20"],'
        '"g":["0.1","0.2","0.3","0.4","0.5"]}'
    )


def test_batched_ticker_validates_compact_arrays_and_sanitizes_only_bad_row() -> None:
    frame = (
        '{"type":"ticker","ts":1793872800000000,"sy":"XBTUSD_CAT",'
        '"sp":"100.10","d":['
        + _ticker_row(product_id=1101, symbol="XBTUSD_CAT")
        + ","
        + _ticker_row(product_id=9999, symbol="SECRET-CANARY")
        + "]}"
    )
    outcomes = _parse(frame)
    ticker = outcomes[0]
    assert type(ticker).__name__ == "TickerObservation"
    assert ticker.spot_price.as_tuple().exponent == -2
    assert ticker.close == 100
    assert ticker.best_ask == 72101
    assert ticker.best_bid == 72100
    assert ticker.lower_price_limit == 70
    assert ticker.upper_price_limit == 130
    assert ticker.open_interest == 3
    assert ticker.turnover == 500
    assert type(outcomes[1]).__name__ == "CatalogueMismatchSignal"
    assert "SECRET-CANARY" not in repr(outcomes[1])


def test_ob_updates_preserves_numeric_lexemes_and_action_error_is_typed_repair() -> None:
    snapshot = _parse(
        '{"type":"ob_updates","action":"snapshot","a":[[100.00,2e0]],'
        '"b":[[99,3]],"ts":1793872800000000,"seq":5,'
        '"sy":"XBTUSD_CAT","cs":123}'
    )[0]
    assert snapshot.asks[0].price_lexeme == "100.00"
    assert snapshot.asks[0].size_lexeme == "2e0"
    assert snapshot.checksum_lexeme == "123"
    assert snapshot.source.venue_sequence == 5

    repair = _parse(
        '{"type":"ob_updates","action":"error","symbol":"XBTUSD_CAT","msg":"snapshot unavailable"}'
    )[0]
    assert type(repair).__name__ == "BookStreamError"
    assert repair.symbol == "XBTUSD_CAT"


def test_ticker_accepts_documented_nullable_option_fields_without_weakening_shape() -> None:
    ticker = _parse(
        '{"type":"ticker","ts":1793872800000000,"sy":"XBTUSD_CAT",'
        '"sp":"100.10","d":[{"i":1101,"s":"XBTUSD_CAT",'
        '"m":"100.00","m24hc":"1.5","ohlc":["90","110","80","100"],'
        '"oi":["3","1"],"pb":["70","130"],'
        '"q":["101","2","100","3",null],'
        '"qiv":[null,null,null],"to":["500","20"],'
        '"g":[null,null,null,null,null]}]}'
    )[0]
    assert ticker.best_ask == 101
    assert ticker.best_bid == 100


def test_non_mark_candle_requires_volume_but_mark_candle_documents_absence() -> None:
    with pytest.raises(ExchangeProtocolError):
        _parse(
            '{"type":"candlestick_1m","sy":"XBTUSD_CAT",'
            '"ts":1793872740000000,"res":"1m","o":"99","h":"102",'
            '"l":"98","c":"101"}'
        )


@pytest.mark.parametrize(
    "frame",
    [
        '{"type":"ob_l2","sy":"XBTUSD_CAT","ts":1793872800000000,'
        '"lts":1793872799000000,"a":[["101","1"],["100","1"]],"b":[]}',
        '{"type":"ob_l2","sy":"XBTUSD_CAT","ts":1793872800000000,'
        '"lts":1793872799000000,"a":[],"b":[["99","1"],["100","1"]]}',
    ],
)
def test_book_sides_reject_out_of_order_wire_rows_instead_of_sorting(frame: str) -> None:
    with pytest.raises(ExchangeProtocolError):
        _parse(frame)


@pytest.mark.parametrize("bad", ("+1", " 1", "1 ", "01", ".5"))
def test_numeric_strings_require_strict_json_number_grammar(bad: str) -> None:
    with pytest.raises(ExchangeProtocolError):
        _parse(
            '{"type":"trades","sy":"XBTUSD_CAT","ts":1793872800000000,'
            f'"t":1793872799000000,"p":"{bad}","s":"2","r":"m"}}'
        )


@pytest.mark.parametrize(
    ("channel", "frame"),
    [
        (
            "ob_l1",
            '{"type":"ob_l1","sy":"UNKNOWN","ts":1793872800000000,'
            '"lts":1793872799000000,"ap":"101","as":"1","bp":"100","bs":"1"}',
        ),
        (
            "ob_l2",
            '{"type":"ob_l2","sy":"UNKNOWN","ts":1793872800000000,'
            '"lts":1793872799000000,"a":[],"b":[]}',
        ),
        (
            "ob_updates",
            '{"type":"ob_updates","action":"error","symbol":"UNKNOWN","msg":"missing"}',
        ),
        (
            "trades",
            '{"type":"trades","sy":"UNKNOWN","ts":1793872800000000,'
            '"t":1793872799000000,"p":"100","s":"2","r":"m"}',
        ),
        (
            "mark_price",
            '{"type":"mark_price","sy":"MARK:UNKNOWN","ts":1793872800000000,"p":"100"}',
        ),
        (
            "candlestick_1m",
            '{"type":"candlestick_1m","sy":"UNKNOWN","ts":1793872740000000,'
            '"res":"1m","o":"99","h":"102","l":"98","c":"101","v":"5"}',
        ),
        (
            "funding_rate",
            '{"type":"funding_rate","sy":"UNKNOWN","ts":1793872800000000,'
            '"fi":28800,"fr":"-0.0001","nfr":1793894400000000}',
        ),
        (
            "product_updates",
            '{"type":"product_updates","event":"market_disruption",'
            '"product":{"id":9999,"symbol":"UNKNOWN",'
            '"trading_status":"disrupted_cancel_only"},'
            '"timestamp":1793872800000000}',
        ),
    ],
)
def test_every_product_channel_emits_typed_catalogue_mismatch(channel: str, frame: str) -> None:
    outcome = _parse(frame)[0]
    assert type(outcome).__name__ == "CatalogueMismatchSignal"
    assert outcome.source.channel == channel
    assert outcome.source.symbol is None


def test_mark_candle_has_no_volume_and_remains_provisional() -> None:
    candle = _parse(
        '{"type":"candlestick_1m","sy":"MARK:XBTUSD_CAT",'
        '"ts":1793872740000000,"res":"1m","o":"99","h":"102",'
        '"l":"98","c":"101"}'
    )[0]
    assert candle.symbol == "XBTUSD_CAT"
    assert candle.volume is None
    assert candle.complete is False


@pytest.mark.parametrize(
    "frame",
    [
        '{"type":"unknown","ts":1793872800000000}',
        '{"type":"ob_l1","sy":"XBTUSD_CAT","ts":1,"ap":"1","as":"1"}',
        '{"type":"ob_l2","sy":"XBTUSD_CAT","ts":1793872800000000,'
        '"lts":1,"a":[["100","1","EXTRA"]],"b":[]}',
        '{"type":"trades","sy":"XBTUSD_CAT","ts":1793872800000000,'
        '"t":1793872800000000,"p":"100","s":"1.5","r":"m"}',
        '{"type":"product_updates","event":"finish_auction",'
        '"product":{"id":1102,"symbol":"XBTUSD_CAT","trading_status":"operational"},'
        '"timestamp":"2026-11-05T10:00:00Z"}',
        '{"type":"ticker","ts":1793872800000000,"sy":"XBTUSD_CAT",'
        '"sp":"100","d":[{"i":1101,"s":"XBTUSD_CAT","m":"100",'
        '"m24hc":"1","ohlc":["1","2","3"],"oi":["1","2"],'
        '"pb":["1","2"],"q":["1","2","3","4","5"],'
        '"qiv":["1","2","3"],"to":["1","2"],'
        '"g":["1","2","3","4","5"]}]}',
    ],
)
def test_schema_drift_nonintegral_contracts_and_unbound_identity_fail_closed(
    frame: str,
) -> None:
    with pytest.raises(ExchangeProtocolError) as captured:
        _parse(frame)
    assert "SECRET" not in repr(captured.value)
    assert captured.value.context == {"operation": "parse_delta_public_ws", "scope": "public"}


def test_unbound_spot_index_emits_sanitized_catalogue_refresh_signal() -> None:
    outcome = _parse('{"type":"spot_price","sy":".UNBOUND","ts":1793872800000000,"p":"1"}')[0]
    assert type(outcome).__name__ == "CatalogueMismatchSignal"
    assert outcome.source.channel == "spot_price"
    assert outcome.source.symbol is None
