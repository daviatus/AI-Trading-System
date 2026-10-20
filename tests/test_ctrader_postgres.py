from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from trading.ctrader import cli
from trading.ctrader.parquet import CTRADER_PARQUET_PROVIDER
from trading.ctrader.postgres import import_api_candles, import_parquet_dataset
from trading.data.domain import Candle, Instrument, InstrumentCode, PriceType, Timeframe
from trading.data.parquet_store import export_partition
from trading.data.repository import CandleImportReport, PostgresMarketDataRepository

START = datetime(2026, 1, 5, tzinfo=UTC)
SOURCE_REFERENCE = "ctrader-demo:EURUSD:M1:fixture"


def _candle(timestamp=START, *, open_value="1.10000", source=InstrumentCode.EURUSD):
    return Candle(
        instrument=source,
        timeframe=Timeframe.M1,
        opened_at=timestamp,
        open=Decimal(open_value),
        high=Decimal("1.10200") if source == InstrumentCode.EURUSD else Decimal("1.2"),
        low=Decimal("1.09800") if source == InstrumentCode.EURUSD else Decimal("0.8"),
        close=Decimal("1.10100") if source == InstrumentCode.EURUSD else Decimal("1.1"),
        volume=Decimal("42"),
        price_type=PriceType.UNKNOWN,
        quality_metadata={"volume_units": "raw_protocol_integer"},
    )


class _Rows:
    def __init__(self, items):
        self.items = items

    def all(self):
        return self.items


class _Transaction:
    def __init__(self, database):
        self.database = database

    def __enter__(self):
        self.original_records = list(self.database.records)
        return self

    def __exit__(self, exception_type, *_args):
        if exception_type is not None:
            self.database.records[:] = self.original_records
        return False


class _MemoryDatabase:
    def __init__(self):
        self.records = []
        self.execute_count = 0
        self.fail_on_execute = None

    def session(self):
        return _MemorySession(self)


class _MemorySession:
    def __init__(self, database):
        self.database = database

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def begin(self):
        return _Transaction(self.database)

    def get(self, _model, _key):
        return object()

    def scalars(self, statement):
        params = statement.compile(dialect=postgresql.dialect()).params
        instrument = params.get("instrument_symbol_1")
        timeframe = params.get("timeframe_minutes_1")
        provider = params.get("source_provider_1")
        lower = params.get("opened_at_1")
        upper = params.get("opened_at_2")
        price_types = params.get("price_type_1")
        rows = [
            record
            for record in self.database.records
            if record.instrument_symbol == instrument
            and record.timeframe_minutes == timeframe
            and record.source_provider == provider
            and record.opened_at >= lower
            and record.opened_at <= upper
            and record.price_type in price_types
        ]
        return _Rows(rows)

    def execute(self, statement):
        self.database.execute_count += 1
        if self.database.execute_count == self.database.fail_on_execute:
            raise RuntimeError("simulated database failure")
        inserted_keys = []
        for values in statement._multi_values[0]:
            key = (
                values["instrument_symbol"],
                values["timeframe_minutes"],
                values["opened_at"],
                values["source_provider"],
                values["price_type"],
            )
            if any(
                (
                    row.instrument_symbol,
                    row.timeframe_minutes,
                    row.opened_at,
                    row.source_provider,
                    row.price_type,
                )
                == key
                for row in self.database.records
            ):
                continue
            self.database.records.append(SimpleNamespace(**values))
            inserted_keys.append((values["opened_at"], values["price_type"]))
        return _Rows(inserted_keys)


@pytest.fixture
def database_repository(monkeypatch):
    from trading.data import repository as repository_module

    database = _MemoryDatabase()
    monkeypatch.setattr(repository_module, "Session", lambda _engine: database.session())
    return database, PostgresMarketDataRepository(engine=object())


def _import(repository, candles, provider=CTRADER_PARQUET_PROVIDER):
    return repository.import_candles(
        Instrument(symbol=InstrumentCode.EURUSD, display_name="EUR/USD"),
        Timeframe.M1,
        provider,
        [(candle, SOURCE_REFERENCE) for candle in candles],
    )


def test_postgres_import_inserts_and_replay_is_idempotent(database_repository):
    database, repository = database_repository
    candle = _candle()

    first = _import(repository, [candle])
    replay = _import(repository, [candle])

    assert first.inserted == 1
    assert first.unchanged == first.conflicting == first.rejected == 0
    assert replay.inserted == 0
    assert replay.unchanged == 1
    assert replay.conflicting == replay.rejected == 0
    assert len(database.records) == 1


def test_postgres_import_does_not_overwrite_conflicting_ohlcv(database_repository):
    database, repository = database_repository
    original = _candle()
    conflicting = _candle(open_value="1.10010")
    before = _import(repository, [original])

    report = _import(repository, [conflicting])

    assert before.inserted == 1
    assert report.conflicting == 1
    assert report.inserted == report.unchanged == report.rejected == 0
    assert database.records[0].open == original.open


def test_postgres_import_keeps_providers_in_separate_series(database_repository):
    database, repository = database_repository
    candle = _candle()

    ctrader_report = _import(repository, [candle])
    histdata_report = _import(repository, [candle], provider="histdata_generic_ascii")

    assert ctrader_report.inserted == histdata_report.inserted == 1
    assert {record.source_provider for record in database.records} == {
        CTRADER_PARQUET_PROVIDER,
        "histdata_generic_ascii",
    }


def test_postgres_import_reports_duplicate_timestamps(database_repository):
    database, repository = database_repository
    candle = _candle()
    identical = _import(repository, [candle, candle])
    assert identical.inserted == 1
    assert identical.rejected == 1

    database.records.clear()
    conflicting_duplicate = _import(
        repository,
        [candle, _candle(open_value="1.10010")],
    )
    assert conflicting_duplicate.inserted == 0
    assert conflicting_duplicate.conflicting == 2
    assert len(database.records) == 0


def test_postgres_import_handles_empty_dataset_without_opening_session(database_repository):
    database, repository = database_repository

    report = _import(repository, [])

    assert report.as_dict() == {
        "seen": 0,
        "inserted": 0,
        "unchanged": 0,
        "conflicting": 0,
        "rejected": 0,
        "issues": (),
    }
    assert database.execute_count == 0


def test_postgres_import_rejects_invalid_candles_and_numeric_precision(database_repository):
    database, repository = database_repository
    zero_price = Candle(
        instrument=InstrumentCode.EURUSD,
        timeframe=Timeframe.M1,
        opened_at=START,
        open=Decimal(0),
        high=Decimal(0),
        low=Decimal(0),
        close=Decimal(0),
        volume=Decimal(1),
    )
    excessive_precision = _candle(open_value="1.100000001")

    report = _import(repository, [zero_price, excessive_precision])

    assert report.rejected == 2
    assert report.inserted == report.unchanged == report.conflicting == 0
    assert not database.records


def test_postgres_import_rolls_back_all_batches_on_failure(database_repository):
    database, repository = database_repository
    candles = [_candle(START + timedelta(minutes=index)) for index in range(1001)]
    database.fail_on_execute = 2

    with pytest.raises(RuntimeError, match="simulated database failure"):
        _import(repository, candles)

    assert database.execute_count == 2
    assert database.records == []


def test_api_import_refuses_invalid_dataset_before_repository_write():
    class Repository:
        called = False

        def import_candles(self, *_args):
            self.called = True
            raise AssertionError("invalid dataset must not be persisted")

    repository = Repository()
    data = [
        {
            "instrument": "EURUSD",
            "timeframe": 1,
            "opened_at": "2026-01-05T00:00:00Z",
            "open": "0",
            "high": "0",
            "low": "0",
            "close": "0",
            "volume": "1",
        }
    ]

    _candles, quality, report = import_api_candles(
        repository,
        data,
        START,
        START + timedelta(minutes=1),
    )

    assert quality["validation_status"] == "invalid"
    assert report.rejected == 1
    assert not repository.called


def test_parquet_import_revalidates_and_preserves_lineage(tmp_path):
    candle = _candle()
    export_partition([(candle, CTRADER_PARQUET_PROVIDER, SOURCE_REFERENCE)], tmp_path)

    class Repository:
        records = None

        def import_candles(self, instrument, timeframe, provider, records):
            self.records = (instrument, timeframe, provider, records)
            return CandleImportReport(seen=1, inserted=1)

    repository = Repository()
    quality, report = import_parquet_dataset(repository, tmp_path)

    assert quality["validation_status"] == "valid"
    assert report.inserted == 1
    assert repository.records[2] == CTRADER_PARQUET_PROVIDER
    assert repository.records[3] == [(candle, SOURCE_REFERENCE)]


def test_parquet_import_rejects_non_ctrader_source(tmp_path):
    candle = _candle()
    export_partition([(candle, "histdata_generic_ascii", SOURCE_REFERENCE)], tmp_path)

    class Repository:
        called = False

        def import_candles(self, *_args):
            self.called = True

    repository = Repository()
    quality, report = import_parquet_dataset(repository, tmp_path)

    assert quality["validation_status"] == "invalid"
    assert report.rejected == 1
    assert not repository.called


def test_parquet_import_revalidates_candle_values_before_database_write(tmp_path):
    invalid = Candle(
        instrument=InstrumentCode.EURUSD,
        timeframe=Timeframe.M1,
        opened_at=START,
        open=Decimal(0),
        high=Decimal(0),
        low=Decimal(0),
        close=Decimal(0),
        volume=Decimal(1),
    )
    export_partition([(invalid, CTRADER_PARQUET_PROVIDER, SOURCE_REFERENCE)], tmp_path)

    class Repository:
        called = False

        def import_candles(self, *_args):
            self.called = True

    repository = Repository()
    quality, report = import_parquet_dataset(repository, tmp_path)

    assert quality["validation_status"] == "invalid"
    assert report.rejected == 1
    assert not repository.called


def test_parquet_import_handles_empty_dataset_without_database_write(tmp_path):
    class Repository:
        called = False

        def import_candles(self, *_args):
            self.called = True

    repository = Repository()
    quality, report = import_parquet_dataset(repository, tmp_path)

    assert quality["validation_status"] == "valid"
    assert report.seen == report.inserted == report.unchanged == 0
    assert not repository.called


def test_parquet_import_rejects_missing_input_directory(tmp_path):
    with pytest.raises(FileNotFoundError, match="Parquet dataset directory"):
        import_parquet_dataset(object(), tmp_path / "missing")


def test_candles_cli_import_is_explicit_and_reports_postgres_results(monkeypatch, capsys):
    class FakeManager:
        def __init__(self, _store):
            pass

        def credentials(self):
            return "client-id", "client-secret"

        def access_token(self):
            return "access-token"

    class FakeClient:
        def __init__(self, *_args):
            pass

        async def get_eurusd_m1_candles(self, *_args, **_kwargs):
            return [
                {
                    "instrument": "EURUSD",
                    "timeframe": 1,
                    "opened_at": "2026-01-05T00:00:00Z",
                    "open": "1.1",
                    "high": "1.102",
                    "low": "1.098",
                    "close": "1.101",
                    "volume": "42",
                    "price_type": "unknown",
                    "quality_metadata": {"volume_units": "raw_protocol_integer"},
                }
            ]

    class FakeRepository:
        def __init__(self, _engine):
            pass

        def import_candles(self, _instrument, _timeframe, _provider, records):
            assert records[0][0].opened_at == START
            return CandleImportReport(seen=1, inserted=1)

    from trading import db

    monkeypatch.setattr(cli, "OAuthManager", FakeManager)
    monkeypatch.setattr(cli, "CTraderReadOnlyClient", FakeClient)
    monkeypatch.setattr(cli, "PostgresMarketDataRepository", FakeRepository)
    monkeypatch.setattr(db, "get_engine", lambda: object())

    exit_code = cli.main(
        [
            "candles",
            "--instrument",
            "EURUSD",
            "--environment",
            "demo",
            "--start",
            "2026-01-05T00:00:00Z",
            "--end",
            "2026-01-05T00:01:00Z",
            "--import-postgres",
        ]
    )

    assert exit_code == 0
    assert __import__("json").loads(capsys.readouterr().out)["postgres_import"]["inserted"] == 1


def test_parquet_cli_import_does_not_require_api_credentials(monkeypatch, capsys, tmp_path):
    candle = _candle()
    export_partition([(candle, CTRADER_PARQUET_PROVIDER, SOURCE_REFERENCE)], tmp_path)

    class FakeRepository:
        def __init__(self, _engine):
            pass

        def import_candles(self, _instrument, _timeframe, _provider, records):
            assert records == [(candle, SOURCE_REFERENCE)]
            return CandleImportReport(seen=1, unchanged=1)

    from trading import db

    monkeypatch.setattr(cli, "OAuthManager", lambda *_args: pytest.fail("unexpected API auth"))
    monkeypatch.setattr(cli, "PostgresMarketDataRepository", FakeRepository)
    monkeypatch.setattr(db, "get_engine", lambda: object())

    exit_code = cli.main(["import-parquet", "--input", str(tmp_path)])

    report = __import__("json").loads(capsys.readouterr().out)
    assert exit_code == 0
    assert report["postgres_import"]["unchanged"] == 1
