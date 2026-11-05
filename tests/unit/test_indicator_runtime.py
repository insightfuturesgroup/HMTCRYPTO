"""Golden parity and hostile-input tests for pure strategy indicators."""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hmt_crypto.strategy import indicators as indicator_module
from hmt_crypto.strategy.indicators import (
    IndicatorInputError,
    calculate_adx,
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_heikin_ashi,
    calculate_macd,
    calculate_rsi,
    calculate_sma,
    calculate_stochastic,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/indicators/hmt_indicators_golden_v1.json"


class _CallbackProbe:
    calls = 0

    @classmethod
    def hit(cls) -> None:
        cls.calls += 1


class _EvilFloat(float):
    def __float__(self) -> float:
        _CallbackProbe.hit()
        return super().__float__()


class _EvilInt(int):
    def __float__(self) -> float:
        _CallbackProbe.hit()
        return super().__float__()

    def __le__(self, other: object) -> bool:
        _CallbackProbe.hit()
        return super().__le__(other)


class _EvilList(list[float]):
    def __iter__(self):  # type: ignore[no-untyped-def]
        _CallbackProbe.hit()
        return super().__iter__()

    def __len__(self) -> int:
        _CallbackProbe.hit()
        return super().__len__()

    def __getitem__(self, key):  # type: ignore[no-untyped-def]
        _CallbackProbe.hit()
        return super().__getitem__(key)


class _EvilMap(dict[str, float]):
    def __iter__(self):  # type: ignore[no-untyped-def]
        _CallbackProbe.hit()
        return super().__iter__()

    def __len__(self) -> int:
        _CallbackProbe.hit()
        return super().__len__()

    def __getitem__(self, key: str) -> float:
        _CallbackProbe.hit()
        return super().__getitem__(key)


class _EvilStr(str):
    def __hash__(self) -> int:
        _CallbackProbe.hit()
        return super().__hash__()

    def __eq__(self, other: object) -> bool:
        _CallbackProbe.hit()
        return super().__eq__(other)


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _assert_close(actual: object, expected: object, *, absolute: float, relative: float) -> None:
    if expected is None:
        assert actual is None
        return
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        assert isinstance(actual, (int, float)) and not isinstance(actual, bool)
        assert math.isclose(float(actual), float(expected), abs_tol=absolute, rel_tol=relative)
        return
    if isinstance(expected, list):
        assert isinstance(actual, list)
        assert len(actual) == len(expected)
        for actual_item, expected_item in zip(actual, expected, strict=True):
            _assert_close(
                actual_item,
                expected_item,
                absolute=absolute,
                relative=relative,
            )
        return
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        assert actual.keys() == expected.keys()
        for key, expected_item in expected.items():
            _assert_close(
                actual[key],
                expected_item,
                absolute=absolute,
                relative=relative,
            )
        return
    assert actual == expected


def _execute_case(case: dict[str, Any], candles: list[dict[str, Any]]) -> object:
    closes = [candle["close"] for candle in candles]
    highs = [candle["high"] for candle in candles]
    lows = [candle["low"] for candle in candles]
    parameters = case["parameters"]
    name = case["indicator"]
    if name == "rsi":
        return calculate_rsi(closes, period=parameters["period"])
    if name == "ema":
        return calculate_ema(closes, period=parameters["period"])
    if name == "sma":
        return calculate_sma(closes, period=parameters["period"])
    if name == "bollinger_bands":
        return calculate_bollinger_bands(
            closes,
            period=parameters["period"],
            std_dev=parameters["std_dev"],
        )
    if name == "adx":
        return calculate_adx(highs, lows, closes, period=parameters["period"])
    if name == "macd":
        return calculate_macd(
            closes,
            fast_period=parameters["fast_period"],
            slow_period=parameters["slow_period"],
            signal_period=parameters["signal_period"],
        )
    if name == "stochastic":
        return calculate_stochastic(
            highs,
            lows,
            closes,
            period=parameters["period"],
            signal_period=parameters["signal_period"],
        )
    if name == "atr":
        return calculate_atr(highs, lows, closes, period=parameters["period"])
    if name == "heikin_ashi":
        return calculate_heikin_ashi(candles)
    raise AssertionError(f"unknown fixture indicator: {name}")


@pytest.mark.parametrize(
    "indicator",
    [
        "rsi",
        "ema",
        "sma",
        "bollinger_bands",
        "adx",
        "macd",
        "stochastic",
        "atr",
        "heikin_ashi",
    ],
)
def test_runtime_matches_the_archived_golden_outputs(indicator: str) -> None:
    fixture = _fixture()
    case = next(item for item in fixture["indicators"] if item["indicator"] == indicator)
    candles = fixture["input"]["candles"]
    tolerance = case["tolerance"]

    actual = _execute_case(case, candles)

    _assert_close(
        actual,
        case["expected"],
        absolute=float(tolerance["absolute"]),
        relative=float(tolerance["relative"]),
    )


def test_runtime_is_deterministic_and_does_not_mutate_inputs() -> None:
    fixture = _fixture()
    candles = fixture["input"]["candles"]
    original = copy.deepcopy(candles)

    first = [_execute_case(case, candles) for case in fixture["indicators"]]
    second = [_execute_case(case, candles) for case in fixture["indicators"]]

    assert first == second
    assert candles == original


@pytest.mark.parametrize("bad_value", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize(
    "invoke",
    [
        pytest.param(lambda value: calculate_rsi([1.0, value], period=2), id="rsi"),
        pytest.param(lambda value: calculate_ema([1.0, value], period=2), id="ema"),
        pytest.param(lambda value: calculate_sma([1.0, value], period=2), id="sma"),
        pytest.param(
            lambda value: calculate_bollinger_bands([1.0, value], period=2),
            id="bollinger",
        ),
        pytest.param(
            lambda value: calculate_adx([2.0, value], [0.5, 1.0], [1.0, 1.5], period=2),
            id="adx",
        ),
        pytest.param(lambda value: calculate_macd([1.0, value], 1, 2, 1), id="macd"),
        pytest.param(
            lambda value: calculate_stochastic(
                [2.0, value], [0.5, 1.0], [1.0, 1.5], period=2, signal_period=1
            ),
            id="stochastic",
        ),
        pytest.param(
            lambda value: calculate_atr([2.0, value], [0.5, 1.0], [1.0, 1.5], period=1),
            id="atr",
        ),
        pytest.param(
            lambda value: calculate_heikin_ashi(
                [{"open": 1.0, "high": 2.0, "low": 0.5, "close": value}]
            ),
            id="heikin_ashi",
        ),
    ],
)
def test_non_finite_inputs_are_rejected(
    invoke: Callable[[float], object], bad_value: float
) -> None:
    with pytest.raises(IndicatorInputError):
        invoke(bad_value)


@pytest.mark.parametrize(
    "bad_value",
    [
        pytest.param(True, id="bool"),
        pytest.param(None, id="none"),
        pytest.param("1.0", id="text"),
        pytest.param(10**10_000, id="unrepresentable_integer"),
    ],
)
def test_non_numeric_or_unrepresentable_series_values_are_rejected(bad_value: object) -> None:
    with pytest.raises(IndicatorInputError):
        calculate_sma([bad_value], period=1)  # type: ignore[list-item]


@pytest.mark.parametrize(
    "invoke",
    [
        lambda: calculate_adx([2.0], [1.0, 1.5], [1.5], period=2),
        lambda: calculate_atr([2.0], [1.0, 1.5], [1.5], period=2),
        lambda: calculate_stochastic([2.0], [1.0, 1.5], [1.5], period=2),
    ],
)
def test_mismatched_ohlc_lengths_are_rejected(invoke: Callable[[], object]) -> None:
    with pytest.raises(IndicatorInputError):
        invoke()


@pytest.mark.parametrize("period", [0, -1, True, 1.5])
@pytest.mark.parametrize(
    "invoke",
    [
        lambda period: calculate_rsi([1.0, 2.0], period=period),
        lambda period: calculate_ema([1.0, 2.0], period=period),
        lambda period: calculate_sma([1.0, 2.0], period=period),
        lambda period: calculate_bollinger_bands([1.0, 2.0], period=period),
        lambda period: calculate_adx([2.0], [1.0], [1.5], period=period),
        lambda period: calculate_atr([2.0], [1.0], [1.5], period=period),
        lambda period: calculate_stochastic([2.0], [1.0], [1.5], period=period),
    ],
)
def test_invalid_periods_are_rejected(invoke: Callable[[object], object], period: object) -> None:
    with pytest.raises(IndicatorInputError):
        invoke(period)


@pytest.mark.parametrize(
    ("fast", "slow", "signal"),
    [(0, 3, 2), (3, 3, 2), (4, 3, 2), (2, 3, 0), (True, 3, 2)],
)
def test_invalid_macd_period_relationships_are_rejected(
    fast: object, slow: object, signal: object
) -> None:
    with pytest.raises(IndicatorInputError):
        calculate_macd([1.0, 2.0, 3.0], fast, slow, signal)


@pytest.mark.parametrize("std_dev", [-1.0, math.nan, math.inf, True])
def test_invalid_bollinger_deviations_are_rejected(std_dev: object) -> None:
    with pytest.raises(IndicatorInputError):
        calculate_bollinger_bands([1.0, 2.0], period=2, std_dev=std_dev)


def test_numeric_indicators_return_empty_outputs_during_warmup() -> None:
    assert calculate_rsi([], period=3) == []
    assert calculate_ema([1.0, 2.0], period=3) == []
    assert calculate_sma([1.0, 2.0], period=3) == []
    assert calculate_bollinger_bands([1.0, 2.0], period=3) == []
    assert calculate_adx([1.0], [0.5], [0.75], period=3) == []
    assert calculate_macd([1.0, 2.0], 2, 3, 2) == []
    assert calculate_stochastic([1.0], [0.5], [0.75], period=3) == []
    assert calculate_atr([1.0], [0.5], [0.75], period=3) == []


def test_heikin_ashi_preserves_the_archived_empty_input_error_class() -> None:
    with pytest.raises(TypeError):
        calculate_heikin_ashi([])


@pytest.mark.parametrize(
    "candles",
    [
        [{}],
        [{"open": 1.0, "high": 2.0, "low": 0.5}],
        [{"open": 1.0, "high": 0.9, "low": 0.5, "close": 0.8}],
        [{"open": 1.0, "high": 2.0, "low": 1.1, "close": 1.2}],
    ],
)
def test_invalid_heikin_ashi_candles_are_rejected(candles: list[dict[str, float]]) -> None:
    with pytest.raises(IndicatorInputError):
        calculate_heikin_ashi(candles)


def _assert_rejected_without_callbacks(invoke: Callable[[], object]) -> None:
    _CallbackProbe.calls = 0
    with pytest.raises(IndicatorInputError):
        invoke()
    assert _CallbackProbe.calls == 0


@pytest.mark.parametrize(
    "invoke",
    [
        pytest.param(lambda: calculate_sma([_EvilFloat(1.0)], 1), id="float-subclass"),
        pytest.param(lambda: calculate_sma([_EvilInt(1)], 1), id="int-subclass"),
        pytest.param(lambda: calculate_sma(_EvilList([1.0]), 1), id="list-subclass"),
        pytest.param(
            lambda: calculate_heikin_ashi([_EvilMap(open=1.0, high=2.0, low=0.5, close=1.5)]),
            id="map-subclass",
        ),
        pytest.param(lambda: calculate_sma([1.0], _EvilInt(1)), id="period-int-subclass"),
    ],
)
def test_callback_bearing_subclasses_are_rejected_without_invocation(
    invoke: Callable[[], object],
) -> None:
    _assert_rejected_without_callbacks(invoke)


@pytest.mark.parametrize(
    "values",
    [
        pytest.param(range(3), id="range"),
        pytest.param({1.0, 2.0}, id="set"),
        pytest.param({0: 1.0}, id="dict"),
        pytest.param((value for value in [1.0]), id="generator"),
    ],
)
def test_series_accept_only_exact_builtin_list_or_tuple(values: object) -> None:
    with pytest.raises(IndicatorInputError):
        calculate_sma(values, 1)  # type: ignore[arg-type]


def test_exact_builtin_tuple_and_numeric_types_are_accepted() -> None:
    assert calculate_sma((1, 2.0), period=2) == [1.5]


def test_documented_period_and_series_limits_include_the_boundary() -> None:
    max_period = indicator_module.MAX_PERIOD
    max_length = indicator_module.MAX_SERIES_LENGTH

    assert calculate_sma((1.0,), period=max_period) == []
    assert len(calculate_sma([1.0] * max_length, period=max_period)) == (
        max_length - max_period + 1
    )


def test_period_and_series_limits_reject_one_past_the_boundary() -> None:
    with pytest.raises(IndicatorInputError):
        calculate_sma([1.0], period=indicator_module.MAX_PERIOD + 1)
    with pytest.raises(IndicatorInputError):
        calculate_sma(
            [1.0] * (indicator_module.MAX_SERIES_LENGTH + 1),
            period=1,
        )


@pytest.mark.parametrize(
    "invoke",
    [
        pytest.param(lambda: calculate_sma([1e308, 1e308], 2), id="sma"),
        pytest.param(lambda: calculate_ema([1e308, 1e308], 2), id="ema"),
        pytest.param(lambda: calculate_rsi([-1e308, 1e308], 1), id="rsi"),
        pytest.param(lambda: calculate_bollinger_bands([-1e308, 1e308], 2), id="bollinger"),
        pytest.param(lambda: calculate_macd([1e308, 1e308], 1, 2, 1), id="macd"),
        pytest.param(
            lambda: calculate_atr([1e308, 1e308], [-1e308, -1e308], [0.0, 0.0], 1),
            id="atr",
        ),
        pytest.param(
            lambda: calculate_stochastic([1e308, 1e308], [-1e308, -1e308], [0.0, 0.0], 2, 1),
            id="stochastic",
        ),
        pytest.param(
            lambda: calculate_adx(
                [1e308, 1e308, 1e308],
                [-1e308, -1e308, -1e308],
                [0.0, 0.0, 0.0],
                1,
            ),
            id="adx",
        ),
        pytest.param(
            lambda: calculate_heikin_ashi(
                [{"open": 1e308, "high": 1e308, "low": -1e308, "close": 1e308}]
            ),
            id="heikin-ashi",
        ),
    ],
)
def test_overflowing_intermediates_fail_closed(invoke: Callable[[], object]) -> None:
    with pytest.raises(IndicatorInputError):
        invoke()


@pytest.mark.parametrize(
    "invoke",
    [
        pytest.param(lambda: calculate_bollinger_bands([5.0, 5.0, 5.0], 3), id="bollinger"),
        pytest.param(lambda: calculate_adx([5.0] * 6, [5.0] * 6, [5.0] * 6, 2), id="adx"),
    ],
)
def test_undefined_constant_ranges_fail_closed(invoke: Callable[[], object]) -> None:
    with pytest.raises(IndicatorInputError):
        invoke()


def test_heikin_ashi_preserves_safe_extra_builtin_keys() -> None:
    valid = {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}
    assert calculate_heikin_ashi([{**valid, "volume": 10.0}]) == [
        {"open": 1.25, "high": 2.0, "low": 0.5, "close": 1.25}
    ]


def test_heikin_ashi_rejects_key_subclasses_without_callbacks() -> None:
    candle = {
        _EvilStr("open"): 1.0,
        "high": 2.0,
        "low": 0.5,
        "close": 1.5,
    }
    _assert_rejected_without_callbacks(lambda: calculate_heikin_ashi([candle]))


def test_every_emitted_numeric_is_finite() -> None:
    fixture = _fixture()
    outputs = [_execute_case(case, fixture["input"]["candles"]) for case in fixture["indicators"]]

    def assert_finite(value: object) -> None:
        if value is None:
            return
        if type(value) in (int, float):
            assert math.isfinite(float(value))
            return
        if type(value) is list:
            for item in value:
                assert_finite(item)
            return
        if type(value) is dict:
            for item in value.values():
                assert_finite(item)
            return
        raise AssertionError(f"unexpected emitted value: {type(value)!r}")

    assert_finite(outputs)
