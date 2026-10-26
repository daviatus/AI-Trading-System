# Approved trading-engine recovery — 2026-10-22

## Result

Only `ai-trading-system-trading-engine-1` was recreated and started. It runs verified image `sha256:551957a8f8a949b51d1a4dc1fb251c784b5e8a7fd2372782306a29fcd897fdaa`, containing migrations 0001–0005. Final checks found it **running, healthy, restart count 0**.

The selected override starts Uvicorn directly with no shell or Alembic entrypoint. `TRADING_MODE=DEMO` and `LIVE_EXECUTION_ENABLED=false` are explicit. The image's API exposes health endpoints and does not start an order-submission service.

## Pre-start safety gate

Before any engine recreation or start, the check script validated the merged Compose configuration privately; verified the pinned image, migration files and application source hashes; checked that the database destination remained the same main database; and rejected connection-URL query options that could override PGOPTIONS. An offline Python inspection container had no network, read-only root filesystem, no healthcheck and no application startup.

Using the application's own connection factory, a SELECT-only preflight connection reported `default_transaction_read_only=on` and `transaction_read_only=on`. The Alembic marker remained `0005_historical_source_jobs`. Connections were rolled back and closed. The recreated container was then inspected while stopped, and the same safety checks passed before it was started.

After startup, the actual in-container application connection factory independently reported both read-only settings as `on`. This is the approved default-read-only configuration; no database roles or privileges were changed.

## Exact recovery and verification commands executed

Commands ran from `C:\Users\jager\ai_trader\AI-Trading-System` in this sequence:

```powershell
.\.venv\Scripts\python.exe reports/trading_engine_recovery_check.py preflight
docker image tag sha256:551957a8f8a949b51d1a4dc1fb251c784b5e8a7fd2372782306a29fcd897fdaa ai-trading-system-trading-engine:recovery-0005-20261022
docker compose -p ai-trading-system -f compose.yaml -f reports/trading-engine-readonly-recovery.compose.yaml up --no-deps --no-start --no-build --pull never --force-recreate trading-engine
.\.venv\Scripts\python.exe reports/trading_engine_recovery_check.py stopped
docker compose -p ai-trading-system -f compose.yaml -f reports/trading-engine-readonly-recovery.compose.yaml start trading-engine
.\.venv\Scripts\python.exe reports/trading_engine_recovery_check.py running
```

The initial `running` check verified container health and read-only settings, then stopped checking when the host HTTP request returned 404. A SELECT/HTTP/metadata-only diagnostic compared host and container responses. The check script was adjusted to distinguish internal service health from that host routing limitation, and the exact `running` command above was executed a second time successfully. No second start or recreation occurred.

Additional direct metadata diagnostics executed:

```powershell
docker port ai-trading-system-trading-engine-1
docker context show
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object LocalAddress,LocalPort,OwningProcess
```

The preserved [check script](trading_engine_recovery_check.py) contains the exact Docker/Python inspection, SELECT and HTTP checks. Docker inspect and merged configuration output were captured privately; credentials were not printed. Baseline inspection values are in [trading-engine-recovery-baseline.json](trading-engine-recovery-baseline.json). The script never runs a lifecycle command, migration or schema-changing SQL.

Compose `start trading-engine` reported waiting for the already healthy main database before starting the engine. It did not restart or recreate that database; baseline comparison confirmed this.

## Final validation

| Check | Observed result |
| --- | --- |
| Engine state / Docker health | running / healthy |
| Engine restart count | 0 |
| Startup command | `uvicorn trading.api:app --host 0.0.0.0 --port 8000` |
| Container database defaults / active transaction | read-only / read-only |
| Alembic marker | `0005_historical_source_jobs`, unchanged |
| In-container `/health/live` | HTTP 200, `{"status":"ok"}` |
| In-container `/health/ready` | HTTP 200, `{"status":"ready"}` |
| Sanitized recent log review | startup complete; 0 error/exception/traceback lines; 0 Alembic lines; 0 unknown-revision lines |
| Main PostgreSQL | running, healthy; identical container ID, start time and restart count |
| Isolated PostgreSQL | running; identical container ID, start time and restart count; no connection made |
| Original EURUSD Docker collector | running, healthy; identical container ID, start time and restart count |

## Remaining limitations

Windows requests to `http://127.0.0.1:8000/health/live` and `/health/ready` returned HTTP 404, while both endpoints returned HTTP 200 inside the engine. Docker reports port 8000 published on IPv4 and IPv6. Windows reports several listeners for port 8000 on different local addresses. This establishes a host access discrepancy, not its definitive cause. No network settings, other listeners or port mappings were changed; host accessibility needs separate investigation.

Future Compose recreation must explicitly include `reports/trading-engine-readonly-recovery.compose.yaml`. The base image command still contains automatic migration startup, so recreating from base Compose alone would remove this recovery safeguard.

The pre-existing multi-asset collector safety stop and completed-with-gaps historical backfills were outside this recovery and were not changed. No collector/backfill lifecycle commands, database migrations, schema changes, database restarts, commits or pushes were executed. No trades were submitted.
