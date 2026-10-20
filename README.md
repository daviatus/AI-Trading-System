# AI Trading System

The foundation includes PostgreSQL, Alembic migrations, structured logs, and liveness/readiness endpoints. The market data layer adds asynchronous historical and streaming provider contracts, CSV imports, deterministic synthetic data, OHLCV validation, gap reporting, PostgreSQL persistence, and resumable ingestion. Phase 2B adds session-aware quality analysis, finalized-only 1m-to-5m aggregation, versioned partitioned Parquet storage, and causal ML dataset preparation.

Trading mode defaults to `DEMO`. Configuration only accepts `DEMO` or `SIMULATION`; enabling `LIVE_EXECUTION_ENABLED` or selecting `LIVE` is rejected. The cTrader integration is limited to explicit, read-only demo account and symbol discovery. It cannot place orders or modify positions. No credentials are stored in source code; model training and strategy optimization are out of scope.

## Requirements

- Python 3.12
- Docker Desktop with Docker Compose v2 (for PostgreSQL and containerized development)

## Local setup (Windows PowerShell)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
docker compose up -d db
alembic upgrade head
```

Start the local API in a separate PowerShell window after activating the environment:

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn trading.api:app --reload
```

Liveness is available at `http://localhost:8000/health/live`; readiness checks PostgreSQL at `http://localhost:8000/health/ready`.

## Tests and lint

With the virtual environment active:

```powershell
pytest
pytest tests\test_market_data.py
ruff check .
```

Health endpoint tests replace the database probe and do not require Docker or broker credentials.

## cTrader Open API (read-only, DEMO only)

The optional cTrader CLI uses the Open API JSON-over-WebSocket endpoint for demo accounts. It requests only the OAuth `accounts` scope, rejects live accounts, and does not write to PostgreSQL or Parquet. The official Twisted SDK currently requires a Windows native build dependency in this environment, so this integration uses the documented JSON protocol with the bounded `websockets` dependency instead.

The cTrader WebSocket receive limit defaults to 8 MiB for symbol-list responses and is bounded to 16 MiB. Library callers may configure `max_message_size_bytes` when constructing `CTraderReadOnlyClient`; the limit cannot be disabled.

Install the optional dependencies and store the application credentials in Windows Credential Manager through the OS keyring:

```powershell
python -m pip install -e ".[ctrader]"
ctrader configure
```

The Client ID is entered visibly at the prompt; the Client Secret is entered with hidden input. Neither is written to `.env` or printed. Run OAuth only when ready to authorize the application; it opens the cTrader authorization page, requests `accounts` access, validates a random OAuth state at `http://localhost:8080/callback`, exchanges the short-lived code, and stores tokens in the OS credential vault. If the callback listener/browser flow is unavailable, use the displayed authorization URL and paste the full callback URL at the hidden terminal prompt. Never use cTrader Playground Sandbox tokens as application OAuth credentials.

After explicit operator approval for a real read-only connection, these commands perform demo-only API calls:

```powershell
ctrader auth
ctrader accounts --environment demo
ctrader symbols --instrument EURUSD --environment demo
```

If more than one demo account is accessible, `ctrader symbols` requires an explicit `--account-id`; it will not fall back to a live account. Symbol discovery reports the broker's observed EURUSD name, ID, account association and precision without persisting a mapping. No OAuth login or live API request is run by automated tests.

## cTrader Copy History exporter

The read-only one-week, 20-trade-capped dry run and interactive DOM diagnostic
are documented in
[tools/ctrader_copy_exporter/README.md](./tools/ctrader_copy_exporter/README.md).
Real-browser selectors and History widget behavior still need validation in an
authorized signed-in cTrader session. The CLI has no full-export mode.

## Phase 2B historical pipeline

The migration adds source-aware OHLCV uniqueness so broker-provided and derived 5-minute candles at the same timestamp can both be retained. Existing migrations/data are preserved; no PostgreSQL volume is recreated. All data timestamps are UTC candle-open times. CLI ranges are half-open: `--start` is included and `--end` excluded.

### Backfill

Use a CSV provider for licensed/history files, or a seeded simulator for deterministic pipeline tests. The simulator is synthetic, emits no weekend candles by default, and does not model a broker's actual hours.

```powershell
python -m trading.data.cli backfill `
  --provider csv `
  --csv .\data\wti-1m.csv `
  --instrument WTI `
  --timeframe 1m `
  --start 2025-01-01T00:00:00Z `
  --end 2025-01-02T00:00:00Z `
  --batch-size 500 `
  --retries 3

python -m trading.data.cli backfill `
  --provider simulated `
  --seed 42 `
  --instrument EURUSD `
  --timeframe 5m `
  --start 2025-01-06T00:00:00Z `
  --end 2025-01-07T00:00:00Z
```

Each candle batch and its contiguous checkpoint commit together. Retries resume from the persisted checkpoint; candles are uniquely keyed by instrument, timeframe, open timestamp, and source provider. Identical reruns do not insert duplicates. Conflicting values from another provider are retained as a separately attributed observation instead of overwriting the first source.

### Session and quality settings

`MARKET_SESSIONS` accepts JSON keyed by canonical instrument. The built-in fallback calendars are instrument-specific provisional baselines: EURUSD uses a Sunday-to-Friday New York session, XAUUSD and WTI use Sunday-to-Friday sessions with a daily break, and SPX500 uses the New York weekday cash session. These are not broker-hour or holiday guarantees. Override each instrument with verified provider/broker sessions before operational quality decisions. The quality report includes an aggregate `missing_volume_count`; unavailable volume does not generate per-candle warnings.

```dotenv
MARKET_SESSIONS={"EURUSD":{"timezone":"America/New_York","weekdays":[0,1,2,3,6],"sessions":[{"open":"17:00","close":"17:00"}]},"XAUUSD":{"timezone":"America/New_York","weekdays":[0,1,2,3,6],"sessions":[{"open":"18:00","close":"17:00"}]},"WTI":{"timezone":"America/New_York","weekdays":[0,1,2,3,6],"sessions":[{"open":"18:00","close":"17:00"}]},"SPX500":{"timezone":"America/New_York","weekdays":[0,1,2,3,4],"sessions":[{"open":"09:30","close":"16:00"}]}}
PRICE_JUMP_THRESHOLDS={"WTI":0.1,"XAUUSD":0.1,"EURUSD":0.03,"SPX500":0.1}
```

Sessions support overnight windows; `weekdays` names the days on which an overnight session opens (so Sunday evening through Friday afternoon uses `[0,1,2,3,6]`; equal open/close times denote a 24-hour overnight session). Quality reports check requested range edges as well as observed candles for missing/duplicate/out-of-order rows, malformed OHLC, negative values, misaligned/non-UTC observations, price jumps, zero-volume bars/runs and expected closures. Price-jump defaults shown are example alert thresholds, not market limits. No public holiday calendar is assumed; configure verified holidays or closures before using quality classifications operationally.

```powershell
python -m trading.data.cli quality `
  --instrument WTI --timeframe 1m `
  --start 2025-01-01T00:00:00Z --end 2025-01-02T00:00:00Z `
  --source-provider csv
```

### 1m to 5m bars and source reconciliation

Aggregation requires five consecutive, aligned, finalized 1-minute candles and excludes buckets that are incomplete, span a configured closure, or extend past the caller's `--finalized-through` watermark. OHLC uses first open, maximum high, minimum low, last close; volume is summed.

```powershell
python -m trading.data.cli aggregate `
  --instrument WTI --timeframe 5m `
  --start 2025-01-01T00:00:00Z --end 2025-01-02T00:00:00Z `
  --finalized-through 2025-01-02T00:00:00Z `
  --source-provider csv
```

Provider 5-minute observations and `aggregate_1m` output are stored under separate source-provider keys. Aggregation requires an explicit `--source-provider`; that lineage is recorded in the derived candle metadata and stable source reference. Replaying identical bars is idempotent; Parquet rejects a conflicting derived candle rather than overwriting it. `compare_aggregated_source` reports field-level OHLCV discrepancies without silently choosing a winner. No missing M1 bars are synthesized. Keep the broker/provider source authoritative only after verifying its candle boundary, timezone, session rules and volume semantics.

### Parquet archives

Historical candles can be backfilled directly into versioned Parquet files with ZSTD compression. PostgreSQL remains the default for backward compatibility; existing PostgreSQL data is not migrated or deleted. Use `--storage parquet` explicitly for a new Parquet-backed backfill. PostgreSQL stores its instrument metadata and a backend-namespaced checkpoint, and only advances the checkpoint after the Parquet file is replaced.

Files use UTC timestamps and are partitioned as `instrument=.../timeframe=.../year=.../month=.../part-000.parquet`. Monthly partitions are merged, deduplicated by timestamp/source and replaced atomically. Use a larger backfill batch (the Parquet CLI default is 50,000 candles to avoid repeatedly rewriting a monthly partition):

```powershell
python -m trading.data.cli backfill `
  --storage parquet --data-root .\data\market\ohlcv `
  --provider simulated --instrument WTI --timeframe 1m `
  --start 2025-01-01T00:00:00Z --end 2025-02-01T00:00:00Z
```

Existing PostgreSQL candles can still be exported and imported non-destructively:

```powershell
python -m trading.data.cli export-parquet `
  --instrument WTI --timeframe 1m `
  --start 2025-01-01T00:00:00Z --end 2025-02-01T00:00:00Z `
  --source-provider csv --output .\data\parquet

python -m trading.data.cli import-parquet `
  --input .\data\parquet --batch-size 1000
```

Parquet import is idempotent against the same database source keys. Exported provenance travels with every row. Feature/target Parquet outputs are separate from the OHLCV archive.
For optional predicate-pushed analytics over these files, install `python -m pip install -e ".[analytics]"` and use `DuckDBParquetAnalytics` from `trading.storage`; DuckDB creates no persistent copy. Feature datasets retain separate targets and include feature schema v2 metadata while remaining readable alongside existing Phase 2B schema v1 datasets. See [the storage architecture](./ai_trading_project_spec/ARCHITECTURE.md) for the typed access facade, SQLite trade schema and backup guidance.

### HistData Generic ASCII M1 imports

Phase 3A.1 adds a local-file-only HistData provider. It does not download data or
contact a broker. Candle timestamps are interpreted using fixed UTC-05:00
(HistData's fixed EST convention); daylight-saving adjustments are never
applied. Source ID, file path, SHA-256, bid price type, original timestamp and
quality metadata are retained. Source/import-job metadata is tracked in
PostgreSQL. Parquet is the default candle destination; use `--storage postgres`
to store candles in PostgreSQL instead.

The instrument mapping is in
[`config/historical_data_sources.json`](./config/historical_data_sources.json):
EURUSD -> EUR/USD, XAUUSD -> XAU/USD, WTI -> WTI/USD and SPX500 -> SPX/USD.
The `file_code` values are configurable hints and must be confirmed against
the actual HistData archive naming before bulk imports. HistData input is M1;
derive finalized M5 bars afterward using the existing `aggregate` command.

```powershell
python -m trading.data.cli import-histdata `
  --source-file .\data\histdata\EURUSD.csv `
  --instrument EURUSD `
  --start "2026-01-01T00:00:00+00:00" `
  --end "2026-02-01T00:00:00+00:00" `
  --storage parquet `
  --data-root .\data\market\ohlcv
```

Use `--storage postgres` for the PostgreSQL destination, or `--help` for
available options. Import ranges are half-open. Duplicate or out-of-order
timestamps in a source file fail validation rather than being silently
discarded. The January 2026 EURUSD M1 file has been imported; validate each
additional source/archive convention with a dry run before importing it.

### Resumable monthly HistData batches

`import-histdata-batch` discovers `DAT_ASCII_<file-code>_M1_YYYYMM.csv` and
matching `.zip` archives recursively. ZIP CSV members are streamed directly
without extracting archives. A dry run parses and validates files but does not
connect to PostgreSQL or write Parquet/checkpoints:

```powershell
python -m trading.data.cli import-histdata-batch `
  --input-dir .\data\histdata `
  --instrument EURUSD `
  --start "2026-01-01T05:00:00+00:00" `
  --end "2026-03-01T05:00:00+00:00" `
  --storage parquet `
  --batch-size 50000 `
  --dry-run
```

For a real two-month pilot, first inspect the dry-run output, then rerun without
`--dry-run` using the same range and input directory:

```powershell
python -m trading.data.cli import-histdata-batch `
  --input-dir .\data\histdata `
  --instrument EURUSD `
  --start "2026-01-01T05:00:00+00:00" `
  --end "2026-03-01T05:00:00+00:00" `
  --storage parquet `
  --batch-size 50000
```

The importer processes months chronologically, clips each file to the
requested half-open range, records a separate import job per file, and skips a
file only when its checksum and exact import parameters already have a
completed job. Failed files are reported while later months continue. Retrying
uses the existing per-file checkpoint and Parquet conflict detection. Duplicate
files for one instrument/month and row dates inconsistent with the filename are
reported as failures for operator review.

Benchmark representative local storage workloads (the default writes one million deterministic candles):

```powershell
python -m tools.storage_benchmark
```

### ML dataset preparation (no training)

```powershell
python -m trading.data.cli dataset `
  --instrument WTI --timeframe 1m `
  --start 2025-01-01T00:00:00Z --end 2025-02-01T00:00:00Z `
  --source-provider csv --target-horizon 5 `
  --purge-gap 5 --embargo 2 --output .\data\datasets\wti-1m
```

The builder produces lagged returns, causal rolling volatility, EMA distances, Wilder-style RSI/ATR, rolling volume statistics and UTC clock/calendar encodings. Feature values at each prediction timestamp use only candles available at that candle's close. Forward-return and direction targets are written to a separate table/file. Rows with incomplete indicator windows or target horizons are excluded. Splits are strictly chronological, with target-horizon purging on both sides of boundaries and optional additional embargo rows; no random splits, SMOTE, fitting, or model training occurs. No scaler/imputer is applied: future preprocessing must be fit on the train slice only. The manifest records source/configuration, feature definitions, horizon, date/split boundaries, schema and source/dataset SHA-256 fingerprints. The CLI caps input at 500,000 bars by default (`--max-bars`); choose narrower date ranges or deliberately adjust the cap on a memory-sized host.

### Docker usage and end-to-end smoke test

Compose mounts the host `.\data` directory at `/app/data` for CSV and Parquet access. The following PowerShell smoke test adds 1,000 deterministic WTI 1-minute rows to the running database, reruns ingestion, aggregates 5-minute bars, checks quality, exports and reimports Parquet, and builds a dataset. It does not stop the API/database or delete volumes:

```powershell
$start = "2026-12-01T00:00:00Z"
$end = "2026-12-01T16:40:00Z"
docker compose build trading-engine
docker compose run --rm --no-deps trading-engine alembic upgrade head
docker compose run --rm --no-deps trading-engine python -m trading.data.cli backfill --provider simulated --seed 4242 --instrument WTI --timeframe 1m --start $start --end $end
docker compose run --rm --no-deps trading-engine python -m trading.data.cli backfill --provider simulated --seed 4242 --instrument WTI --timeframe 1m --start $start --end $end
docker compose exec db psql -U trading -d trading -c "SELECT source_provider, timeframe_minutes, COUNT(*) FROM ohlcv_candles WHERE instrument_symbol='WTI' AND opened_at >= '$start' AND opened_at < '$end' GROUP BY source_provider,timeframe_minutes ORDER BY source_provider,timeframe_minutes"
docker compose run --rm --no-deps trading-engine python -m trading.data.cli aggregate --instrument WTI --timeframe 5m --start $start --end $end --finalized-through $end --source-provider simulated
docker compose run --rm --no-deps trading-engine python -m trading.data.cli quality --instrument WTI --timeframe 1m --start $start --end $end --source-provider simulated
docker compose run --rm --no-deps trading-engine python -m trading.data.cli export-parquet --instrument WTI --timeframe 1m --start $start --end $end --source-provider simulated --output /app/data/phase2b-parquet
docker compose run --rm --no-deps trading-engine python -m trading.data.cli import-parquet --input /app/data/phase2b-parquet
docker compose run --rm --no-deps trading-engine python -m trading.data.cli dataset --instrument WTI --timeframe 1m --start $start --end $end --source-provider simulated --target-horizon 5 --purge-gap 5 --output /app/data/phase2b-dataset
```

The SQL count should show exactly 1,000 simulated 1-minute candles before and after the second ingestion; aggregation should report 200 derived candles (and SQL should show them under `aggregate_1m`). Dataset preparation produces separate features/targets Parquet files and `manifest.json` in the mounted host data folder.

Run automated tests and lint locally with `pytest` and `ruff check .`. Tests use deterministic synthetic fixtures and temporary Parquet directories and do not require broker credentials.

## Historical CSV import

Apply migrations and start PostgreSQL first (`docker compose up -d db`, then `alembic upgrade head`). CSV files must contain these columns, timestamps must include a timezone (`Z` is UTC), and rows must be chronological; identical adjacent duplicates are ignored while conflicting duplicates and out-of-order rows are rejected:

```csv
timestamp,open,high,low,close,volume
2025-01-01T00:00:00Z,75.10,75.30,75.00,75.20,120
2025-01-01T00:01:00Z,75.20,75.35,75.10,75.25,98
```

Import the half-open UTC interval (start included, end excluded) with the requested canonical symbol and timeframe:

```powershell
python -m trading.data.cli `
  --csv .\data\wti-1m.csv `
  --instrument WTI `
  --timeframe 1m `
  --start 2025-01-01T00:00:00Z `
  --end 2025-01-02T00:00:00Z
```

Supported symbols are `WTI`, `XAUUSD`, `EURUSD`, and `SPX500`; the migration seeds their canonical catalog records. Supported timeframes are `1m` and `5m`. The CSV is checked for required fields, timezone-aware and aligned timestamps, valid OHLC bounds, non-negative volume, and conflicting duplicate timestamps. Identical repeated CSV rows are deduplicated. Missing intervals between observed candles are reported, never filled.

Ingestion stores each batch of candles and its contiguous checkpoint in one database transaction. The checkpoint is keyed by provider, instrument, timeframe, and CSV file identity/content hash; re-running a failed import resumes from the last gap-free candle. Database uniqueness constraints prevent duplicate candles if a batch is retried. Reimported candles do not overwrite previously stored values.

`BROKER_SYMBOL_MAPPINGS` in `.env` accepts a JSON object keyed by provider and canonical symbol, for example `{"ctrader":{"WTI":"<broker symbol from approved account metadata>"}}`. Replace the placeholder only with a symbol discovered from an approved broker account; no broker symbols are assumed by this project.

To exercise the async pipeline without a database or credentials, use `SimulatedMarketDataProvider` from tests or a Python script. It produces deterministic synthetic candles and is not real market data. The provider protocol also defines an asynchronous candle stream; CSV explicitly supports history only.

Run `pytest tests\test_market_data.py` to test CSV validation, deterministic simulation, streaming, gap detection, duplicate-safe ingestion, and restart recovery without PostgreSQL or broker credentials.

## Docker Compose

Create `.env` from `.env.example`, set a strong URL-safe database password consistently in `DATABASE_URL` and `POSTGRES_PASSWORD`, then run:

```powershell
docker compose up --build -d
docker compose ps
Invoke-RestMethod http://localhost:8000/health/ready
docker compose down
```

PostgreSQL is bound to loopback only, not exposed on a public interface. The engine waits for the database health check, applies migrations, and then starts the API. Persisted database files live in the `postgres_data` named volume. On a Hetzner VPS, keep firewall access to PostgreSQL closed and terminate public HTTPS traffic through a separately configured reverse proxy/TLS layer.

## Configuration

See `.env.example` for the available variables. Replace placeholder passwords before deployment and keep `.env` out of version control. This foundation deliberately contains no broker credentials or live-order implementation.
