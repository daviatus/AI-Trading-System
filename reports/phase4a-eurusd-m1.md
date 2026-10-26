# Phase 4A: EURUSD M1

Measured on October 22, 2026 at **08:28:19 UTC** (10:28:19 Budapest).
The collector continues adding data, so these are reproducible snapshot counts.

| Check | Result |
| --- | --- |
| Database | Isolated test instance, 127.0.0.1:15432, read-only repeatable-read snapshot |
| Schema | Alembic `0005_historical_source_jobs`; existing `ohlcv_candles` table |
| Selected stream | `ctrader_open_api_demo`, EURUSD, M1, price type `unknown` |
| Available candles | **2,101** |
| First / last candle open | **2026-10-20 21:20 / 2026-10-22 08:22 UTC** |
| Expected minutes within coverage | 2,103 |
| Coverage | **99.9049%** |
| Duplicate timestamps / out-of-order rows | **0 / 0** |
| OHLC inconsistencies / invalid rows / nonpositive prices | **0 / 0 / 0** |
| Missing or zero volumes | **0 / 0** |
| Internal missing trading minutes | **2**, October 21 at 20:59 and 21:00 UTC |
| Scheduled closure minutes within internal gaps | 0 |
| Latest close age | 5.32 minutes; five completed trading minutes absent at snapshot tail |
| Freshness assessment | Within the stated ten-trading-minute stale threshold |

The two internal minutes coincide with the New York rollover. Broker maintenance
or a lack of ticks could explain them, but neither was confirmed. They remain
unresolved missing trading minutes. The calendar excludes the DST-aware FX weekend
and accepts explicitly confirmed broker closure intervals; it does not guess holidays.

The database schema permits multiple sources and price types at one timestamp.
Inventory found only the selected stream. The audit retains that separation and
does not silently deduplicate or mix prices. No migrations or database writes ran.

## Feature dataset

The local artifacts are in
[`data/phase4a/20261022T082819Z`](../data/phase4a/20261022T082819Z/quality.md)
(intentionally ignored by Git). They include machine-readable quality findings,
separate feature/target Parquet files, and a manifest with definitions, configuration,
fingerprints and split boundaries.

**2,039 complete feature rows, 20 features**, with chronological train/validation/test
row counts **1,422 / 301 / 306**. Ten rows are purged between splits. The remaining
62 input rows are lost to per-segment indicator warmup and unavailable forward labels.

Features: simple returns at 1/2/5 bars; 20-bar return volatility (population standard
deviation); EMA 12/26 levels, relative price distances and one-bar slopes; normalized
fast/slow EMA trend spread; RSI 14; ATR 14 in price units and relative to close;
20-bar volume mean/z-score; and UTC time-of-day/weekday sine/cosine encodings.
RSI and ATR use the existing recursive Wilder-style EWM initialization, not an SMA seed.
Constant observed volume has zero z-score; missing volume is not imputed.
cTrader volume remains a raw protocol integer, and price type remains unknown.

Features are available only at each completed candle's close. All calculations are
causal; there is no centered rolling window, backward fill or fitted normalization.
Every absent minute resets indicator state, even over a scheduled closure. Five-bar
forward-return/direction targets are separate and cannot cross a gap. A five-bar
purge separates chronological splits. No model has been trained.

This dataset is **sufficient for pipeline checks and small initial ML experiments**.
Its approximately 35 hours of coverage cannot support credible generalization claims,
robust model selection, or evaluation across market conditions. Obtain multiple
months before meaningful time-series model evaluation; fit any later transforms
on training data alone and account for execution costs separately.

## Available cTrader history and safe backfill

Three separate read-only DEMO requests each returned all five requested M1 candles:

| Requested UTC interval, end exclusive | Returned |
| --- | --- |
| 2026-01-05 12:00–12:05 | 5 candles, 12:00–12:04 |
| 2025-10-01 12:00–12:05 | 5 candles, 12:00–12:04 |
| 2024-10-01 12:00–12:05 | 5 candles, 12:00–12:04 |

Thus M1 history is demonstrably retrievable at least as far back as **October 1,
2024** for this DEMO account/symbol. These sparse probes do not establish continuous
coverage or the earliest retained candle. Only 15 historical candles were retrieved;
they were counted in memory and not inserted into the database or training dataset.
The existing token was read without refresh or credential changes.

The **existing client** limits each request to 24 hours and splits jobs of at most
31 calendar days into daily requests, with bounded retries and rejection of partial,
malformed or mismatched responses. These are local safety bounds, not a 31-day
retention limit. Its timestamp validator accepts 1970-01-01 through
2038-01-19 03:14:06 UTC; that is representability, not a promise of available history.
The [official message reference](https://help.ctrader.com/open-api/messages/#protooagettrendbarsreq)
documents the timestamp and count fields, while the
[official rate limits](https://help.ctrader.com/open-api/)
allow at most five historical requests per second per connection.

After explicit authorization for a larger backfill:

1. Choose a bounded pilot range, preferably one previous full trading week.
   Pin the same test database identity and loopback port 15432 explicitly; do not
   inherit the main application's default database URL.
2. Use the existing sync dry-run to review missing open-session intervals without
   API requests or writes. Keep the supervisor and collector settings unchanged.
3. Retrieve daily windows sequentially at a conservative rate, with normal retry
   backoff, and validate count/coverage/OHLC/provenance before any persistence.
   Treat an empty or partial result as unresolved, not as proof of a closure.
4. If persistence is authorized, use the existing idempotent import path on the
   test database only. Review conflicts rather than replacing candles. Partition
   larger approved jobs into ranges no longer than 31 days; keep checkpoints.
5. Regenerate the quality report and features in a new output directory. Compare
   coverage and fingerprints before selecting a dataset for later ML work.

No large backfill, trades, LIVE configuration changes, worker changes, commits,
pushes or merges were performed.

## Validation

Full existing and new test suite: **367 passed, 13 skipped**, with one existing
Starlette/AnyIO deprecation warning. Skips cover platform-specific POSIX behavior
and opt-in Docker lifecycle tests; no worker lifecycle test was enabled.
Focused tests verify indicator values, prefix invariance under future-price changes,
gap/label boundaries, calendar/DST closures, malformed OHLC, duplicates, freshness,
constant volume, unfinalized bars, unsafe endpoint rejection and Parquet round trips.
Ruff checks pass for the changed Python files.
