# STRAT-2D memory optimization and resource profiling v1

**Outcome: optimization and synthetic equivalence PASS; optimized historical validation BLOCKED by the unchanged system-RAM guard.**

No threshold was lowered. No user process was terminated. Windows services, registry, pagefile and WSL settings were not changed. Docker was not started; pre-existing Docker/WSL processes were only inventoried.

## Measurement protocol

A frozen copy of all eight pre-optimization STRAT-2D modules plus their four fingerprinted dependencies was preserved before editing. Fresh Python 3.12 processes run the original package from that snapshot or the optimized package, against the same installed NumPy/Pandas/Arrow versions and the same immutable 6,144-row synthetic preparation. The historical scope is only the already prepared EURUSD Jan-Feb 2022 dataset. No model parameters, feature scales, seeds, calendar rules, costs, thresholds, splits, batch sizes or update order were changed for the comparison.

The synthetic fixture builder performs preparation only and uses the existing approved preparation resource policy. Every model, simulator, checkpoint/guard benchmark and historical training pipeline uses the ordinary **1.5 GiB available physical RAM / 15 GiB free disk** admission and runtime guards. No training experiment used the preparation-only exception.

RSS and peak RSS use Windows GetProcessMemoryInfo current working set and OS high-water working set. Private commit is reported separately. Available physical RAM uses GlobalMemoryStatusEx and includes the whole machine, not just Python. No tracemalloc or large profiling dependency was installed. The profiling sampler itself caches its ctypes types.

Baseline component measurements have three fresh-process repetitions; optimized component measurements have two. The full synthetic pipeline has two completed repetitions per condition; its third baseline repetition stopped safely. Adjacent baseline/optimized pairs were interleaved where possible. This is not a quiescent laboratory: Windows/background-process activity was not controlled. RSS ranges show repeatability; available RAM and elapsed time cannot be attributed solely to the code change.

## Before/after memory comparison

Values below are medians of completed runs in **MiB**. The checkpoint benchmark writes 20 immutable transactions with 1,024 synthetic exclusion intervals each and restores the entire chain three times. The online-model benchmark fits 10,000 ordered synthetic rows and calibrates/infers 1,000 more; portfolio simulation processes 3,000 synthetic bars.

| Workload | Baseline peak RSS | Optimized peak RSS | Change | Completed repetitions (B/O) |
| --- | ---: | ---: | ---: | --- |
| Checkpoint write + restore | 26.10 | 24.90 | -1.20 | 3/2 |
| Full prepared-data startup | 101.35 | 76.75 | -24.60 | 3/2 |
| Incremental fitting/calibration/inference | 31.45 | 30.21 | -1.24 | 3/2 |
| 1,000 resource-guard calls | 36.96 | 22.40 | -14.56 | 3/2 |
| Complete 6,144-row synthetic pipeline | 112.26 | 84.35 | -27.90 | 2/2 |
| Portfolio simulation | 76.26 | 75.97 | -0.29 | 3/2 |

The full synthetic pipeline saves **27.90 MiB (24.9%)** at peak. Private commit decreases from a median **85.75 MiB to 60.70 MiB**. Startup saves **24.60 MiB**. Model-only and portfolio-only savings are small; these components were already bounded. Their NumPy/Pandas import footprint dominates their working data. Do not equate the synthetic 84.35 MiB peak with an optimized historical measurement.

Completed-run peak RSS ranges:

| Workload | Baseline range MiB | Optimized range MiB |
| --- | --- | --- |
| Checkpoint write + restore | 25.96 to 26.27 | 24.81 to 24.99 |
| Full prepared-data startup | 101.06 to 101.36 | 76.63 to 76.87 |
| Incremental fitting/calibration/inference | 31.41 to 31.46 | 30.09 to 30.32 |
| 1,000 resource-guard calls | 36.95 to 37.07 | 21.93 to 22.87 |
| Complete 6,144-row synthetic pipeline | 112.23 to 112.29 | 84.23 to 84.47 |
| Portfolio simulation | 76.02 to 76.30 | 75.92 to 76.02 |

Raw per-stage RSS, OS peak, private commit, available RAM, module-load flags, elapsed times and ctypes-cache counts are retained in the evidence JSON files. Guard samples also record phase-specific maxima. Checkpoint stage high-water counters are cumulative within the process; the after-restore current RSS is distinct from that cumulative peak. The data does not establish a robust checkpoint speed improvement; elapsed times vary with background work and file I/O.

Minimum sampled available system RAM during completed full pipelines:

| Workload | Run 1 GiB | Run 2 GiB |
| --- | ---: | ---: |
| Baseline synthetic | 1.696 | 1.695 |
| Optimized synthetic | 1.631 | 1.566 |
| Baseline historical | 1.578 | Not repeated |

These are runtime guard-sample minima, not continuous OS-wide minima. The lower headroom in optimized runs reflects changing background conditions despite lower process RSS. A baseline synthetic attempt later stopped at the guard's 1.48 GiB observation, and optimized historical admission stopped at 1.42 GiB. These failures are excluded from completed-run memory medians.

## Findings and implementation

1. **Removed the prepared-reader import dependency on the feature/SQLAlchemy stack.** A new standard-library STRAT-2C verifier checks the v1 checkpoint envelope, manifest identity, every receipt/file hash, predecessor chain and completion state. Scientific UTC/OHLC checks are lazy and local to the bounded reader. SQLAlchemy is present in baseline startup profiles and absent in optimized ones; SciPy/XGBoost are not needed. Source files are checksum-read once instead of twice per invocation. Integrity verification is not skipped on restart.

2. **Fixed retained ctypes pointer-type growth.** The original Monitor called two helpers that declared a fresh structure class on each call. ctypes caches POINTER types with strong references. Three baseline 1,000-call runs each ended at **2,021 pointer-cache entries**; both optimized runs stayed at **22**. The historical baseline ended at **3,773**. Cached Windows structures and API signatures now provide current/peak RSS in one native probe per guard. The system-RAM sample used for the threshold is also the sample recorded in metrics.

3. **Reduced bounded frame copies and lifetimes.** Project only required OHLC, selected predictor/ATR and label/end columns. Preserve float32 feature batches, then use the same float64 conversion inside each model vector as before. Avoid Series construction through iterrows, cache column arrays, create OHLC mappings only for simulation, and release feature views/Arrow-frame references and diagnostic event buffers after each committed batch. No predictions, trades or equity history accumulate globally.

4. **Compact deterministic model constants.** Fixed scales and the 3-class basis are cached per model object. The existing SGD expressions, calibration expressions, float64 weights, class order, seed metadata and update order are unchanged. No quantization, sampling substitution or numerical-tolerance equivalence was used.

5. **Stream checkpoint serialization/hashing.** JSONEncoder.iterencode avoids constructing a complete canonical JSON string plus encoded copy for larger states. Sorted keys, float representations, ASCII escaping, separators and trailing newline are preserved. Checkpoints and ledgers remain atomically published immutable transactions. Creation/restoration memory stays bounded; previous transaction states are validated one at a time.

6. **Added reproducible profiling and recovery commands.** The new tool creates exclusive report versions automatically. A fixed --output path lets a later invocation resume the same journal while writing a new profile report, rather than overwriting failure evidence. Historical scope, resource guards and code/data identity checks remain in the production pipeline.

Files changed for this task:

- `src/trading/research/strat2d/pipeline.py`: reader projection, import decoupling, row/array lifetimes and efficient monitoring.
- `src/trading/research/strat2d/prepared.py`: new sealed-input verifier and bounded UTC/OHLC checks.
- `src/trading/research/strat2d/runtime.py`: cached Windows probes.
- `src/trading/research/strat2d/model.py`: cached fixed arrays.
- `src/trading/research/strat2d/journal.py`: exact streamed canonical serialization/hashing.
- `tests/test_strat2d.py`: adapt the peak-RSS injection to the single cached probe API.
- `tests/test_strat2d_memory.py`: 10 new memory/serialization/import regressions.
- `tools/strat2d_memory_profile.py`: fresh-process baseline/optimized profiling and optional interruption hook.
- `config/strat2d-eurusd-2022-memory-validation-v1.json`: separate opt-in, stricter RSS profile.
- This report and new versioned profiling/evidence outputs. Existing reports/configurations were not overwritten.

## Numerical, accounting and recovery validation

**52 targeted tests passed**: all 42 existing STRAT-2D cases plus 10 memory-specific cases. These cover chronological fits, boundary purging, future-label isolation, calibration, next-bar costs/accounting, pending/open-position recovery, duplicate prevention, corrupt checkpoints, before/after-publication interruptions, and repeated resume. Native-probe tests make 1,000 calls and assert no pointer-type accumulation. New import tests prove the verifier loads no NumPy/Pandas/Arrow/SQLAlchemy/SciPy/XGBoost stack.

Independent baseline-versus-optimized checks passed for both completed repetitions:

- Exact model state hash after 10,000 updates plus calibration/inference.
- Exact portfolio state hash after 3,000 forced-signal synthetic bars, including fills and costs.
- Exact checkpoint state and all checkpoint artifact hashes in the fixed-format serialization benchmark.
- Exact final full-pipeline/model/portfolio logical hashes.
- **112 state/event files per synthetic pipeline pair** (56 transactions) are byte-for-byte identical; two pairs were checked.

Scientific transaction state/event bytes do not require normalization or tolerances. Full-pipeline manifests/receipts intentionally bind different code fingerprints and therefore differ; that is expected metadata provenance, not a numerical difference. No result was tuned against test observations.

Ruff lint, Ruff format --check, git diff --check and profiler --help checks passed. Known NumPy timedelta deprecations remain. Computational tests/benchmarks were not repeated after the resource stop; subsequent work was limited to static checks, read-only integrity inspection and reporting.

## Historical baseline and blocked optimized validation

An admitted **pre-optimization historical baseline** completed the entire authorized two-month pipeline under the original code snapshot and unchanged 1.5 GiB guard:

- 58,683 prepared EURUSD rows scanned per chronological pass.
- **12,212 training rows**, **13,915 calibration rows**, **25,446 labeled test predictions**.
- **145.43 MiB measured peak RSS**, approximately **151.0 seconds** elapsed.
- **240 immutable transactions**, generation 239, complete=true.
- Zero trade fills at the unchanged signal threshold; cash/equity remained 10,000. Historical trade execution is therefore not exercised by this baseline; forced-signal synthetic tests cover it. No profitability claim follows.

Two guard stops were retained rather than retried under pressure:

| Run | Outcome | Preserved progress |
| --- | --- | --- |
| Third baseline synthetic pipeline | Available RAM **1.48 GiB**, below 1.5 GiB | Immutable generation 14; phase=train; no checkpoint reset |
| First optimized historical attempt | Available RAM **1.42 GiB**, below 1.5 GiB | Stopped at bootstrap before model fitting; no historical output directory created |

**Optimized historical training/backtest peak RSS is still unknown.** The 24.64 MiB high-water observation in the rejected attempt belongs only to the bootstrap process, not training. Historical optimized interruption/resume, independent replay and baseline numerical comparison remain **not executed**. Their hook is implemented, but it is not presented as a successful historical test.

## Resource policy decision

**Keep the 1.5 GiB minimum unchanged.** The measurements demonstrate a much smaller bounded process footprint, and the 1.42 GiB bootstrap rejection shows that system admission can be restrictive even before scientific imports. Neither fact proves safe operation under lower headroom while other Windows workloads vary. No low-memory pressure/paging study was performed, and optimized historical measurements are absent. A single admitted historical baseline is not sufficient evidence for a lower threshold.

The explicit opt-in config is `config/strat2d-eurusd-2022-memory-validation-v1.json`. It changes **only version, output directory and max_worker_ram_gib=0.25 (256 MiB)** relative to the original engineering config. It retains **1.5 GiB available RAM, 15 GiB free disk**, offline mode, one worker, identical model/portfolio/scientific parameters and 1024-row batches. This is a tighter process ceiling, not a lower system-RAM exception. Its syntax/scope/limits validate, but historical execution under that config has not been admitted yet.

The approved preparation-only 1.25 GiB setting is unchanged and is never extended to model training/backtesting. Ordinary experiments retain their original policy and configuration. Any future lower-floor profile needs separate repeated, monitored low-headroom evidence, strict model/batch/scope restrictions, process RSS/private-commit limits and fail-closed admission. The successful synthetic measurements alone do not provide that evidence.

## Windows read-only diagnostics

Initial available physical RAM was **1.57 GiB**, with **28.97 GiB free disk**. At final reporting it was **1.46 GiB**, with **28.98 GiB free disk**. These point-in-time figures do not track Python RSS alone. The profiling processes were launched sequentially; no model workloads were run concurrently to inflate memory demand.

Largest processes in the initial snapshot:

| Process | PID | Working set MiB | Private commit MiB |
| --- | ---: | ---: | ---: |
| vmmemWSL | 15292 | 372.9 | 3877.5 |
| Memory Compression | 3632 | 273.4 | 7.0 |
| MsMpEng | 6844 | 227.0 | 462.5 |
| codex | 16452 | 188.9 | 215.2 |
| explorer | 20536 | 179.6 | 742.2 |
| codex | 23024 | 150.8 | 204.6 |
| TextInputHost | 12616 | 122.4 | 127.6 |
| com.docker.backend | 3772 | 103.1 | 152.8 |
| DDPM.Subagent.User | 8124 | 94.9 | 119.8 |
| dwm | 2452 | 75.9 | 8751.6 |
| powershell | 4672 | 68.7 | 61.5 |
| DDPM.Subagent | 15336 | 68.2 | 79.6 |

An existing WSL VM had about **372.9 MiB resident working set**; its private commit was about **3877.5 MiB**. Existing Docker-named processes summed to about **233.9 MiB working set**, with shared-page double counting possible. These processes were already running and were not started, stopped or reconfigured by this task.

Private commit is committed virtual memory, not currently resident physical RAM or an amount that can simply be reclaimed. RSS includes shared DLL pages, so summing process working sets also does not equal total system consumption. In particular, the WSL private-commit figure must not be described as that much resident RAM. Chrome and VS Code Code.exe were not observed in the snapshots. Codex-related processes are distinct from VS Code. Full before/after process-name/PID/counter snapshots are retained without command lines or credentials.

## Checkpoint compatibility and explicit migration plan

The canonical JSON and logical-state schema remain v1. The optimized journal successfully read and hash-verified an original checkpoint. New pipeline runs nevertheless bind the new code fingerprint and deliberately reject old-code pipeline manifests. No existing manifest or checkpoint was silently rewritten.

Preferred path: use new versioned outputs for optimized research. If an old incomplete journal must be continued, retain its verified original code/environment or use a separately approved migration tool. A migration must: (1) verify the entire old chain under the archived implementation, (2) preserve it byte-for-byte, (3) create a **new** versioned manifest referencing old receipt/code/dataset fingerprints and the unchanged logical-state hash, (4) restore only the verified cursor/model/portfolio state, and (5) compare a resumed replay against an independent uninterrupted run before accepting it. Such a migration was documented, not implemented or executed here.

## Remaining bottlenecks and readiness

- Pandas/Arrow and native-library startup still account for most of the approximately 77 MiB optimized startup footprint. Replacing their timestamp/column conversion path is a separate compatibility-sensitive change.
- Full receipt/file verification is intentionally retained on every new process/resume. Disk I/O and small-file/fsync costs remain; skipping integrity checks is not a safe memory optimization.
- Completed-run recovery still imports scientific libraries before returning. A future metadata-only completed-run path could reduce recovery startup further, but requires its own compatibility tests.
- Background system memory fluctuated enough to reject work despite repeatable Python RSS. The task did not free memory by terminating processes or changing system settings.
- Historical calendar verification, unexpected gaps and broader model support remain the prior STRAT-2D limitations. No full-year backtest or 2026 holdout was touched.

Bounded optimized historical validation may proceed **only when the unchanged guards admit it**. It cannot be claimed safe to start at the observed 1.42 to 1.46 GiB headroom. The next phase is to complete the optimized two-month engineering profile and historical crash/replay checks after stable headroom is restored, before broader development-period validation.

## Exact next command

From the STRAT-2C worktree, after the ordinary resource gates pass:

```powershell
.\.venv\Scripts\python.exe -B -m tools.strat2d_memory_profile --condition optimized --mode pipeline --historical --memory-config --output data/research/strat2d-eurusd-2022-memory-validation-v1
```

This uses the explicit 256 MiB process profile, processes the complete two-month window in bounded batches, and writes a new unused profiling report version automatically. Repeat the same command after a resource stop to resume that same journal; do not delete/reset its transactions. Existing failed measurement files are preserved. Code/config/data mismatch still fails closed. No manual command is needed for individual chunks.

## Final verification status

| Requirement | Status |
| --- | --- |
| Startup and synthetic model/simulator/checkpoint profiling | PASS, repeated fresh processes |
| Import/type-cache/frame/serialization optimizations | PASS |
| Exact synthetic model, accounting, state and event equivalence | PASS |
| Synthetic corruption/interruption/resume and duplicate prevention | PASS |
| Baseline historical peak measurement | PASS, 145.43 MiB |
| Optimized historical peak and historical replay | BLOCKED, not executed |
| Ordinary RAM/disk guards preserved; lower threshold not enabled | PASS |
| Windows read-only diagnostics and no process/system changes | PASS |
| STRAT-2C preservation | PASS, 47 hashes and mtimes unchanged |
| Tests and lint/format/whitespace checks | PASS, 52 targeted tests |

Evidence: `data/research/strat2d-memory-optimization-evidence-v1/summary.json`, raw stage profiles, process snapshots and the frozen baseline source. Detailed test evidence: `.tmp/strat2d-memory-v1/tests-first.xml`. All benchmark outputs and the safely stopped synthetic journal remain preserved. No commit, push, source-data modification, broker connection, live order, Docker startup or unrelated service change occurred.
