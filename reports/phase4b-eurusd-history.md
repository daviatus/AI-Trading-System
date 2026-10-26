# Phase 4B: Pepperstone DEMO EURUSD M1 history

Requested interval: **2021-10-22 00:00 UTC through 2026-10-22 00:00 UTC**, end
exclusive. A separate hidden Windows Python supervisor was started; the existing
300-second collector was left running unchanged. This document records a snapshot;
the job continues independently. Use `status --verify` for current database evidence.

## Availability

The broker identified itself as `pepperstoneuk`, with a DEMO account. Three targeted
requests of five M1 candles each returned every requested bar:

| UTC date | Requested interval | Result |
| --- | --- | --- |
| 2021-10-22 | 12:00–12:05, end exclusive | 5 candles |
| 2022-10-24 | 12:00–12:05, end exclusive | 5 candles |
| 2023-10-23 | 12:00–12:05, end exclusive | 5 candles |

Phase 4A had already confirmed 2024-10-01, 2025-10-01 and 2026-01-05. The first
backfill insert also established availability at **2021-10-22 00:00 UTC**, exactly
the requested lower bound. No retention limit preventing a five-year attempt has
been observed. Sparse probes cannot establish continuous five-year coverage;
the job checks each historical batch and records genuine absences separately.

## Isolation and execution

The exact verified identity is database `ctrader_demo_fe1a7194a87c`, user
`ctrader_demo_test`, PostgreSQL cluster `7699226691421290540`. All host database
connections use **127.0.0.1:15432**. No main database connection, restart, migration
or configuration change was performed.

The immutable job definition is `data/phase4b/job.json`, job ID
`349a16aa7ad25221`. It defines **261** batches, each at most seven calendar days,
ending at **2026-10-20 21:20 UTC**. The already-existing stream from that instant
onward belongs to the collector. This prevents historical writes from overlapping
the collector's rolling recent window while retaining the full requested objective.
Final verification includes the requested range through October 22.

The implementation reuses `CTraderSyncService`, `CTraderReadOnlyClient` daily
chunking/validation/retries, and `PostgresMarketDataRepository.import_candles`.
Inserts are idempotent and conflicting records are preserved and reported. Each
scanned batch has its own append-only `ctrader_history_backfill_checkpoint` event
with PostgreSQL-verified coverage, missing intervals and API/import counts.
API/database failures use a separate event type. Existing worker events are intact.
A process crash between candle insertion and checkpoint insertion repeats the
batch safely. An unresolved empty open-session batch is confirmed twice; errors
are never checkpointed as a successful scan.

The supervisor retries transient exits with bounded backoff. Safety failures stop
without automatic destructive remediation. File and database locks prevent
competing historical processes. A read-only credential snapshot uses the existing
collector's token, with **no OAuth refresh or credential/key changes**. The collector
remains responsible for token refresh. No ML training, orders or LIVE actions ran.

## Disk protection

Initial measurements on October 22:

| Measurement | Initial value |
| --- | --- |
| Windows C: free | **26,495,684,608 bytes**, approximately 24.68 GiB |
| PostgreSQL database size | **9,245,155 bytes** |
| Candle table plus indexes | **974,848 bytes** |
| Docker images | 3.395 GB |
| Docker local volumes, all containers | 164.2 MB |
| Docker build cache | 3.697 GB |

Both Windows drive free space and the test PostgreSQL filesystem free space are
checked throughout the job. Windows free space is the binding limit for Docker's
growing virtual disk; guest filesystem free space alone would be insufficient.
The job stops below **10 GiB**, conservatively above 10 decimal GB. The supervisor
also monitors host space while a child is busy. No images, volumes, datasets or
databases are deleted. No CSV or Parquet candle intermediates are generated.

## Progress, stop and resume

Run from the project directory in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m trading.ctrader.history_backfill status --verify
.\.venv\Scripts\python.exe -m trading.ctrader.history_backfill stop
.\.venv\Scripts\python.exe -m trading.ctrader.history_backfill start
```

Stop is cooperative at an API/write boundary. Wait until the supervisor exits
before `start`; an active supervisor rejects a competing start. Resume uses durable
database checkpoints and existing candles. The background job does not require an
active Codex session or terminal, but it is not a boot-time Windows service. After
a machine restart, Docker outage exceeding retry bounds, or a safety stop, use
`start` after resolving the cause. Never delete the job definition to resume.

## Coverage interpretation

Weekend minutes are excluded using Sunday 17:00 to Friday 17:00 America/New_York,
including DST. Unconfirmed holidays, rollover maintenance and tickless minutes
remain unresolved, rather than being silently declared valid closures. Detailed
gap intervals are persisted, so a scanned batch with gaps is distinguishable from
complete trading-minute coverage. A long date span alone is not proof that five
years have been collected.

There was a startup checkpoint serialization failure after the first candle
insert. The backfill was stopped safely, serialization was fixed and regression
tested, and it resumed from the preserved database rows. The collector was not
stopped or restarted. Final measured progress is recorded below once a durable
checkpoint is verified.

## Verified checkpoint snapshot

At **2026-10-22 09:00:06 UTC** (11:00:06 Budapest), after a clean cooperative stop:

| PostgreSQL-verified measurement | Result |
| --- | --- |
| Total stored EURUSD M1 candles / distinct timestamps | **30,632 / 30,632** |
| Earliest stored candle | **2021-10-22 00:00 UTC** |
| Latest stored candle, including ongoing collector | **2026-10-22 08:57 UTC** |
| Durable completed batches / remaining | **4 / 257**, out of 261 |
| Contiguously scanned prefix | **2021-10-22 00:00 through 2021-11-19 00:00 UTC**, end exclusive |
| Historical rows within that prefix | **28,496** |
| Expected trading minutes within that prefix | **28,740** |
| Missing open-session minutes in scanned prefix | **244**, unresolved |
| Missing scheduled closure minutes excluded | **11,580**, including DST adjustment |
| Absent intervals, including weekend intervals | **160**, detailed in verified-status.json |
| API / database errors in durable job error events | **0 / 0** |
| Historical requests in checkpointed batch results | **77**; excludes initial pre-checkpoint attempts |
| PostgreSQL database size | **21,909,987 bytes**, approximately 20.90 MiB |
| Test PostgreSQL data directory allocated usage | **77,344 KiB**, including WAL and cluster files |
| Windows free disk space | **25,833,648,128 bytes**, approximately 24.06 GiB |
| Original collector | **Healthy**, start time unchanged at 2026-10-21 21:28:16 UTC |
| Five years collected? | **No**. Four weeks are scanned; the long stored date span includes the recent collector range. |

The checkpoint serialization startup error was a code failure, separate from API
and PostgreSQL error counters. It was corrected before the successful checkpoints.
The four-checkpoint job was resumed with the same job definition and locks; it is
left running independently. Checkpoint resume was tested without restarting the
collector. Current counts will increase after this snapshot.

Focused new and relevant existing tests: **156 passed**. Ruff and `git diff --check`
passed. No commits, pushes or merges were performed.
