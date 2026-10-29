# AI-Trading-System new-conversation handoff — 2026-10-29

## Purpose and verified repository state

Offline research infrastructure for causal EURUSD prediction and simulated execution; no demonstrated independent profitability. Completed phases STRAT-2C–2I. **STRAT-NN-1 is planned, not implemented.**

- Working directory: `C:\Users\jager\ai_trader\AI-Trading-STRAT-2C`.
- Repository root is that worktree. Required branch: `feature/strat-2c-multimonth-checkpoint`.
- Related worktree: `C:\Users\jager\ai_trader\AI-Trading-System`, actually on `feature/3e-ctrader-supervised-worker`. Another worktree is `.worktrees/phase6-diagnostics` on its own fix branch. Do not alter either.
- Remote: `origin`, `https://github.com/daviatus/AI-Trading-System.git`. No upstream existed at audit start; the closure task creates the matching feature-branch upstream with a normal push if verified safe.
- Last commit before this checkpoint: `86d086d92e2226a199e51bfbb690a97b41b87fd0` (`docs: add Phase 4-6 research and recovery reports`). The new checkpoint is the normal commit containing this handoff, with subject `feat(research): complete STRAT-2C to 2I pipeline and audit`. A Git document cannot contain its own commit SHA without circularity. Resolve the exact closure commit and tracking state using the commands below; the closure assistant's final response provides the verified local/remote SHA.

```powershell
git rev-parse --show-toplevel
git branch --show-current
git log -1 --format='%H %s' -- reports/ai-trading-next-chat-handoff-2026-10-29.md
git rev-parse HEAD
git status --short
git branch -vv
git ls-remote --heads origin refs/heads/feature/strat-2c-multimonth-checkpoint
```

Stop on branch/worktree mismatch or unexpected conflicts. No switching, reset, rebase, merge, amend or force push. A later task's commit/push requires its own authorization; the closure authorization is not standing permission for NN changes.

## Environment and exact resource policy

Windows11 Home,8GiB physical RAM, limited available system memory; Python3.12 at `.venv\Scripts\python.exe`. System Python may be3.14 and is not the research environment. Single-threaded numerical/Arrow processing, disk floor15GiB. RSS and system-available RAM are different counters. Native worker PID—not the Windows venv launcher—must be measured.

| Workflow | Configuration / admission |
| --- | --- |
| General training/backtesting |1.50GiB available physical RAM; preserve normal guards in `safety.py` and D pipeline |
| C preparation only |`config/phase6-strat2c-eurusd-2022-jan-feb-validation-v1.json`,1.25GiB; only prepare command authorized by this config |
| D/E/F bounded historical exception |`config/strat2d-eurusd-2022-offline-130-validation-v1.json`,1.30GiB,256MiB worker RSS |
| G frozen research exception |`config/strat2g-eurusd-2022-offline-120-v1.json`,1.20GiB,256MiB RSS |
| H/I engineering diagnostics exception |`config/strat2h-eurusd-2022-offline-100-v1.json`,1,073,741,824 bytes available RAM,268,435,456 bytes worker RSS,1,024-row batches |

Exceptions are exact-purpose/date/model/profile opt-ins, not global defaults or authorization for NN training. Never lower guards to finish a run. Preserve checkpoints on a stop. No process termination outside the tool's own worker/launcher, service/pagefile/registry/WSL changes, Docker or application shutdown is authorized.

The local development-test interpreter is currently at `..\AI-Trading-System\.venv-research\Scripts\python.exe` and Ruff at `..\AI-Trading-System\.venv\Scripts\ruff.exe`; these are read-only tools in another worktree, not files to modify. Verify availability; use this worktree's source via `$env:PYTHONPATH="$pwd\src"`. No new dependency is installed by the closure task.

## Data scope and restrictions

Only historical EURUSD M1 `[2022-01-01T00:00:00Z,2022-03-01T00:00:00Z)` is authorized:58,683 observations. This repeatedly examined period is exploratory, not untouched confirmation. Never open/evaluate2026 holdout, expand dates without later explicit authorization, run a full-year backtest, connect to a broker, run live/paper trading or start collectors/Docker.

`data\research\phase5-v1` is a Windows junction to original data under the related project. It is **read-only** and not tracked. Do not recursively scan it during onboarding or Git work. Do not stage `data/`, raw Parquet, model/checkpoint directories, secrets or virtual environments. Existing root `.gitignore` excludes them; still stage selectively.

Prepared input: `data/research/phase6-strat2c-eurusd-2022-jan-feb-validation-v1/prepared-streams/1d06fff895ee69a12b38/EURUSD`. Job ID `1d06fff895ee69a12b38`,9 chunks, head8 complete. Published chunks/receipts/manifests/checkpoints are immutable. The accompanying technical review and artifact manifest list specific output paths/hashes without requiring traversal of the raw junction. Local artifacts are intentionally not in Git; a new clone needs explicit, verified artifact provisioning before historical use.

## Architecture and important files

1. `research/data.py`, `phase6/controller.py`, `phase6/runner.py`: source registration, scope/resources, command restrictions, preparation entrypoint and sanitized failure evidence.
2. `phase6/streaming.py`, `features.py`, `labels.py`: bounded Arrow preparation, causal rolling features/EMA continuity, forward/barrier labels, month/gap handling.
3. `strat2d/prepared.py`, `pipeline.py`: sealed source integrity, chronological bounded batches, four quality/train/calibration/test passes.
4. `strat2d/model.py`: deterministic incremental three-class softmax and scalar temperature calibration; explicit unsupported status for nonincremental models.
5. `strat2d/portfolio.py`: offline single-position next-bar simulation, sizing/capital checks, spread/slippage/fees, cash/equity/P&L and pending/open-position state. Zero historical fills do not exercise historical execution costs; synthetic forced trades do.
6. `strat2d/journal.py`: canonical SHA-256 state, chained receipts, exclusive writes/fsync/directory-rename commit. Retain orphan staging; reject incompatible code/config/source/environment.
7. `strat2d/runtime.py`, G/H `resources.py`, `tools/strat2d_historical_130_validation.py`: native cached probes, real child PID RSS, external fail-closed monitor and synthetic/real crash harness.
8. `strat2e/audit.py`: execution funnel, frozen ledger reproduction and independent synthetic accounting.
9. `strat2f`: metrics/baselines/calibration/threshold/dependent uncertainty diagnostics; `strat2g`: frozen9-configuration causal experiment matrix/selection protocol.
10. `strat2h`: frozen-candidate confidence, return, temporal/cost diagnostics, no fitting; `strat2i`: raw/prepared integrity reconciliation, exact pip/cash scenarios and future target design.

Main historical commands live in `tools/strat2*_validation.py`. Existing versioned output paths already exist; **do not rerun them as resets**. Use a fresh output/evidence version for an authorized independent replay, or unchanged identity/explicit resume for an interrupted journal. Do not run any historical command just to start the next chat.

Focused tests: `tests/test_phase6_*`, `tests/test_strat2*`, `tests/test_research*`. The closure task runs27 synthetic modules,66 candidate Python files through Ruff, formatting and whitespace checks. See the reviewed change manifest for actual results, not historical test totals added together.

## Verified research results

- C:58,683 prepared rows,47/47 matching files,152.4MiB peak;9 committed chunks.
- D: incremental model and simulation implemented; later two-month engineering validation721/721 replay,90.20MiB corrected historical peak; zero fills.
- E:58,683 rows and28,617 equity records reconciled;181/181 replay;87.68MiB peak. All25,830 historical predictions fail0.55 before orders. Synthetic long/short/cost/open-position recovery passes.
- F:25,446 labeled observations; balanced accuracy≈0.33334; calibrated loss1.05039 versus prior1.06685, no demonstrated directional advantage;181/181 replay;93.32MiB peak.
- G:3 target horizons ×3 feature/model families=9 predefined configurations;73,137 prediction events independently verified. Selected h15 linear12 BA0.35070 versus reference0.33604; loss advantage remains uncertain.181/181 replay;98.47MiB peak.
- H:49,482 diagnostic events,12,304 final development rows;60 transactions (head59),181/181 replay,96.73MiB peak. Initial1.20GiB attempt was resource-blocked; later authorized1.00GiB run passed. Frozen raw LONG/SHORT maxima0.443710/0.402011; calibrated0.412021/0.382596; no0.55 signal.
- I:58,683 raw/prepared rows match;49,482 frozen events;**180 transactions** (head179),541/541 replay;101.06MiB peak, minimum sampled RAM1.670196533GiB. Two hard crashes, immutable-prefix resume, repeat-resume and independent arithmetic/source verification pass.
- G/H/I research conclusion is **INCONCLUSIVE**. No candidate promotion, threshold change or independent profitability result.

Frozen selected model: `linear12/h15_cost`, `online_softmax_v1`, schema `strat2g-model-state-v1`, LR0.003,L2=0.01,seed20261027,12 predictors,11,675 train/13,428 calibration rows, temperature≈1.38405. Canonical candidate hash `bc72d1443ecca6ca69040ef814c521e23077d45a12ebb4f4a7fe4b95b2c967a6`.

G split UTC boundaries: Jan1–15 train; Jan15–Feb1 calibration; Feb1–15 selection; Feb15–Mar1 final development evaluation. Nontraining embargo60min; common30min contiguous label path with strict split-end purging. Selection used only selection loss and a simplicity hurdle. The final slice is nevertheless repeatedly examined development data, not independent proof.

I reconciled301 internal gaps /23,516 minutes =23,088 weekend-hypothesis +50 session-break +378 expected-open. Another2,761 leading-edge minutes explain26,277 full-window absences.16 observed outside-session candles are present, not missing, all21:59UTC/16:59NY. Actual broker/account 2022 sessions/holidays, price side and fees remain **UNVERIFIED**. No calendar corrections or candle filling occurred.

I forced-direction diagnostic: gross+0.294708bps/+0.331396pips; conditional exact base-cost net−1.805295bps/−2.042351pips. Dependent intervals are negative. Sparse lower-gate positive means have intervals crossing zero and become negative under cost stress. These overlapping counterfactual opportunities are not realized portfolio P&L.0.55 stays unchanged.

## Frozen documents and unresolved issues

- `config/strat2g-protocol-v1.json`: nine-candidate matrix, features/targets/splits/selection freeze.
- `config/strat2h-diagnostics-protocol-v1.json` and `strat2h-confirmation-protocol-v1.json`: immutable selected-candidate identity and confirmation design.
- `config/strat2i-data-quality-economic-research-v1.json`: I audit/costs/three future target designs; no target winner.
- `config/strat2i-confirmation-protocol-proposal-v2.json`: separate proposal clarifying selected12 features, fee/calendar attestation, dependent week/day evidence and no substitutions. Does not alter H or authorize future-period access.
- `reports/strat-2i-data-integrity-economic-signal-v1.md`: final I findings; phase reports preserve early blocks and later completions.
- `reports/strat-2c-to-2i-technical-review-2026-10-29.md`: closure chronology, architecture and risks.
- `reports/strat-2c-to-2i-artifact-manifest-2026-10-29.json`: local evidence identities, hashes and verification limits.
- `reports/strat-2c-to-2i-reviewed-change-manifest-2026-10-29.json`: committed scope, excluded paths, checks and initial Git state.

Technical debt: duplicated per-phase harnesses/resource dispatch; H-prefixed I output paths; multiple scientifically distinct feature implementations; strict package-wide code fingerprints invalidate old resume after changes; full receipt-chain scanning grows startup cost; environment-sensitive deterministic floats; historical encoding artifacts and incremental report statuses; local ignored data dependency and source-oriented CLI packaging; known NumPy-timedelta deprecations. Do not refactor frozen modules merely to tidy them before NN work.

The closure fixes only two synthetic test-isolation defects (E fresh-process import checking, I temporary admission paths). Narrow `.gitattributes` rules preserve exact research file bytes under Windows `core.autocrlf=true`; do not run renormalization or line-ending cleanup on frozen code/configs/reports. Fresh-checkout staged blob hashes were checked against the reviewed working bytes.

## Immediate next objective: STRAT-NN-1 design and synthetic feasibility

No neural network, new framework, NN training, new data period or live deployment is implemented/authorized by this handoff. The next user task should authorize the scope explicitly. First design one compact causal baseline, fixed comparisons/targets, train/calibration/evaluation policy, serial uncertainty, optimizer/RNG checkpoint schema, bounded resource profiling and crash/replay tests in a **new module namespace**. Existing NumPy may suffice for feasibility; assess dependency/import memory before proposing a framework. Keep H/I resource exceptions isolated; default1.50GiB remains for NN training unless a new explicit policy is approved.

Calendar/price/fee provenance and untouched-data audit remain prerequisites for independent confirmation. A neural network cannot resolve them. New historical access needs separate authorization. Do not silently adopt an I-designed target or retune0.55 using the already examined sample.

### Suggested first Codex task

> Inspect this handoff, the closure review and manifests, then verify worktree/branch/HEAD and synthetic-test availability. Design STRAT-NN-1 as one compact deterministic causal neural baseline with bounded optimizer/RNG checkpoints and synthetic-only profiling. Write a versioned protocol and implementation plan before experiments. Reuse sealed input, splits, journal, monitors and independent verification interfaces without editing frozen research artifacts. Do not install a large framework, access new historical periods, change resource guards or trading thresholds, train on historical data, connect to brokers, commit or push until explicitly authorized by the new task. Report calendar/cost/data prerequisites separately from model capacity.

### Suggested ChatGPT opening message

> Continue AI-Trading-System from `C:\Users\jager\ai_trader\AI-Trading-STRAT-2C`, branch `feature/strat-2c-multimonth-checkpoint`. Read `reports/ai-trading-next-chat-handoff-2026-10-29.md`, the C–I technical review and manifests. Verify HEAD/upstream/status before changes. C–I engineering passed, but independent predictive/economic value remains unproven; Jan–Feb2022 is repeatedly examined development data and2026 is prohibited. Begin STRAT-NN-1 with a bounded synthetic-only design/feasibility protocol in a new namespace. Preserve all frozen protocols, artifacts, calendar uncertainty,0.55 threshold and existing resource guards. No historical expansion, broker/Docker/live/paper execution, commit or push is authorized by this opening message.

Legacy E report publication: the original `reports/strat-2e-trade-execution-calendar-validation-v1.md` is preserved locally, byte-for-byte, and deliberately untracked because131 lines contain doubled CR terminators. The reviewed Git reading copy is `reports/publication/strat-2e-trade-execution-calendar-validation-v1.md`; only its line endings are normalized. Both hashes and the transformation are in the artifact manifest. This does not rewrite original evidence.
