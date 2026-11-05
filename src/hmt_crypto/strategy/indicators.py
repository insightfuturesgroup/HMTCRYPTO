"""Pure technical indicators characterized against the archived HMT oracle."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import TypedDict, cast

# Public hard bounds; the boundary value itself is accepted. Both are checked
# before any caller-owned container is traversed or output storage is allocated.
MAX_SERIES_LENGTH = 100_000
MAX_PERIOD = 10_000


class IndicatorInputError(ValueError):
    """A technical-indicator input violated the finite numeric contract."""

    def __init__(self) -> None:
        super().__init__("indicator input is invalid")


class BollingerValue(TypedDict):
    """One emitted Bollinger-band observation."""

    upper: float
    middle: float
    lower: float
    pb: float


class AdxValue(TypedDict):
    """One emitted directional-movement observation."""

    adx: float
    pdi: float
    mdi: float


class MacdValue(TypedDict):
    """One emitted MACD observation; signal components warm up separately."""

    macd: float
    signal: float | None
    histogram: float | None


class StochasticValue(TypedDict):
    """One emitted stochastic observation; ``d`` warms up after ``k``."""

    k: float
    d: float | None


class HeikinAshiValue(TypedDict):
    """One emitted Heikin-Ashi candle."""

    open: float
    high: float
    low: float
    close: float


def _invalid() -> IndicatorInputError:
    return IndicatorInputError()


def _period(value: object) -> int:
    if type(value) is not int or value <= 0 or value > MAX_PERIOD:
        raise _invalid()
    return value


def _finite(value: object) -> float:
    if type(value) not in (int, float):
        raise _invalid()
    try:
        normalized = float(cast("int | float", value))
    except (OverflowError, ValueError):
        raise _invalid() from None
    if not math.isfinite(normalized):
        raise _invalid()
    return normalized


def _result(value: float) -> float:
    if not math.isfinite(value):
        raise _invalid()
    return value


def _add(left: float, right: float) -> float:
    return _result(left + right)


def _subtract(left: float, right: float) -> float:
    return _result(left - right)


def _multiply(left: float, right: float) -> float:
    return _result(left * right)


def _divide(numerator: float, denominator: float) -> float:
    if denominator == 0:
        raise _invalid()
    return _result(numerator / denominator)


def _container_length(values: object) -> int:
    if type(values) not in (list, tuple):
        raise _invalid()
    container = cast("list[object] | tuple[object, ...]", values)
    size = len(container)  # exact built-ins cannot dispatch caller callbacks
    if size > MAX_SERIES_LENGTH:
        raise _invalid()
    return size


def _series(values: Sequence[float]) -> list[float]:
    _container_length(values)
    return [_finite(value) for value in values]


def _ohlc(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
) -> tuple[list[float], list[float], list[float]]:
    sizes = (
        _container_length(highs),
        _container_length(lows),
        _container_length(closes),
    )
    if sizes[0] != sizes[1] or sizes[0] != sizes[2]:
        raise _invalid()
    checked_highs = _series(highs)
    checked_lows = _series(lows)
    checked_closes = _series(closes)
    for high, low, close in zip(checked_highs, checked_lows, checked_closes, strict=True):
        if high < low or high < close or low > close:
            raise _invalid()
    return checked_highs, checked_lows, checked_closes


def _sma(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    total = 0.0
    for value in values[:period]:
        total = _add(total, value)
    result = [_divide(total, float(period))]
    for index in range(period, len(values)):
        total = _subtract(total, values[index - period])
        total = _add(total, values[index])
        result.append(_divide(total, float(period)))
    return result


def _ema(values: list[float], period: int, exponent: float) -> list[float]:
    seeds = _sma(values, period)
    if not seeds:
        return []
    previous = seeds[0]
    result = [previous]
    for value in values[period:]:
        delta = _subtract(value, previous)
        previous = _add(_multiply(delta, exponent), previous)
        result.append(previous)
    return result


def _true_ranges(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    return [
        max(
            _subtract(highs[index], lows[index]),
            abs(_subtract(highs[index], closes[index - 1])),
            abs(_subtract(lows[index], closes[index - 1])),
        )
        for index in range(1, len(highs))
    ]


def _wilder_average(values: list[float], period: int) -> list[float]:
    return _ema(values, period, _divide(1.0, float(period)))


def _wilder_sums(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    total = 0.0
    for value in values[:period]:
        total = _add(total, value)
    result = [total]
    for value in values[period:]:
        total = _subtract(total, _divide(total, float(period)))
        total = _add(total, value)
        result.append(total)
    return result


def calculate_sma(values: Sequence[float], period: int = 20) -> list[float]:
    """Return compressed simple-moving-average outputs after the warmup window."""

    checked_period = _period(period)
    return _sma(_series(values), checked_period)


def calculate_ema(values: Sequence[float], period: int = 20) -> list[float]:
    """Return compressed EMA outputs seeded by an SMA of the same period."""

    checked_period = _period(period)
    exponent = _divide(2.0, float(checked_period + 1))
    return _ema(_series(values), checked_period, exponent)


def calculate_rsi(values: Sequence[float], period: int = 14) -> list[float]:
    """Return Wilder RSI outputs rounded to the archived oracle's two decimals."""

    checked_period = _period(period)
    checked = _series(values)
    if len(checked) <= checked_period:
        return []
    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, len(checked)):
        change = _subtract(checked[index], checked[index - 1])
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    average_gain = _sma(gains[:checked_period], checked_period)[0]
    average_loss = _sma(losses[:checked_period], checked_period)[0]
    result: list[float] = []
    for index in range(checked_period - 1, len(gains)):
        if index >= checked_period:
            weight = float(checked_period - 1)
            divisor = float(checked_period)
            average_gain = _divide(_add(_multiply(average_gain, weight), gains[index]), divisor)
            average_loss = _divide(_add(_multiply(average_loss, weight), losses[index]), divisor)
        if average_loss == 0:
            result.append(100.0)
        elif average_gain == 0:
            result.append(0.0)
        else:
            relative_strength = _divide(average_gain, average_loss)
            denominator = _add(1.0, relative_strength)
            rsi = _subtract(100.0, _divide(100.0, denominator))
            result.append(_result(float(f"{rsi:.2f}")))
    return result


def calculate_bollinger_bands(
    values: Sequence[float],
    period: int = 20,
    std_dev: float = 2.0,
) -> list[BollingerValue]:
    """Return population-standard-deviation Bollinger observations."""

    checked_period = _period(period)
    checked = _series(values)
    checked_std_dev = _finite(std_dev)
    if checked_std_dev < 0:
        raise _invalid()
    means = _sma(checked, checked_period)
    result: list[BollingerValue] = []
    for offset, middle in enumerate(means):
        window = checked[offset : offset + checked_period]
        squared: list[float] = []
        for value in window:
            distance = _subtract(value, middle)
            squared.append(_multiply(distance, distance))
        variance = _sma(squared, checked_period)[0]
        deviation = _result(math.sqrt(variance))
        spread = _multiply(deviation, checked_std_dev)
        upper = _add(middle, spread)
        lower = _subtract(middle, spread)
        result.append(
            {
                "upper": upper,
                "middle": middle,
                "lower": lower,
                "pb": _divide(_subtract(window[-1], lower), _subtract(upper, lower)),
            }
        )
    return result


def calculate_atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> list[float]:
    """Return compressed Wilder average-true-range outputs."""

    checked_period = _period(period)
    checked_highs, checked_lows, checked_closes = _ohlc(highs, lows, closes)
    return _wilder_average(
        _true_ranges(checked_highs, checked_lows, checked_closes), checked_period
    )


def calculate_stochastic(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
    signal_period: int = 3,
) -> list[StochasticValue]:
    """Return stochastic K immediately and D after its independent SMA warmup."""

    checked_period = _period(period)
    checked_signal = _period(signal_period)
    checked_highs, checked_lows, checked_closes = _ohlc(highs, lows, closes)
    if len(checked_highs) < checked_period:
        return []
    k_values: list[float] = []
    for index in range(checked_period - 1, len(checked_highs)):
        start = index - checked_period + 1
        period_high = max(checked_highs[start : index + 1])
        period_low = min(checked_lows[start : index + 1])
        price_range = _subtract(period_high, period_low)
        k_value = (
            0.0
            if price_range == 0
            else _multiply(
                _divide(_subtract(checked_closes[index], period_low), price_range),
                100.0,
            )
        )
        k_values.append(k_value)
    d_values = _sma(k_values, checked_signal)
    return [
        {
            "k": value,
            "d": None if index < checked_signal - 1 else d_values[index - checked_signal + 1],
        }
        for index, value in enumerate(k_values)
    ]


def calculate_macd(
    values: Sequence[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> list[MacdValue]:
    """Return EMA-based MACD with null signal components during signal warmup."""

    fast = _period(fast_period)
    slow = _period(slow_period)
    signal = _period(signal_period)
    if fast >= slow:
        raise _invalid()
    checked = _series(values)
    if len(checked) < slow:
        return []
    fast_values = _ema(checked, fast, _divide(2.0, float(fast + 1)))
    slow_values = _ema(checked, slow, _divide(2.0, float(slow + 1)))
    macd_values = [
        _subtract(fast_values[index + slow - fast], slow_value)
        for index, slow_value in enumerate(slow_values)
    ]
    signal_values = _ema(macd_values, signal, _divide(2.0, float(signal + 1)))
    result: list[MacdValue] = []
    for index, macd_value in enumerate(macd_values):
        signal_value = None if index < signal - 1 else signal_values[index - signal + 1]
        result.append(
            {
                "macd": macd_value,
                "signal": signal_value,
                "histogram": (
                    None if signal_value is None else _subtract(macd_value, signal_value)
                ),
            }
        )
    return result


def calculate_adx(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> list[AdxValue]:
    """Return Wilder ADX and its positive/negative directional components."""

    checked_period = _period(period)
    checked_highs, checked_lows, checked_closes = _ohlc(highs, lows, closes)
    true_ranges = _true_ranges(checked_highs, checked_lows, checked_closes)
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for index in range(1, len(checked_highs)):
        up_move = _subtract(checked_highs[index], checked_highs[index - 1])
        down_move = _subtract(checked_lows[index - 1], checked_lows[index])
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
    smoothed_tr = _wilder_sums(true_ranges, checked_period)
    smoothed_plus = _wilder_sums(plus_dm, checked_period)
    smoothed_minus = _wilder_sums(minus_dm, checked_period)
    pdi_values = [
        _multiply(_divide(plus, true_range), 100.0)
        for plus, true_range in zip(smoothed_plus, smoothed_tr, strict=True)
    ]
    mdi_values = [
        _multiply(_divide(minus, true_range), 100.0)
        for minus, true_range in zip(smoothed_minus, smoothed_tr, strict=True)
    ]
    dx_values = [
        _multiply(
            _divide(abs(_subtract(pdi, mdi)), _add(pdi, mdi)),
            100.0,
        )
        for pdi, mdi in zip(pdi_values, mdi_values, strict=True)
    ]
    adx_values = _wilder_average(dx_values, checked_period)
    return [
        {
            "adx": adx_value,
            "pdi": pdi_values[index + checked_period - 1],
            "mdi": mdi_values[index + checked_period - 1],
        }
        for index, adx_value in enumerate(adx_values)
    ]


def calculate_heikin_ashi(
    candles: Sequence[Mapping[str, float]],
) -> list[HeikinAshiValue]:
    """Return one Heikin-Ashi candle per exact-dict, range-consistent candle."""

    _container_length(candles)
    if not candles:
        raise TypeError("at least one candle is required")
    checked: list[tuple[float, float, float, float]] = []
    for candle in candles:
        if type(candle) is not dict:
            raise _invalid()
        keys = list(candle)
        if any(type(key) is not str for key in keys):
            raise _invalid()
        if not {"open", "high", "low", "close"}.issubset(keys):
            raise _invalid()
        open_value = _finite(candle["open"])
        high = _finite(candle["high"])
        low = _finite(candle["low"])
        close = _finite(candle["close"])
        if high < max(open_value, close) or low > min(open_value, close):
            raise _invalid()
        checked.append((open_value, high, low, close))
    previous_open = checked[0][0]
    previous_close = checked[0][3]
    result: list[HeikinAshiValue] = []
    for open_value, high, low, close in checked:
        close_total = _add(_add(open_value, high), _add(low, close))
        ha_close = _divide(close_total, 4.0)
        ha_open = _divide(_add(previous_open, previous_close), 2.0)
        result.append(
            {
                "open": ha_open,
                "high": max(high, ha_open, ha_close),
                "low": min(low, ha_open, ha_close),
                "close": ha_close,
            }
        )
        previous_open = ha_open
        previous_close = ha_close
    return result


__all__ = [
    "AdxValue",
    "BollingerValue",
    "HeikinAshiValue",
    "IndicatorInputError",
    "MAX_PERIOD",
    "MAX_SERIES_LENGTH",
    "MacdValue",
    "StochasticValue",
    "calculate_adx",
    "calculate_atr",
    "calculate_bollinger_bands",
    "calculate_ema",
    "calculate_heikin_ashi",
    "calculate_macd",
    "calculate_rsi",
    "calculate_sma",
    "calculate_stochastic",
]
