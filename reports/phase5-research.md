# Phase 5 research sprint — implementation and resource blocker

**Status: blocked before data export and experiments. This is not a completed or validated ML research result.**

## Measured blocker

On 2026-10-22 the Windows host had 7.79 GiB physical RAM, with 95% in use. Repeated
measurements found approximately 0.28–0.53 GiB available. The 19:55 UTC checkpoint
recorded 559,493,120 available bytes and 39,999,119,360 free disk bytes. Disk space
passed the requested 15 GiB floor; RAM did not provide safe research headroom.

The actual command `python -m trading.research.runner all` stopped with
`RESEARCH_SAFETY_STOP: Available RAM 0.51 GiB below research minimum`. This gate
runs using the standard library, before importing NumPy, pandas, SciPy or XGBoost.
No Phase 5 database export, model fitting or holdout evaluation began. No research
process is left training or waiting to start automatically.

RAM should have been checked before environment setup. The correction is an
explicit gate before scientific imports and every data-intensive stage; no further
database export or model experiment began under the observed memory pressure.

The user was asked whether at least 2 GiB can be freed without stopping protected
services, or whether another research machine is available. Existing authorization
covers resuming offline research once safe headroom is observed; further approval
between normal phases is unnecessary.

## Implemented functionality

All new runtime functionality is under `src/trading/research/`; no research code is
connected to application startup, ingestion workers or order submission.

| Module / artifact | Implementation |
| --- | --- |
| `data.py` | Exact isolated database/port/user/cluster validation, default-read-only transactions, optional effective `pg_read_all_data` role, statement/lock timeouts, indexed monthly reads capped at 50,000 rows, atomic Parquet/checksum checkpoints |
| Local audit | Per-year availability, duplicates, OHLC/price checks, volume issues, missing intervals, exposed session/holiday classification and out-of-session candles; no manufactured prices |
| Features | Phase 4A return/EMA/RSI/ATR/volatility/tick-volume/calendar features, normalized candle and rolling-position features; 64-bar warm-up; resets after missing, invalid or excluded-session bars |
| `execution.py` | Cost-aware LONG/SHORT/no-trade targets, 15/60-minute configurable horizons, next-bar entry, stop-first ambiguous bars, adverse gap treatment, instrument quantity steps, USD account ledger, one shared position, leverage/risk/daily-loss limits |
| `models.py` | Independent per-asset CPU XGBoost models, deterministic temporal training samples, class weights, disjoint chronological temperature/class-bias calibration, gain importance, causal regime labels, cost/confidence/risk-aware opportunity ranking |
| `runner.py` | Frozen configuration, per-model/per-asset checkpoints, bounded development selection, chronological walk-forward evaluation, one-time frozen final-holdout choices, cost sensitivity, trade/equity artifacts, metrics and daily block-bootstrap summaries |
| `safety.py` | Standard-library resource gate; 15 GiB disk and 1.5 GiB available-RAM minimums cannot be lowered through config; one training thread |

The default research protocol is in `config/phase5-research.json`. Development
windows evaluate 2024 H2 and 2025 H2; the untouched final period is 2026-01-01
through 2026-10-22 exclusive. Each window has earlier, disjoint training,
calibration and tuning periods, with target-horizon purging at boundaries.

The bounded candidate set contains two small tree configurations, two holding
horizons, three probability thresholds and two regime policies. Selection uses
tuning net return penalized for drawdown, not classification accuracy. Fewer than
30 tuning trades or unresolved terminal positions disqualify a candidate; cash is
selected when no candidate has a positive economic selection score. Fixed trend
and probability-threshold XGBoost baselines provide comparisons, including when
the selected policy chooses cash. Costs are stressed at 1×, 2× and 3× without
retuning the final holdout.

The initial scenario uses a $10,000 USD account, 0.25% maximum initial account risk
per trade, 1.5% daily realized-loss budget, 5× gross exposure ceiling and a 3R gross
profit barrier. These are research assumptions. Quantity rounding and exposure
caps can reduce actual risk and attainable winning-trade returns. A 0.5–1.0% net
account win is not presumed or demonstrated.

## Tested versus untested

- Ruff and Python syntax checks passed for the new modules/tests.
- **Six standard-library safety tests passed**, covering low disk, low RAM, passing headroom,
  non-lowerable floors and rejection before scientific imports.
- `tests/test_research.py` contains focused tests for target isolation, causal
  features, reset behavior, calendar equivalence, next-bar execution, ambiguous
  fills, future gaps, costs, ranking, position sizing, daily loss, temporal purging
  and a real tiny XGBoost/calibration fit. **That suite has not run because of the
  memory blocker.** Existing feature/worker tests have not been rerun this sprint.
- The complete new research implementation is therefore experimental and has not
  been validated end to end. Its code must not be treated as financial evidence.

## Measured research results

**None.** No asset models were trained, no strategy performance was measured and
no new full-history data-quality report was generated. There are no defensible
Phase 5 profitability, profit-factor, drawdown or opportunity-frequency claims.
No final holdout choices have been opened, evaluated or tuned.

The earlier Phase 4C completion/gap observations remain historical evidence only;
see `reports/phase4c-collector-recovery-2026-10-22.md`. They are not presented here
as a fresh Phase 5 data audit. The research output directory currently contains
lightweight status/environment metadata, not cached candles or experiment results.

## Environment and exact resume commands

A separate `.venv-research` was created. XGBoost 3.4.1, SciPy 1.18.1 and NumPy 2.5.4
were installed there. The collector environment was not upgraded. A research-only
`.pth` file makes existing project dependencies and `src` readable from that
environment; it does not modify or execute collector settings. A redundant
research-only editable-install process was stopped; its completion is not claimed.

After making 2–3 GiB RAM available while preserving the collectors and all Docker
services, run from the project directory:

```powershell
# Check actual resources before scientific imports.
.\.venv-research\Scripts\python.exe -c "import json; from pathlib import Path; from trading.research.safety import resources; resources(json.loads(Path('config/phase5-research.json').read_text()))"

# Execute the still-pending focused suite before processing data or fitting models.
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:OMP_NUM_THREADS = '1'
$phase5TestRoot = Join-Path 'data\research\phase5-v1' ('tests-' + [guid]::NewGuid().ToString('N'))
.\.venv-research\Scripts\python.exe -m pytest tests/test_research_safety.py tests/test_research.py tests/test_phase4a.py tests/test_multiasset.py -q -p no:cacheprovider --basetemp $phase5TestRoot

# Proceed only after the focused tests pass.
.\.venv-research\Scripts\python.exe -m trading.research.runner snapshot
.\.venv-research\Scripts\python.exe -m trading.research.runner prepare
.\.venv-research\Scripts\python.exe -m trading.research.runner run
.\.venv-research\Scripts\python.exe -m trading.research.runner report
```

For a fresh, fully independent environment on a resource-sufficient machine,
install the project with `pip install -e '.[research,dev]'` inside a new virtual
environment. Do not install into the active collector environment. Record exact
package versions with the experiment; the runner writes `environment.json`.

Artifacts belong under `data/research/phase5-v1/`, including monthly cache,
contracts, audits, features, model/calibrator files, frozen choices, opportunities,
trade ledgers, equity curves, result JSON and `results.md`. Snapshot and model
checkpoints support continuation. Changed experiment parameters require a new
output directory. Preserve the original final holdout; do not tune against it.

## Limitations and next work

A final read-only operational check also found that Phase 4C safety-stopped again
at **19:16:27 UTC** after **three Docker storage-probe `TimeoutExpired` failures**;
its last heartbeat was **19:15:02 UTC** and no supervisor state file remained.
EURUSD's original Docker collector was **running but unhealthy**, with unchanged
start time **2026-10-21 21:28:16 UTC** and restart count **1**. These states were
discovered during this sprint, not corrected. No collector stop or restart command
was sent, and the evidence does not establish their causes or attribute them to
RAM pressure or research setup. No claim of uninterrupted healthy collection is
made. Their recovery requires separate scope because this brief explicitly
forbids collector/runtime modification.

Resume with the focused suite, fix observed failures, then complete the bounded
data audit before model experiments. Run all predefined development experiments,
freeze choices and only then evaluate the 2026 holdout. Write the final comparative
research report from those actual artifacts. Phase 5B priorities cannot yet be
chosen from measured model evidence; likely infrastructure needs include reliable
historical calendars, bid/ask/cost observations and additional hardware headroom.

The stored price type is unknown. Midpoint pricing, spreads, commissions, slippage,
USD conversion and static contract specifications are explicit sensitivity
assumptions, not verified historical execution conditions. Raw cTrader quantity
fields are interpreted in hundredths of units using the official protocol; dynamic
leverage tiers and historical specification changes are not established. The
ledger supports one simultaneous position. Gap losses can exceed planned stop
risk; unresolved terminal positions are marked rather than fabricated as fills.
Drawdown uses observed minute-close marks and cannot establish tick-level extrema.

Technical references: [cTrader protocol models](https://help.ctrader.com/open-api/model-messages/),
[official protocol definitions](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto),
[XGBoost parameters](https://xgboost.readthedocs.io/en/stable/parameter.html),
[disjoint probability calibration](https://scikit-learn.org/stable/modules/calibration.html).

No main database connection, schema change, migration, Docker lifecycle change,
collector modification, OAuth change, trade, LIVE enablement, commit or push was
performed during this research sprint.
