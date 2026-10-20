import asyncio
import json
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from sqlalchemy.exc import OperationalError

from trading.ctrader.client import CTraderError
from trading.ctrader.sync import CTraderSyncService
from trading.ctrader.worker import (
    WorkerConfig,
    evaluate_data_quality,
    run_foreground_worker,
    synchronize_worker_cycle,
)

START = datetime(2026, 1, 5, 14, 1, tzinfo=UTC)
NOW = datetime(2026, 1, 5, 15, 1, tzinfo=UTC)


class MemoryRepository:
    def __init__(self):
        self.records = []
        self.import_calls = []

    def iter_candle_records(
        self, instrument, timeframe, start, end, *, source_provider=None, batch_size=2000
    ):
        del instrument, timeframe, batch_size
        return iter(
            record
            for record in self.records
            if start <= record[0].opened_at < end
            and (source_provider is None or record[1] == source_provider)
        )

    def import_candles(self, instrument, timeframe, provider, records):
        from trading.data.repository import CandleImportReport

        self.import_calls.append(records)
        for candle, reference in records:
            self.records.append((candle, provider, reference))
        return CandleImportReport(seen=len(records), inserted=len(records))


class FakeClient:
    def __init__(self, responses=None, failure=None):
        self.responses = responses or []
        self.failure = failure
        self.calls = []

    async def get_eurusd_m1_candles_multi_day(
        self, start, end, account_id=None, *, diagnostics=False, request_observer=None
    ):
        self.calls.append((start, end))
        if request_observer:
            request_observer()
        if self.failure:
            raise self.failure
        return self.responses


class MemoryOperations:
    def __init__(self, retry_state=None):
        self.retry_state = retry_state or {}
        self.reports = []

    def load_retry_state(self):
        return dict(self.retry_state)

    def record_run(self, report, retry_state):
        self.reports.append(report)
        self.retry_state = retry_state


def _service(repo, client, current):
    return CTraderSyncService(repo, client, now=lambda: current[0])


def test_worker_scheduler_runs_once_and_stops_after_completion():
    runs = []

    run_foreground_worker(lambda: runs.append("one"), interval_seconds=300, once=True)

    assert runs == ["one"]


def test_worker_scheduler_uses_monotonic_deadlines_without_overlapping_cycles():
    monotonic_time = [0.0]
    start_times = []
    end_times = []
    active = 0
    max_active = 0

    def run():
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        start_times.append(monotonic_time[0])
        monotonic_time[0] += 2.5
        end_times.append(monotonic_time[0])
        active -= 1
        if len(start_times) == 3:
            stop.set()

    stop = Event()

    def wait(seconds):
        monotonic_time[0] += seconds

    run_foreground_worker(
        run,
        interval_seconds=5,
        stop_event=stop,
        monotonic=lambda: monotonic_time[0],
        wait=wait,
    )

    assert max_active == 1
    assert start_times == [0, 5, 10]
    assert all(start >= end for start, end in zip(start_times[1:], end_times[:-1], strict=True))


def test_worker_scheduler_stops_gracefully_when_shutdown_is_requested():
    stop = Event()
    runs = []

    def run():
        runs.append(1)
        stop.set()

    run_foreground_worker(run, stop_event=stop)

    assert runs == [1]


def test_worker_dry_run_does_not_call_api_or_persist_operational_reports():
    current = [NOW]
    repository = MemoryRepository()
    client = FakeClient()
    operations = MemoryOperations()

    report = asyncio.run(
        synchronize_worker_cycle(
            _service(repository, client, current),
            operations,
            WorkerConfig(lookback_hours=1),
            now=lambda: current[0],
            dry_run=True,
            save_postgres=False,
        )
    )

    assert report["synchronization_status"] == "dry_run"
    assert report["dry_run"] is True
    assert not client.calls
    assert not repository.import_calls
    assert not operations.reports
    json.dumps(report)


def test_worker_persists_structured_report_and_retry_state_only_with_write_opt_in():
    current = [NOW]
    repository = MemoryRepository()
    operations = MemoryOperations()

    report = asyncio.run(
        synchronize_worker_cycle(
            _service(repository, FakeClient(), current),
            operations,
            WorkerConfig(lookback_hours=1, critical_missing_minutes=100),
            now=lambda: current[0],
            save_postgres=True,
        )
    )

    assert len(operations.reports) == 1
    assert operations.reports[0]["quality"]["missing_candle_count"] == 60
    assert len(operations.retry_state) == 60
    json.dumps(report)


def test_operations_repository_appends_persistent_event_payload(monkeypatch):
    from trading.ctrader import operations as operations_module

    class Transaction:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def begin(self):
            return Transaction()

        def add(self, event):
            self.event = event

    session = Session()
    monkeypatch.setattr(operations_module, "Session", lambda _engine: session)
    operations = operations_module.CTraderOperationsRepository(engine=object())
    report = {"status": "warning"}
    retry_state = {"2026-01-05T15:00:00Z": {"attempts": 1}}

    operations.record_run(report, retry_state)

    assert session.event.event_type == "ctrader_worker_run"
    assert session.event.payload == {"report": report, "retry_state": retry_state}
    assert session.event.occurred_at.utcoffset() == timedelta(0)


def test_database_failure_is_reported_without_attempting_api_requests():
    class BrokenOperations:
        def load_retry_state(self):
            raise OperationalError("SELECT", {}, RuntimeError("database unavailable"))

        def record_run(self, *_args):
            pytest.fail("a failed database read must not trigger report writes")

    current = [NOW]
    client = FakeClient()
    report = asyncio.run(
        synchronize_worker_cycle(
            _service(MemoryRepository(), client, current),
            BrokenOperations(),
            WorkerConfig(lookback_hours=1),
            now=lambda: current[0],
            save_postgres=True,
        )
    )

    assert report["status"] == "critical"
    assert report["quality"]["database_error_count"] == 1
    assert not client.calls
    json.dumps(report)


def test_persistent_gap_retry_backoff_defers_requests_and_manual_backfill_bypasses_it():
    current = [NOW]
    repository = MemoryRepository()
    client = FakeClient()
    operations = MemoryOperations()
    service = _service(repository, client, current)
    config = WorkerConfig(
        lookback_hours=1,
        retry_base_seconds=900,
        retry_max_seconds=3600,
        critical_missing_minutes=100,
    )

    first = asyncio.run(
        synchronize_worker_cycle(
            service,
            operations,
            config,
            now=lambda: current[0],
            save_postgres=True,
        )
    )
    calls_after_first_run = len(client.calls)
    assert first["status"] == "warning"
    assert len(operations.retry_state) == 60
    assert next(iter(operations.retry_state.values()))["attempts"] == 1

    current[0] += timedelta(minutes=5)
    service.now = lambda: current[0]
    second = asyncio.run(
        synchronize_worker_cycle(
            service,
            operations,
            config,
            now=lambda: current[0],
            save_postgres=True,
        )
    )
    assert second["retry_deferred_count"] == 55
    assert len(client.calls) == calls_after_first_run + 1
    assert len(second["quality"]["missing_intervals"]) == 2

    third = asyncio.run(
        synchronize_worker_cycle(
            service,
            operations,
            config,
            now=lambda: current[0],
            save_postgres=True,
            manual_backfill=True,
        )
    )
    assert third["retry_deferred_count"] == 0
    assert len(client.calls) == calls_after_first_run + 2
    assert all(state["attempts"] >= 1 for state in operations.retry_state.values())


def test_retry_delay_increases_and_persists_attempt_reason():
    current = [NOW]
    repository = MemoryRepository()
    service = _service(repository, FakeClient(), current)
    operations = MemoryOperations()
    config = WorkerConfig(
        lookback_hours=1,
        retry_base_seconds=600,
        retry_max_seconds=3600,
        critical_missing_minutes=100,
    )

    asyncio.run(
        synchronize_worker_cycle(
            service, operations, config, now=lambda: current[0], save_postgres=True
        )
    )
    key, state = next(iter(operations.retry_state.items()))
    assert state["reason"] == "not_returned_by_ctrader"
    assert state["retry_at"] == "2026-01-05T15:11:00Z"

    operations.retry_state[key] = {
        **state,
        "attempts": 2,
        "retry_at": "2026-01-05T15:01:00Z",
    }
    asyncio.run(
        synchronize_worker_cycle(
            service, operations, config, now=lambda: current[0], save_postgres=True
        )
    )
    assert operations.retry_state[key]["attempts"] == 3
    assert operations.retry_state[key]["retry_at"] == "2026-01-05T15:41:00Z"


def test_data_quality_classifies_rollover_gaps_as_warning_not_critical():
    from trading.data.market_calendar import MarketCalendar

    calendar = MarketCalendar()
    summary = {
        "expected_market_minutes": 60,
        "missing_candle_count": 6,
        "market_closed_minutes": 0,
        "unresolved_gaps": [{"missing_candle_count": 6}],
        "api_errors": [],
        "conflicts": 0,
        "rejected_records": 0,
        "duplicate_records": 0,
    }

    quality = evaluate_data_quality(
        summary, NOW - timedelta(minutes=1), NOW, calendar, WorkerConfig()
    )

    assert quality.status == "warning"
    assert quality.missing_candle_count == 6
    assert quality.completeness_percent == 90


def test_data_quality_critical_threshold_and_api_database_errors():
    from trading.data.market_calendar import MarketCalendar

    summary = {
        "expected_market_minutes": 60,
        "missing_candle_count": 30,
        "market_closed_minutes": 0,
        "unresolved_gaps": [],
        "api_errors": ["API unavailable"],
        "conflicts": 0,
        "rejected_records": 0,
        "duplicate_records": 0,
    }
    quality = evaluate_data_quality(
        summary,
        NOW - timedelta(minutes=1),
        NOW,
        MarketCalendar(),
        WorkerConfig(),
        database_error_count=1,
    )

    assert quality.status == "critical"
    assert quality.api_error_count == 1
    assert quality.database_error_count == 1


def test_weekend_closure_does_not_make_freshness_critical():
    from trading.data.market_calendar import MarketCalendar

    sunday_before_open = datetime(2026, 1, 11, 21, 59, tzinfo=UTC)
    last_friday_candle = datetime(2026, 1, 9, 21, 59, tzinfo=UTC)
    summary = {
        "expected_market_minutes": 0,
        "missing_candle_count": 0,
        "market_closed_minutes": 2880,
        "unresolved_gaps": [],
        "api_errors": [],
        "conflicts": 0,
        "rejected_records": 0,
        "duplicate_records": 0,
    }

    quality = evaluate_data_quality(
        summary,
        last_friday_candle,
        sunday_before_open,
        MarketCalendar(),
        WorkerConfig(),
    )

    assert quality.status == "healthy"
    assert quality.market_open_lag_minutes == 0
    assert quality.freshness_seconds > 24 * 60 * 60


def test_sync_failure_surfaces_as_cTrader_error_for_operational_reporting():
    current = [NOW]
    service = _service(MemoryRepository(), FakeClient(failure=CTraderError("temporary")), current)
    result = asyncio.run(service.synchronize(START, NOW, save_postgres=True))

    assert result["api_errors"] == ["temporary"]
    assert result["unresolved_gaps"]


def test_worker_parser_defaults_to_no_postgres_write():
    from trading.ctrader.cli import _parser

    arguments = _parser().parse_args(["worker", "--once", "--dry-run"])

    assert arguments.command == "worker"
    assert arguments.interval_seconds == 300
    assert arguments.lookback_hours == 24
    assert arguments.save_postgres is False


def test_worker_requires_explicit_postgres_target(monkeypatch):
    from trading import db
    from trading.config import Settings

    monkeypatch.setattr(db, "get_settings", lambda: Settings(_env_file=None))

    with pytest.raises(ValueError, match="explicitly configured"):
        db.get_explicit_postgres_engine()


def test_worker_rejects_ambiguous_or_non_postgres_database_target(monkeypatch):
    from trading import db
    from trading.config import Settings

    monkeypatch.setattr(
        db,
        "get_settings",
        lambda: Settings(_env_file=None, database_url="sqlite:///temporary.db"),
    )

    with pytest.raises(ValueError, match="explicit PostgreSQL"):
        db.get_explicit_postgres_engine()


def test_status_parser_supports_report_alias():
    from trading.ctrader.cli import _parser

    assert _parser().parse_args(["report", "--json"]).command == "report"
