import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq
import pytest
from sqlalchemy import create_engine

from tools.ctrader_copy_exporter.schedule import require_single_week
from tools.ctrader_copy_exporter.store import ExportStore
from trading.data.dataset import FeatureConfig, build_ml_dataset
from trading.data.domain import Candle, Instrument, InstrumentCode, Timeframe
from trading.data.ingestion import ingest_historical
from trading.data.parquet_store import export_partition, read_parquet
from trading.data.providers import SimulatedMarketDataProvider
from trading.storage.access import OperationalDatabase, StorageAccess
from trading.storage.duckdb_analytics import DuckDBParquetAnalytics
from trading.storage.features import ParquetFeatureStore
from trading.storage.parquet import ParquetMarketDataRepository


class CheckpointRecorder:
    def __init__(self) -> None:
        self.last_checkpoint = None
        self.persist_calls = 0
        self.source_keys: list[str] = []

    def get_checkpoint(self, provider, instrument, timeframe, source_key):
        self.source_keys.append(source_key)
        return self.last_checkpoint

    def persist_batch(
        self,
        instrument,
        timeframe,
        provider,
        source_key,
        source_reference,
        candles,
        checkpoint,
    ):
        self.persist_calls += 1
        self.source_keys.append(source_key)
        self.last_checkpoint = checkpoint
        return len(candles)


def _candle(timestamp: datetime, index: int = 0) -> Candle:
    price = Decimal("100") + Decimal(index) / Decimal("100")
    return Candle(
        instrument=InstrumentCode.WTI,
        timeframe=Timeframe.M1,
        opened_at=timestamp,
        open=price,
        high=price + Decimal("0.02"),
        low=price - Decimal("0.02"),
        close=price + Decimal("0.01"),
        volume=Decimal("5") + Decimal(index % 7),
    )


def test_parquet_repository_replay_is_idempotent_and_checkpoint_follows_write(tmp_path) -> None:
    recorder = CheckpointRecorder()
    repository = ParquetMarketDataRepository(tmp_path, recorder)
    start = datetime(2025, 1, 31, 23, 58, tzinfo=UTC)
    candles = [_candle(start + timedelta(minutes=index), index) for index in range(5)]
    instrument = Instrument(symbol=InstrumentCode.WTI, display_name="WTI")

    assert (
        repository.persist_batch(
            instrument, Timeframe.M1, "simulated", "seed-1", "test", candles, candles[-1].opened_at
        )
        == 5
    )
    assert recorder.last_checkpoint == candles[-1].opened_at
    assert (
        repository.persist_batch(
            instrument, Timeframe.M1, "simulated", "seed-1", "test", candles, candles[-1].opened_at
        )
        == 0
    )
    restored = list(
        repository.iter_candle_records(
            InstrumentCode.WTI,
            Timeframe.M1,
            start,
            start + timedelta(minutes=5),
            batch_size=2,
        )
    )
    assert [record[0] for record in restored] == candles
    assert all(record[2] == "test" for record in restored)
    assert len(list(tmp_path.rglob("part-000.parquet"))) == 2
    assert all(
        pq.ParquetFile(path).metadata.row_group(0).column(0).compression == "ZSTD"
        for path in tmp_path.rglob("*.parquet")
    )


def test_storage_access_facade_writes_and_reads_the_selected_candle_backend(tmp_path) -> None:
    recorder = CheckpointRecorder()
    repository = ParquetMarketDataRepository(tmp_path, recorder)
    timestamp = datetime(2025, 1, 1, tzinfo=UTC)
    candle = _candle(timestamp)
    engine = create_engine("sqlite://")
    trades = ExportStore(tmp_path / "trades", "access-run", csv_enabled=False)
    access = StorageAccess(
        candles=repository,
        features=ParquetFeatureStore(),
        trades=trades,
        operational=OperationalDatabase(engine),
    )

    try:
        inserted = access.write_candles(
            Instrument(symbol=InstrumentCode.WTI, display_name="WTI"),
            Timeframe.M1,
            "simulated",
            "seed-1",
            "test",
            [candle],
            timestamp,
        )
        restored = list(
            access.read_candles(
                InstrumentCode.WTI,
                Timeframe.M1,
                timestamp,
                timestamp + timedelta(minutes=1),
            )
        )
        interval = require_single_week(
            datetime(2025, 1, 6, tzinfo=UTC),
            datetime(2025, 1, 13, tzinfo=UTC),
            ZoneInfo("UTC"),
        )
        trade = {
            "trade_id": "facade-trade",
            "opened_at_utc": interval.start.isoformat(),
            "displayed_timestamp": interval.start.isoformat(),
            "ui_timezone": "UTC",
            "raw_fields": {"symbol": "WTI"},
        }

        assert inserted == 1
        assert restored == [candle]
        assert access.write_trades(interval, [trade]) == (1, 0)
        assert access.get_trade("facade-trade")["symbol"] == "WTI"
        assert len(list(access.read_trades())) == 1
    finally:
        trades.close()
        engine.dispose()


def test_parquet_repository_rejects_conflicts_without_advancing_checkpoint(tmp_path) -> None:
    recorder = CheckpointRecorder()
    repository = ParquetMarketDataRepository(tmp_path, recorder)
    instant = datetime(2025, 1, 1, tzinfo=UTC)
    instrument = Instrument(symbol=InstrumentCode.WTI, display_name="WTI")
    original = _candle(instant)
    repository.persist_batch(
        instrument, Timeframe.M1, "simulated", "seed-1", "test", [original], instant
    )
    conflicting = original.__class__(
        instrument=original.instrument,
        timeframe=original.timeframe,
        opened_at=original.opened_at,
        open=original.open,
        high=original.high + Decimal("1"),
        low=original.low,
        close=original.close,
        volume=original.volume,
    )

    with pytest.raises(ValueError, match="Conflicting export candle"):
        repository.persist_batch(
            instrument,
            Timeframe.M1,
            "simulated",
            "seed-1",
            "test",
            [conflicting],
            instant + timedelta(minutes=1),
        )
    assert recorder.last_checkpoint == instant


def test_historical_backfill_runs_directly_against_parquet_and_resumes(tmp_path) -> None:
    recorder = CheckpointRecorder()
    repository = ParquetMarketDataRepository(tmp_path, recorder)
    provider = SimulatedMarketDataProvider(seed=44)
    start = datetime(2025, 1, 6, 12, tzinfo=UTC)
    end = start + timedelta(minutes=20)

    gaps = asyncio.run(
        ingest_historical(
            provider,
            repository,
            InstrumentCode.WTI,
            Timeframe.M1,
            start,
            end,
            batch_size=50,
        )
    )
    replay_gaps = asyncio.run(
        ingest_historical(
            provider,
            repository,
            InstrumentCode.WTI,
            Timeframe.M1,
            start,
            end,
            batch_size=50,
        )
    )
    restored = list(
        repository.iter_candles(
            InstrumentCode.WTI,
            Timeframe.M1,
            start,
            end,
            source_provider=provider.name,
        )
    )

    assert not gaps
    assert not replay_gaps
    assert len(restored) == 20
    assert recorder.last_checkpoint == restored[-1].opened_at
    assert recorder.source_keys[0].startswith("parquet:")
    assert provider.source_key in recorder.source_keys[0]
    assert recorder.source_keys == [recorder.source_keys[0]] * 3


def test_interrupted_parquet_replacement_preserves_existing_file(tmp_path, monkeypatch) -> None:
    recorder = CheckpointRecorder()
    repository = ParquetMarketDataRepository(tmp_path, recorder)
    instant = datetime(2025, 1, 1, tzinfo=UTC)
    instrument = Instrument(symbol=InstrumentCode.WTI, display_name="WTI")
    existing = _candle(instant)
    repository.persist_batch(
        instrument, Timeframe.M1, "simulated", "seed-1", "test", [existing], instant
    )
    path = next(tmp_path.rglob("part-000.parquet"))
    before = list(read_parquet(path, include_lineage=True))

    def fail_replace(source, destination):
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr("trading.data.parquet_store.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        repository.persist_batch(
            instrument,
            Timeframe.M1,
            "simulated",
            "seed-1",
            "test",
            [_candle(instant + timedelta(minutes=1), 1)],
            instant + timedelta(minutes=1),
        )

    assert list(read_parquet(path, include_lineage=True)) == before
    assert not list(tmp_path.rglob("*.tmp"))
    assert recorder.last_checkpoint == instant


def test_feature_files_are_versioned_selective_and_reproducible(tmp_path) -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    candles = [_candle(start + timedelta(minutes=index), index) for index in range(150)]
    dataset = build_ml_dataset(
        candles,
        InstrumentCode.WTI,
        Timeframe.M1,
        source="test",
        feature_config=FeatureConfig(return_lags=(1,), volatility_window=5, ema_spans=(5,)),
    )
    store = ParquetFeatureStore()
    store.write(dataset, tmp_path)

    features = store.read_features(tmp_path, columns=["return_1"])
    targets = store.read_targets(tmp_path, columns=["forward_return"])
    schema = pq.read_schema(tmp_path / "features.parquet")

    assert list(features.columns) == ["return_1"]
    assert features.index.tz is not None
    assert list(targets.columns) == ["forward_return"]
    assert schema.metadata[b"trading.instrument"] == b"WTI"
    assert schema.metadata[b"trading.timeframe_minutes"] == b"1"
    assert schema.metadata[b"trading.feature_schema_version"] == b"2"
    assert schema.metadata[b"trading.dataset_fingerprint"].decode()
    assert json.loads(schema.metadata[b"trading.feature_configuration"])["features"]


def test_feature_store_reads_existing_phase_2b_schema_v1(tmp_path) -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    dataset = build_ml_dataset(
        [_candle(start + timedelta(minutes=index), index) for index in range(150)],
        InstrumentCode.WTI,
        Timeframe.M1,
        source="legacy-test",
    )
    store = ParquetFeatureStore()
    store.write(dataset, tmp_path)

    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("feature_schema_version")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    for table_name in ("features", "targets"):
        path = tmp_path / f"{table_name}.parquet"
        table = pq.read_table(path)
        metadata = dict(table.schema.metadata)
        for key in (
            b"trading.feature_schema_version",
            b"trading.instrument",
            b"trading.timeframe_minutes",
            b"trading.feature_configuration",
            b"trading.dataset_fingerprint",
        ):
            metadata.pop(key, None)
        pq.write_table(
            table.replace_schema_metadata(metadata),
            path,
            compression="zstd",
        )

    restored = store.read_features(tmp_path, columns=["return_1"])
    assert list(restored.columns) == ["return_1"]


def test_duckdb_queries_partitioned_parquet_without_materializing_other_ranges(
    tmp_path,
) -> None:
    pytest.importorskip("duckdb")
    starts = [
        datetime(2025, 1, 31, 23, 59, tzinfo=UTC),
        datetime(2025, 2, 1, 0, 0, tzinfo=UTC),
        datetime(2025, 2, 1, 0, 1, tzinfo=UTC),
    ]
    export_partition(
        (
            (_candle(timestamp, index), "simulated", "benchmark")
            for index, timestamp in enumerate(starts)
        ),
        tmp_path,
    )
    analytics = DuckDBParquetAnalytics(tmp_path)

    frame = analytics.query_candles(
        InstrumentCode.WTI,
        Timeframe.M1,
        starts[1],
        starts[2] + timedelta(minutes=1),
        columns=["opened_at", "close"],
    )
    aggregate = analytics.aggregate_candles(
        InstrumentCode.WTI,
        Timeframe.M1,
        starts[0],
        starts[2] + timedelta(minutes=1),
        bucket="1 hour",
    )

    assert len(frame) == 2
    assert frame["opened_at"].is_monotonic_increasing
    assert aggregate["candle_count"].sum() == 3
