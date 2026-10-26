Phase 4C stopped-collector diagnosis - 2026-10-23, Europe/Budapest

XAUUSD and WTI remain stopped. No collector or service was launched, stopped,
restarted, recreated or reconfigured during this investigation. The main database
on port 5432 was never connected to. Existing data, checkpoints, credentials,
progress files and heartbeats were preserved.

Both assets belong to one existing Windows collector, not two independent jobs.
Its entry point is `trading.ctrader.multiasset`: `start` calls
`history_backfill.start_background(..., module="trading.ctrader.multiasset")`,
which starts `supervise`, which starts one sequential `run` worker. On Windows
this uses `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`, with output appended to
`data/phase4c/collector/supervisor.log`. It survives the launching terminal but has
no boot-time task/service registration in this launch path. The previous recovery
report records a manual `python -m trading.ctrader.multiasset start` launch at
2026-10-22 18:56:37 UTC. Read-only Windows process inspection now found zero
matching multiasset/history_backfill Python processes. No scheduled task was
found in the supplied task audit; this inspection also queried matching actions.

The job is configured for XAUUSD (broker symbol 41) and WTI/SpotCrude (250), DEMO
only, a 300-second cadence, and Windows C: disk checks. EURUSD remains owned by
the independent Docker worker. The worker uses existing incremental ingestion,
per-asset retry reports and idempotent inserts. A supervisor file lock and shared
PostgreSQL advisory lock prevent competing instances. Its default repair window
is the preceding 24 hours; checkpoints do not extend this window automatically.

The last heartbeat was **2026-10-22 19:15:02 UTC**. At **19:16:27 UTC**, progress
changed to `safety_stopped` with:

```text
PostgreSQL storage free-space check unavailable after 3 attempts (TimeoutExpired); writes forbidden
```

The exact trigger was three timeouts of the Docker `df` probe, each limited to
20 seconds, separated by one- and two-second delays. This is a confirmed probe
failure, not evidence of exhausted disk or API/database ingestion errors. The
supervisor deliberately does not retry safety-stop exit code 2 and removes its
PID file on exit. An empty persistent `supervisor.lock` file is expected; it is
not evidence of a live process and must not be deleted to force recovery. The
older generic failure in the append-only log predates the previous recovery.

Why Docker stopped answering within 20 seconds cannot be proved from retained
logs. Severe host RAM pressure is a plausible contributor, not an established
root cause. No OOM kill or PostgreSQL restart was established. The identical probe
now succeeds in **7.438 seconds**. Windows C: had **36.36 GiB** free; the isolated
PostgreSQL filesystem had **944.74 GiB**. Both exceed the unchanged **10 GiB**
floor. The container uses its existing named volume and loopback host port 15432.

Database evidence was captured at **2026-10-22 22:21 UTC** (00:21 Budapest on
October 23). The window is **[2026-10-21 22:21, 2026-10-22 22:21) UTC**. These are
read-only PostgreSQL observations, not estimates from process logs:

| Asset | Latest stored M1, UTC on Oct 22 | Missing open minutes in window | Interpretation |
| --- | --- | --- | --- |
| EURUSD | 22:17 | 8 | Five internal absences plus three minutes of normal polling tail |
| XAUUSD | 19:05 | 133 | All after its last stored candle |
| WTI | 19:11 | 129 | 128 after its last candle, plus Oct 21 22:57 |

XAUUSD's outage intervals are **[19:06,20:59)** and **[22:01,22:21)** on Oct 22.
WTI's are **[19:12,20:59)** and **[22:00,22:21)**. Its earlier one-minute absence
at Oct 21 22:57 remains retry-deferred in the last worker report. The stored broker
calendar excludes scheduled closures **[20:59,22:01)** for XAUUSD,
**[20:59,22:00)** for WTI, and **[20:59,21:01)** for EURUSD. No weekend falls inside
this window. EURUSD's internal absences are **[21:01,21:05)** and **[21:08,21:09)**.
Those are unexplained relative to the saved schedule; absence alone cannot prove
whether the broker published a candle during rollover.

EURUSD's latest successful worker report independently agrees with the supplied
**99.5139%**, seven-missing-minute, zero-API/database-error result. That report uses
a different cutoff and calendar (including the two rollover-closure minutes),
whereas this audit excludes closures and includes a three-minute polling tail.
The Docker container is running and inserting candles, but its health label was
`unhealthy` at inspection, contrary to the earlier supplied snapshot. It was left
untouched. Its recorded start time and isolated PostgreSQL's start time predate
this investigation.

The last XAUUSD report succeeded at 19:07 UTC, with zero API/database errors and
100% rolling-window coverage. WTI last succeeded at 19:14:48 UTC, with zero such
errors and the one prior gap. XAUUSD's continuous checkpoint matches 19:05; WTI's
checkpoint is 19:06 although candles exist through 19:11. Candle persistence and
the guarded checkpoint update are separate operations, so a safety stop can leave
this lag. Replay is idempotent; no checkpoint edit is needed or authorized.

Both historical progress files are `completed_with_gaps`, **261/261 batches**,
finished at 15:11 and 15:22 UTC on Oct 22. Their preserved final summaries contain
**27,137 XAUUSD** and **69,446 WTI** missing open-session minutes over their
requested five-year windows. These archive totals were read from existing local
verification summaries, not rescanned. Historical holiday/schedule changes can
explain some absences; current schedules are not conclusive historical calendars.
Older `verified-status.json` snapshots still show 4/261 and must not override the
later completed progress/final summaries. No historical job was restarted.

The minimal source change moves the Arrow-backed `export_partition` import inside
`persist_candles`, after empty/existing-output checks. PostgreSQL-only supervisors
and workers therefore no longer preload PyArrow/NumPy/Pandas. No guard, timeout,
retry policy, job configuration or ingestion behavior changed. This affects future
Python launches; running Docker images/processes were untouched. A fresh-interpreter
regression test forbids analytical imports while importing the complete collector
module graph and checking empty exports. The existing export-failure test patches
the import at its new location.

An **import-only** measurement at 22:27 UTC used **56.8 MiB working set / 45.1 MiB
private memory**, with none of PyArrow/NumPy/Pandas loaded. This did not invoke any
collector CLI or connect to a database. Two application interpreters imply roughly
114 MiB of base working set, before data, network buffers and Docker subprocesses.
Allow **0.2-0.4 GiB** for the combined supervisor/worker as a planning estimate;
reserve **0.5 GiB** and allow additional Docker/WSL headroom. A live peak was not
measured. Available Windows RAM fluctuated around **0.31-0.61 GiB**, most recently
**0.61 GiB**, so recovery is not recommended until an operator establishes at least
**1 GiB available RAM** without disrupting the protected services. This is operating
guidance, not a new code-enforced memory threshold.

Future recovery, only after approval and resource preflight:

1. Establish memory headroom separately. Confirm only the existing XAUUSD/WTI DEMO
   job is selected, no matching supervisor/worker is active, and the isolated
   database identity/port are unchanged. Preserve the lock and checkpoint files.
2. Repeat bounded PostgreSQL-storage probes under normal load and confirm Windows
   and PostgreSQL free space both exceed 10 GiB. Persistent timeouts require Docker/
   host diagnosis; do not disable the guard, accept stale readings or force restarts.
3. From the project directory, launch **one** shared supervisor using the command
   below. Do not run `init`, `discover`, `history-init`, a historical backfill, or
   separate per-asset workers. Runtime identity, DEMO, storage and exclusivity checks
   remain mandatory.
4. Inspect the existing heartbeat/progress files and append-only log. Verify an
   advancing heartbeat, then separately verify fresh indexed M1 timestamps after
   two completed cycles. A first cycle may exceed 300 seconds on this host; missed
   deadlines are skipped rather than starting concurrent cycles.
5. The 24-hour rolling repair should cover the current short outage if recovery
   occurs promptly. XAUUSD's earliest missing minute ages out after Oct 23 19:06 UTC,
   WTI's new outage after 19:12 UTC; the old Oct 21 22:57 WTI gap ages out much sooner.
   Longer delays require a separately scoped, authorized targeted repair. Archive
   gaps will not be repaired by continuous collection. Do not assume all missing
   broker minutes can be retrieved.

```powershell
# FUTURE LAUNCH ONLY - NOT EXECUTED
Set-Location C:\Users\jager\ai_trader\AI-Trading-System
.\.venv\Scripts\python.exe -m trading.ctrader.multiasset start --state-dir .\data\phase4c\collector

# Lightweight inspection, avoiding the broad SQL aggregation in multiasset status
Get-Content .\data\phase4c\collector\progress.json
Get-Content .\data\phase4c\collector\heartbeat.json
Get-Content .\data\phase4c\collector\supervisor.log -Tail 10

# Future graceful stop, if required; wait for exit before resuming with start
.\.venv\Scripts\python.exe -m trading.ctrader.multiasset stop --state-dir .\data\phase4c\collector
```

Validation: **25 tests passed**: 20 existing historical/supervisor/guard cases,
one new clean-import test, and four existing Parquet tests covering roundtrip,
empty data, refusal to overwrite, and atomic export failure. Ruff passed on the
changed source/tests; scoped `git diff --check` passed. Tests used local fixtures
and workspace temporary directories, without production API/DB calls.

The audit connection was pinned to loopback port **15432** and verified the
expected database/user/cluster identity. Both default and transaction read-only
settings were asserted, then the non-superuser `pg_read_all_data` role was used.
The transaction was rolled back. Every candle query had an index-checked plan,
LIMIT and/or a 24-hour bound; timestamp reads used index-only scans. No whole-archive
count, schema SQL, migration, OAuth refresh, trade, commit or push was performed.
Sanitized local evidence: `data/phase4c/diagnosis-20261023.json` and
`data/phase4c/collector-import-footprint.json`; the adjacent diagnosis script can
repeat the bounded read-only audit without starting collectors.
