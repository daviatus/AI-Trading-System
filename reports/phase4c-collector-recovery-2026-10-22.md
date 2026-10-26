# Phase 4C continuous collector recovery — 2026-10-22

## Outcome

The existing detached XAUUSD/WTI DEMO collector supervisor was restored at **18:56:37 UTC**, supervisor PID **21144**. Its exclusive file lock is held and its heartbeat advanced through **19:02:39 UTC**. Two scheduled cycles succeeded for both assets, with zero API/database errors. The first cycle also reported zero conflicts, rejected records or duplicates. No historical backfill was restarted.

At the **19:02:41 UTC** isolated PostgreSQL read-only snapshot:

| Instrument | Latest stored M1 candle, UTC | Result |
| --- | --- | --- |
| EURUSD | 2026-10-22 18:57 | Original Docker collector remains healthy and unchanged |
| XAUUSD | 2026-10-22 19:00 | Continuous collector restored; advancing on scheduled cycles |
| WTI / SpotCrude | 2026-10-22 19:00 | Continuous collector restored; advancing on scheduled cycles |

Both additional assets had stopped advancing at 16:50 UTC. Indexed database reads verified **125 newly present candles each** from 16:51 through 18:55 inclusive, with **zero missing open-session minutes in that outage interval**. The XAUUSD worker reported 126 total inserts in its rolling window; WTI reported 125. These are actual database observations, not just process-log claims.

The next scheduled cycle inserted **five additional candles per asset**, finishing at 19:01:52 UTC for XAUUSD and 19:02:04 UTC for WTI. Subsequent indexed database queries independently confirmed both latest timestamps advanced to 19:00 UTC. Their watermarks were independently checked against the first restored cycle's latest timestamps.

## Root cause and minimal fix

The previous process safety-stopped at **16:56:23 UTC** with `PostgreSQL storage free-space check unavailable`; its preceding successful heartbeat was at 16:55:50 UTC. The supervisor correctly treated safety-stop exit code 2 as requiring operator intervention and did not restart it. No supervisor remained active and its lock was free before restoration.

The storage guard previously made a **single** `docker exec ctrader-worker-test df -PB1 /var/lib/postgresql/data` request, with a 20-second timeout. Any CLI, timeout, exit-status or parsing failure immediately caused a permanent safety stop. The broad exception handler discarded the exception category and stderr. Consequently the precise historical transport/probe failure cannot be established from that retained log; a timeout is possible, but is not proven. The current identical probe succeeds. There was no evidence of exhausted space or a PostgreSQL-container restart.

The only runtime code change is in `src/trading/ctrader/history_backfill.py`, reused by Phase 4C:

- Retry an unavailable Docker storage probe at most three times, with 1- and 2-second backoff and the existing 20-second per-attempt timeout.
- Stop after persistent failure and include only its sanitized exception class, never raw subprocess diagnostics or credentials.
- Do not accept an expired cached result after failed probes. Reject malformed or negative available-space values.
- Preserve both Windows and PostgreSQL **10 GiB** floors, identity/session checks and operator stop handling. A successful low-space reading stops immediately without retry; Windows low space is now checked before invoking Docker.

The probe was not bypassed, replaced with an estimate or disabled. The append-only supervisor log still contains its original 49-byte historical stop message; current JSON progress and heartbeat show the restored state.

## Storage checks

The PostgreSQL mount was verified as the existing volume `ctrader-worker-test-data-fe1a7194a87c`, mounted at `/var/lib/postgresql/data`, in the isolated container exposed only on host port **15432**. Latest measurements were:

| Filesystem | Available bytes | Available GiB |
| --- | --- | --- |
| Windows C: | 41,088,806,912 | 38.27 |
| Isolated PostgreSQL filesystem | 1,014,402,138,112 | 944.74 |

Both exceed 10 GiB. The VM filesystem and Windows host space are measured separately; the larger virtual-filesystem value does not replace the host check.

## Historical completion and remaining gaps

Read-only, indexed queries confirmed **261 distinct durable checkpoints per instrument**, exactly covering all configured seven-day batches, with **0 remaining batches**. Both local jobs are `completed_with_gaps`; neither was resumed. Indexed edge reads verified the first historical candle at **2021-10-22 00:00 UTC** and the last before the reserved continuous boundary at **2026-10-21 11:50 UTC**. The exclusive historical boundary is 11:51 UTC on that date; continuous collection owns the subsequent range.

| Instrument | Historical checkpoint missing minutes | Final requested-range report missing minutes | Reported open-session gap intervals | Recorded historical API / database errors |
| --- | --- | --- | --- | --- |
| XAUUSD | 27,137 | 27,137 | 273 | 0 / 0 |
| WTI | 69,445 | 69,446 | 28,973 | 7 / 0 |

The final reports classify the requested **2021-10-22 through 2026-10-22 00:00 UTC** window and exclude 833,721 scheduled-closed minutes for XAUUSD and 832,417 for WTI. The historical checkpoint sums independently agree with the historical portion of those totals. The extra WTI minute belongs to the continuous range: **2026-10-21 22:57 UTC**, still absent in an exact indexed database lookup and marked retry-deferred in the restored worker report. XAUUSD's recent rolling window is complete.

These are unresolved absences relative to the broker calendar, not proof that every minute is recoverable. Current broker sessions and exposed holidays do not establish every historical holiday or schedule change. Several large intervals overlap Christmas/New Year closures. No candles were imputed and no gap-repair backfill was launched. This recovery did not rescan the full archive; gap quantities come from durable per-batch database verification summaries and preserved final reports.

## Safety and validation

- All database connections in this investigation targeted the verified isolated database on `127.0.0.1:15432`. Candle/checkpoint diagnostic connections used enforced read-only transactions, bounded statement/lock timeouts and rollback. The real guard preflight issued SELECT-only queries through its existing connection settings and updated only its local heartbeat. Database/user/cluster identity was checked before collector writes.
- Latest-candle queries used backward index-only scans with LIMIT 1. Outage verification used bounded timestamp index ranges; historical verification used the existing event-type index and projected checkpoint metadata. No full candle-table scan or whole-archive revalidation was executed.
- The isolated PostgreSQL container and original EURUSD worker retained their original container IDs, start times and restart counts. The isolated database was not restarted or modified administratively; only the authorized collectors resumed ordinary ingestion.
- The main database on port 5432 was never connected to, and the trading-engine and both PostgreSQL containers were not restarted or modified. No OAuth refresh, orders, LIVE enablement, history/checkpoint deletion, commit or push was performed.
- Focused tests: **26 passed** (`test_ctrader_history_backfill.py`, `test_multiasset.py`). Ruff passed for the changed source/tests; `git diff --check` passed. An initial test run hit Windows permission errors in pytest's existing shared temporary directory; rerunning with a fresh workspace-local base directory passed.

Restoration used the existing command:

```powershell
.\.venv\Scripts\python.exe -m trading.ctrader.multiasset start
```

Lightweight progress inspection and safe stop/resume from the project directory:

```powershell
Get-Content .\data\phase4c\collector\progress.json
Get-Content .\data\phase4c\collector\heartbeat.json
.\.venv\Scripts\python.exe -m trading.ctrader.multiasset stop
# Resume only after the supervisor exits and storage checks pass:
.\.venv\Scripts\python.exe -m trading.ctrader.multiasset start
```

The existing `multiasset status` command performs broad inventory aggregation, so it was deliberately avoided for this focused recovery. Private local evidence snapshots are `data/phase4c/recovery-before.json`, `recovery-after-first-cycle.json`, `recovery-final.json` and `recovery-second-cycle.json`; they contain no credentials.

Follow-up: reconcile historical closure calendars before authorizing targeted gap repairs. If storage probing fails persistently again, the new exception category will support diagnosis while collection remains stopped safely. The detached supervisor survives the launching session but is not a Windows boot service; existing boot-time limitations remain.
