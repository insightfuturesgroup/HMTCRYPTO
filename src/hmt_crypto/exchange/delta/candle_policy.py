"""Local integrity and completeness policy for Delta candle series."""

from __future__ import annotations

from datetime import timedelta

from hmt_crypto.domain import Candle
from hmt_crypto.errors import ExchangeProtocolError
from hmt_crypto.exchange.base import CandleRequest, CandleResolution

_RESOLUTION_SECONDS = {
    CandleResolution.ONE_MINUTE: 60,
    CandleResolution.THREE_MINUTES: 180,
    CandleResolution.FIVE_MINUTES: 300,
    CandleResolution.FIFTEEN_MINUTES: 900,
    CandleResolution.THIRTY_MINUTES: 1_800,
    CandleResolution.ONE_HOUR: 3_600,
    CandleResolution.TWO_HOURS: 7_200,
    CandleResolution.FOUR_HOURS: 14_400,
    CandleResolution.SIX_HOURS: 21_600,
    CandleResolution.ONE_DAY: 86_400,
    CandleResolution.ONE_WEEK: 604_800,
}


def normalize_candles(
    candles: tuple[Candle, ...],
    *,
    request: CandleRequest,
    maximum: int,
    context: dict[str, object],
) -> tuple[Candle, ...]:
    """Validate a bounded inclusive request interval and sort it ascending."""
    timestamps = tuple(candle.timestamp for candle in candles)
    duration = _RESOLUTION_SECONDS[request.resolution]
    maximum_buckets = (request.end_epoch_seconds - request.start_epoch_seconds) // duration + 1
    if (
        len(candles) > maximum
        or len(candles) > maximum_buckets
        or len(set(timestamps)) != len(timestamps)
        or any(timestamp < request.start or timestamp > request.end for timestamp in timestamps)
    ):
        raise ExchangeProtocolError(context=context)
    ordered = tuple(sorted(candles, key=lambda candle: candle.timestamp))
    if any(
        right.timestamp - left.timestamp < timedelta(seconds=duration)
        for left, right in zip(ordered, ordered[1:], strict=False)
    ):
        raise ExchangeProtocolError(context=context)
    return ordered


__all__: list[str] = []
