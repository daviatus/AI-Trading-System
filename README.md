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

The optional cTrader CLI uses the Open API JSON-over-WebSocket endpoint for demo accounts. It requests only the OAuth `accounts` scope, rejects live accounts, and writes to PostgreSQL or Parquet only when the corresponding explicit option is supplied. The official Twisted SDK currently requires a Windows native build dependency in this environment, so this integration uses the documented JSON protocol with the bounded `websockets` dependency instead.

The cTrader WebSocket receive limit defaults to 8 MiB for symbol-list responses and is bounded to 16 MiB. Library callers may configure `max_message_size_bytes` when constructing `CTraderReadOnlyClient`; the limit cannot be disabled.

Install the optional dependencies and store the application credentials in Windows Credential Manager through the OS keyring:

```powershell
python -m pip install -e ".[ctrader]"
ctrader configure
```

The Client ID is entered visibly at the prompt; the Client Secret is entered with hidden input. Neither is written to `.env` or printed. Run OAuth only when ready to authorize the application; it opens the cTrader authorization page, requests `accounts` access, validates a random OAuth state at `http://localhost:8080/callback`, exchanges the short-lived code, and stores tokens in the OS credential vault. If the callback listener/browser flow is unavailable, use the displayed authorization URL and paste the full callback URL at the hidden terminal prompt. Never use cTrader Playground Sandbox tokens as application OAuth credentials.

### Encrypted file credential store (Linux Docker, opt-in)

Windows Credential Manager remains the default. For Linux/Docker set `CTRADER_CREDENTIAL_BACKEND=encrypted-file`, `CTRADER_CREDENTIAL_FILE` (data path) and `CTRADER_CREDENTIAL_KEY_FILE` (key path). Credentials and refreshed OAuth tokens are stored as one Fernet (authenticated-encryption) file, written atomically with mode `0600`. The key must live in a separate file, never in an environment variable, Compose file, argument or the data volume.

Initialise the key once on the host (nothing is printed; refuses to overwrite):

```bash
install -d -m 700 ./secrets
python -c "from trading.ctrader.encrypted_store import init_key_file; init_key_file('./secrets/ctrader_credential_key')"
```

Mount the key as a Docker secret or read-only bind mount (for example at `/run/secrets/ctrader_credential_key`) and the data directory as a dedicated named volume (for example `/var/lib/ctrader`, not group/other writable). Run `ctrader configure` and `ctrader auth` inside the container (or any environment with the same variables) once to populate it. Keep `secrets/` out of git and back the key up separately from the data volume.

The store fails closed, with no plaintext fallback, on a missing/invalid key, wrong key, corrupted or tampered data, a data file readable by group/other, a symlinked or non-regular file, or any I/O error. The key file may be read-only/world-readable (Docker secrets default to `0444`) but not group/other writable.

Recovery: if the key is lost or the data is corrupted, the stored credentials cannot be recovered. Delete the data file (and `.lock`) deliberately, create a new key with `init_key_file`, then re-run `ctrader configure` and `ctrader auth`. Rotate the cTrader client secret if the key or data may have been exposed. Never delete the key while the data file is still needed.

After explicit operator approval for a real read-only connection, these commands perform demo-only API calls:

```powershell
ctrader auth
ctrader accounts --environment demo
ctrader symbols --instrument EURUSD --environment demo
```

If more than one demo account is accessible, `ctrader symbols` requires an explicit `--account-id`; it will not fall back to a live account. Symbol discovery reports the broker's observed EURUSD name, ID, account association and precision without persisting a mapping. No OAuth login or live API request is run by automated tests.

Retrieve a short, read-only EURUSD M1 interval by supplying explicit UTC timestamps:

```powershell
ctrader candles --instrument EURUSD --environment demo --start 2026-06-01T00:00:00Z --end 2026-06-01T00:05:00Z
```

The command treats the requested interval as half-open (`--start` included, `--end` excluded), requires an interval no longer than 24 hours, uses the selected demo account's discovered EURUSD symbol ID, and returns UTC JSON candles. Prices are decoded from cTrader's relative trendbar fields using `low + delta` and the protocol's 100000 price scale. `volume` is retained as cTrader's raw protocol integer because the trendbar schema does not define a conversion unit. This command makes a real demo API request; automated tests use mocks only.

For multi-day ranges, opt in with `--multi-day`. Requests are split into consecutive, non-overlapping 24-hour UTC chunks, and the total range is capped at 31 days to bound API usage and memory. Existing calls without this flag retain the single-interval limit.

```powershell
ctrader candles --instrument EURUSD --environment demo --start 2026-06-01T00:00:00Z --end 2026-06-04T00:00:00Z --multi-day
```

The multi-day mode retries only transient transport failures and cTrader `BLOCKED_PAYLOAD_TYPE` rate limits, for up to three chunk attempts with exponential backoff. Existing connection setup also retains its bounded socket retries. A documented `retryAfter` delay is honored up to 60 seconds; longer delays and non-retryable errors are returned to the caller. Missing candles during market closures are valid. Duplicate timestamps or out-of-order results fail the request. Output remains JSON on stdout; retry and error messages go to stderr. Candle data is returned in memory only and is not persisted by default.

Add `--quality-report` to return the candles together with a structured validation and gap report. Gaps are reported, not filled; missing market minutes do not by themselves make validation invalid. Invalid timestamps, duplicate or unsorted candles, non-positive prices, malformed OHLC, and out-of-range candles are errors. `volume` retains the raw cTrader protocol integer semantics (and remains nullable in the shared Parquet schema).

Parquet persistence is explicit and publishes a new dataset atomically. The output root must not already exist, preventing accidental overwrites; use a new destination for each export. The default root is `data/ctrader-parquet`, and `--parquet-root` can select another location:

```powershell
ctrader candles --instrument EURUSD --environment demo --start 2026-06-01T00:00:00Z --end 2026-06-01T00:05:00Z --quality-report --save-parquet
ctrader candles --instrument EURUSD --environment demo --start 2026-06-01T00:00:00Z --end 2026-06-04T00:00:00Z --multi-day --quality-report --save-parquet --parquet-root .\data\ctrader-june
```

Parquet output uses the repository's versioned OHLCV schema and Hive-style `instrument=EURUSD/timeframe=1m/year=YYYY/month=MM/part-000.parquet` partitions, with UTC timestamps, Decimal OHLCV, and cTrader demo lineage metadata. Only candles that pass validation are saved. Empty results create no files. An existing output root is never merged or replaced, and a failed write is staged outside the published root and cleaned up.

PostgreSQL writes are opt-in. Add `--import-postgres` to a retrieval command to validate the complete response and then import it into the existing `ohlcv_candles` table. Without that flag, the `candles` retrieval command never opens a database connection. The source is recorded as `ctrader_open_api_demo`, separate from `histdata_generic_ascii`; repeated identical imports are unchanged, and conflicting existing OHLCV values are reported without being overwritten:

```powershell
ctrader candles --instrument EURUSD --environment demo --start 2026-06-01T00:00:00Z --end 2026-06-01T00:05:00Z --import-postgres
```

An already saved cTrader Parquet dataset can be imported without another cTrader request. Parquet schema, source, instrument, timeframe, timestamps, OHLCV and ordering are revalidated first; imports beyond 31 days or datasets containing a different source are refused. Original Parquet files are read only:

```powershell
ctrader import-parquet --input .\data\ctrader-june
```

Both explicit import workflows return `postgres_import` counts for `seen`, `inserted`, `unchanged`, `conflicting` and `rejected`. Conflicts and rejected records produce a nonzero exit status; records with conflicting values are never updated. PostgreSQL uniqueness remains scoped to instrument, timeframe, UTC open time, source provider and price type, so cTrader records do not replace HistData records. No migration is required: those source-aware columns and constraints already exist.

### Incremental historical synchronization

`ctrader sync` compares the requested interval with existing `ctrader_open_api_demo` EURUSD M1 candles, excluding other providers and price types. Only expected market-open gaps are requested; only the known weekend closure (Friday 17:00 to Sunday 17:00 New York time, DST-aware) is skipped; there is no daily rollover closure, so weekday minutes without a candle (including around 22:00 UTC) are reported as unresolved gaps, and candles at or after the current minute are never fetched. An explicit EURUSD `MARKET_SESSIONS` setting replaces this default calendar. Requests use the existing 24-hour API chunks and bounded retry policy. Returned data is validated with the shared cTrader validator; missing API candles are reported as unresolved rather than interpolated. The repository's source-aware unique constraint and transactional import make interrupted or concurrent runs safe; conflicting prices are not overwritten.

Always begin with a database-only plan. Dry-run never reads cTrader credentials, calls the API, or writes to PostgreSQL:

```powershell
ctrader sync --instrument EURUSD --environment demo --timeframe M1 --start 2026-06-01T00:00:00Z --end 2026-06-02T00:00:00Z --dry-run --json
```

Without `--dry-run`, the command retrieves and validates missing history but does not write it. Use `--save-postgres` to explicitly allow validated inserts. Add `--max-days` to lower the maximum requested window (the API-supported upper bound is 31 days), and `--json` for a machine-readable summary. The summary includes the requested and effective UTC intervals, gaps, API attempts, validation, persistence counts, and status; a range with unresolved gaps is never reported complete.

For manual PostgreSQL integration testing, use a disposable container on a separate loopback-only port. Apply migrations only to this disposable database. Begin with dry-run; a write test additionally needs configured cTrader demo credentials and retrieves history from the demo API:

```powershell
docker run -d --rm --name ctrader-sync-test `
  -e POSTGRES_USER=trading -e POSTGRES_PASSWORD=temporary-test-only `
  -e POSTGRES_DB=trading -p 127.0.0.1:55432:5432 postgres:16
$env:DATABASE_URL = "postgresql+psycopg://trading:temporary-test-only@127.0.0.1:55432/trading"
.\.venv\Scripts\alembic.exe upgrade head
ctrader sync --instrument EURUSD --environment demo --timeframe M1 `
  --start 2026-06-01T00:00:00Z --end 2026-06-01T00:05:00Z --dry-run --json
# Only after reviewing the plan and verifying DATABASE_URL points to the disposable container:
ctrader sync --instrument EURUSD --environment demo --timeframe M1 `
  --start 2026-06-01T00:00:00Z --end 2026-06-01T00:05:00Z --save-postgres --json
docker stop ctrader-sync-test
Remove-Item Env:DATABASE_URL
```

Never use a personal, shared, or production database for write testing. Automated tests use in-memory fakes and do not connect to PostgreSQL or cTrader.

### Phase 3D: foreground DEMO candle worker

The worker runs in the foreground, performs a synchronization immediately, then uses monotonic deadlines for subsequent cycles. It is sequential and never overlaps runs. The default interval is 300 seconds and the rolling lookback is 24 hours; interval, lookback, retry delays, and quality thresholds are configurable. The reused Phase 3C synchronizer currently supports EURUSD M1 only, and the worker explicitly exposes those instrument/timeframe options. It handles Ctrl+C and SIGTERM where available. It does not install a service, schedule a Windows task, start automatically, or call a trading/order API.

The worker requires `DATABASE_URL` to be explicitly configured with a PostgreSQL host and database; it refuses the application's fallback database target. Set the variable only after verifying that it points to the intended disposable or approved database. Candle writes and persistent worker reports are disabled by default; `--save-postgres` is the explicit opt-in for both. `--dry-run` reads the selected database to report the plan, but does not call cTrader or write candles, retry state, or reports. It cannot be combined with `--save-postgres`. The worker never modifies `.env`.

With the development environment active, configure and migrate a disposable database first, then inspect a one-shot plan:

```powershell
$env:POSTGRES_PASSWORD = "temporary-test-only"
$env:DATABASE_URL = "postgresql+psycopg://trading" + ":" + $env:POSTGRES_PASSWORD + "@127.0.0.1:55432/trading"
alembic upgrade head
ctrader worker --instrument EURUSD --environment demo --timeframe M1 --once --dry-run --json
```

After reviewing the target and plan, a real one-shot DEMO synchronization requires configured cTrader DEMO credentials and explicit write opt-in:

```powershell
ctrader worker --instrument EURUSD --environment demo --timeframe M1 --once --save-postgres --json
ctrader status --json
```

To keep collecting in the current PowerShell window, omit `--once`; stop cleanly with Ctrl+C:

```powershell
ctrader worker --instrument EURUSD --environment demo --timeframe M1 --interval-seconds 300 --lookback-hours 24 --save-postgres
```

`ctrader status` (also available as `ctrader report`) reads the most recent persisted run, last successful synchronization, candle freshness, missing gaps, and API/database/rejection counts. “Last successful synchronization” means the latest run that completed without an API or database execution error; unresolved broker gaps may remain and are reported separately in the synchronization and quality status. Persistent reports and the retry snapshot use the existing append-only `system_events` table, so Phase 3D adds no database migration. A run without `--save-postgres` is intentionally not persisted and will not appear in status.

Retries for broker-empty minutes use persistent exponential backoff (default 15 minutes, capped at 6 hours); retry intervals can be changed with `--retry-base-seconds` and `--retry-max-seconds`. Deferred minutes remain visible as missing gaps in each report. The existing manual `ctrader sync` command ignores worker retry state, and `--manual-backfill` bypasses retry timers on a worker cycle. A weekday gap is never classified as a market closure just because cTrader did not return it. The existing DST-aware weekend calendar remains in force; daily rollover minutes are still requested. The six confirmed Jan 5–6, 2026 rollover candles are not excluded.

Quality status is `healthy`, `warning`, or `critical`. Any API/database error, rejected/conflicting record, at least `--critical-missing-minutes` missing candles (default 30), or market-open ingestion lag greater than `--max-lag-minutes` (default 10) is critical. Missing candles above `--warning-missing-minutes` (default 1) are warning-level, so the known six-candle rollover gap does not cause an endless critical alert. `--warning-missing-minutes`, `--critical-missing-minutes`, and `--max-lag-minutes` can be adjusted to the operator's tolerance; the configured weekend closure is excluded from market-open lag.

For manual PostgreSQL verification, start an isolated database on loopback and remove it when finished. The dry-run below needs no cTrader login; the write-enabled command does contact the DEMO history API:

```powershell
docker run -d --rm --name ctrader-worker-test `
  -e POSTGRES_USER=trading -e POSTGRES_PASSWORD=temporary-test-only `
  -e POSTGRES_DB=trading -p 127.0.0.1:55432:5432 postgres:16
$env:POSTGRES_PASSWORD = "temporary-test-only"
$env:DATABASE_URL = "postgresql+psycopg://trading" + ":" + $env:POSTGRES_PASSWORD + "@127.0.0.1:55432/trading"
alembic upgrade head
ctrader worker --instrument EURUSD --environment demo --timeframe M1 --once --dry-run --json
# Only after verifying the disposable target and approving a DEMO API read:
ctrader worker --instrument EURUSD --environment demo --timeframe M1 --once --save-postgres --json
ctrader status --json
docker stop ctrader-worker-test
Remove-Item Env:DATABASE_URL
Remove-Item Env:POSTGRES_PASSWORD
```

Do not point this verification at the project's normal PostgreSQL database. For a future Linux/Docker deployment, provide the approved `DATABASE_URL` and cTrader credentials through the deployment's secret manager, run the same worker command as the container's foreground process, and let the orchestrator send SIGTERM; no container or service deployment is part of this phase.

For a safe manual integration check, use an isolated disposable PostgreSQL container on a separate port, never the project's configured database:

```powershell
$container = docker run -d --rm --name ctrader-import-test `
  -e POSTGRES_USER=trading -e POSTGRES_PASSWORD=temporary-test-only `
  -e POSTGRES_DB=trading -p 127.0.0.1:55432:5432 postgres:16.8
$env:DATABASE_URL = "postgresql+psycopg://trading:temporary-test-only@localhost:55432/trading"
alembic upgrade head
ctrader import-parquet --input .\data\ctrader-june
ctrader import-parquet --input .\data\ctrader-june
docker exec ctrader-import-test psql -U trading -d trading -c "SELECT source_provider, COUNT(*) FROM ohlcv_candles GROUP BY source_provider"
docker stop $container
Remove-Item Env:DATABASE_URL
```

Use a Parquet dataset created by the explicit cTrader save option. The second import should report unchanged rows, and the query should show the cTrader provider independently of any other source. Do not point `DATABASE_URL` at a personal, shared, or production database for this test.

If a server response contains a candle outside the requested interval, validation remains strict and fails rather than discarding it. Add `--diagnostics` to print the requested UTC bounds, request timestamps/count, response count, and offending candle timestamp to stderr for troubleshooting. It does not print account identifiers, credentials, or payload contents.

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
