from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from trading.data.domain import InstrumentCode, Timeframe
from trading.data.market_calendar import MarketCalendar


@dataclass(frozen=True)
class QualityIssue:
    code: str
    timestamp: datetime | None
    detail: str
    severity: str = "warning"


@dataclass(frozen=True)
class GapFinding:
    missing_from: datetime
    missing_through: datetime
    missing_count: int
    unexpected_count: int
    expected_closure_count: int


@dataclass(frozen=True)
class CandleQualityReport:
    instrument: str
    timeframe_minutes: int
    candle_count: int
    duplicate_count: int
    out_of_order_count: int
    zero_volume_count: int
    missing_volume_count: int
    gaps: tuple[GapFinding, ...]
    issues: tuple[QualityIssue, ...]
    coverage_start: datetime | None = None
    coverage_end: datetime | None = None

    @property
    def missing_candle_count(self) -> int:
        return sum(gap.missing_count for gap in self.gaps)

    @property
    def unexpected_gap_count(self) -> int:
        return sum(gap.unexpected_count for gap in self.gaps)

    def as_dict(self) -> dict[str, object]:
        return {
            "instrument": self.instrument,
            "timeframe_minutes": self.timeframe_minutes,
            "candle_count": self.candle_count,
            "duplicate_count": self.duplicate_count,
            "out_of_order_count": self.out_of_order_count,
            "coverage_start": self.coverage_start.isoformat() if self.coverage_start else None,
            "coverage_end": self.coverage_end.isoformat() if self.coverage_end else None,
            "zero_volume_count": self.zero_volume_count,
            "missing_volume_count": self.missing_volume_count,
            "missing_candle_count": self.missing_candle_count,
            "unexpected_gap_count": self.unexpected_gap_count,
            "gaps": [asdict(gap) for gap in self.gaps],
            "issues": [asdict(issue) for issue in self.issues],
        }


class CandleObservation(Protocol):
    instrument: InstrumentCode
    timeframe: Timeframe
    opened_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None


def check_candle_quality(
    candles: Iterable[CandleObservation],
    instrument: InstrumentCode,
    timeframe_minutes: int,
    *,
    calendar: MarketCalendar | None = None,
    price_jump_threshold: Decimal | float = Decimal("0.10"),
    zero_volume_run_threshold: int = 3,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
) -> CandleQualityReport:
    if timeframe_minutes not in (1, 5):
        raise ValueError("Quality checks support only 1-minute and 5-minute candles")
    if price_jump_threshold <= 0:
        raise ValueError("price_jump_threshold must be positive")
    if zero_volume_run_threshold <= 0:
        raise ValueError("zero_volume_run_threshold must be positive")

    calendar = calendar or MarketCalendar()
    interval = timedelta(minutes=timeframe_minutes)
    seen: set[datetime] = set()
    issues: list[QualityIssue] = []
    gaps: list[GapFinding] = []
    previous: CandleObservation | None = None
    candle_count = 0
    duplicate_count = 0
    out_of_order_count = 0
    zero_volume_count = 0
    missing_volume_count = 0
    zero_volume_run = 0
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    if range_start is not None and (range_start.tzinfo is None or range_start.utcoffset() is None):
        raise ValueError("range_start must be timezone-aware")
    if range_end is not None and (range_end.tzinfo is None or range_end.utcoffset() is None):
        raise ValueError("range_end must be timezone-aware")
    normalized_start = range_start.astimezone(UTC) if range_start is not None else None
    normalized_end = range_end.astimezone(UTC) if range_end is not None else None
    if normalized_start is not None and (
        normalized_start.second
        or normalized_start.microsecond
        or normalized_start.minute % timeframe_minutes
    ):
        raise ValueError("range_start must align to the requested timeframe")
    if normalized_end is not None and (
        normalized_end.second
        or normalized_end.microsecond
        or normalized_end.minute % timeframe_minutes
    ):
        raise ValueError("range_end must align to the requested timeframe")
    if normalized_start is not None and normalized_end is not None:
        if normalized_start >= normalized_end:
            raise ValueError("range_start must be earlier than range_end")

    def record_gap(gap_start: datetime, missing_count: int) -> None:
        if missing_count <= 0:
            return
        expected_count, closed_count = calendar.classify_missing_timestamps(
            instrument,
            gap_start,
            missing_count,
            timeframe_minutes,
        )
        finding = GapFinding(
            missing_from=gap_start,
            missing_through=gap_start + interval * (missing_count - 1),
            missing_count=missing_count,
            unexpected_count=expected_count,
            expected_closure_count=closed_count,
        )
        gaps.append(finding)
        issues.append(
            QualityIssue(
                "data_gap" if expected_count else "expected_market_closure",
                gap_start,
                f"{expected_count} missing candles during sessions; "
                f"{closed_count} during configured closures",
                "warning" if expected_count else "info",
            )
        )

    for candle in candles:
        candle_count += 1
        if candle.instrument != instrument:
            issues.append(
                QualityIssue("wrong_instrument", candle.opened_at, candle.instrument.value, "error")
            )
        if candle.timeframe.value != timeframe_minutes:
            issues.append(
                QualityIssue(
                    "wrong_timeframe",
                    candle.opened_at,
                    candle.timeframe.label,
                    "error",
                )
            )
        timestamp_is_aware = (
            candle.opened_at.tzinfo is not None and candle.opened_at.utcoffset() is not None
        )
        if timestamp_is_aware:
            if first_timestamp is None or candle.opened_at < first_timestamp:
                first_timestamp = candle.opened_at
            if last_timestamp is None or candle.opened_at > last_timestamp:
                last_timestamp = candle.opened_at
        if not timestamp_is_aware:
            issues.append(
                QualityIssue(
                    "naive_timestamp", candle.opened_at, "Timestamp has no timezone", "error"
                )
            )
        elif candle.opened_at.utcoffset() != timedelta(0):
            issues.append(
                QualityIssue(
                    "non_utc_timestamp",
                    candle.opened_at,
                    "Timestamp was not normalized to UTC",
                    "error",
                )
            )
        if timestamp_is_aware and (
            candle.opened_at.second
            or candle.opened_at.microsecond
            or candle.opened_at.minute % timeframe_minutes
        ):
            issues.append(
                QualityIssue(
                    "misaligned_timestamp",
                    candle.opened_at,
                    f"Timestamp is not aligned to {timeframe_minutes}m",
                    "error",
                )
            )
        if any(value < 0 for value in (candle.open, candle.high, candle.low, candle.close)):
            issues.append(
                QualityIssue("negative_price", candle.opened_at, "Price is negative", "error")
            )
        if candle.volume is not None and candle.volume < 0:
            issues.append(
                QualityIssue("negative_volume", candle.opened_at, "Volume is negative", "error")
            )
        elif candle.volume is None:
            missing_volume_count += 1
        if (
            candle.high < candle.low
            or candle.high < candle.open
            or candle.high < candle.close
            or candle.low > candle.open
            or candle.low > candle.close
        ):
            issues.append(
                QualityIssue(
                    "invalid_ohlc",
                    candle.opened_at,
                    "High/low do not bound open and close",
                    "error",
                )
            )

        if candle.opened_at in seen:
            duplicate_count += 1
            issues.append(
                QualityIssue("duplicate_candle", candle.opened_at, "Timestamp repeated", "error")
            )
        else:
            seen.add(candle.opened_at)

        if candle.volume == 0:
            zero_volume_count += 1
            zero_volume_run += 1
            if zero_volume_run == zero_volume_run_threshold:
                issues.append(
                    QualityIssue(
                        "zero_volume_run",
                        candle.opened_at,
                        f"At least {zero_volume_run_threshold} consecutive zero-volume candles",
                    )
                )
        else:
            zero_volume_run = 0
        if candle.volume == 0:
            issues.append(
                QualityIssue("zero_volume", candle.opened_at, "Zero-volume candle is suspicious")
            )

        if previous is not None and timestamp_is_aware:
            if candle.opened_at <= previous.opened_at:
                out_of_order_count += int(candle.opened_at < previous.opened_at)
                issues.append(
                    QualityIssue(
                        "out_of_order_candle",
                        candle.opened_at,
                        "Candles are not strictly increasing",
                        "error",
                    )
                )
            elif candle.opened_at - previous.opened_at > interval:
                missing_count = int((candle.opened_at - previous.opened_at) // interval) - 1
                gap_start = previous.opened_at + interval
                record_gap(gap_start, missing_count)

            if timestamp_is_aware and previous.close > 0 and candle.close > 0:
                change = abs(candle.close / previous.close - Decimal(1))
                if change > Decimal(str(price_jump_threshold)):
                    issues.append(
                        QualityIssue(
                            "suspicious_price_jump",
                            candle.opened_at,
                            f"Close changed by {change:.2%}",
                        )
                    )
        previous = candle if timestamp_is_aware else None

    if candle_count == 0 and normalized_start is not None and normalized_end is not None:
        count = int((normalized_end - normalized_start) // interval)
        record_gap(normalized_start, count)
    elif normalized_start is not None and first_timestamp is not None:
        if first_timestamp > normalized_start:
            count = int((first_timestamp - normalized_start) // interval)
            record_gap(normalized_start, count)
    if normalized_end is not None and last_timestamp is not None:
        next_expected = last_timestamp + interval
        if next_expected < normalized_end:
            count = int((normalized_end - next_expected) // interval)
            record_gap(next_expected, count)

    return CandleQualityReport(
        instrument=instrument.value,
        timeframe_minutes=timeframe_minutes,
        candle_count=candle_count,
        duplicate_count=duplicate_count,
        out_of_order_count=out_of_order_count,
        zero_volume_count=zero_volume_count,
        missing_volume_count=missing_volume_count,
        gaps=tuple(gaps),
        issues=tuple(issues),
        coverage_start=first_timestamp,
        coverage_end=last_timestamp,
    )
