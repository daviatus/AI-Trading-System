import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading.ctrader.client import CTraderError
from trading.ctrader.parquet import CTRADER_PARQUET_PROVIDER
from trading.ctrader.sync import CTraderSyncService
from trading.data.domain import Candle, InstrumentCode, PriceType, Timeframe
from trading.data.repository import CandleImportReport

START = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
NOW = START + timedelta(days=2)
OTHER_PROVIDER = "histdata_generic_ascii"


def _candle(timestamp: datetime, *, price_type: PriceType = PriceType.UNKNOWN) -> Candle:
    return Candle(
        instrument=InstrumentCode.EURUSD,
        timeframe=Timeframe.M1,
        opened_at=timestamp,
        open=Decimal("1.10000"),
        high=Decimal("1.10100"),
        low=Decimal("1.09900"),
        close=Decimal("1.10050"),
        volume=Decimal("10"),
        price_type=price_type,
        quality_metadata={"source": "ctrader_open_api"},
    )


class MemoryRepository:
    def __init__(self, records=()):
        self.records = list(records)
        self.reads = []
        self.import_calls = []

    def iter_candle_records(
        self,
        instrument,
        timeframe,
        start,
        end,
        *,
        source_provider=None,
        batch_size=2000,
    ):
        self.reads.append((instrument, timeframe, start, end, source_provider))
        return iter(
            record
            for record in self.records
            if start <= record[0].opened_at < end
            and (source_provider is None or record[1] == source_provider)
        )

    def import_candles(self, instrument, timeframe, provider, records):
        self.import_calls.append((instrument, timeframe, provider, list(records)))
        inserted = 0
        unchanged = 0
        for candle, reference in records:
            existing = next(
                (
                    stored
                    for stored, stored_provider, _ in self.records
                    if stored.opened_at == candle.opened_at
                    and stored.price_type == candle.price_type
                    and stored_provider == provider
                ),
                None,
            )
            if existing is None:
                self.records.append((candle, provider, reference))
                inserted += 1
            elif existing == candle:
                unchanged += 1
        return CandleImportReport(seen=len(records), inserted=inserted, unchanged=unchanged)


class FakeClient:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []
        self.failure = None

    async def get_eurusd_m1_candles_multi_day(
        self,
        start,
        end,
        account_id=None,
        *,
        diagnostics=False,
        request_observer=None,
    ):
        self.calls.append((start, end, account_id))
        if request_observer:
            request_observer()
        if self.failure:
            raise self.failure
        return self.responses.get(start, [])


def _data(candle: Candle) -> dict[str, object]:
    return candle.model_dump(mode="json")


def _service(repository, client=None):
    return CTraderSyncService(
        repository,
        client,
        now=lambda: NOW,
    )


def test_empty_range_dry_run_reports_plan_without_api_or_database_writes():
    repository = MemoryRepository()
    client = FakeClient()
    service = _service(repository, client)

    summary = asyncio.run(
        service.synchronize(
            START,
            START + timedelta(minutes=3),
            dry_run=True,
            save_postgres=True,
        )
    )

    assert summary["status"] == "dry_run"
    assert summary["existing_candle_count"] == 0
    assert summary["missing_candle_count"] == 3
    assert summary["api_requests_attempted"] == 0
    assert summary["postgres_write_enabled"] is False
    assert not client.calls
    assert not repository.import_calls


def test_fully_synchronized_range_does_not_call_api_or_import():
    repository = MemoryRepository(
        [
            (_candle(START + timedelta(minutes=index)), CTRADER_PARQUET_PROVIDER, "existing")
            for index in range(3)
        ]
    )
    client = FakeClient()

    summary = asyncio.run(
        _service(repository, client).synchronize(
            START,
            START + timedelta(minutes=3),
            save_postgres=True,
        )
    )

    assert summary["status"] == "complete"
    assert summary["existing_candle_count"] == 3
    assert summary["missing_candle_count"] == 0
    assert summary["api_requests_attempted"] == 0
    assert not client.calls
    assert not repository.import_calls


def test_partial_sync_fetches_only_missing_intervals_and_persists_source_identity():
    present = _candle(START + timedelta(minutes=1))
    missing_first = _candle(START)
    missing_last = _candle(START + timedelta(minutes=2))
    repository = MemoryRepository([(present, CTRADER_PARQUET_PROVIDER, "existing")])
    client = FakeClient(
        {
            START: [_data(missing_first)],
            START + timedelta(minutes=2): [_data(missing_last)],
        }
    )

    summary = asyncio.run(
        _service(repository, client).synchronize(
            START,
            START + timedelta(minutes=3),
            save_postgres=True,
        )
    )

    assert summary["status"] == "complete"
    assert summary["existing_candle_count"] == 1
    assert summary["missing_candle_count"] == 2
    assert summary["api_requests_attempted"] == 2
    assert summary["candles_retrieved"] == summary["candles_validated"] == 2
    assert summary["candles_inserted"] == 2
    assert [call[:2] for call in client.calls] == [
        (START, START + timedelta(minutes=1)),
        (START + timedelta(minutes=2), START + timedelta(minutes=3)),
    ]
    assert repository.import_calls[0][2] == CTRADER_PARQUET_PROVIDER
    assert len(repository.import_calls[0][3]) == 2


def test_repeated_sync_is_idempotent_and_skips_fully_saved_range():
    candle = _candle(START)
    repository = MemoryRepository()
    client = FakeClient({START: [_data(candle)]})
    service = _service(repository, client)

    first = asyncio.run(
        service.synchronize(START, START + timedelta(minutes=1), save_postgres=True)
    )
    second = asyncio.run(
        service.synchronize(START, START + timedelta(minutes=1), save_postgres=True)
    )

    assert first["candles_inserted"] == 1
    assert second["status"] == "complete"
    assert second["existing_candle_count"] == 1
    assert second["api_requests_attempted"] == 0
    assert len(client.calls) == 1
    assert len(repository.records) == 1


def test_market_closure_is_not_requested_as_a_gap():
    start = datetime(2026, 1, 9, 21, 59, tzinfo=UTC)
    end = datetime(2026, 1, 11, 22, 6, tzinfo=UTC)
    repository = MemoryRepository()
    client = FakeClient()

    service = CTraderSyncService(
        repository,
        client,
        now=lambda: end + timedelta(minutes=1),
    )
    plan = service.build_plan(start, end)

    assert plan.expected_market_minutes == 7
    assert plan.market_closed_minutes > 0
    assert plan.missing_timestamps == (
        start,
        *(datetime(2026, 1, 11, 22, minute, tzinfo=UTC) for minute in range(6)),
    )
    assert len(plan.missing_intervals) == 2


ROLLOVER_CANDLES = [
    datetime(2026, 1, 5, 22, 1, tzinfo=UTC),
    datetime(2026, 1, 5, 22, 2, tzinfo=UTC),
    datetime(2026, 1, 6, 22, 1, tzinfo=UTC),
    datetime(2026, 1, 6, 22, 2, tzinfo=UTC),
    datetime(2026, 1, 6, 22, 3, tzinfo=UTC),
    datetime(2026, 1, 6, 22, 4, tzinfo=UTC),
]


def test_weekday_22_utc_minutes_are_not_treated_as_closed():
    start = datetime(2026, 1, 5, 21, 58, tzinfo=UTC)
    end = datetime(2026, 1, 5, 22, 8, tzinfo=UTC)
    service = CTraderSyncService(
        MemoryRepository(),
        FakeClient(),
        now=lambda: end + timedelta(minutes=1),
    )

    plan = service.build_plan(start, end)

    assert plan.expected_market_minutes == 10
    assert plan.market_closed_minutes == 0
    assert len(plan.missing_intervals) == 1
    assert len(plan.missing_timestamps) == 10


def test_confirmed_rollover_candles_are_planned_retrieved_and_imported_once():
    start = datetime(2026, 1, 5, 22, 0, tzinfo=UTC)
    end = datetime(2026, 1, 6, 23, 0, tzinfo=UTC)
    stored = []
    minute = start
    while minute < end:
        if not (minute.hour == 22 and minute.minute < 5):
            stored.append((_candle(minute), CTRADER_PARQUET_PROVIDER, "stored"))
        minute += timedelta(minutes=1)
    repository = MemoryRepository(stored)
    client = FakeClient()
    service = CTraderSyncService(repository, client, now=lambda: end + timedelta(days=1))

    plan = service.build_plan(start, end)
    assert set(ROLLOVER_CANDLES) <= set(plan.missing_timestamps)
    assert plan.market_closed_minutes == 0

    # 22:00 UTC is genuinely not returned; the six confirmed minutes are.
    day_one, day_two = (interval.start for interval in plan.missing_intervals)
    client.responses = {
        day_one: [_data(_candle(timestamp)) for timestamp in ROLLOVER_CANDLES[:2]],
        day_two: [_data(_candle(timestamp)) for timestamp in ROLLOVER_CANDLES[2:]],
    }
    summary = asyncio.run(service.synchronize(start, end, save_postgres=True))

    assert summary["candles_inserted"] == 6
    assert set(ROLLOVER_CANDLES) <= {c.opened_at for c, _, _ in repository.records}
    assert summary["status"] == "incomplete"
    assert [gap["missing_candle_count"] for gap in summary["unresolved_gaps"]] == [1, 2, 1]

    repeat = asyncio.run(service.synchronize(start, end, save_postgres=True))
    assert repeat["candles_inserted"] == 0
    assert len(repository.records) == len(stored) + 6


def test_missing_rollover_minutes_remain_unresolved_not_fabricated():
    start = datetime(2026, 1, 6, 22, 0, tzinfo=UTC)
    end = datetime(2026, 1, 6, 22, 5, tzinfo=UTC)
    repository = MemoryRepository()
    client = FakeClient()
    service = CTraderSyncService(repository, client, now=lambda: end + timedelta(minutes=1))

    summary = asyncio.run(service.synchronize(start, end, save_postgres=True))

    assert summary["status"] == "incomplete"
    assert summary["candles_inserted"] == 0
    assert repository.records == []
    assert summary["unresolved_gaps"][0]["missing_candle_count"] == 5


def test_fewer_api_candles_than_expected_reports_unresolved_gap():
    repository = MemoryRepository()
    client = FakeClient({START: []})

    summary = asyncio.run(
        _service(repository, client).synchronize(
            START,
            START + timedelta(minutes=2),
            save_postgres=True,
        )
    )

    assert summary["status"] == "incomplete"
    assert summary["candles_retrieved"] == 0
    assert summary["candles_inserted"] == 0
    assert summary["unresolved_gaps"] == [
        {
            "start": "2026-01-05T15:00:00Z",
            "end": "2026-01-05T15:02:00Z",
            "missing_candle_count": 2,
            "reason": "not_returned_by_ctrader",
        }
    ]


def test_api_error_reports_unresolved_gap_and_counts_attempted_request():
    repository = MemoryRepository()
    client = FakeClient()
    client.failure = CTraderError("temporary connection failure")

    summary = asyncio.run(
        _service(repository, client).synchronize(
            START,
            START + timedelta(minutes=1),
            save_postgres=True,
        )
    )

    assert summary["status"] == "incomplete"
    assert summary["api_requests_attempted"] == 1
    assert summary["unresolved_gaps"][0]["reason"] == "api_error"
    assert summary["api_errors"] == ["temporary connection failure"]
    assert not repository.import_calls


def test_invalid_candles_are_rejected_and_never_persisted():
    invalid = _candle(START).model_copy(update={"open": Decimal("0")})
    repository = MemoryRepository()
    client = FakeClient({START: [_data(invalid)]})

    summary = asyncio.run(
        _service(repository, client).synchronize(
            START,
            START + timedelta(minutes=1),
            save_postgres=True,
        )
    )

    assert summary["status"] == "incomplete"
    assert summary["rejected_records"] == 1
    assert summary["unresolved_gaps"][0]["reason"] == "validation_error"
    assert not repository.import_calls


def test_source_and_price_type_isolation_do_not_satisfy_ctrader_gap():
    timestamp = START
    repository = MemoryRepository(
        [
            (_candle(timestamp), OTHER_PROVIDER, "histdata"),
            (_candle(timestamp, price_type=PriceType.BID), CTRADER_PARQUET_PROVIDER, "bid"),
        ]
    )

    plan = _service(repository).build_plan(START, START + timedelta(minutes=1))

    assert plan.existing_candle_count == 0
    assert plan.missing_timestamps == (timestamp,)
    assert repository.reads[0][4] == CTRADER_PARQUET_PROVIDER


def test_no_save_is_read_only_and_reports_validated_data_as_not_saved():
    repository = MemoryRepository()
    client = FakeClient({START: [_data(_candle(START))]})

    summary = asyncio.run(
        _service(repository, client).synchronize(
            START,
            START + timedelta(minutes=1),
        )
    )

    assert summary["status"] == "retrieved_not_saved"
    assert summary["candles_validated"] == 1
    assert summary["candles_inserted"] == 0
    assert summary["unresolved_gaps"][0]["reason"] == "validated_but_not_saved"
    assert not repository.import_calls


def test_utc_range_and_current_candle_boundary_are_enforced():
    repository = MemoryRepository()
    service = _service(repository)

    with pytest.raises(ValueError, match="UTC"):
        service.build_plan(START.replace(tzinfo=None), START + timedelta(minutes=1))
    with pytest.raises(ValueError, match="whole minutes"):
        service.build_plan(START + timedelta(seconds=1), START + timedelta(minutes=1))

    current_minute = NOW.replace(second=0, microsecond=0)
    plan = service.build_plan(
        current_minute - timedelta(minutes=1),
        current_minute + timedelta(minutes=1),
    )

    assert plan.effective_end == current_minute
    assert plan.missing_timestamps == (current_minute - timedelta(minutes=1),)


def test_max_days_is_an_enforced_upper_bound():
    service = _service(MemoryRepository())
    with pytest.raises(ValueError, match="configured 1-day"):
        service.build_plan(START, START + timedelta(days=2), max_days=1)
    with pytest.raises(ValueError, match="between 1 and 31"):
        service.build_plan(START, START + timedelta(minutes=1), max_days=32)


def test_database_import_failure_is_not_reported_as_success():
    class FailingRepository(MemoryRepository):
        def import_candles(self, *_args):
            raise RuntimeError("database transaction failed")

    client = FakeClient({START: [_data(_candle(START))]})
    service = _service(FailingRepository(), client)

    with pytest.raises(RuntimeError, match="database transaction failed"):
        asyncio.run(
            service.synchronize(
                START,
                START + timedelta(minutes=1),
                save_postgres=True,
            )
        )


def test_sync_cli_parser_exposes_safe_opt_in_controls():
    from trading.ctrader.cli import _parser

    args = _parser().parse_args(
        [
            "sync",
            "--instrument",
            "EURUSD",
            "--environment",
            "demo",
            "--timeframe",
            "M1",
            "--start",
            "2026-01-05T15:00:00Z",
            "--end",
            "2026-01-05T15:01:00Z",
            "--dry-run",
            "--json",
        ]
    )

    assert args.command == "sync"
    assert args.dry_run is True
    assert args.save_postgres is False
    assert args.json is True


def test_sync_cli_dry_run_never_loads_credentials_or_constructs_client(monkeypatch, capsys):
    from trading import db
    from trading.ctrader import cli

    repository = MemoryRepository()
    monkeypatch.setattr(db, "get_engine", lambda: object())
    monkeypatch.setattr(cli, "PostgresMarketDataRepository", lambda _engine: repository)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("dry-run must not access cTrader credentials or API")

    monkeypatch.setattr(cli, "OAuthManager", fail_if_called)
    monkeypatch.setattr(cli, "CTraderReadOnlyClient", fail_if_called)

    exit_code = cli.main(
        [
            "sync",
            "--instrument",
            "EURUSD",
            "--environment",
            "demo",
            "--timeframe",
            "M1",
            "--start",
            "2026-01-05T15:00:00Z",
            "--end",
            "2026-01-05T15:01:00Z",
            "--dry-run",
            "--json",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run"
    assert not repository.import_calls
