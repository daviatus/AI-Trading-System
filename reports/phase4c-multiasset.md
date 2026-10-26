# Phase 4C: multi-asset market data

Implemented and deployed on October 22, 2026. Final PostgreSQL snapshot:
**12:06 UTC / 14:06 Budapest**. All three instruments use Pepperstone DEMO,
source `ctrader_open_api_demo`, M1, price type `unknown`.

| Instrument | Actual broker symbol / ID | Price digits | Stored candles | Latest candle, UTC |
| --- | --- | --- | --- | --- |
| EURUSD | EURUSD / 1 | 5 | **1,855,044** | 2026-10-22 12:02 |
| XAUUSD | XAUUSD / 41 | 2 | **82,129** | 2026-10-22 12:00 |
| WTI | **SpotCrude / 250** | 3 | **81,635** | 2026-10-22 12:00 |

Every instrument's earliest stored candle is **2021-10-22 00:00 UTC**. This long
span includes a separate recent collection range; it does not establish continuous
five-year coverage for the new assets. Counts grouped by instrument/source/price
are timestamp-distinct under the validated existing unique constraint. Direct
new-asset checkpoint checks also found zero duplicates and OHLC inconsistencies.

## Deployment and preservation

The existing EURUSD Docker worker remains **healthy**, with unchanged start time
**2026-10-21 21:28:16 UTC** and restart count **1**. It is still the exclusive
EURUSD collector and OAuth refresher. A separate hidden supervisor runs the same
generalized worker/sync pipeline for **XAUUSD and WTI only**, every **300 seconds**.
Several successful incremental cycles were verified, including separate five-bar
inserts. Latest reports have **zero API/database errors**.

Independent watermarks use `continuous:XAUUSD:M1` and `continuous:WTI:M1`;
reports/retries use `ctrader_worker_run:XAUUSD` and `ctrader_worker_run:WTI`.
Before/after checks preserved all **1,855,014** pre-existing EURUSD rows through
11:32 UTC, with identical ID sum **1,720,539,397,605**, and all **261** EURUSD
historical checkpoints. No duplicate EURUSD historical job was started.

Exact enabled aliases are configured in `config/ctrader_instruments.json`.
Discovery resolved **SpotCrude**, rather than assuming USOIL or selecting Brent,
perpetuals, or the disabled XTIUSD entry. Verified precision, sessions, holidays,
measurement units and raw min/max/step/lot fields are retained in
`data/phase4c/symbols.json` and an append-only database discovery event.
No contract specifications were guessed or converted into trading instructions.

## Historical progress

Both new assets returned all five requested bars in a targeted probe for
**2021-10-22 12:00–12:05 UTC**. Their independent backfills are running:

| Instrument | Durable batches completed / remaining | Scanned through, exclusive |
| --- | --- | --- |
| EURUSD | **261 / 0**, finished scanning with gaps before this phase | 2026-10-20 21:20 UTC historical boundary |
| XAUUSD | **13 / 248**, running | **2022-01-21 00:00 UTC** |
| WTI / SpotCrude | **13 / 248**, running | **2022-01-21 00:00 UTC** |

The Phase 4B seven-day implementation is reused, with per-instrument job IDs,
locks, durable PostgreSQL checkpoints, idempotent inserts, bounded backoff and
at most one historical request per second per client. Each new asset has one
historical job; its range ends before the seeded continuous range beginning
**2026-10-21 11:51 UTC**, avoiding overlapping ownership. Crash recovery replays
uncheckpointed inserts safely. Credentials are read without refresh or writes.

The detailed **12:00 UTC** quality checkpoint covered the first four weeks through
November 19, 2021: gold had **5 unresolved trading minutes**, oil **281**; respective
scheduled closures excluded were **12,836 / 12,816 minutes**. Both job error counters
were **0 API / 0 database errors**. Later batches continue updating the database;
use the progress commands for current gaps and counters. **Five years have not
yet been collected for gold or oil.**

## Calendars, quality and features

Per-symbol broker weekly sessions, timezones, DST and exposed holidays are used.
Gold reopens at 18:01 New York time, SpotCrude at 18:00. The
[official cTrader model reference](https://help.ctrader.com/open-api/model-messages/)
defines the session/holiday fields. Historical schedule changes and unexposed
holidays remain uncertainties; unexplained open-session absences are not imputed.

Recent separately timed Phase 4C audits:

| Instrument | Candles audited | Duplicates / OHLC errors | Missing trading / scheduled closed minutes | Complete feature rows |
| --- | --- | --- | --- | --- |
| EURUSD | 1,435 | 0 / 0 | **0 / 2** | 1,373 |
| XAUUSD | 1,378 | 0 / 0 | **0 / 62** | 1,316 |
| WTI | 1,373 | 0 / 0 | **1 / 61** | 1,280 |

Oil's unresolved recent minute is October 21 at **22:57 UTC**. The unchanged legacy
EURUSD worker retains its original two-minute rollover warning; the new audit
correctly classifies those broker-confirmed minutes as closures without rewriting
existing reports.

Each sample dataset has **20 causal features** covering returns, EMA/trend, RSI,
ATR, volatility, volume and calendar encodings, with normalized measures and no
hardcoded pip assumptions. Indicators and labels reset at gaps, targets are
separate, splits are chronological/purged, and mixed instruments are rejected.
Artifacts are under `data/phase4c/quality/<instrument>/`, intentionally Git-ignored.
No ML training, strategies, order execution or LIVE changes were introduced.

## Storage and handover

At the final snapshot, PostgreSQL size was **915,411,427 bytes** and Windows free
space **20,183,130,112 bytes**. Windows and PostgreSQL filesystem space are monitored;
all jobs stop below **10 GiB**, without deleting data. Host database connections
use only **127.0.0.1:15432**, with exact database/user/cluster checks before writes.
The main port 5432 was not accessed or migrated. Credentials, keys, existing data,
Docker volumes and worker reports were preserved.

From the project directory in PowerShell:

```powershell
# Continuous collection, stored counts/ranges, errors and watermarks:
.\.venv\Scripts\python.exe -m trading.ctrader.multiasset status

# Historical progress; replace XAUUSD with WTI for oil:
.\.venv\Scripts\python.exe -m trading.ctrader.history_backfill status --state-dir .\data\phase4c\history\XAUUSD --verify

# Safe historical stop / resume:
.\.venv\Scripts\python.exe -m trading.ctrader.history_backfill stop --state-dir .\data\phase4c\history\XAUUSD
.\.venv\Scripts\python.exe -m trading.ctrader.history_backfill start --state-dir .\data\phase4c\history\XAUUSD

# Original completed EURUSD historical job, without restarting it:
.\.venv\Scripts\python.exe -m trading.ctrader.history_backfill status --verify
```

Wait for a supervisor to stop before resuming; locks reject competing processes.
The detached supervisors outlive Codex/terminal sessions and retry transient
failures within bounded limits. They are not boot-time Windows services: after a
machine restart, restore Docker/the original collector, explicitly resume each
history, and run `python -m trading.ctrader.multiasset start` for the additional
continuous collector. Preserve job definitions and checkpoints; initialization
and discovery are not needed again.

**Validation:** full suite **387 passed, 13 skipped**; final targeted checks
**42 passed**. Ruff and whitespace checks pass. Skips cover POSIX-only and opt-in
Docker lifecycle tests; one existing Starlette/AnyIO warning remains. No commits,
pushes or merges were performed. Background jobs are left running independently.
