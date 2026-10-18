from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from trading.data.domain import Candle


@dataclass(frozen=True)
class CandleGap:
    missing_from: datetime
    missing_through: datetime
    missing_count: int


def detect_gaps(candles: Iterable[Candle]) -> list[CandleGap]:
    ordered = sorted(candles, key=lambda candle: candle.opened_at)
    gaps: list[CandleGap] = []
    previous: Candle | None = None
    for candle in ordered:
        if previous is not None:
            if candle.instrument != previous.instrument or candle.timeframe != previous.timeframe:
                raise ValueError("Gap detection requires a single instrument and timeframe")
            interval = timedelta(minutes=candle.timeframe.value)
            difference = candle.opened_at - previous.opened_at
            if difference <= timedelta(0):
                raise ValueError("Candle timestamps must be unique")
            if difference > interval:
                missing_count = int(difference // interval) - 1
                gaps.append(
                    CandleGap(
                        missing_from=previous.opened_at + interval,
                        missing_through=candle.opened_at - interval,
                        missing_count=missing_count,
                    )
                )
        previous = candle
    return gaps
