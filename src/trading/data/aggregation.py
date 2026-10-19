from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading.data.domain import Candle, Timeframe
from trading.data.market_calendar import MarketCalendar


@dataclass(frozen=True)
class AggregationResult:
    candles: tuple[Candle, ...]
    incomplete_bucket_starts: tuple[datetime, ...]
    excluded_unfinalized: int


@dataclass(frozen=True)
class SourceDiscrepancy:
    opened_at: datetime
    derived_close: Decimal
    provider_close: Decimal
    absolute_difference: Decimal
    differing_fields: tuple[str, ...]


def aggregate_complete_minutes(
    candles: Iterable[Candle],
    finalized_through: datetime,
    *,
    calendar: MarketCalendar | None = None,
    source_provider: str | None = None,
) -> AggregationResult:
    if finalized_through.tzinfo is None or finalized_through.utcoffset() is None:
        raise ValueError("finalized_through must be timezone-aware")
    watermark = finalized_through.astimezone(UTC)
    calendar = calendar or MarketCalendar()
    aggregated: list[Candle] = []
    incomplete: list[datetime] = []
    excluded_unfinalized = 0
    previous_timestamp: datetime | None = None
    previous_candle: Candle | None = None
    instrument = None
    current_bucket: datetime | None = None
    current_minutes: list[Candle] = []

    def finalize_bucket(bucket_start: datetime | None, minute_bars: list[Candle]) -> None:
        nonlocal excluded_unfinalized
        if bucket_start is None or not minute_bars:
            return
        expected_times = [bucket_start + timedelta(minutes=offset) for offset in range(5)]
        if [candle.opened_at for candle in minute_bars] != expected_times:
            incomplete.append(bucket_start)
            return
        if not all(
            calendar.is_open(minute_bars[0].instrument, timestamp) for timestamp in expected_times
        ):
            incomplete.append(bucket_start)
            return
        if bucket_start + timedelta(minutes=5) > watermark:
            excluded_unfinalized += len(minute_bars)
            return
        aggregated.append(
            Candle(
                instrument=minute_bars[0].instrument,
                timeframe=Timeframe.M5,
                opened_at=bucket_start,
                open=minute_bars[0].open,
                high=max(candle.high for candle in minute_bars),
                low=min(candle.low for candle in minute_bars),
                close=minute_bars[-1].close,
                volume=(
                    sum((candle.volume for candle in minute_bars), Decimal(0))
                    if all(candle.volume is not None for candle in minute_bars)
                    else None
                ),
                price_type=minute_bars[0].price_type,
                quality_metadata={
                    "derived_from": "1m",
                    "source_provider": source_provider,
                    "source_price_type": minute_bars[0].price_type.value,
                },
            )
        )

    for candle in candles:
        if candle.timeframe != Timeframe.M1:
            raise ValueError("Aggregation input must contain only 1-minute candles")
        if instrument is None:
            instrument = candle.instrument
        elif candle.instrument != instrument:
            raise ValueError("Aggregation input must contain only one instrument")
        elif previous_candle is not None and candle.price_type != previous_candle.price_type:
            raise ValueError("Aggregation input must contain only one price type")
        if previous_timestamp is not None and candle.opened_at <= previous_timestamp:
            if candle.opened_at < previous_timestamp:
                raise ValueError("1-minute aggregation input must be strictly chronological")
            if candle != previous_candle:
                raise ValueError(f"Conflicting 1-minute candle at {candle.opened_at}")
            raise ValueError(f"Duplicate 1-minute candle at {candle.opened_at}")
        if candle.opened_at + timedelta(minutes=1) > watermark:
            excluded_unfinalized += 1
            previous_timestamp = candle.opened_at
            previous_candle = candle
            continue
        bucket_start = candle.opened_at.replace(
            minute=(candle.opened_at.minute // 5) * 5,
            second=0,
            microsecond=0,
        )
        if current_bucket is not None and bucket_start != current_bucket:
            finalize_bucket(current_bucket, current_minutes)
            current_minutes = []
        current_bucket = bucket_start
        current_minutes.append(candle)
        previous_timestamp = candle.opened_at
        previous_candle = candle
    finalize_bucket(current_bucket, current_minutes)

    return AggregationResult(
        candles=tuple(aggregated),
        incomplete_bucket_starts=tuple(incomplete),
        excluded_unfinalized=excluded_unfinalized,
    )


def compare_aggregated_source(
    derived: Iterable[Candle],
    provider: Iterable[Candle],
    *,
    absolute_tolerance: Decimal = Decimal("0.00000001"),
) -> tuple[SourceDiscrepancy, ...]:
    if absolute_tolerance < 0:
        raise ValueError("absolute_tolerance cannot be negative")
    derived_by_time = {candle.opened_at: candle for candle in derived}
    provider_by_time = {candle.opened_at: candle for candle in provider}
    discrepancies = []
    for timestamp in sorted(derived_by_time.keys() & provider_by_time.keys()):
        derived_candle = derived_by_time[timestamp]
        provider_candle = provider_by_time[timestamp]
        if derived_candle.instrument != provider_candle.instrument:
            raise ValueError("Cannot reconcile candles for different instruments")
        if derived_candle.timeframe != provider_candle.timeframe:
            raise ValueError("Cannot reconcile candles with different timeframes")
        if provider_candle.price_type != derived_candle.price_type:
            raise ValueError("Cannot reconcile candles with different price types")
        values = {
            "open": (derived_candle.open, provider_candle.open),
            "high": (derived_candle.high, provider_candle.high),
            "low": (derived_candle.low, provider_candle.low),
            "close": (derived_candle.close, provider_candle.close),
        }
        if derived_candle.volume is not None and provider_candle.volume is not None:
            values["volume"] = (derived_candle.volume, provider_candle.volume)
        differences = {
            name: abs(derived_value - provider_value)
            for name, (derived_value, provider_value) in values.items()
        }
        different_fields = tuple(
            name for name, difference in differences.items() if difference > absolute_tolerance
        )
        if different_fields:
            discrepancies.append(
                SourceDiscrepancy(
                    opened_at=timestamp,
                    derived_close=derived_candle.close,
                    provider_close=provider_candle.close,
                    absolute_difference=max(differences.values()),
                    differing_fields=different_fields,
                )
            )
    return tuple(discrepancies)
