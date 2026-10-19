from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from trading.data.domain import Candle, InstrumentCode
from trading.data.market_calendar import MarketCalendar
from trading.data.quality import CandleQualityReport, check_candle_quality


class HistoricalDataValidator(Protocol):
    def validate(
        self,
        candles: Iterable[Candle],
        instrument: InstrumentCode,
        timeframe_minutes: int,
        *,
        calendar: MarketCalendar | None = None,
        range_start: datetime | None = None,
        range_end: datetime | None = None,
    ) -> CandleQualityReport: ...


class DefaultHistoricalDataValidator:
    """Adapter over the existing quality pipeline for imported historical series."""

    def validate(
        self,
        candles: Iterable[Candle],
        instrument: InstrumentCode,
        timeframe_minutes: int,
        *,
        calendar: MarketCalendar | None = None,
        range_start: datetime | None = None,
        range_end: datetime | None = None,
    ) -> CandleQualityReport:
        return check_candle_quality(
            candles,
            instrument,
            timeframe_minutes,
            calendar=calendar,
            range_start=range_start,
            range_end=range_end,
        )


@dataclass(frozen=True)
class SourceCoverage:
    requested_start: datetime
    requested_end: datetime
    observed_start: datetime | None
    observed_end: datetime | None
    candle_count: int
    missing_candle_count: int
    unexpected_gap_count: int


def summarize_source_coverage(
    report: CandleQualityReport,
    requested_start: datetime,
    requested_end: datetime,
) -> SourceCoverage:
    if requested_start.tzinfo is None or requested_start.utcoffset() is None:
        raise ValueError("requested_start must be timezone-aware")
    if requested_end.tzinfo is None or requested_end.utcoffset() is None:
        raise ValueError("requested_end must be timezone-aware")
    if requested_start >= requested_end:
        raise ValueError("requested_start must be earlier than requested_end")
    return SourceCoverage(
        requested_start=requested_start,
        requested_end=requested_end,
        observed_start=report.coverage_start,
        observed_end=report.coverage_end,
        candle_count=report.candle_count,
        missing_candle_count=report.missing_candle_count,
        unexpected_gap_count=report.unexpected_gap_count,
    )
