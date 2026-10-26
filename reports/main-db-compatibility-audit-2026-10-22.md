# Main database compatibility audit — 2026-10-22

## Decision

**Migration 0005 is already applied to the main database.** Its single Alembic marker is `0005_historical_source_jobs`, and the inspected schema matches the migration's completed state. No relevant partial application, incompatible object, or schema drift was found. Do not directly rerun migration 0005, stamp the database, or downgrade it.

The image has been rebuilt, but the trading-engine container still uses the old image. Recovery requires replacing that container with the verified image; no database migration is needed for this observed schema. No recovery action was executed during this audit.

## Image and configuration evidence

- Current image tag `ai-trading-system-trading-engine` resolves to `sha256:551957a8f8a949b51d1a4dc1fb251c784b5e8a7fd2372782306a29fcd897fdaa`, created 2026-10-22 14:52:01 UTC.
- An ephemeral Python inspection container used this exact image with no network, a read-only root filesystem, no healthcheck, and a Python entrypoint. It did not execute application startup or Alembic's migration environment.
- All five image migration files matched the local files byte for byte. Alembic script metadata resolved the single head `0005_historical_source_jobs` and chain 0001 → 0002 → 0003 → 0004 → 0005.
- Existing container `ai-trading-system-trading-engine-1` still references old image `sha256:e3e83942f2284115f332c759bbd798cd9bf89277ebf12f307610a08470d2d011`. It was already exited, with 1,908 restarts. No container was stopped or recreated by this audit.
- Dockerfile already copies the entire `alembic` directory; `.dockerignore` does not exclude it. The original missing migration was caused by the stale image, not a missing COPY instruction. No Dockerfile fix is necessary.
- Compose directs the engine to the MAIN database: service `db`, port 5432, database `trading`. The image's default command automatically runs Alembic before Uvicorn. The only application bind mount is `/app/data`; local migrations do not overlay image migrations.

## Read-only enforcement and scope

The main database was reached through its verified localhost port 5432 mapping. Credentials were handled privately and not logged. The isolated port-15432 database was not queried.

The connection set `default_transaction_read_only=on` at establishment and opened an explicit `REPEATABLE READ READ ONLY` transaction. Both read-only settings were verified. `SET LOCAL ROLE pg_read_all_data` reduced the effective role; its superuser/create-role/create-database flags were false. On every inspected table, SELECT was available and INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES and TRIGGER privileges were absent. Statement and lock timeouts bounded the queries. The transaction ended with explicit ROLLBACK and connection closure.

Queries inspected PostgreSQL catalog metadata and the Alembic revision row only. Relevant objects were `public.alembic_version`, `instruments`, `ohlcv_candles`, `market_data_sources`, `market_data_import_jobs`, and associated constraint/index names. No candle/source/job records were read, and no schema or data changes were issued. This is a focused migration-0004/0005 compatibility audit, not a full database or data-quality audit.

## Exact migration requirements and observed schema

For a database genuinely at 0004, migration 0005 requires the existing candle/instrument tables, the named volume check and the 0004 unique constraint. Its new columns, tables and index names must be absent. Its operations are not idempotent.

Migration 0004 replaces `uq_ohlcv_instrument_tf_opened_at` with `uq_ohlcv_instrument_tf_time_source`, adding `source_provider` to candle uniqueness. Migration 0005 then makes these changes:

| Object | Required final state | Audit result |
| --- | --- | --- |
| `ohlcv_candles.price_type` | varchar(16), NOT NULL, default `'unknown'` | Matches |
| `ohlcv_candles.quality_metadata` | jsonb, NOT NULL, default `'{}'::jsonb` | Matches |
| `ohlcv_candles.volume` | numeric(24,8), nullable | Matches |
| `ck_ohlcv_nonnegative_volume` | `volume IS NULL OR volume >= 0` | Matches; validated |
| Candle uniqueness | `uq_ohlcv_instrument_tf_time_source_price` on `(instrument_symbol, timeframe_minutes, opened_at, source_provider, price_type)` | Matches; valid index and validated constraint |
| Superseded unique constraints | Both 0003 and 0004 names absent after replacement | Matches |
| `market_data_sources` | Six columns and primary key described below | Matches |
| `market_data_import_jobs` | Nineteen columns, primary key, checks and foreign keys described below | Matches |
| `ix_market_data_import_jobs_source_id` | Nonunique btree index on `source_id` | Matches; valid and ready |

`market_data_sources` columns, all NOT NULL:

- `source_id varchar(100)` primary key; `provider varchar(100)`; `format varchar(100)`; `timestamp_timezone varchar(64)`.
- `fixed_offset boolean`, with **no database default**.
- `metadata jsonb`, default `'{}'::jsonb`.

`market_data_import_jobs` columns, NOT NULL unless explicitly marked nullable:

- `job_id varchar(36)` primary key; `source_id varchar(100)`; `instrument_symbol varchar(16)`; `timeframe_minutes smallint`; `price_type varchar(16)`.
- `source_reference varchar(1024)`; `source_checksum varchar(64)`; `requested_start timestamptz`; `requested_end timestamptz`; `status varchar(16)`.
- `rows_seen bigint`, `rows_stored bigint`, `gap_count bigint`, each default 0.
- `coverage_start timestamptz` and `coverage_end timestamptz`, both nullable.
- `quality_summary jsonb`, default `'{}'::jsonb`; `error text`, nullable.
- `started_at timestamptz`, default `now()`; `finished_at timestamptz`, nullable.

Its validated checks require timeframe IN (1,5), status IN ('pending','running','completed','failed'), and nonnegative `rows_seen`/`rows_stored`. The migration does not define a nonnegative `gap_count` check. Foreign keys reference `market_data_sources.source_id` and `instruments.symbol`, both ON DELETE RESTRICT. Existing candle primary key, instrument foreign key, OHLC bounds check and timeframe check were also consistent. All seven inspected indexes were valid and ready; all inspected constraints were validated. No conflicting named relation was found.

**Safety assessment:** executing 0005's upgrade function again would fail on existing objects. With the verified image and observed marker, normal Alembic version resolution should find no pending migration; that command was not executed. Prefer bypassing migration startup for this recovery, because no schema change is required. Recheck image identity and schema marker before a later approved deployment if other actors may have changed them.

## Proposed recovery — approval required, not executed

1. Preserve the verified image identity and existing configuration. No rebuild is required. After approval, tag the verified image with a dedicated recovery tag:

   ```powershell
   docker image tag sha256:551957a8f8a949b51d1a4dc1fb251c784b5e8a7fd2372782306a29fcd897fdaa ai-trading-system-trading-engine:recovery-0005-20261022
   ```

2. Prepare an explicitly selected Compose override, `reports/trading-engine-readonly-recovery.compose.yaml`, with this content. It has not been created or activated by this audit:

   ```yaml
   services:
     trading-engine:
       image: ai-trading-system-trading-engine:recovery-0005-20261022
       pull_policy: never
       entrypoint: []
       command: ["uvicorn", "trading.api:app", "--host", "0.0.0.0", "--port", "8000"]
       environment:
         PGOPTIONS: "-c default_transaction_read_only=on"
   ```

   This bypasses Alembic and defaults psycopg database sessions to read-only. It is a recovery safeguard, not a substitute for a dedicated least-privilege application login. The current API exposes health endpoints; its readiness check uses SELECT 1.

3. Validate the merged configuration privately, without printing environment values. Verify the pinned image, Uvicorn-only command, read-only option, unchanged database destination and unchanged mounts. Recreate **only the engine**, leaving it stopped:

   ```powershell
   docker compose -p ai-trading-system -f compose.yaml -f reports/trading-engine-readonly-recovery.compose.yaml up --no-deps --no-start --no-build --pull never --force-recreate trading-engine
   ```

4. Inspect the stopped container's image ID and command before starting it. Abort if they differ from the approved configuration. Then start only that container:

   ```powershell
   docker compose -p ai-trading-system -f compose.yaml -f reports/trading-engine-readonly-recovery.compose.yaml start trading-engine
   ```

5. Check `/health/live`, `/health/ready`, restart count and sanitized logs; confirm that no Alembic startup ran. Health endpoints establish liveness/connectivity, not complete schema compatibility. Reconfirm collectors independently without changing them.

### Rollback considerations

If recovery fails, stop only the trading-engine container under separate recovery authorization. Preserve the database and volumes. Because this plan bypasses migrations, it requires no schema rollback. Retain the verified compatible image and override for another attempt; reverting to the old four-migration image reproduces the unknown-revision failure. Future default Compose recreation would restore the automatic migration command unless the override is explicitly included.

Do not attempt an automatic downgrade: migration 0005 explicitly refuses it when source/job records or candles with NULL volume/nondefault metadata exist. Those data conditions were not inspected in this schema-only audit. Any future database migration needs separate approval, an appropriate backup/recovery plan, and lock/availability assessment.

## Collector status and limitations

- The original EURUSD Docker worker remained healthy in the final container-status check. Main PostgreSQL also remained healthy. No service lifecycle commands were executed.
- Local XAUUSD and WTI historical progress files already reported `completed_with_gaps`, 261 completed batches each, finishing at 15:11:43 UTC and 15:22:06 UTC respectively. Their history was not revalidated against PostgreSQL in this audit.
- The separate multi-asset collector progress file reported `safety_stopped` at 16:56:23 UTC, reason: `PostgreSQL storage free-space check unavailable`. This predated the audit; no restart/resume or storage changes were attempted. The reason indicates an unavailable check, not proof that free space is exhausted. Treat its investigation as a separate task.

No recovery plan, migrations, database modifications, collector changes, commits or pushes were performed. The only repository addition from this audit is this report.
