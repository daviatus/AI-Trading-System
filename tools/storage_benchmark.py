from __future__ import annotations

import argparse
import json
import tempfile
import time
import tracemalloc
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow
import pyarrow.parquet as pq

from tools.ctrader_copy_exporter.schedule import ExportInterval, require_single_week
from tools.ctrader_copy_exporter.store import ExportStore
from trading.data.dataset import build_ml_dataset
from trading.data.domain import Candle, Instrument, InstrumentCode, Timeframe
from trading.storage.duckdb_analytics import DuckDBParquetAnalytics
from trading.storage.features import ParquetFeatureStore
from trading.storage.parquet import ParquetMarketDataRepository


class _CheckpointRecorder:
    def get_checkpoint(self, provider, instrument, timeframe, source_key):
        return None

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
        return len(candles)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark local market-data storage backends")
    parser.add_argument("--rows", type=int, default=1_000_000)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.rows <= 0:
        parser.error("--rows must be positive")

    if arguments.output is None:
        with tempfile.TemporaryDirectory(prefix="trading-storage-benchmark-") as temporary:
            report = run_benchmark(Path(temporary), arguments.rows)
        report["storage_retained"] = False
    else:
        root = arguments.output.resolve()
        if root.exists() and any(root.iterdir()):
            parser.error("--output must not exist or must be empty; refusing to overwrite data")
        root.mkdir(parents=True, exist_ok=True)
        report = run_benchmark(root, arguments.rows)
        report["storage_retained"] = True
    print(json.dumps(report, indent=2, sort_keys=True))


def run_benchmark(root: Path, row_count: int) -> dict[str, object]:
    candle_root = root / "candles"
    candle_repository = ParquetMarketDataRepository(candle_root, _CheckpointRecorder())
    instrument = Instrument(symbol=InstrumentCode.WTI, display_name="WTI")
    start = datetime(2020, 1, 1, tzinfo=UTC)

    def write_candles() -> int:
        batch: list[Candle] = []
        batch_month = None
        inserted = 0
        last: datetime | None = None
        for index in range(row_count):
            opened_at = start + timedelta(minutes=index)
            month = (opened_at.year, opened_at.month)
            if batch and month != batch_month:
                inserted += candle_repository.persist_batch(
                    instrument,
                    Timeframe.M1,
                    "benchmark",
                    "synthetic-million",
                    "deterministic-benchmark",
                    batch,
                    last,
                )
                batch.clear()
            if not batch:
                batch_month = month
            batch.append(_benchmark_candle(opened_at, index))
            last = opened_at
        if batch:
            inserted += candle_repository.persist_batch(
                instrument,
                Timeframe.M1,
                "benchmark",
                "synthetic-million",
                "deterministic-benchmark",
                batch,
                last,
            )
        return inserted

    written, write_stats = _measure(write_candles, trace_memory=False)
    if written != row_count:
        raise RuntimeError(f"Expected {row_count} candles written, found {written}")
    write_stats["peak_python_bytes"] = _measure_candle_batch_peak(
        start,
        min(row_count, 44_640),
    )
    write_stats["memory_probe_rows"] = min(row_count, 44_640)
    parquet_paths = list(candle_root.rglob("*.parquet"))
    candle_bytes = sum(path.stat().st_size for path in parquet_paths)
    candle_uncompressed_bytes = 0
    for path in parquet_paths:
        parquet_file = pq.ParquetFile(path)
        candle_uncompressed_bytes += sum(
            parquet_file.metadata.row_group(index).total_byte_size
            for index in range(parquet_file.metadata.num_row_groups)
        )

    range_start = start + timedelta(minutes=max(0, row_count // 2))
    range_end = range_start + timedelta(days=7)

    def read_selected_range() -> int:
        return sum(
            1
            for _ in candle_repository.iter_candles(
                InstrumentCode.WTI,
                Timeframe.M1,
                range_start,
                range_end,
                source_provider="benchmark",
                batch_size=8192,
            )
        )

    selected_count, range_stats = _measure(read_selected_range)
    feature_dir = root / "features"
    feature_candles = [
        _benchmark_candle(start + timedelta(minutes=index), index)
        for index in range(min(max(row_count, 5000), 20_000))
    ]
    dataset = build_ml_dataset(
        feature_candles,
        InstrumentCode.WTI,
        Timeframe.M1,
        source="deterministic-storage-benchmark",
    )
    feature_store = ParquetFeatureStore()

    def write_features() -> int:
        feature_store.write(dataset, feature_dir)
        return len(dataset.features)

    feature_rows, feature_write_stats = _measure(write_features)

    def read_selected_features() -> int:
        return len(feature_store.read_features(feature_dir, columns=["return_1", "rsi_14"]))

    feature_read_count, feature_read_stats = _measure(read_selected_features)
    feature_bytes = sum(path.stat().st_size for path in feature_dir.glob("*.parquet"))

    trade_dir = root / "trade-history"
    trade_store = ExportStore(trade_dir, "benchmark")
    trade_count = min(max(row_count // 100, 10_000), 100_000)
    timezone = ZoneInfo("UTC")
    interval = require_single_week(
        datetime(2026, 10, 12, tzinfo=timezone),
        datetime(2026, 10, 19, tzinfo=timezone),
        timezone,
    )
    synthetic_trades = _synthetic_trade_rows(trade_count, start)

    def write_trades() -> int:
        inserted, _ = trade_store.upsert_batch(interval, synthetic_trades)
        return inserted

    try:
        trade_rows, trade_write_stats = _measure(write_trades, trace_memory=False)
        trade_lookup, trade_lookup_stats = _measure(
            lambda: trade_store.get_trade(f"benchmark-trade-{trade_count // 2}")
        )
        trade_write_stats["peak_python_bytes"] = _measure_trade_batch_peak(
            trade_count,
            start,
            interval,
        )
        trade_write_stats["memory_probe_rows"] = trade_count
        if trade_lookup is None or trade_rows != trade_count:
            raise RuntimeError("SQLite benchmark trade insert or lookup failed")
        sqlite_database_bytes = trade_store.database_path.stat().st_size
        csv_bytes = trade_store.csv_path.stat().st_size if trade_store.csv_path.exists() else 0
    finally:
        trade_store.close()

    analytics = DuckDBParquetAnalytics(candle_root)

    def query_partitions() -> int:
        result = analytics.aggregate_candles(
            InstrumentCode.WTI,
            Timeframe.M1,
            start,
            start + timedelta(minutes=row_count),
            bucket="1 day",
        )
        return int(result["candle_count"].sum())

    analytics_count, analytics_stats = _measure(query_partitions)
    if analytics_count != row_count:
        raise RuntimeError(f"DuckDB returned {analytics_count} of {row_count} candles")

    return {
        "benchmark": "deterministic synthetic local storage workloads",
        "python_peak_memory_measurement": (
            "tracemalloc peak Python allocations; excludes native allocations"
        ),
        "rows_written": written,
        "parquet": {
            "partitions": len(parquet_paths),
            "bytes": candle_bytes,
            "uncompressed_row_group_bytes": candle_uncompressed_bytes,
            "write": write_stats,
            "selected_range_rows": selected_count,
            "selected_range": {"start": range_start.isoformat(), "end": range_end.isoformat()},
            "selected_range_read": range_stats,
            "duckdb_aggregate_rows": analytics_count,
            "duckdb_multi_partition_query": analytics_stats,
        },
        "features": {
            "rows": feature_rows,
            "bytes": feature_bytes,
            "write": feature_write_stats,
            "selected_feature_rows": feature_read_count,
            "selected_columns": ["return_1", "rsi_14"],
            "selected_column_read": feature_read_stats,
        },
        "sqlite_trade_history": {
            "rows": trade_rows,
            "sqlite_database_bytes": sqlite_database_bytes,
            "csv_projection_bytes": csv_bytes,
            "write": trade_write_stats,
            "indexed_trade_lookup": trade_lookup_stats,
        },
        "versions": {"pandas": pd.__version__, "pyarrow": pyarrow.__version__},
        "storage_root": str(root),
    }


def _benchmark_candle(timestamp: datetime, index: int) -> Candle:
    close = Decimal("70") + Decimal(index % 10_000) / Decimal("100")
    return Candle(
        instrument=InstrumentCode.WTI,
        timeframe=Timeframe.M1,
        opened_at=timestamp,
        open=close,
        high=close + Decimal("0.02"),
        low=close - Decimal("0.02"),
        close=close + Decimal("0.01"),
        volume=Decimal("1") + Decimal(index % 17),
    )


def _measure(operation, *, trace_memory: bool = True):
    if trace_memory:
        tracemalloc.start()
    started = time.perf_counter()
    result = operation()
    elapsed = time.perf_counter() - started
    peak = None
    if trace_memory:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    return result, {"seconds": round(elapsed, 6), "peak_python_bytes": peak}


def _measure_candle_batch_peak(start: datetime, count: int) -> int:
    with tempfile.TemporaryDirectory(prefix="trading-storage-memory-") as temporary:
        tracemalloc.start()
        try:
            candles = [
                _benchmark_candle(start + timedelta(minutes=index), index) for index in range(count)
            ]
            repository = ParquetMarketDataRepository(
                Path(temporary),
                _CheckpointRecorder(),
            )
            repository.persist_batch(
                Instrument(symbol=InstrumentCode.WTI, display_name="WTI"),
                Timeframe.M1,
                "benchmark",
                "memory-probe",
                "synthetic-memory-probe",
                candles,
                candles[-1].opened_at,
            )
            _, peak = tracemalloc.get_traced_memory()
            return peak
        finally:
            tracemalloc.stop()


def _measure_trade_batch_peak(
    trade_count: int,
    start: datetime,
    interval: ExportInterval,
) -> int:
    with tempfile.TemporaryDirectory(prefix="trading-storage-trades-memory-") as temporary:
        store = ExportStore(Path(temporary), "memory-probe")
        tracemalloc.start()
        try:
            trades = _synthetic_trade_rows(trade_count, start)
            store.upsert_batch(interval, trades)
            _, peak = tracemalloc.get_traced_memory()
            return peak
        finally:
            tracemalloc.stop()
            store.close()


def _synthetic_trade_rows(count: int, start: datetime) -> list[dict[str, object]]:
    return [
        {
            "trade_id": f"benchmark-trade-{index}",
            "opened_at_utc": (start + timedelta(minutes=index)).isoformat(),
            "displayed_timestamp": (start + timedelta(minutes=index)).isoformat(),
            "ui_timezone": "UTC",
            "raw_fields": {
                "symbol": "WTI",
                "side": "BUY",
                "volume": "0.01",
                "entry_price": "75.25",
                "commission": "-0.02",
                "net_profit": "0.12",
            },
        }
        for index in range(count)
    ]


if __name__ == "__main__":
    main()
