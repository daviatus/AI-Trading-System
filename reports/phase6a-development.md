# Phase 6A: advanced multi-asset research foundation

Date: 2026-10-23. Scope: offline research and simulation only.

Phase 6A adds an isolated, bounded research implementation in
`src/trading/research/phase6/`, configured by
`config/phase6a-research-v1.json`, with separate outputs under
`data/research/phase6a-v1/`. Existing Phase 5/5B implementations, frozen
configurations and analytical results are retained. This report describes
capabilities and measured software validation; it does not establish a profitable
model or authorize trading.

## Architecture and evidence audit

The existing pipeline is `snapshot -> prepare -> run -> report -> diagnostics`.
Its stable calendar, source metadata, resource gates and historical evidence are
reused without rewriting frozen research behavior. The detailed data/label audit
is in [phase6a-data-audit.md](phase6a-data-audit.md). Historical evidence comes from
[Phase 5 results](../data/research/phase5-v1/results.md),
[Phase 5B diagnostics](../data/research/phase5b-v1/diagnostics.md) and
[Phase 5B implementation notes](phase5b-diagnostics.md).

Confirmed findings from source and stored evidence:

| Area | Existing behavior and evidence |
| --- | --- |
| Data loading | Isolated read-only snapshot queries create checksummed monthly instrument partitions. Subsequent full-instrument concatenation, complete-minute audit grids and outcome arrays create substantial temporary allocations. |
| Features | Causal returns, realized volatility, EMA distances/slopes/spread, Wilder-style RSI/ATR, volume z-score and cyclic time features are supplemented by candle structure, range position, momentum and volatility ratio. State resets after absent/invalid/closed-session bars; the first 63 segment rows are unavailable. Volume reliability is unverified. |
| Labels | Next contiguous open enters two ATR stop/target paths. Positive cost-adjusted outcomes that exceed the other side become long/short; ties and nonpositive outcomes become neutral; unavailable paths are `-1`. Same-bar ambiguity takes the stop. These are execution-path classes, not simply future price direction. |
| Models | Independent per-asset, three-class XGBoost histogram models use depths 2/3, 80/100 rounds and learning rates 0.06/0.05. Training is bounded at 80,000 rows, calibration at 30,000, with seed 20261022 and one thread. No early stopping or saved reliability bins exist in Phase 5. |
| Trend baseline | EMA spread sign plus RSI above 55/below 45 produces fixed directional score 0.55. These fixed scores are not empirically calibrated probabilities. |
| Calibration/splits | Temperature and class-bias calibration uses a disjoint chronological slice. Fixed-horizon label purging exists at training/calibration/tuning boundaries; explicit embargo and test-end purging are absent. |
| Signal gates | Directional probability thresholds are 0.45, 0.55 and 0.65, with all/exclude-abnormal regime variants, schedule/next-entry requirements and an expected-payoff gate for selected candidates. The recorded XGBoost evaluation baseline bypasses the economic gate. |
| Selection | Score is tuning net return minus half drawdown; fewer than 30 trades or unresolved exits disqualify a candidate, and nonpositive scores select cash. |
| Execution | The previous portfolio already enforces one shared position, risk sizing and scenario costs. Explicit instrument margin caps, maintenance closeout scenarios and complete rejection streams were missing. |

The 216 recorded candidate evaluations comprise three folds, three assets, two
horizons, two models, three thresholds and two regime filters. **206 failed the
30-trade minimum; the remaining ten had nonpositive selection scores.** All nine
selected decisions were inactive. Full historical probability streams and rejected
decision traces were not saved; accepted opportunity probabilities cannot recover
the missing distribution or gate counts. Phase 6 never presents reconstructed
historical scores as observed evidence.

The recorded 2024 XGBoost baseline lost 10.31% over 147 trades; the 2025 baseline
made no trades. The inspected 2026 baseline gained 0.97% over only 12 trades.
Those observations show instability and insufficient evidence, not a reliable
edge. Exposed calendars do not prove historical holidays, and midpoint/reference
price identity, publication/revision timing, historical bid/ask and financing
remain unverified.

Hypotheses requiring new development experiments include calibration drift,
restrictive economic gates, horizon/label mismatch, redundant features and
instrument opportunity differences. Their contribution to inactivity has not
been measured by Phase 5B. A weaker standalone asset might improve portfolio
opportunities; this is also a hypothesis requiring shared-account replay.

## Implementation map

| Module | Responsibilities |
| --- | --- |
| `features.py` | Bounded OHLC validation, session-aware missingness audit, causal compact features and explicit decision timestamps. |
| `labels.py` | Forward, volatility-normalized and barrier labels; actual outcome information-use times; purged chronological splits and embargo. |
| `models.py` | Per-asset trend, regularized logistic, XGBoost and conservative shallow-tree models; calibration; deterministic artifacts and probability metrics. |
| `signals.py` | Decision-time signal gates, training-only conditional payoff profile, regime attribution and exact first rejection reasons. |
| `execution.py` | One-position portfolio replay, costs, risk/margin constraints, adverse execution scenarios, instrument/year/regime metrics and descriptive uncertainty. |
| `instrumentation.py` | Deterministic bounded JSONL streams; detached observer records and diagnostic failure/completeness accounting. |
| `controller.py` | Standard-library bootstrap, immutable manifests, bounded queue, locks, resource monitoring, checkpoints and failure isolation. |
| `runner.py` | Audit, registration, synthetic smoke, bounded run and worker orchestration; selection freeze before test predictions. |

The principal model interface is
`fit_model(features, labels, masks, spec, config, *, asset, label_end=None)`.
It returns a `ModelBundle` with `.predict(features, mask=None)`, `.save(directory)`
and `.metadata`; `load_model(directory)` restores it. Predictions retain the
full input index, with three probabilities in class order neutral/long/short and
NaNs outside eligible feature rows. Selected feature names are explicit. The
linear baseline fits its scaling on training rows only; missing unused indicators
do not invalidate an otherwise valid selected-feature prediction.

No new deep-learning dependency or unrestricted parameter optimizer is introduced.
The conservative tree alternative is one regularized XGBoost histogram round at
depth at most three; it tests a low-capacity nonlinear hypothesis while reusing
the installed research dependency.

## Chronology, labels and calibration

Raw candles use open timestamps. Both features and labels use
`decision_time = opened_at + 1 minute`, when the preceding candle is assumed
complete. Entry requires the next contiguous observed candle open at that same
decision time. No price is interpolated across missing data or market closure.
Duplicate, backward, naive/local/nonminute and malformed OHLC input is rejected.
Features reset across absent or excluded session bars. Optional volume indicators
require an explicit reliability assertion; they are excluded by default.

The label framework documents three controlled definitions:

- Forward: next open to horizon close, net of explicit roundtrip scenario cost,
  with a configurable absolute return threshold.
- Volatility: forward return with an additional causal per-minute volatility
  threshold scaled by the square root of the horizon.
- Barrier: long/short stop and target paths, adverse-first ambiguous bars, worse
  adverse opening fills and conservative target fills.

Unavailable outcomes are `-1`; neutral, long and short are `0`, `1` and `2`.
`label_end` is the latest candle close consumed by either directional outcome.
All supervised windows require label end strictly before their right boundary.
Calibration, tuning and test each omit their first 60 minutes by default.
Overlapping outcomes inside a window still reduce the effective sample size.

XGBoost early stopping uses a chronological tail of the training period only.
Fitting rows whose outcomes reach that tail, plus the configured embargo, are
purged. Calibration is a later independent slice for regularized temperature and
class biases; it never supplies early-stopping evaluations. Tuning chooses the
threshold and portfolio subset. Test predictions, probability statistics and
trading decisions start in a second sequential pass only after all per-asset
choices and the portfolio policy have been written to `frozen-selection.json`.
Prepared test features/labels may be persisted before that freeze; they do not
enter fitting, payoff-profile estimation or selection.

Signal eligibility uses clock boundaries and causal features, **never future
label validity**. Future gaps or unavailable labels may exclude predictive
metrics, but cannot cancel an otherwise valid decision retrospectively. The
execution engine attributes a missing observed next entry separately.

Metadata records feature selection, version, seed, package versions, row bounds,
chronological extrema, early-stopping purges, calibration parameters, class
counts, log loss, multiclass Brier score and reliability bins. Fitted calibration
scores describe the fitting slice and are not independent validation. The fixed
trend baseline keeps its original scores unless calibration is explicitly enabled.

The native XGBoost predictor explicitly limits prediction to the saved best
iteration, as required by the [official prediction documentation](https://xgboost.readthedocs.io/en/stable/prediction.html#early-stopping).
Logistic fitting and regularized calibration use bounded L-BFGS-B optimization;
its convergence status is checked, following the [SciPy optimizer interface](https://docs.scipy.org/doc/scipy/reference/optimize.minimize-lbfgsb.html).

All 2026 candles and outcome information reaching 2026 are excluded. The already
inspected 2024/2025 periods remain reused **development evidence**, including
their chronologically separated test slices. They must not be described as fresh
independent final validation. Final evidence requires new untouched data or
prospective paper trading after the research policy is frozen.

## Diagnostics and deterministic artifacts

Schema v1 JSONL records contain experiment/model/version, instrument, UTC decision
time, window, all three probabilities or an explicit null, threshold, candidate
status, acceptance and first rejection reason. Execution records add position
conflict state, sizing, margin, entry/exit, direction, stop/target, scenario costs,
gross/net PnL, planned/realized risk and exit reason where applicable.

`calibration_id` references the SHA-256 of the asset's `models/<asset>/model.json`;
full calibration and reliability metadata lives in that model artifact. Repeating
large reliability tables on every prediction is avoided. Each stream is bounded
at 128 MiB. Exhaustion, serialization errors or callback failures mark diagnostics
incomplete and invalidate research acceptance; observer failures cannot change
computed signal gates, position arbitration, fills, equity or ranking.

| Record | Required fields and semantics |
| --- | --- |
| Prediction | `schema_version=1`, `experiment_id`, `model_id`, `model_version`, `asset`, `timestamp` (UTC decision time), `window`, `calibration_id`, `probabilities` (neutral/long/short list or null), `threshold`, `direction` (+1/-1), `candidate_signal`, `accepted`, `reason`, `expected_net_return` (training-label proxy), `regime`. |
| Execution decision | `event=execution_decision`, `asset`/`instrument`, `timestamp`, `model_id`, `model_version`, `research_window`, `probability`, `threshold`, `existing_position`, `accepted`, `reason`/`rejection_reason`; causal sizing, planned risk and margin fields once those gates are reached. |
| Execution outcome | `event=execution_outcome`, entry/exit times and prices, direction, quantity/units, stop/target, gross/net PnL, separate cost components, equity, planned risk, realized loss, available/actual leverage, margin, regime and exit/censorship flags. |

Gate reasons use first-failure attribution in documented code order; a row may
violate additional gates that are not reached. Stream summaries include event and
gate counts, candidate/accepted funnels, class probability means and ten-bin
distributions. Records stream directly to `.partial` files and are renamed on
successful completion; interrupted/incomplete evidence is preserved.

Models use deterministic JSON/UBJ artifacts with predictor checksums. Attempt
outputs include source/quality evidence, model and label artifacts, frozen
selection, opportunity rows, scenario trade/equity files, `diagnostics.json` and
an artifact hash manifest. Unavailable historical data remains null or explicitly
unavailable. No complete historical probability stream is fabricated.

## Instrument and portfolio comparison

Every configured experiment fits EURUSD, XAUUSD and WTI separately. It reports
completed trades, unresolved positions, net return, profit factor, maximum equity
drawdown, win rate, average wins/losses, mean net PnL, costs, holding time,
observed-day activity, zero-trade days and instrument/year/regime attribution.
Per-instrument closed-PnL contribution is distinguished from a standalone replay.

The engine replays all seven subsets on the same chronological timeline:
EURUSD; XAUUSD; WTI; EURUSD+XAUUSD; EURUSD+WTI; XAUUSD+WTI; and all three. Each
subset uses one account and one open position. Independent asset returns are
never summed. Overlaps are resolved deterministically by time, score, confidence
and stable instrument/direction/model tie breakers.

Per-asset thresholds and the portfolio subset are selected from tuning replay
only. At least 30 tuning trades, resolved positions and a positive
`net_return - 0.5 * maximum_drawdown` score are required; otherwise cash is valid.
Fixed-threshold individual evaluation replays may still be produced for inactive
policies as descriptive diagnostics. They are not activated selections and do not
justify changing asset priority after observing evaluation results.

For forward/volatility labels, the training conditional payoff score refers to
horizon-close label outcomes while execution uses configured stop/target paths.
It is an expected-score proxy, **not verified executable expectancy**. Realized
cost-adjusted tuning replays determine eligibility. Neither lowering thresholds
to force activity nor using leverage to conceal a losing base strategy is valid.

## Cost, risk and leverage scenarios

Spread, roundtrip commission, slippage per side and financing per UTC rollover are
explicit scenario debits. Default spread/slippage/commission inputs reuse the
Phase 5 scenarios, with 1 bp/day financing and cost multipliers 1, 2 and 3.
Historical spreads and fills are not invented. Entry/exit precision is rounded
conservatively and contract quantity is stepped down. The cTrader adapter treats
volume fields as hundredths of measurement units and retains lot size metadata
without applying it twice.

Supported leverage scenarios are 1:1, 1:2, 1:5, 1:10, 1:20 and 1:30. Selection
uses 1:1. The assumed instrument caps are EURUSD 30, XAUUSD 20 and WTI 10; these
are unverified simulation settings, not broker permissions. Actual available
leverage is the minimum of scenario availability and the instrument cap.

Sizing uses equity and stop distance plus reserved scenario costs. Default planned
risk is 0.25%; the hard ceiling is 0.5% per trade. One position is permitted
portfolio-wide. Margin, contract minimum/maximum/step, leverage and risk constraints
are checked before entry; insufficient margin rejects the intended risk-sized
trade rather than maximizing exposure. Actual leverage used is reported separately
from leverage availability.

Ambiguous bars follow an adverse-first reference path. Stops and simulated margin
closeout use worse observed opening fills when appropriate. A genuine observed
post-gap open can produce a conservative gap stop and losses beyond planned risk.
Dataset exhaustion produces an unresolved marked position, never a fabricated
completed fill. Maintenance closeout uses an explicit constant-margin scenario;
broker tiers, intraminute sequencing, dynamic requirements and liquidation fees
remain unverified. Drawdown includes conservative adverse OHLC marks, not only
closing equity, but is not a tick-verified equity path.

## Statistical interpretation and acceptance

Reports compare cash, fixed trend and learned models. Descriptive Wilson win-rate
intervals and deterministic five-observed-day block bootstrap intervals expose
trade-count/PnL/PF uncertainty where sample requirements permit. Very small
samples, unresolved outcomes or insufficient wins/losses yield explicit warnings
or unavailable intervals. Overlapping labels, correlated assets and longer serial
dependence remain limitations. Bootstrap intervals condition on executed trades
and do not adjust for model/threshold/subset selection.

The registered matrix, threshold trials and seven subset trials are recorded for
multiple-testing awareness. A positive tuning score is an eligibility rule, not
a claim that a model meets the longer-term research targets. Those targets remain
PF above 1.60, drawdown below 10%, positive net return and at least 100 completed
trades per **full** evaluation year. The 100-trade criterion is not scaled down for
a partial year; partial-year activity status is null. Approximately 400-800 annual
portfolio trades and 2-4 per trading day are future research objectives; no trades
are forced and zero-trade days are allowed.

Development runs remain unaccepted as independent deployment evidence even if a
single period exceeds a target. No profitable-model conclusion is drawn from the
software checks or synthetic smoke exercise.

## Experiment control and resource safety

The default matrix registers 16 jobs: two development folds, two label definitions
(15-minute barrier and 60-minute volatility) and four model families. Assets are
processed sequentially within a job. Registration creates a bounded queue and
deterministic experiment IDs; it does not start training. A normal `run` starts
at most one pending job. Running the full batch requires explicit user initiation
with both `--full-batch` and an appropriate limit.

| Default limit | Value |
| --- | ---: |
| Free disk floor, unchanged | 15 GiB |
| Available physical RAM floor, unchanged | 1.5 GiB |
| Concurrent training jobs/native threads | 1 / 1 |
| Worker resident RAM budget | 1 GiB |
| Worker wall-time budget | 1,800 seconds |
| Raw rows loaded per asset | 400,000 |
| Training/calibration samples | 40,000 / 10,000 |
| Arrow read batch | 8,192 rows |
| Prediction batch | 8,192 rows |
| Per-stream diagnostic budget | 128 MiB |

Only registered, checksummed pre-2026 monthly snapshot partitions are read.
Source paths cannot traverse outside the source directory. Native thread settings
and standard-library resource checks precede scientific imports. The controller
monitors free disk, host RAM, child resident working set and timeout; exceeding a
limit stops the child and preserves completed/partial evidence. Limits supplement
each other and cannot be disabled to force training. A 400,000-row bound is not a
promise that every proposed window will fit the current host load.

An OS controller lock protects checkpoint ownership. A separate global worker
lock under `data/research/phase6-control-v1/` prevents simultaneous training across
output versions and direct worker invocations; the OS releases it after a crash.
Configuration, code, source and queue manifests are immutable. Changed code/config
requires a new versioned output directory; old evidence is not overwritten.

Checkpoints track running, rejected, complete, failed, timeout and safety-stopped
attempts. Interrupted/recoverable jobs receive a new numbered attempt while the
prior attempt remains intact. Completed rejections, failures and timeouts remain
final evidence for that version; retrying them requires a new research version.
Sanitized failure records omit exception payloads and retain the failure type,
resource/rejection reason and checkpoint state. No broker or database calls are
made by the Phase 6 execution/controller path.

## Validation status

The final applicable regression suite executed **215 tests: 215 passed, zero
failed, zero errors and zero skipped**, with **313 warnings**, in 13.26 seconds.
It included all eight Phase 6 test files plus existing research, diagnostics,
resource safety, Phase 4A and multiasset tests. This is the relevant regression
suite, not a claim that every repository integration test was run. The machine
record is `data/research/phase6a-v1/tests-regression-v1.xml`.

Warnings are NumPy/pandas timedelta deprecations, including reused Phase 5 code,
and one test-only runpy warning caused by exercising the bootstrap after the
module was imported during collection. No execution assumptions were changed
merely to silence warnings. The earlier component run passed 106 tests; its
predecessor recorded 94 passes, two failures and five missing-parent-directory
setup errors. Those issues were corrected and the final suite passed.

Ruff check passed for every research module and the Phase 6/research test files.
Ruff format check passed for all 18 Phase 6 Python/test files. Bytecode compilation
of the new package and `git diff --check` passed. No dependency was installed.

Two final synthetic end-to-end runs, `smoke-final-a/` and `smoke-final-b/`, each
produced **67 byte-identical files**, including model artifacts, probabilities,
labels, portfolio trade/equity files, JSON diagnostics and Markdown reports.
`reproducibility.json` records the comparison. They recorded 5,037 prediction
rows, 4,436 threshold candidate signals and zero accepted signals; all streams
were complete and cash remained valid. These are software checks, not market
performance. Fill, PnL and margin behavior are exercised by the execution tests.

One real-data experiment was initiated with the registered default configuration:
`560ce591165e80aef255`, attempt 001 (trend / 15-minute barrier / development-2024).
The host available-RAM measurement fell below the unchanged 1.5 GiB floor
(rounded CLI diagnostic: **1.50 GiB**) and the controller stopped only its child.
The checkpoint is `safety_stopped`; attempt configuration, code manifest,
failure evidence and EURUSD quality audit remain intact. **No historical model
performance result was produced.** The full 16-job batch was not started and
the stopped experiment was not retried.

The new EURUSD audit measured 340,585 raw rows for October 2023 through August
2024, with zero duplicate, malformed or nonminute candles, 1,222 gaps, 4,363
missing expected-open minutes, 138,892 known-closed absent minutes and 20 bars
outside the exposed schedule. These classifications inherit the historical
calendar limitation. New XAUUSD/WTI audits and trading metrics were not reached;
existing Phase 5B observations remain historical evidence, not new measurements.

Tests cover causal features, label ends, chronology/embargo, calibration separation,
artifact determinism, rejection attribution, observer independence, shared position
conflicts, seven subset replays, costs, stops/targets/gaps, sizing, precision,
margin/leverage, adverse equity drawdown, zero trades, checkpoint recovery,
holdout exclusion, immutable evidence and RAM/disk behavior. Existing research
regressions remain part of the final validation requirement.

LIVE trading, broker orders/credentials, production permissions, the main database,
Docker/PostgreSQL, collector processes and checkpoints are outside this change.
Phase 5 and Phase 5B are read-only inputs. All **357 protected-file hashes** matched
before and after the work, including frozen configuration and 2026 research data.
The final `safety-verification.json` records the comparison. No LIVE switch,
broker order, credential, production database, collector, collector checkpoint,
Docker or PostgreSQL change was made. The original dirty working-tree changes
were preserved. The root machine summary is
`data/research/phase6a-v1/diagnostics.json`.

The implementation is ready for controlled experiments; its real-data workload
has not completed on the current host load. Peak-resource benchmarking, complete
instrument/model comparisons, full-year activity evidence and fresh independent
validation remain outstanding. Exact training sample membership is reproducible
from frozen inputs, deterministic masks and sampling rules; only row counts and
extrema are currently exported directly in model metadata. Memory limits and
per-stream limits can leave incomplete attempts, which cannot establish acceptance.

## Exact PowerShell commands

Run from `C:\Users\jager\ai_trader\AI-Trading-System`. The installed research
environment supplies the existing scientific dependencies. The CLI checks RAM and
disk before importing them.

```powershell
Set-Location 'C:\Users\jager\ai_trader\AI-Trading-System'
$env:OMP_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
$env:NUMEXPR_NUM_THREADS = '1'

# Read-only evidence audit; writes separate Phase 6 audit files.
.\.venv-research\Scripts\python.exe -m trading.research.phase6.runner audit --config config/phase6a-research-v1.json

# Register the 16-job queue and immutable manifests; starts no training.
.\.venv-research\Scripts\python.exe -m trading.research.phase6.runner plan --config config/phase6a-research-v1.json

# Synthetic integration exercise. Use a new smoke version to preserve smoke-v1.
.\.venv-research\Scripts\python.exe -m trading.research.phase6.runner smoke --config config/phase6a-research-v1.json --smoke-output data/research/phase6a-v1/smoke-v2

# Safely initiate only the first pending Phase 6B research experiment.
.\.venv-research\Scripts\python.exe -m trading.research.phase6.runner run --config config/phase6a-research-v1.json --limit 1
```

Inspect the resulting checkpoint, worker log, frozen selection and diagnostics
before initiating more work. Resource stops preserve prior attempts; resume uses
the same bounded command after enough RAM is available for imports and the
workload while retaining the 1.5 GiB reserve. The next command creates attempt 002
for the stopped first job. Do not
reduce the resource floors. If registered code/config changed, choose a new
versioned output configuration rather than replacing existing manifests.

The exact validation commands used were:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
.\.venv-research\Scripts\python.exe -c "import json; from pathlib import Path; from trading.research.safety import resources; resources(json.loads(Path('config/phase6a-research-v1.json').read_text()))"
if ($LASTEXITCODE -ne 0) { throw 'Research resource gate did not pass' }
.\.venv-research\Scripts\python.exe -m pytest tests/test_phase6_controller.py tests/test_phase6_controller_resources.py tests/test_phase6_features.py tests/test_phase6_models.py tests/test_phase6_execution.py tests/test_phase6_signals.py tests/test_phase6_runner.py tests/test_phase6_validation.py tests/test_research.py tests/test_research_diagnostics.py tests/test_research_safety.py tests/test_phase4a.py tests/test_multiasset.py -q -p no:cacheprovider --basetemp data/research/phase6a-v1/tests-regression-v1 --junitxml=data/research/phase6a-v1/tests-regression-v1.xml
.\.venv\Scripts\ruff.exe check src/trading/research tests/test_phase6* tests/test_research* --no-cache
.\.venv\Scripts\ruff.exe format --check src/trading/research/phase6 tests/test_phase6* --no-cache
.\.venv-research\Scripts\python.exe -m compileall -q src/trading/research/phase6
```

Use a new `--basetemp` and JUnit filename for future test runs to preserve this
validation evidence. Tests require their output parent directory to exist.

The following command is documented for a later **explicit user-initiated** full
batch; it is not part of setup, smoke or the default run:

```powershell
.\.venv-research\Scripts\python.exe -m trading.research.phase6.runner run --config config/phase6a-research-v1.json --limit 16 --full-batch
```

## Bounded Phase 6B next steps

1. Restore sufficient non-collector RAM headroom, then resume one registered job
   with the safe command. Inspect data quality, source hashes, gate funnel,
   calibration and completeness before evaluating additional candidates.
2. Compare label/horizon behavior and model calibration using only registered
   development slices. Record each attempted model/threshold/subset, including
   cash selections and failures. Do not use 2026 for new choices.
3. Review standalone and shared-account instrument results together. Require
   cross-period stability, sufficient activity and conservative cost sensitivity
   before proposing asset exclusions or priorities.
4. Treat forward-label score/execution mismatch as a controlled research question;
   compare against barrier labels and directly executable tuning outcomes.
5. Verify historical calendars, quote-price semantics, contract quantities,
   spreads/financing and broker-specific margin limits where evidence can be
   obtained. Retain explicit scenario uncertainty where it cannot.
6. Before assessing annual acceptance, pre-register full evaluation years in a
   new output/configuration version with measured, bounded resource requirements.
   The default six-month test slices cannot establish annual activity acceptance.
7. Expand only within the remaining registered budget after reviewing the first
   evidence. Freeze a final policy before collecting genuinely new untouched
   validation data or prospective DEMO paper-trading results.
