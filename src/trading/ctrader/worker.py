import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from math import isfinite
from threading import Event
from typing import Any, Protocol

from sqlalchemy.exc import SQLAlchemyError

from trading.ctrader.parquet import CTRADER_PARQUET_PROVIDER
from trading.data.domain import InstrumentCode, PriceType, Timeframe

_MINUTE = timedelta(minutes=1)
_MAX_SYNC_DAYS = 31


@dataclass(frozen=True)
class WorkerConfig:
    interval_seconds: float = 300
    lookback_hours: int = 24
    retry_base_seconds: int = 900
    retry_max_seconds: int = 21_600
    warning_missing_minutes: int = 1
    critical_missing_minutes: int = 30
    max_lag_minutes: int = 10

    def __post_init__(self) -> None:
        if not isfinite(self.interval_seconds) or self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be greater than zero")
        if not 1 <= self.lookback_hours <= _MAX_SYNC_DAYS * 24:
            raise ValueError("lookback_hours must be between 1 and 744")
        if self.retry_base_seconds < 1 or self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError(
                "retry intervals must be positive and retry_max_seconds >= retry_base_seconds"
            )
        if self.warning_missing_minutes < 0:
            raise ValueError("warning_missing_minutes cannot be negative")
        if self.critical_missing_minutes < 1:
            raise ValueError("critical_missing_minutes must be positive")
        if self.critical_missing_minutes <= self.warning_missing_minutes:
            raise ValueError("critical_missing_minutes must exceed warning_missing_minutes")
        if self.max_lag_minutes < 1:
            raise ValueError("max_lag_minutes must be positive")


class SyncService(Protocol):
    repository: Any
    calendar: Any

    def build_plan(self, start: datetime, end: datetime) -> Any: ...

    async def synchronize(
        self,
        start: datetime,
        end: datetime,
        *,
        dry_run: bool = False,
        save_postgres: bool = False,
        deferred_timestamps: set[datetime] | None = None,
    ) -> dict[str, object]: ...


class OperationsRepository(Protocol):
    def load_retry_state(self) -> dict[str, dict[str, Any]]: ...

    def record_run(
        self,
        report: dict[str, Any],
        retry_state: dict[str, dict[str, Any]],
    ) -> None: ...


@dataclass(frozen=True)
class DataQualityReport:
    status: str
    expected_candle_count: int
    stored_candle_count: int
    missing_candle_count: int
    completeness_percent: float
    latest_stored_candle: str | None
    freshness_seconds: int | None
    market_open_lag_minutes: int | None
    market_closed_minutes: int
    missing_intervals: list[dict[str, object]]
    duplicate_records: int
    conflicting_records: int
    rejected_records: int
    api_error_count: int
    database_error_count: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_foreground_worker(
    run_once: Callable[[], object],
    interval_seconds: float = 300,
    *,
    once: bool = False,
    stop_event: Event | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    wait: Callable[[float], object] | None = None,
) -> None:
    """Run sequential cycles against monotonic deadlines until stopped."""
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be greater than zero")
    stop = stop_event or Event()
    wait_for_stop = wait or stop.wait
    deadline = monotonic()
    while not stop.is_set():
        run_once()
        if once or stop.is_set():
            return
        deadline += interval_seconds
        current = monotonic()
        if deadline <= current:
            missed_intervals = int((current - deadline) // interval_seconds) + 1
            deadline += missed_intervals * interval_seconds
        while not stop.is_set():
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            wait_for_stop(min(0.2, remaining))


async def synchronize_worker_cycle(
    service: SyncService,
    operations: OperationsRepository,
    config: WorkerConfig,
    *,
    now: Callable[[], datetime] | None = None,
    dry_run: bool = False,
    save_postgres: bool = False,
    manual_backfill: bool = False,
) -> dict[str, Any]:
    """Synchronize one rolling window, monitor it, and persist enabled operations."""
    clock = now or (lambda: datetime.now(UTC))
    instrument = getattr(service, "instrument", InstrumentCode.EURUSD)
    finished_at = clock()
    if finished_at.tzinfo is None or finished_at.utcoffset() != timedelta(0):
        raise ValueError("Worker clock must return a timezone-aware UTC datetime")
    end = finished_at.astimezone(UTC).replace(second=0, microsecond=0)
    start = end - timedelta(hours=config.lookback_hours)
    retry_state: dict[str, dict[str, Any]] = {}
    deferred: set[datetime] = set()
    database_error_count = 0
    try:
        retry_state = operations.load_retry_state()
        missing = set(service.build_plan(start, end).missing_timestamps)
        retry_now = finished_at.astimezone(UTC)
        deferred = {
            timestamp
            for timestamp in missing
            if not manual_backfill
            and timestamp.isoformat().replace("+00:00", "Z") in retry_state
            and _retry_time(retry_state[timestamp.isoformat().replace("+00:00", "Z")]) > retry_now
        }
        summary = await service.synchronize(
            start,
            end,
            dry_run=dry_run,
            save_postgres=save_postgres,
            deferred_timestamps=deferred,
        )
        stored_records = [
            candle
            for candle, provider, _ in service.repository.iter_candle_records(
                instrument,
                Timeframe.M1,
                start,
                end,
                source_provider=CTRADER_PARQUET_PROVIDER,
            )
            if provider == CTRADER_PARQUET_PROVIDER and candle.price_type == PriceType.UNKNOWN
        ]
        stored_timestamps = {
            candle.opened_at
            for candle in stored_records
            if service.calendar.is_open(instrument, candle.opened_at)
        }
        latest = max(stored_timestamps, default=None)
    except SQLAlchemyError as error:
        database_error_count = 1
        summary = {
            "status": "database_error",
            "expected_market_minutes": 0,
            "missing_candle_count": 0,
            "market_closed_minutes": 0,
            "unresolved_gaps": [],
            "duplicate_records": 0,
            "conflicts": 0,
            "rejected_records": 0,
            "api_errors": [],
            "database_error": type(error).__name__,
            "api_requests_attempted": 0,
            "candles_inserted": 0,
        }
        latest = None

    quality = evaluate_data_quality(
        summary,
        latest,
        finished_at.astimezone(UTC),
        service.calendar,
        config,
        stored_candle_count=len(stored_timestamps) if not database_error_count else None,
        database_error_count=database_error_count,
        instrument=instrument,
    )

    if database_error_count:
        updated_retry_state = retry_state
    elif save_postgres and not dry_run:
        updated_retry_state = _updated_retry_state(
            retry_state,
            missing,
            deferred,
            summary.get("unresolved_gaps", []),
            finished_at.astimezone(UTC),
            config,
        )
    else:
        updated_retry_state = retry_state

    report = {
        "started_at": _format_utc(finished_at.astimezone(UTC)),
        "finished_at": _format_utc(clock().astimezone(UTC)),
        "instrument": instrument.value,
        "timeframe": Timeframe.M1.label,
        "dry_run": dry_run,
        "postgres_write_enabled": save_postgres and not dry_run,
        "manual_backfill": manual_backfill,
        "synchronization_status": summary.get("status", "unknown"),
        "execution_status": (
            "not_run"
            if dry_run
            else "failed"
            if database_error_count or summary.get("api_errors")
            else "succeeded"
        ),
        "status": quality.status,
        "sync": summary,
        "quality": quality.as_dict(),
        "retry_deferred_count": len(deferred),
        "retry_state_count": len(updated_retry_state),
    }
    if save_postgres and not dry_run and not database_error_count:
        try:
            operations.record_run(report, updated_retry_state)
        except SQLAlchemyError as error:
            quality = replace(
                quality,
                status="critical",
                database_error_count=quality.database_error_count + 1,
            )
            report["status"] = "critical"
            report["quality"] = quality.as_dict()
            report["sync"]["database_error"] = type(error).__name__
            report["execution_status"] = "failed"
    return report


def evaluate_data_quality(
    summary: dict[str, object],
    latest_stored: datetime | None,
    now: datetime,
    calendar: Any,
    config: WorkerConfig,
    *,
    stored_candle_count: int | None = None,
    database_error_count: int = 0,
    instrument: InstrumentCode = InstrumentCode.EURUSD,
) -> DataQualityReport:
    expected = int(summary.get("expected_market_minutes", 0))
    stored = (
        max(0, min(expected, stored_candle_count))
        if stored_candle_count is not None
        else max(0, expected - int(summary.get("missing_candle_count", 0)))
    )
    missing = max(0, expected - stored)
    completeness = 100.0 if expected == 0 else stored * 100 / expected
    freshness_seconds = (
        max(0, int((now - latest_stored).total_seconds())) if latest_stored is not None else None
    )
    open_lag = (
        _market_open_lag_minutes(calendar, latest_stored, now, instrument)
        if latest_stored is not None
        else None
    )
    api_errors = summary.get("api_errors", [])
    conflicts = int(summary.get("conflicts", 0))
    rejected = int(summary.get("rejected_records", 0))
    duplicates = int(summary.get("duplicate_records", 0))
    if (
        database_error_count
        or api_errors
        or conflicts
        or rejected
        or missing >= config.critical_missing_minutes
        or (open_lag is not None and open_lag > config.max_lag_minutes)
    ):
        status = "critical"
    elif (
        missing > config.warning_missing_minutes
        or (missing > 0 and config.warning_missing_minutes == 0)
        or duplicates > 0
    ):
        status = "warning"
    else:
        status = "healthy"
    return DataQualityReport(
        status=status,
        expected_candle_count=expected,
        stored_candle_count=stored,
        missing_candle_count=missing,
        completeness_percent=round(completeness, 4),
        latest_stored_candle=_format_utc(latest_stored) if latest_stored else None,
        freshness_seconds=freshness_seconds,
        market_open_lag_minutes=open_lag,
        market_closed_minutes=int(summary.get("market_closed_minutes", 0)),
        missing_intervals=list(summary.get("unresolved_gaps", [])),
        duplicate_records=duplicates,
        conflicting_records=conflicts,
        rejected_records=rejected,
        api_error_count=len(api_errors) if isinstance(api_errors, list) else 0,
        database_error_count=database_error_count,
    )


def _updated_retry_state(
    current: dict[str, dict[str, Any]],
    missing: set[datetime],
    deferred: set[datetime],
    unresolved: object,
    now: datetime,
    config: WorkerConfig,
) -> dict[str, dict[str, Any]]:
    unresolved_timestamps: dict[datetime, str] = {}
    if isinstance(unresolved, list):
        for gap in unresolved:
            if not isinstance(gap, dict) or gap.get("reason") == "retry_deferred":
                continue
            start = _parse_utc(gap.get("start"))
            count = int(gap.get("missing_candle_count", 0))
            if start is not None:
                for index in range(count):
                    unresolved_timestamps[start + index * _MINUTE] = str(
                        gap.get("reason", "missing")
                    )

    updated: dict[str, dict[str, Any]] = {}
    for timestamp in missing:
        key = _format_utc(timestamp)
        previous = current.get(key, {})
        if timestamp in deferred:
            updated[key] = previous
            continue
        reason = unresolved_timestamps.get(timestamp)
        if reason is None:
            continue
        attempts = int(previous.get("attempts", 0)) + 1
        delay = min(
            config.retry_base_seconds * (2 ** min(attempts - 1, 30)),
            config.retry_max_seconds,
        )
        updated[key] = {
            "attempts": attempts,
            "last_attempt_at": _format_utc(now),
            "retry_at": _format_utc(now + timedelta(seconds=delay)),
            "reason": reason,
        }
    return updated


def _retry_time(state: dict[str, Any]) -> datetime:
    retry_at = _parse_utc(state.get("retry_at"))
    return retry_at or datetime.min.replace(tzinfo=UTC)


def _market_open_lag_minutes(
    calendar: Any, latest: datetime, now: datetime,
    instrument: InstrumentCode = InstrumentCode.EURUSD,
) -> int:
    latest = latest.astimezone(UTC).replace(second=0, microsecond=0)
    latest_expected = now.astimezone(UTC).replace(second=0, microsecond=0) - _MINUTE
    lag = 0
    timestamp = latest + _MINUTE
    while timestamp <= latest_expected:
        if calendar.is_open(instrument, timestamp):
            lag += 1
        timestamp += _MINUTE
    return lag


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None
    return parsed.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
