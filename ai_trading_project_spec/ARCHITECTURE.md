# Architecture

## Storage responsibilities

| Data | Primary store | Current implementation and compatibility |
| --- | --- | --- |
| Historical OHLCV | Partitioned Parquet + ZSTD | Versioned schema 1, UTC candle-open timestamps, Decimal price/volume fields, deterministic monthly partitions and atomic replacement. New backfills can opt into this repository. Existing PostgreSQL candle data is retained and remains the default for current consumers. |
| Feature and target datasets | Separate Parquet files + manifest | Causal feature definitions and chronological purge/embargo splits remain in `trading.data.dataset`. Features and targets are stored separately, with ZSTD, version/configuration/instrument/timeframe/fingerprint metadata, and selected-column reads. |
| cTrader Copy trade history | SQLite | `export.sqlite3` is authoritative for trades, run manifests, checksummed batches and weekly/daily checkpoints. Trade IDs are unique per export run. Structured common fields are additive columns; all UI fields remain in the original JSON payload. CSV is an optional, atomically rebuilt projection. |
| Operational and relational state | PostgreSQL | Keep instruments, ingestion checkpoints, system events and future orders/positions/model metadata here. Reuse existing SQLAlchemy models and Alembic history; do not create equivalent tables or migrate existing candles implicitly. |
| Ad-hoc analytics | DuckDB over Parquet | Optional `.[analytics]` dependency. Queries scan existing Parquet files with predicates and projections; the in-memory DuckDB connection is disposable and is not a second persistent data copy. |

The typed access facade is `trading.storage.StorageAccess`. Its candle interface
continues to match `MarketDataRepository`, so ingestion, quality reporting and
aggregation can receive either the existing PostgreSQL repository or
`ParquetMarketDataRepository`. The latter uses the existing PostgreSQL
instrument/checkpoint tables for metadata, with checkpoint keys namespaced to
the Parquet backend and data root. It writes and atomically replaces Parquet
partitions before advancing a checkpoint; after an interruption, retrying the
same batch is idempotent. PostgreSQL remains the CLI default until an operator
explicitly selects `--storage parquet`.

## Data flow

```text
CSV / simulated provider
        |
        v
backfill -> validation / gap reporting -> PostgreSQL (existing default)
                                  \----> Parquet + ZSTD (opt-in new backfills)
                                            |                 |
                                            v                 v
                                  feature preparation     DuckDB analytics
                                  -> features.parquet
                                  -> targets.parquet
                                  -> manifest.json

authorized cTrader History UI -> incremental SQLite trade/checkpoint store
                                      \-> optional atomic trades.csv

PostgreSQL -> instruments, checkpoints, events and operational relational state
```

Parquet exports of existing PostgreSQL candles remain supported by
`export-parquet`; `import-parquet` remains a non-destructive, idempotent import
to PostgreSQL. Neither operation deletes its source. Choosing Parquet for a new
backfill does not migrate existing PostgreSQL candles. If a future cutover is
desired, first compare date-bounded counts, unique `(instrument, timeframe,
opened_at, source_provider)` keys, representative OHLCV values, provenance and
quality reports; retain the PostgreSQL source and rollback path until an
operator verifies the results.

## Directory layout

```text
data/
  market/
    ohlcv/
      instrument=WTI/
        timeframe=1m/
          year=2025/month=01/part-000.parquet
      instrument=XAUUSD/timeframe=5m/year=2025/month=01/part-000.parquet
  ml/
    instrument=WTI/timeframe=1m/<dataset-fingerprint>/
      features.parquet
      targets.parquet
      manifest.json
  ctrader_copy_export/
    <dry-run-id>/export.sqlite3
    <dry-run-id>/trades.csv       # only when CSV projection is enabled
  ctrader-browser-profile/        # private local authentication state
```

Use one active writer per OHLCV partition. Files are ordered by UTC
`opened_at`, instrument, timeframe and provider. The fixed `part-000.parquet`
file per monthly partition avoids a proliferation of tiny files; it is
atomically replaced on an upsert. Keep generated datasets and browser profiles
outside source control.

## Schema and timestamp conventions

- Candle schema version `1` is defined in `trading.data.parquet_store`.
  `opened_at` is a microsecond-resolution UTC timestamp marking candle open.
  OHLCV uses `decimal128(20, 8)` prices and `decimal128(24, 8)` volume.
  Partitions are `instrument`, `timeframe`, calendar `year`, and `month`.
- Candle files use ZSTD compression, schema metadata, stable ordering, and
  source-provider/source-reference provenance. Duplicate keys are ignored only
  when values and provenance agree; conflicting values fail explicitly.
- Feature and target Parquet tables are separate. Dataset schema version `1`
  remains stable; feature schema version `2` adds instrument, timeframe,
  feature configuration and dataset fingerprint to Arrow metadata and the
  manifest. The reader remains compatible with existing Phase 2B v1 files.
  `prediction_at` is UTC.
  `ParquetFeatureStore` can load selected columns without loading targets or
  unrelated feature columns.
- Features remain causal and targets remain separate forward-return labels.
  The established chronological train/validation/test splits, purge gap and
  embargo are unchanged. Any future fitted preprocessing must be fit on the
  training split only.
- Trade timestamps include canonical `opened_at_utc`, the original displayed
  timestamp and UI timezone. Numeric trade values are stored as text to avoid
  floating-point loss; raw UI fields are preserved in JSON. Common structured
  fields are nullable so legacy and sparse rows remain valid.
- PostgreSQL stores timezone-aware timestamps and `NUMERIC` OHLCV. No schema
  migration is needed for this storage layer.

## Storage and access setup

Install optional local analytics and exporter dependencies from PowerShell:

```powershell
py -3.12 -m pip install -e ".[dev,analytics,ctrader-export]"
docker compose up -d db
```

Backfill directly to Parquet while retaining PostgreSQL as the checkpoint and
instrument metadata store:

```powershell
python -m trading.data.cli backfill `
  --storage parquet `
  --data-root .\data\market\ohlcv `
  --provider simulated `
  --instrument WTI `
  --timeframe 1m `
  --start "2025-01-01T00:00:00+00:00" `
  --end "2025-02-01T00:00:00+00:00"
```

Use `--storage postgres` (the default) to preserve the previous pipeline.
Quality, aggregation and dataset commands accept the same backend arguments.
The existing `export-parquet` and `import-parquet` commands retain their
PostgreSQL source/destination behavior.

Python components can inject the same adapters directly:

```python
from trading.storage import (
    DuckDBParquetAnalytics,
    ParquetFeatureStore,
    ParquetMarketDataRepository,
    StorageAccess,
)
```

`StorageAccess` accepts the selected candle repository, feature store,
`ExportStore`, SQLAlchemy `Engine` wrapper (`OperationalDatabase`), and optional
DuckDB adapter. DuckDB queries filter instrument, timeframe and UTC bounds and
select only requested candle columns; `aggregate_candles` groups directly over
the Parquet scan.

## cTrader Copy trade export and CSV

Use only the authorized read-only browser flow; the exporter does not call
broker APIs or place orders. SQLite remains the durable source of truth. The
exporter creates CSV by default for backward compatibility; disable that
projection with `--no-csv`:

```powershell
ctrader-copy-exporter dry-run `
  --url "<authorized Copy History URL>" `
  --selectors ".\data\ctrader-copy-selectors.json" `
  --timezone "Europe/London" `
  --start "2026-10-12T00:00:00+01:00" `
  --end "2026-10-19T00:00:00+01:00" `
  --no-csv
```

To produce CSV, omit `--no-csv`; `trades.csv` is rebuilt atomically from
SQLite after every committed batch and includes structured common fields plus
the JSON payload of all visible raw fields. Existing CSV files are not deleted
by the SQLite schema upgrade.

## Retention, backup and recovery

- Retain source CSVs, original PostgreSQL data and Parquet exports until their
  counts, checksums, ranges and quality reports have been independently
  verified. Retention must also comply with the relevant data license.
- Back up PostgreSQL with a consistent `pg_dump` and back up the entire Parquet
  partition tree. Because Parquet-backed ingestion checkpoints live in
  PostgreSQL, take coordinated backups or retain a replayable source provider.
  Restore tests should compare counts and representative OHLCV values before
  resuming ingestion.
- SQLite is authoritative for exporter recovery. Close the exporter before
  copying the database, or use SQLite's online backup API. CSV can be
  regenerated from SQLite; browser authentication profiles must be treated as
  secrets and must not be copied to untrusted backup targets.
- Writes use same-directory temporary files and atomic replacement where
  supported. A failed Parquet replacement leaves the old file intact and does
  not advance the checkpoint. Parquet writers should be serialized per
  partition.
- No automatic deletion or destructive migration is performed. Database
  migrations remain unchanged.

## Benchmarking

The synthetic benchmark measures one million deterministic WTI 1-minute
candles by default, a seven-day filtered candle read, selective feature-column
loading, indexed SQLite trade lookup, and DuckDB aggregation across monthly
Parquet partitions. It reports actual elapsed time, byte sizes and Python
`tracemalloc` peak allocations (not native-library/process RSS):

```powershell
python -m tools.storage_benchmark
python -m tools.storage_benchmark --rows 100000
```

By default, benchmark files are written to a temporary directory and removed
when the run ends. To retain them, pass `--output` to a new or empty directory.
The benchmark does not compare against PostgreSQL timing because results depend
on the configured local database, volume and cache state; it performs no
database writes.

## Modules

- `src/trading/config`: typed configuration, environment separation and secret loading
- `src/trading/domain`: canonical instruments, bars, quotes, orders, fills, positions, events
- `src/trading/data`: providers, ingestion, validation, aggregation and Parquet schema
- `src/trading/storage`: typed storage facade, Parquet candle/feature stores and DuckDB analytics
- `src/trading/storage` plus existing `src/trading/models.py`: PostgreSQL operational persistence
- `src/trading/features`: leak-free indicator generation
- `src/trading/strategies`: deterministic rule-based strategies
- `src/trading/ml`: feature pipelines, models, training and registry
- `src/trading/backtest`: event-driven simulation, transaction costs
- `src/trading/risk`: position sizing, limits and kill switch
- `src/trading/execution`: paper broker, cTrader demo adapter and reconciliation
- `src/trading/portfolio`: exposure, PnL and multi-asset controls
- `src/trading/news`: optional RSS/news adapters, dedup and timestamped scoring
- `src/trading/api`: local read-only dashboard API
- `src/trading/monitoring`: structured logs, health and alerts

## Interface design

Define protocols for `MarketDataProvider`, `BrokerAdapter`, `Strategy`,
`RiskPolicy`, `ModelRegistry` and `OrderRepository`. Broker-dependent values
come from `InstrumentSpecification`. Strategies never call broker APIs
directly. All proposed orders must pass independent risk checks. `StorageAccess`
keeps callers independent of whether candles come from PostgreSQL or Parquet.

## Reliability

Use idempotency keys for orders; explicit order state machines; reconnect and
backoff; stale quote detection; heartbeat; durable event log; broker/local
reconciliation; and circuit breakers. A disconnected system must not assume a
rejected/cancelled order without broker confirmation. Live execution remains
disabled by default.
