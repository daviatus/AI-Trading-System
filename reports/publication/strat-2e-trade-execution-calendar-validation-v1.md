# STRAT-2E trade execution and historical calendar validation v1

**Final engineering result: PASS.** The previously blocked bounded historical replay completed on 2026-10-28 using the unchanged 1.30 GiB offline profile. Hard-crash recovery, repeated resume, independent replay and execution accounting passed. Historical broker-calendar verification remains unresolved, so performance conclusions remain blocked.

## Completion update (2026-10-28)

The original failed attempt and its evidence remain preserved. This completion uses new version-2 paths and makes no implementation, strategy, model or resource-policy changes. The earlier report is archived byte-for-byte at `data/research/strat2d-strat2e-execution-calendar-evidence-v2/report-before-completion.md`. The prior attempt section below is historical context; its resource/replay BLOCKED statuses are superseded by this update.

Executed command:

```powershell
.\.venv\Scripts\python.exe -B -m tools.strat2e_validation --evidence-version 2
```

Initial read-only precheck measured **1.7268 GiB available RAM** and **27.1958 GiB free disk**. The driver independently admitted the run and monitored every child using the unchanged **1.30 GiB physical available-RAM floor, 256 MiB worker RSS ceiling, 15 GiB disk floor, single-threaded execution and 1,024-row batches**. Real worker PID monitoring samples RSS/RAM/disk externally about every 0.1 seconds; worker guards also run between bounded operations. Minima are sampled values, not continuous OS reservations. General training/backtesting defaults remain 1.5 GiB.

Only the sealed EURUSD M1 preparation from **2022-01-01T00:00:00Z through 2022-03-01T00:00:00Z exclusive** was used. This is the existing STRAT-2E frozen-model/portfolio replay: it validates source and model/checkpoint identity and reproduces training/calibration eligibility; it does **not** refit or tune the already sealed STRAT-2D model. Calibration and strategy parameters are unchanged.

### Historical execution, crash and replay

- **58,683 input rows** were streamed across the two authorized months. The finished audit has **60 committed transactions, generation 59, complete=true**.
- The intentional hard crash used `os._exit(86)` after fully staging transaction 35 and before publication. Integrity checks recovered committed generation 34, preserved its **106** committed file hashes, and ignored the uncommitted staging directory.
- A fresh worker resumed to completion. Every prefix hash remained unchanged. The one orphan staging directory remains preserved; no checkpoint or staging was deleted.
- A completed repeat-resume changed **no committed bytes or transactions**.
- An independent uninterrupted worker produced **181/181 identical committed SHA-256 hashes**, including manifest, receipts, state and events. Final canonical logical-state hash: `3036f4bb16f17f1ff928a2150eba4c93d39b11bc3a1ec0426d939e060341950e`.
- Both journals were independently reopened, their complete receipt/file/logical-hash chains revalidated, and their final states matched exactly. All **721 STRAT-2D foundation files** remained unchanged; all **47 STRAT-2C published hashes and mtimes** also remained unchanged.
- No unexpected exception, checksum mismatch, corruption or resource stop occurred. The crash worker's exit 86 was expected; resume, repeat and reference workers each exited 0. The orchestrator returned PASS and exit 0.

Output journals:

```text
data/research/strat2d-strat2e-execution-calendar-resumed-v2
data/research/strat2d-strat2e-execution-calendar-reference-v2
```

### Complete admission and signal funnel

The following are ordered, mutually exclusive admission reasons within each decision-time window. A row is attributed to its **first failed check**. Session-excluded candles may already have nonfinite prepared predictors, and labels crossing gaps may already be unavailable. Consequently a zero extra session/quality-block count does not mean that those data-quality problems do not exist.

| Window | Window rows | Nonfinite predictors | Unavailable label | Split-boundary label | Eligible rows |
| --- | ---: | ---: | ---: | ---: | ---: |
| Training | 14,258 | 1,792 | 254 | 0 | 12,212 |
| Calibration | 15,807 | 1,687 | 205 | 0 | 13,915 |
| Test after embargo | 28,557 | 2,727 | 383 | 1 | 25,446 |

Each partition sums exactly to its window row count. Training and calibration eligible counts exactly equal the sealed original fit counts. No fitting occurred on test rows. Test label eligibility is diagnostic/metric admission only; future labels and future exclusion blocks never admit a trading signal.

| Signal/execution stage | Count |
| --- | ---: |
| February simulation bars/equity records | 28,617 |
| Frozen-model predictions after embargo/readiness | 25,830 |
| Session or terminal-horizon gate blocks | 239 |
| Remaining predictions failing directional threshold | 25,591 |
| Invalid stop distance / busy / censored gate blocks after earlier checks | 0 |
| Confidence-qualified predictions, independently of earlier gates | 0 |
| Pending orders / order attempts / order rejections | 0 / 0 / 0 |
| Entry fills / exit fills / completed trades | 0 / 0 / 0 |

**Zero-fill cause is confirmed by fresh inference.** The two ordered signal reasons reconcile: 239 + 25,591 = 25,830. In addition, *all* 25,830 probability vectors independently fail the necessary 0.55 directional confidence condition, including those already blocked by session/end boundaries. Maximum calibrated long probability is **0.47834644828455575**; short is **0.4109943218315877**, exactly matching the earlier published-ledger diagnosis. Winners remain 25,812 no-trade, 8 long and 10 short. No threshold was relaxed to create orders. The raw uncalibrated comparison is explanatory only; execution always uses the frozen calibrated model.

### Accounting and chronological verification

Ledger reconciliation passed: event sequence is gap-free, and cash + unrealized P&L equals equity. All **28,617 equity events**, after removing only their differing global event-sequence numbers, are byte-logically identical to the sealed original STRAT-2D equity events. Their timestamps are strictly increasing; none were skipped or duplicated. Final portfolio state exactly equals the original foundation state: cash/equity **10,000**, realized/unrealized P&L **0**, transaction costs **0**, reserved margin **0**, zero fills/trades, no position, no pending order and no censoring.

This zero-fill historical run validates chronology, deterministic inference, gate decisions, ledger persistence and accounting invariants. Historical entry/exit cost handling is not exercised by zero orders; the existing independent synthetic long/short, numerical cost, open-position hard-crash and replay tests remain the evidence for that path. No profitability conclusion follows.

### Resource measurements

| Worker/run | Peak worker RSS MiB | Minimum available RAM GiB (worker/parent) | Minimum free disk GiB |
| --- | ---: | ---: | ---: |
| funnel-crash | 87.04 | 1.606 | 27.19 |
| funnel-resume | 87.43 | 1.625 | 27.19 |
| funnel-repeat | 77.80 | 1.621 | 27.18 |
| funnel-reference | 87.68 | 1.570 | 27.18 |

Overall measured execution peak worker RSS: **87.68 MiB**. Minimum sampled system-available RAM across the execution workers and their parents: **1.570 GiB**. Minimum free disk: **27.18 GiB**. All remained within the approved limits. Completed-resume startup is shown separately because it performs verification without another historical simulation. The previous rejected 1.25 GiB attempt remains recorded in version-1 evidence; these are new, admitted execution measurements.

### Checks and final status

| Requirement | Final status |
| --- | --- |
| Exact scope and unchanged resource/strategy parameters | PASS |
| Prepared source, frozen model and checkpoint identity/integrity | PASS |
| Bounded historical replay and granular funnel | PASS |
| Hard process crash, preserved prefix and safe resume | PASS |
| Completed repeat-resume idempotence | PASS |
| Independent replay: every committed byte/hash and canonical state | PASS |
| Equity chronology, ledger and portfolio accounting | PASS |
| Original zero-fill cause | PASS, independently confirmed |
| STRAT-2C and sealed STRAT-2D artifact preservation | PASS |
| RSS/RAM/disk monitoring and fail-closed limits | PASS |
| Historical broker session/holiday calendar verification | UNVERIFIED; performance conclusions remain BLOCKED |

No implementation changed in this completion task. Code hashes were checked against the preceding tested STRAT-2E assessment and match. The existing **98 pytest + 4 lightweight unittest cases** remain applicable; they were not rerun or represented as new test executions. Fresh checks were the four monitored historical child runs, full journal verification, independent equity/ledger/accounting/partition checks, Ruff lint/format checks and `git diff --check`; all passed.

Evidence directory: `data/research/strat2d-strat2e-execution-calendar-evidence-v2`. Its `summary.json`, four child/parent resource logs, crash record, `interrupted-prefix-hashes.json`, `resumed-hashes.json`, `reference-hashes.json`, `accounting-and-invariants.json`, and `final-assessment.json` provide the consolidated evidence. Run-specific metrics/logs and preserved orphan staging are outside committed scientific comparisons. No existing artifact was reset or overwritten; only this explicitly requested report was updated.

### Remaining blockers and next phase

The resource blocker and fresh historical replay blocker are resolved. Calendar uncertainty remains: exact 2022 broker hours, holiday/exceptional-session evidence, the 16 observed 21:59 UTC boundary candles, and no-tick versus missing-data interpretation still need authoritative resolution before performance research. Gap counts remain 301 internal gaps totaling 23,516 minutes. The existing calendar/feature rules and observed data were preserved; no unsupported calendar correction was made.

Recommended next phase: obtain the source broker's historical calendar/holiday evidence, resolve the boundary convention, and validate separately prepared gap-safe episodes if the rules change. Broader development-period validation requires its own authorized scope and ordinary resource policy. No 2026 holdout, annual backtest, broker/live connection, Docker or service startup, Phase 5 source change, commit or push occurred.

---

## Previous attempt record (superseded for historical execution)

The following is the original report content preserved as historical context. Its resource/replay BLOCKED statements describe the earlier attempt; the completion update above is authoritative for current engineering status. Calendar UNVERIFIED statements remain current.

**Outcome: zero-fill diagnosis and synthetic execution PASS; new historical execution BLOCKED by resources; exact historical calendar remains UNVERIFIED.** No strategy thresholds or scientific parameters were changed. No profitability conclusion is supported.

## Root cause of zero historical fills

All **25,830 published February predictions** fail the production confidence condition `max(p_long, p_short) >= max(p_no_trade, 0.55)`. Maximum calibrated probabilities are **0.4783464483 long** and **0.4109943218 short**. Therefore no prediction can create an order, regardless of session/ATR/capital checks. The ledger contains **zero pending orders, execution rejections, entry fills, exit fills and completed trades**. Cash/equity remain 10,000, with no open or pending positions.

This is intended conservative behavior of the calibrated baseline, not an observed next-bar execution failure. In `src/trading/research/strat2d/portfolio.py:285`, signal validation precedes state, session/distance and confidence gates; the threshold condition is at line 300. Failed confidence gates return silently, which was an observability limitation. STRAT-2E adds diagnostic explanations without changing production events or decisions. Sizing, order execution and closing cannot be blamed for historical orders that never existed. This evidence does not prove the engine is defect-free under all inputs.

The fitted model used 12,212 training labels: 6,557 no-trade, 2,832 long, 2,823 short. Calibration used 13,915 labels: 7,577 no-trade, 2,981 long, 3,357 short. The final scalar temperature is **1.7498770395** (`log_temperature=0.5595455223`), which flattens softmax distributions while preserving class ranking. `model.py:61` applies that temperature at inference. Published winners are **25,812 no-trade, 8 long and 10 short**; being the largest class probability still falls short of the required confidence.

For explanatory inspection only, reversing the temperature mathematically via normalized `p**T` reconstructs approximate pre-temperature maxima of 0.5909076067 long and 0.4703376656 short, with one confidence-qualified prediction. These are transformations of stored probabilities, not new model fitting, a changed-strategy backtest, or evidence that any alternate trade would execute or profit. Calibration and the 0.55 threshold remain intact. No policy was tuned using these test observations.

## Signal and order funnel

Counts below come from previously committed STRAT-2D evidence, verified against **721 saved file hashes** and a gap-free event sequence. No new candles or features were evaluated for this postmortem.

| Stage | Count | Interpretation |
| --- | ---: | --- |
| Prepared source | 58,683 | Authorized EURUSD M1 January-February 2022 only |
| Prior quality/train/calibration/test source visits | 58,683 each | Four chronological passes; not four independent datasets |
| Accepted training updates | 12,212 | Chronological, eligible labels and finite predictors |
| Accepted calibration updates | 13,915 | Separate January interval; weights fixed |
| February simulation bars/equity records | 28,617 | Production `on_bar` calls, including warmup/embargo observations |
| Frozen inference/prediction records | 25,830 | Finite causal predictors, open-session check and decision-time test window |
| Labeled test metric rows | 25,446 | 384 predictions lack eligible metric labels; labels never gate signals |
| Highest class: no-trade / long / short | 25,812 / 8 / 10 | Model argmax before confidence gate |
| Pass directional confidence | 0 | All 25,830 fail the necessary 0.55 gate |
| Pending orders created | 0 | No execution request exists |
| Next-bar order attempts / order rejections | 0 / 0 | Not a capital/margin rejection issue |
| Entry fills / exit fills / closed trades | 0 / 0 / 0 | No positions to manage or close |

Equity and prediction counts use different opened-at versus completed-bar decision-time windows; their difference must not be called a pure warmup count. The existing artifacts do not contain exhaustive mutually exclusive counts for every train/calibration exclusion or session/ATR signal gate. The new bounded diagnostic replay implements those counts, but its fresh historical execution was not admitted. These granular counters remain **unverified**, rather than invented from aggregate differences. The confidence-stage root cause above is independently conclusive.

## Implemented bounded diagnostics

`src/trading/research/strat2e/audit.py` adds an ordered signal-gate explanation, incremental probability statistics, a hash-checked published-ledger postmortem, a frozen-model execution replay and a tiny synthetic journal fixture. The replay streams at most 1,024 rows, validates the sealed source and original model/manifest identity, reproduces training/calibration eligibility diagnostically, and simulates test observations through the unchanged portfolio class. It never refits or injects historical signals. Missing-label/exclusion information feeds diagnostics only, not order decisions. It checks reconstructed portfolio state against the sealed foundation. Future label availability is not used to admit an order.

The new replay checkpoints cursor, bounded counters, at most the two-month daily calendar summaries, portfolio state and event sequence in the existing immutable atomic journal. Partial state/events/receipt staging is ignored on restart and preserved. Code/config/source/model provenance is bound into its manifest. Native numerical and Arrow work stays single-threaded. No prediction or equity history accumulates in memory.

`tools/strat2e_validation.py` orchestrates a crash before transaction 35, resume, completed repeat-resume and independent reference, comparing every committed artifact and logical state. The STRAT-2D launcher gained optional module/argument selection while retaining its defaults. Real-worker PID publication, external RSS sampling and guard behavior are reused. The new replay/orchestration implementation is **not claimed historically validated** because admission failed before launch. Existing production preparation, fitting, portfolio and calendar algorithms are unchanged.

## Synthetic execution validation

**98 targeted pytest cases passed in 42.55 seconds** (146 existing NumPy timedelta deprecation warnings), including 27 new STRAT-2E cases. An additional **4 lightweight standard-library tests passed in 0.845 seconds** after the admission stop; those inspect synthetic ledger hashes and mock a resource rejection, without importing the scientific stack or executing historical data.

New tests include:

- Sixteen long/short x target/stop/profitable-horizon/losing-horizon x zero/explicit-cost scenarios. Expected cash and net P&L are calculated independently from adverse reference-price adjustments and per-side commissions. Equity, unrealized P&L, released margin and transaction costs reconcile numerically.
- Affordable position sizing and insufficient-capital quantity rejection with unchanged cash; pending-order rejection at a closed session.
- Exposure retained and execution censored across a missing bar or closed-session candle. No fabricated exit is emitted.
- Actual subprocess `os._exit(86)` before and after publication of a synthetic open-position transaction. Independent uninterrupted and resumed runs have identical canonical state and every committed byte. Both recover four fills and two trades without duplicates; repeated completion is idempotent.
- Signal-gate explanations and Sunday/Friday boundaries, explicit unverified-calendar status and New York winter/DST conversion.
- Existing STRAT-2D leakage, corruption, checkpoint, cost, pending/open-position, chronology, memory and resource-profile regressions.

Synthetic probabilities and quantity limits are explicit tiny fixtures, never substituted into historical validation. Existing scientific/trading parameters and approved profile are unchanged. Static Ruff checks, format checks, CLI help and `git diff --check` pass. Initial lint findings and a missing import in the new postmortem helper were corrected; postmortem regression tests cover the correction. No unresolved automated test failure remains.

Executed test commands:

```powershell
$env:PYTHONPATH = "$pwd\src"
..\AI-Trading-System\.venv-research\Scripts\python.exe -B -m pytest tests/test_strat2d.py tests/test_strat2d_memory.py tests/test_strat2d_resource_profile.py tests/test_strat2e.py -q -p no:cacheprovider --basetemp=.tmp/strat2e-tests-v1 --junitxml=.tmp/strat2e-tests-v1.xml
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_strat2e_postmortem.py -v
```

## Calendar findings and evidence

A separate versioned evidence registry is `config/strat2e-eurusd-2022-calendar-evidence-v1.json`. It leaves `historical_schedule_verified=false`, binds the existing calendar fingerprint, and blocks unsupported performance admission. It does not silently replace preparation rules or declare the broker's historical hours verified.

1. **Protocol timestamp and boundary semantics verified.** Spotware defines bar timestamps as Unix minutes and weekly sessions as Sunday-based seconds in `scheduleTimeZone`, with inclusive start/exclusive end. The repository converts timestamp minutes explicitly to UTC (`src/trading/ctrader/client.py:874`); the Phase 5 snapshot reader explicitly parses `opened_at` as UTC. A broker-server wall clock is not substituted for this field. Code review found no reason to shift these published observations by one, two or three hours. This verifies interpretation of the protocol and local conversion, not independent tick-by-tick ground truth. [Spotware model definitions](https://help.ctrader.com/open-api/model-messages/).

2. **No DST transition in this window.** New York remains UTC-05:00 during January-February 2022. Under the US rules in effect since 2007, the next transition was March 13, 2022; zoneinfo tests confirm it. The earlier observed zero offset transitions is consistent with that fact. A DST hypothesis cannot explain a January/February gap. [NIST DST rules](https://www.nist.gov/pml/time-and-frequency-division/popular-links/daylight-saving-time-dst).

3. **Frozen weekly rules remain historical hypotheses.** They imply Sunday 17:01 New York reopening (22:01 UTC), Friday 16:55 close (21:55 UTC), and Monday-Thursday 16:59-17:01 breaks (21:59-22:01 UTC), with an exclusive end. The first observed bar is January 2 at 22:01 UTC, which is consistent with the opening hypothesis, not proof of all historical sessions. Current OANDA hours use a different reopening/break convention; current IC Markets hours also have their own server clock and Friday close. Neither proves this source's 2022 schedule, so neither was used as an override. [Current OANDA hours](https://www.oanda.com/us-en/trading/hours-of-operation/), [current IC Markets hours](https://www.icmarkets.eu/en/trading-pricing/trading-hours).

4. **Gaps require qualified labels.** Existing evidence records 301 internal gaps, 23,516 internal missing minutes, and a separate 2,761-minute leading closure. Under the unverified frozen rules, internal missing minutes split into 23,088 weekend, 50 session-break and 378 expected-open minutes. Full-window weekend total is 25,849 including the leading closure. There are 260 gap records containing expected-open missing minutes and nine long weekend-classified records including the leading closure; categories can overlap. No absent candle was filled, invented or deleted. Absence of an M1 bar can reflect no tick as well as missing collection; this dataset has no independent tick evidence to distinguish them. Spotware describes trendbar volume as ticks and timestamp as open-tick time, which is consistent with treating absent bars cautiously. [Spotware trendbar definition](https://help.ctrader.com/open-api/model-messages/).

5. **All 16 outside-session observations are boundary cases.** Every one is at 21:59 UTC (16:59 New York), the first minute excluded by the frozen weekday break. Dates: January 27, 31; February 1, 2, 3, 8, 9, 10, 14, 15, 16, 21, 22, 23, 24, 28. Possible explanations include historical session changes, bar/session endpoint convention or sparse boundary ticks. Their common minute does not establish which explanation is correct. They remain raw observations with exclusion flags; none were shifted or relabeled as valid training evidence.

6. **Holidays and exceptional closures remain unverified for this venue.** January 1 was a Saturday; the 2022 US federal schedule includes January 17 and February 21. Government/settlement holidays are not proof of a spot FX venue closure. A dated December 17, 2021 IC Markets announcement confirms a GMT+2 platform convention and holiday liquidity warning, but its detailed FX table was absent from retrieved text. That is insufficient to invent January 3 or other closure hours for this source. No exceptional 2022 broker closures were established. Current rules have no applicable supplied 2022 holidays; absence of metadata is not verification of absence of holidays. [US OPM 2022 holiday table](https://www.opm.gov/policy-data-oversight/pay-leave/federal-holidays/), [dated IC Markets announcement](https://www.ic.com/blog/holiday-trading-schedule-dec-2021-jan-2022/).

Performance conclusions remain blocked until the exact source broker/symbol's historical session and holiday evidence resolves those assumptions. The strict verified-calendar policy remains available; the approved 2022 configuration explicitly uses observed-contiguous engineering only. A changed calendar requires separate preparation and validation, never rewriting STRAT-2C outputs.

## Historical attempt, resource measurements and preservation

The existing opt-in config was used unchanged: **1.30 GiB available physical RAM**, **256 MiB worker peak RSS**, **15 GiB disk floor**, **one thread**, **1,024-row batches**, exact EURUSD January 1-March 1 exclusive scope. Ordinary workflows still require 1.5 GiB.

Command attempted:

```powershell
.\.venv\Scripts\python.exe -B -m tools.strat2e_validation
```

Admission failed with **`Available RAM 1.25 GiB below research minimum`**, originating at `src/trading/research/strat2d/pipeline.py:152`. No historical child started, no new replay output/checkpoint was created, and no threshold was lowered or process terminated to force admission. The admission failure is preserved in `data/research/strat2d-strat2e-execution-calendar-evidence-v1/summary.json`. The driver returned failure and stopped. No later historical execution was attempted.

**New historical peak RSS/minimum running RAM: not measured, because no worker ran.** The initial failed gate recorded rounded available RAM only; the new driver now retains parent monitor metrics even on startup rejection, with a mocked regression test. Postmortem log-inspection peak RSS was approximately 17.13 MiB; this is not a training/simulation measurement. For context only, the prior completed STRAT-2D run measured 90.20 MiB worker peak and 1.456 GiB minimum sampled RAM. Those earlier measurements are not new STRAT-2E resource results.

The read-only postmortem opened existing event/state/receipt evidence, not source candle/feature batches. The existing foundation's 721 file hashes and event sequence match the saved STRAT-2D audit. All 47 STRAT-2C published hashes and mtimes remain unchanged. The original Phase 6 research config hash also remains unchanged. No Phase 5 junction target, source file or published checkpoint was modified. Standard-library evidence inspection and reporting continued after rejection; fitting, inference and portfolio simulation did not.

## PASS/FAIL assessment

| Subsystem | Status | Evidence/limit |
| --- | --- | --- |
| Exact zero-fill diagnosis | PASS | 25,830/25,830 fail confidence; no orders reach execution |
| Complete per-row exclusion/session/ATR funnel | BLOCKED | Implemented replay not admitted; existing aggregate counts do not support every partition |
| Synthetic long/short accounting and costs | PASS | Independent numeric oracles and deterministic scenarios |
| Synthetic hard-crash/open-position recovery/replay | PASS | Real process exits before/after publication; identical committed bytes |
| Existing historical artifact integrity/preservation | PASS | 721 foundation hashes, sequential events, 47 source hashes/mtimes |
| New historical checkpoint/resume/reference replay | BLOCKED | RAM rejection before worker launch |
| UTC interpretation and winter DST | PASS | Primary protocol/time-rule sources and tests |
| Exact historical broker hours/holidays | UNVERIFIED / performance BLOCKED | Current metadata and other-broker schedules are insufficient |
| Resource safeguards | PASS | Fail-closed admission; RSS/disk/threads/default floor unchanged |
| Automated tests and static checks | PASS | 98 pytest + 4 lightweight unittest cases; Ruff/format/whitespace checks |

## Files and evidence

New files: `src/trading/research/strat2e/__init__.py`, `src/trading/research/strat2e/audit.py`, `tools/strat2e_validation.py`, `tests/test_strat2e.py`, `tests/test_strat2e_postmortem.py`, `config/strat2e-eurusd-2022-calendar-evidence-v1.json`, and this report. The existing STRAT-2D launcher accepts optional child module/arguments; its defaults are retained. No STRAT-2D model, portfolio, preparation or calendar implementation changed.

New versioned evidence includes `summary.json` (admission failure), `published-ledger-postmortem.json`, `model-published-evidence.json`, `calendar-published-evidence.json` (all interval details and durations), `preservation-and-policy.json`, `tests.xml`, `checks.log` and `final-assessment.json`. Existing reports and evidence were preserved. The failed invocation's version is never reused as a reset.

## Remaining blockers and next phase

**STRAT-2E is partially complete, not a fresh historical validation PASS.** Once stable headroom meets the unchanged 1.30 GiB gate, the next autonomous diagnostic run uses new version-2 output/evidence paths:

```powershell
.\.venv\Scripts\python.exe -B -m tools.strat2e_validation --evidence-version 2
```

It needs no manual chunk commands. It will verify the old source/model, collect the granular eligibility/signal funnel, crash/resume, verify repeated completion and perform independent replay with the same strategy parameters. A new stop must preserve its artifacts; do not delete/reset evidence to obtain a clean run. The new orchestration remains pending historical integration validation.

Next development phase: finish that resource-admitted diagnostic replay, obtain the exact historical broker calendar/holiday evidence, resolve boundary conventions and document no-tick versus missing-data uncertainty. Then validate gap-safe simulation episodes with deliberate synthetic trade coverage before broader development-period performance work. Do not lower thresholds to obtain historical fills. No 2026 holdout access, annual backtest, broker/live connection, Docker/service startup, source modification, commit or push occurred.
