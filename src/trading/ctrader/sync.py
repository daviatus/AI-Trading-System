from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from trading.config import get_settings
from trading.ctrader.client import MAX_CANDLE_REQUEST_INTERVAL, CTraderError
from trading.ctrader.parquet import CTRADER_PARQUET_PROVIDER
from trading.ctrader.quality import validate_candle_data
from trading.data.domain import (
    Candle,
    Instrument,
    InstrumentCode,
    PriceType,
    Timeframe,
    configured_instruments,
)
from trading.data.market_calendar import MarketCalendar
from trading.data.repository import CandleImportReport

MAX_SYNC_DAYS = 31
_MINUTE = timedelta(minutes=1)


class CTraderSyncRepository(Protocol):
    def iter_candle_records(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        source_provider: str | None = None,
        batch_size: int = 2000,
    ) -> Iterator[tuple[Candle, str, str]]: ...

    def import_candles(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        provider: str,
        records: list[tuple[Candle, str]],
    ) -> CandleImportReport: ...


class CTraderHistoricalClient(Protocol):
    async def get_eurusd_m1_candles_multi_day(
        self,
        start: datetime,
        end: datetime,
        account_id: int | None = None,
        *,
        diagnostics: bool = False,
        request_observer: Callable[[], None] | None = None,
    ) -> list[dict]: ...


@dataclass(frozen=True)
class MissingInterval:
    start: datetime
    end: datetime
    missing_timestamps: tuple[datetime, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "start": _format_utc(self.start),
            "end": _format_utc(self.end),
            "missing_candle_count": len(self.missing_timestamps),
        }


@dataclass(frozen=True)
class CTraderSyncPlan:
    requested_start: datetime
    requested_end: datetime
    effective_end: datetime
    existing_candle_count: int
    market_closed_minutes: int
    expected_market_minutes: int
    missing_intervals: tuple[MissingInterval, ...]
    instrument: InstrumentCode = InstrumentCode.EURUSD

    @property
    def missing_timestamps(self) -> tuple[datetime, ...]:
        return tuple(
            timestamp
            for interval in self.missing_intervals
            for timestamp in interval.missing_timestamps
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "instrument": self.instrument.value,
            "timeframe": Timeframe.M1.label,
            "requested_interval": {
                "start": _format_utc(self.requested_start),
                "end": _format_utc(self.requested_end),
                "boundary": "[start, end)",
            },
            "effective_interval": {
                "start": _format_utc(self.requested_start),
                "end": _format_utc(self.effective_end),
                "boundary": "[start, end)",
            },
            "existing_candle_count": self.existing_candle_count,
            "expected_market_minutes": self.expected_market_minutes,
            "market_closed_minutes": self.market_closed_minutes,
            "missing_candle_count": len(self.missing_timestamps),
            "missing_intervals_identified": [
                interval.as_dict() for interval in self.missing_intervals
            ],
        }


class CTraderSyncService:
    """Plan and execute incremental read-only cTrader history synchronization."""

    def __init__(
        self,
        repository: CTraderSyncRepository,
        client: CTraderHistoricalClient | None = None,
        *,
        calendar: MarketCalendar | None = None,
        now: Callable[[], datetime] | None = None,
        account_id: int | None = None,
        instrument: InstrumentCode = InstrumentCode.EURUSD,
        broker_symbol: str | None = None,
    ) -> None:
        self.repository = repository
        self.client = client
        if calendar is not None:
            self.calendar = calendar
        else:
            self.calendar = MarketCalendar.from_config(get_settings().market_sessions)
        self.now = now or (lambda: datetime.now(UTC))
        self.account_id = account_id
        self.instrument = instrument
        self.broker_symbol = broker_symbol

    def build_plan(
        self,
        start: datetime,
        end: datetime,
        *,
        max_days: int = MAX_SYNC_DAYS,
    ) -> CTraderSyncPlan:
        start, end = _validate_sync_range(start, end, max_days=max_days)
        now = self.now()
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("Synchronization clock must return a timezone-aware UTC datetime")
        effective_end = min(end, now.astimezone(UTC).replace(second=0, microsecond=0))

        stored_timestamps = set()
        if start < effective_end:
            records = self.repository.iter_candle_records(
                self.instrument,
                Timeframe.M1,
                start,
                effective_end,
                source_provider=CTRADER_PARQUET_PROVIDER,
            )
            stored_timestamps = {
                candle.opened_at
                for candle, provider, _ in records
                if provider == CTRADER_PARQUET_PROVIDER and candle.price_type == PriceType.UNKNOWN
            }

        expected: list[datetime] = []
        closed_minutes = 0
        timestamp = start
        while timestamp < effective_end:
            if self.calendar.is_open(self.instrument, timestamp):
                expected.append(timestamp)
            else:
                closed_minutes += 1
            timestamp += _MINUTE

        missing = [timestamp for timestamp in expected if timestamp not in stored_timestamps]
        intervals = _build_missing_intervals(missing)
        return CTraderSyncPlan(
            requested_start=start,
            requested_end=end,
            effective_end=effective_end,
            existing_candle_count=len(stored_timestamps),
            market_closed_minutes=closed_minutes,
            expected_market_minutes=len(expected),
            missing_intervals=tuple(intervals),
            instrument=self.instrument,
        )

    async def synchronize(
        self,
        start: datetime,
        end: datetime,
        *,
        max_days: int = MAX_SYNC_DAYS,
        dry_run: bool = False,
        save_postgres: bool = False,
        deferred_timestamps: set[datetime] | None = None,
    ) -> dict[str, object]:
        plan = self.build_plan(start, end, max_days=max_days)
        deferred = set(deferred_timestamps or ()) & set(plan.missing_timestamps)
        intervals_to_fetch = _build_missing_intervals(
            [timestamp for timestamp in plan.missing_timestamps if timestamp not in deferred]
        )
        result: dict[str, object] = plan.as_dict()
        result.update(
            {
                "api_requests_attempted": 0,
                "candles_retrieved": 0,
                "candles_validated": 0,
                "duplicate_records": 0,
                "candles_inserted": 0,
                "unchanged_records": 0,
                "conflicts": 0,
                "rejected_records": 0,
                "unresolved_gaps": [],
                "api_errors": [],
                "postgres_write_enabled": save_postgres and not dry_run,
            }
        )
        if dry_run:
            result["status"] = "dry_run"
            return result
        if deferred:
            result["unresolved_gaps"] = _gap_entries(
                sorted(deferred),
                "retry_deferred",
            )
        if not intervals_to_fetch:
            result["status"] = "incomplete" if deferred else "complete"
            return result
        if not plan.missing_timestamps:
            result["status"] = "complete"
            return result
        if self.client is None:
            raise ValueError("A cTrader historical client is required outside dry-run mode")

        request_count = 0
        retrieved_count = 0
        validated_count = 0
        rejected_count = 0
        duplicate_count = 0
        fetched_by_timestamp: dict[datetime, tuple[Candle, str]] = {}
        unresolved: list[dict[str, object]] = []
        api_errors: list[str] = []

        def record_request() -> None:
            nonlocal request_count
            request_count += 1

        for interval in intervals_to_fetch:
            try:
                data = await self.client.get_eurusd_m1_candles_multi_day(
                    interval.start,
                    interval.end,
                    self.account_id,
                    request_observer=record_request,
                    **({"instrument": self.instrument, "broker_symbol": self.broker_symbol}
                       if self.instrument != InstrumentCode.EURUSD else {}),
                )
            except CTraderError as error:
                api_errors.append(str(error))
                unresolved.extend(
                    _gap_entries(interval.missing_timestamps, "api_error", str(error))
                )
                continue

            retrieved_count += len(data)
            valid_candles, quality = validate_candle_data(
                data, interval.start, interval.end, instrument=self.instrument,
                calendar=self.calendar,
            )
            duplicate_count += int(quality["duplicate_count"])
            wrong_identity = [
                candle
                for candle in valid_candles
                if (
                    candle.instrument != self.instrument
                    or candle.timeframe != Timeframe.M1
                    or candle.price_type != PriceType.UNKNOWN
                )
            ]
            if quality["validation_status"] == "invalid" or wrong_identity:
                rejected_count += len(data)
                detail = "cTrader response failed candle validation"
                if wrong_identity:
                    detail = (
                        "cTrader response contained the wrong instrument, timeframe, or price type"
                    )
                unresolved.extend(
                    _gap_entries(interval.missing_timestamps, "validation_error", detail)
                )
                continue

            validated_count += len(valid_candles)
            requested_timestamps = set(interval.missing_timestamps)
            for candle in valid_candles:
                if candle.opened_at in requested_timestamps:
                    source_reference = (
                        f"ctrader-demo:{self.instrument.value}:M1:"
                        f"[{_format_utc(interval.start)},{_format_utc(interval.end)})"
                    )
                    fetched_by_timestamp[candle.opened_at] = (candle, source_reference)

            returned_timestamps = {
                candle.opened_at
                for candle in valid_candles
                if candle.opened_at in requested_timestamps
            }
            not_returned = [
                timestamp
                for timestamp in interval.missing_timestamps
                if timestamp not in returned_timestamps
            ]
            unresolved.extend(_gap_entries(not_returned, "not_returned_by_ctrader"))

        result["api_requests_attempted"] = request_count
        result["candles_retrieved"] = retrieved_count
        result["candles_validated"] = validated_count
        result["duplicate_records"] = duplicate_count
        result["rejected_records"] = rejected_count
        result["api_errors"] = api_errors

        if save_postgres:
            records = [
                fetched_by_timestamp[timestamp]
                for timestamp in plan.missing_timestamps
                if timestamp in fetched_by_timestamp
            ]
            if records:
                import_report = self.repository.import_candles(
                    configured_instruments()[self.instrument],
                    Timeframe.M1,
                    CTRADER_PARQUET_PROVIDER,
                    records,
                )
            else:
                import_report = CandleImportReport()
            result["candles_inserted"] = import_report.inserted
            result["unchanged_records"] = import_report.unchanged
            result["conflicts"] = import_report.conflicting
            result["rejected_records"] = rejected_count + import_report.rejected
            result["postgres_import"] = import_report.as_dict()
        else:
            result["unresolved_gaps"].extend(
                _gap_entries(
                    list(fetched_by_timestamp),
                    "validated_but_not_saved",
                )
            )

        result["unresolved_gaps"] = result["unresolved_gaps"] + unresolved
        has_conflicts = bool(result["conflicts"] or result["rejected_records"])
        if not save_postgres and fetched_by_timestamp and not unresolved and not has_conflicts:
            result["status"] = "retrieved_not_saved"
        elif result["unresolved_gaps"] or has_conflicts:
            result["status"] = "incomplete"
        else:
            result["status"] = "complete"
        return result


def _validate_sync_range(
    start: datetime,
    end: datetime,
    *,
    max_days: int,
) -> tuple[datetime, datetime]:
    if isinstance(max_days, bool) or not isinstance(max_days, int) or not 1 <= max_days <= 31:
        raise ValueError("max_days must be an integer between 1 and 31")
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise ValueError("Synchronization range must use timezone-aware UTC datetimes")
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or start.utcoffset() != timedelta(0)
        or end.utcoffset() != timedelta(0)
    ):
        raise ValueError("Synchronization range must use UTC timestamps")
    start = start.astimezone(UTC)
    end = end.astimezone(UTC)
    if start.second or start.microsecond or end.second or end.microsecond:
        raise ValueError("Synchronization range must align to whole minutes")
    if start >= end:
        raise ValueError("Synchronization range start must be earlier than end")
    if end - start > timedelta(days=max_days):
        raise ValueError(f"Synchronization range exceeds the configured {max_days}-day limit")
    return start, end


def _build_missing_intervals(missing: list[datetime]) -> list[MissingInterval]:
    if not missing:
        return []
    intervals = []
    run = [missing[0]]
    for timestamp in missing[1:]:
        if timestamp != run[-1] + _MINUTE:
            intervals.extend(_split_missing_run(run))
            run = []
        run.append(timestamp)
    intervals.extend(_split_missing_run(run))
    return intervals


def _split_missing_run(run: list[datetime]) -> list[MissingInterval]:
    intervals = []
    chunk: list[datetime] = []
    for timestamp in run:
        if chunk and timestamp >= chunk[0] + MAX_CANDLE_REQUEST_INTERVAL:
            intervals.append(MissingInterval(chunk[0], chunk[-1] + _MINUTE, tuple(chunk)))
            chunk = []
        chunk.append(timestamp)
    if chunk:
        intervals.append(MissingInterval(chunk[0], chunk[-1] + _MINUTE, tuple(chunk)))
    return intervals


def _gap_entries(
    timestamps: list[datetime] | tuple[datetime, ...],
    reason: str,
    detail: str | None = None,
) -> list[dict[str, object]]:
    entries = []
    for interval in _build_missing_intervals(list(timestamps)):
        entry: dict[str, object] = {
            "start": _format_utc(interval.start),
            "end": _format_utc(interval.end),
            "missing_candle_count": len(interval.missing_timestamps),
            "reason": reason,
        }
        if detail is not None:
            entry["detail"] = detail
        entries.append(entry)
    return entries


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
