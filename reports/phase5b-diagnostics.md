# Phase 5B — artifact diagnostics

The implementation is `src/trading/research/diagnostics.py`, dispatched by
`trading.research.runner diagnostics` before scientific, model-training or database
imports. It reads only local research artifacts. It does not fit a calibrator,
predict with a model, reconstruct trading decisions, rerun a backtest, contact a
database or broker, or change collectors, credentials, containers or configuration.

The existing `reports/phase5-research.md` describes an earlier resource blocker.
The completed evidence for this task is under `data/research/phase5-v1/`, including
`results.md`; that evidence takes precedence over the earlier progress narrative.

## Source artifacts actually present

| Artifact | Available evidence | Limitation |
| --- | --- | --- |
| Frozen config, contracts, snapshot, environment | Boundaries, scenarios, current schedule/specifications, source checksums and versions | No historical cost/contract verification or executed-code hash |
| 183 monthly cache Parquets | Exported reference-price candles and snapshot hashes | Price type unknown; publication/revision timestamps absent |
| Three cleaned bar and three feature Parquets | Timestamp alignment, chronology, complete-feature samples and gap-reset checks | No saved label-validity masks or sampled row identities |
| Three quality and three preparation JSON files | Missing intervals/yearly coverage, OHLC checks, excluded sessions and warm-up totals | Completeness uses exposed current schedules and dated holidays |
| Nine choice JSON files, each with 24 candidates | Stored tuning trade counts, performance, predictive scores and activation choices | No rejected-signal or execution gate traces |
| 36 model metadata JSON and 36 model files | Training/calibration sample counts and extrema, raw/fitted calibration scores, calibrator parameters | Full prediction/label streams and reliability bins absent |
| Three frozen evaluation locks | Choice content and hashes at evaluation opening | Historical source-code version not recorded |
| Nine opportunity, 27 trade and 27 equity Parquets | Accepted probabilities, executed asset attribution, close-equity drawdown, costs and exit flags | Opportunities omit rejected decisions; portfolio marks have no native per-asset equity allocation |
| Three result JSON files and measured Markdown | Portfolio comparisons, predictive sample/class counts and saved descriptive bootstrap intervals | Sparse/dependent trades and uncertain execution assumptions limit interpretation |

## Traceable zero-trade explanation

All nine saved selected choices have `active: false`. Across 216 stored candidates:

- 206 failed the minimum of 30 tuning trades.
- The other 10 had a nonpositive tuning return-minus-half-drawdown score.
- No candidate recorded an unresolved terminal position or a positive eligible score.

These are counts of candidate configurations, not independent signals or trades.
The current evaluator skips selected signal generation when the choice is inactive.
The frozen locks preserve those inactive choices. This explains selected-strategy
zero trades at the policy activation stage. It does **not** establish why individual
tuning signals failed to appear: historical probability, expected-payoff, regime,
calendar, position, budget and quantity rejection counts were not persisted.

## Output semantics and safety

The default independent output directory is `data/research/phase5b-v1/`:

- `diagnostics.json`: evidence status, source SHA-256/size manifest, sample counts,
  model/calibration records, gates, aggregation and integrity checks.
- `diagnostics.md`: portfolio/cost and asset tables, candidate attribution,
  probability summaries, calibration evidence, execution audit and limitations.

The command refuses source/output overlap, mismatched frozen configuration and
nonchronological/overlapping evaluation windows. Source artifacts are hashed before
and after analysis. Changed evidence or code requires a new diagnostic output
version; unchanged reruns leave identical files and modification times intact.
Writes are atomic per output file. An interrupted run can be restarted; no model
or data preparation checkpoint is changed. Input files are processed in 8,192-row,
single-thread Arrow batches; there are no new dependencies. Peak memory has not
been benchmarked.

The original 15 GiB disk and 1.5 GiB available-RAM floors remain enforced before
scientific imports and throughout processing. A safety stop publishes no completed
diagnostics. Missing artifacts are reported explicitly. Null does not mean zero.

Asset return is an executed-PnL contribution divided by the initial shared account
equity, not an independently sized asset backtest. Asset minute-close MTM drawdown
is unavailable. A separately named realized attribution drawdown is calculated
from closed PnL on constant starting capital. Portfolio MTM drawdown is checked
against the saved equity curves. Profit factor is undefined without losing trades.

Probability summaries are conditional on accepted opportunities. Calibration-fitted
metrics use calibrator fitting rows; they are not independent validation scores.
Diagnostics never search thresholds, recalibrate, retrain or retrospectively select
a policy. Cost scenarios can change sizing and trade counts, so their differences
are not fixed-trade fee adjustments. Stored bootstrap intervals are descriptive.

## Measured diagnostic run and validation

The full diagnostic command completed after the user freed memory in other
applications. Earlier attempts stopped at the unchanged RAM gate; they did not
publish completed outputs. The final run found all 350 analytical input files and
no missing expected artifacts. All 183 snapshot-part checksums, three prepared
fingerprints, three frozen-choice locks, 36 recorded model boundary checks, 27
trade-ledger reconciliations and 27 equity-curve reconciliations passed. Streaming
checks found no backward, duplicate-adjacent or non-minute timestamps, no
bars/features misalignment and no complete features inside the first 63 bars after
a gap. These checks do not prove historical decision-time availability or verify
the original execution code version.

Additional measured observations:

- 2025 XGB evaluation recorded zero accepted opportunities for every asset despite
  432,848 complete-feature rows. Rejection-gate attribution remains unavailable.
- The 2026 XGB baseline recorded 15 accepted opportunities, all XAUUSD longs,
  with accepted probabilities 0.450384–0.452837 at the frozen 0.45 threshold.
  Cost1 executed 12 trades. This is conditional accepted-signal evidence, not a
  full probability distribution or a reason to adjust the threshold.
- Existing quality reports count 16,974 EURUSD, 27,137 XAUUSD and 69,446 WTI missing
  open-session minutes using the exposed calendar. Historical calendar uncertainty
  remains unresolved.
- Recorded unresolved-terminal counts are zero. The current implementation's
  dataset-end gap/exhaustion ordering remains an explicit audit limitation rather
  than a claim that all terminal exits were independently verified.

The relevant regression suite completed with **56 passed, 71 warnings, no skips**:
`test_research_diagnostics.py`, `test_research_safety.py`, `test_research.py`,
`test_phase4a.py` and `test_multiasset.py`. The warnings are existing NumPy generic
timedelta deprecations in research execution/tests; trading semantics were not
changed to silence them. Ruff passed for all research modules and the three
research test files. No new dependency was installed.

A second complete diagnostic run verified byte-identical outputs and unchanged
output modification times. Both full runs rehashed all analytical source inputs
before and after processing and found no changes. Incidental historical status,
progress logs and old test directories are not analytical inputs to the manifest.

## Run in PowerShell

From the repository root:

```powershell
.\.venv-research\Scripts\python.exe -m trading.research.runner diagnostics --config config/phase5-research.json --diagnostics-output data/research/phase5b-v1
```

Relevant regression commands (resource check precedes scientific test imports):

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:OMP_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'
.\.venv-research\Scripts\python.exe -c "import json; from pathlib import Path; from trading.research.safety import resources; resources(json.loads(Path('config/phase5-research.json').read_text()))"
if ($LASTEXITCODE -ne 0) { throw 'Research resource gate did not pass' }
$phase5bTestRoot = Join-Path 'data\research' ('phase5b-tests-' + [guid]::NewGuid().ToString('N'))
.\.venv-research\Scripts\python.exe -m pytest tests/test_research_diagnostics.py tests/test_research_safety.py tests/test_research.py tests/test_phase4a.py tests/test_multiasset.py -q -p no:cacheprovider --basetemp $phase5bTestRoot
.\.venv\Scripts\ruff.exe check src/trading/research tests/test_research_diagnostics.py tests/test_research.py tests/test_research_safety.py --no-cache
```

## Next research iteration

Use only the predefined training, calibration and tuning slices in development
windows to investigate inactivity, calibration drift and execution assumptions.
Persist a gate funnel, all class probabilities and labels, row identities and
code/data hashes. Check historical calendars, reference-price semantics, costs,
terminal exits and label dependence before changing a strategy. Pre-register any
new choices in a separate experiment version. Keep the 2026 holdout untouched;
its sparse positive result is diagnostic evidence, not a threshold or model target.
