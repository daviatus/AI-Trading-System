# STRAT-2D streaming training and backtest v1

Engineering validation: **FAIL**. Performance remains unproven.

## Implementation summary

An isolated STRAT-2D package streams immutable STRAT-2C bars/features/labels in 1024-row Arrow batches through quality, training, calibration and test passes. No Phase 5 files or original Phase 6 configuration are used as output or modified.

The explicitly named online softmax baseline uses fixed, predeclared feature scales and chronological one-row SGD. Calibration changes only scalar temperature. Weights and calibrator are frozen before February predictions. Labels are purged at split ends; calibration/test have a 60-minute embargo. Test labels affect metrics only.

## Model support matrix

| Model | STRAT-2D status |
| --- | --- |
| online_softmax_v1 | Incremental fitting, calibration, inference and recovery |
| trend | Existing Phase 6 fixed rule unchanged; STRAT-2D adapter unsupported |
| logistic | Existing batch L-BFGS definition unchanged; no silent SGD substitution |
| tree / xgboost | Explicit unsupported status; no validated bounded continuation |

## Major requirements

| Requirement | Status | Evidence |
| --- | --- | --- |
| synthetic_tests | PASS | 138 tests passed, including model, calendar, execution and crash cases |
| static_checks | PASS | Ruff lint/format and git diff --check passed |
| quality_audit | FAIL | Not completed; stopped safely, see failure evidence |
| chronological_fitting | FAIL | Not completed; stopped safely, see failure evidence |
| frozen_inference | FAIL | Not completed; stopped safely, see failure evidence |
| portfolio_accounting | FAIL | Not completed; stopped safely, see failure evidence |
| checkpoint_recovery | FAIL | Not completed; stopped safely, see failure evidence |
| independent_replay | FAIL | Not completed; stopped safely, see failure evidence |
| completed_resume | FAIL | Not completed; stopped safely, see failure evidence |
| bounded_real_engineering | FAIL | Not completed; stopped safely, see failure evidence |
| source_preservation | FAIL | Not completed; stopped safely, see failure evidence |
| historical_calendar_verification | FAIL | Unverified; engineering-only policy, performance admission blocked |
| statistical_performance | FAIL | Not established by this two-month engineering validation |

## Calendar and data-integrity policy

The versioned America/New_York weekly rules are copied from frozen current contract metadata, not verified for 2022. No 2022 holiday schedule was supplied; later dated holidays are not applied retroactively. DST conversion uses ZoneInfo and its recorded tzdata version. Weekend/session-break/holiday classifications remain hypotheses until verified.

The strict require_verified_calendar policy blocks all fitting under an unverified calendar. This separately configured observed_contiguous_engineering run admits only finite predictors and observed, target-contiguous paths. Labels intersecting any missing or excluded observation are blocked. No candles are filled. Published prepared features must match the selected calendar; changing rules requires a new prepared dataset.

At an unobservable gap or excluded session candle, an open simulated position is censored with exposure retained; subsequent execution is blocked. No retrospective last-close or fictional stop fill is invented. Predictions may continue, but affected portfolio performance is not evaluable. Pending orders require the exact next observed open.

## Simulation and accounting

Single asset/position, CFD-style balance with reserved entry margin. Equity equals balance plus unrealized P&L. Risk and exposure cap quantity; step/min/max constraints apply. Explicit spread/slippage are adverse fill adjustments; commission is charged per side. Stop wins same-bar ambiguity; targets do not improve on favorable gaps. The 15-bar horizon exits at an observed close. No terminal fill is fabricated. Financing, FX conversion, dynamic broker margin tiers and bid/ask history remain unverified/unsupported.

## Checkpoint protocol and replay

Immutable numbered transactions contain state.json, events.jsonl and a chained receipt with file and logical-state hashes. Model, cursor, calibration, pending orders, open position, balance, cost counters and history references commit in one same-volume directory rename after file flush/fsync. There is no mutable checkpoint to reset. All committed receipts are checked before resume; staging is preserved and ignored. Windows power-loss persistence still depends on filesystem/hardware behavior.

One real validation child is deliberately terminated with os._exit(86) after staging an actual training update and before publication. A fresh process resumes it. An independent uninterrupted process uses a different output directory. Runtime logs and RSS/timestamps live outside scientific transactions; canonical state and artifact hashes are compared without run-specific metadata.

## Results and evidence

```json
{
  "status": "FAIL",
  "checks": {
    "synthetic_tests": {
      "status": "PASS",
      "details": "138 tests passed, including model, calendar, execution and crash cases"
    },
    "static_checks": {
      "status": "PASS",
      "details": "Ruff lint/format and git diff --check passed"
    },
    "quality_audit": {
      "status": "FAIL",
      "details": "Not completed; stopped safely, see failure evidence"
    },
    "chronological_fitting": {
      "status": "FAIL",
      "details": "Not completed; stopped safely, see failure evidence"
    },
    "frozen_inference": {
      "status": "FAIL",
      "details": "Not completed; stopped safely, see failure evidence"
    },
    "portfolio_accounting": {
      "status": "FAIL",
      "details": "Not completed; stopped safely, see failure evidence"
    },
    "checkpoint_recovery": {
      "status": "FAIL",
      "details": "Not completed; stopped safely, see failure evidence"
    },
    "independent_replay": {
      "status": "FAIL",
      "details": "Not completed; stopped safely, see failure evidence"
    },
    "completed_resume": {
      "status": "FAIL",
      "details": "Not completed; stopped safely, see failure evidence"
    },
    "bounded_real_engineering": {
      "status": "FAIL",
      "details": "Not completed; stopped safely, see failure evidence"
    },
    "source_preservation": {
      "status": "FAIL",
      "details": "Not completed; stopped safely, see failure evidence"
    },
    "historical_calendar_verification": {
      "status": "FAIL",
      "details": "Unverified; engineering-only policy, performance admission blocked"
    },
    "statistical_performance": {
      "status": "FAIL",
      "details": "Not established by this two-month engineering validation"
    }
  },
  "config": "C:\\Users\\jager\\ai_trader\\AI-Trading-STRAT-2C\\config\\strat2d-eurusd-2022-engineering-v1.json",
  "primary": "C:\\Users\\jager\\ai_trader\\AI-Trading-STRAT-2C\\data\\research\\strat2d-eurusd-2022-engineering-v1",
  "reference": "C:\\Users\\jager\\ai_trader\\AI-Trading-STRAT-2C\\data\\research\\strat2d-eurusd-2022-uninterrupted-v1",
  "resource_policy": {
    "ram_gib": 1.5,
    "disk_gib": 15,
    "process_gib": 1.0
  },
  "tests": {
    "tests": 138,
    "errors": 0,
    "failures": 0,
    "skipped": 0
  },
  "static_checks": {
    "ruff_lint": "PASS",
    "ruff_format": "PASS",
    "git_diff_check": "PASS"
  },
  "failure": {
    "schema_version": 1,
    "stage": "strat2d_engineering_validation",
    "exception_type": "ResearchSafetyStop",
    "status": "safety_stopped",
    "reason": "Available RAM 1.35 GiB below research minimum",
    "origin": {
      "file": "src/trading/research/safety.py",
      "line": 76,
      "function": "resources"
    }
  },
  "process_measurements": {}
}
```

All gap durations and individual outside-session observations are preserved in data/research/strat2d-eurusd-2022-validation-evidence-v1/data-quality-intervals.jsonl when the historical run reaches ledger verification.

## Files changed

New src/trading/research/strat2d/{__init__,calendar,journal,model,pipeline,portfolio,runner,runtime}.py; config/strat2d-eurusd-2022-engineering-v1.json; tests/test_strat2d.py; tools/strat2d_validation.py; reports/strat-2d-implementation-plan-v1.md; this report. Existing production code and STRAT-2C published artifacts are preserved.

## Limitations, blockers and next phase

This is a two-month engineering validation of one new baseline, not statistical performance evidence or live-trading approval. Historical calendar verification and unexpected gaps remain data-quality blockers for performance research. A censored portfolio episode cannot establish a valid return/drawdown claim. Multi-asset arbitration, other estimators, broker financing and complete realistic margin simulation are unsupported.

Recommended STRAT-2E: verify historical calendar/session evidence, resolve or formally exclude unexpected gaps, validate gap-safe independent episodes, then expand the supported baseline to additional bounded pre-2026 development windows with unchanged leakage and resource checks. Add multi-asset shared-account recovery only after these checks pass.

If a resource gate failed, preserve all evidence and restore headroom above 1.5 GiB; do not use the preparation-only 1.25 GiB exception. Resume unchanged code/config at a new bounded invocation; never reset committed transactions. On corruption/identity failure, investigate preserved evidence before any resume.

No full-year 2022 backtest, 2026 holdout access, broker/DB connection, live order, Docker/service startup, commit or push occurred.

## Authoritative execution outcome and readiness

**STRAT-2D implementation and synthetic validation passed; bounded historical execution is BLOCKED.**

**No historical training, calibration, portfolio replay or independent replay process started.** The parent preflight rejected available RAM of **1.35 GiB**, below the unchanged **1.5 GiB** training/backtesting floor. The later read-only reporting check measured 1.29 GiB RAM and 29.27 GiB free disk. No preparation-only exception was used and no retry under pressure was attempted.

The hard-crash and independent historical replay paragraphs above describe the implemented **planned protocol**, not completed real-data runs. Those cases passed synthetically; their real-data status remains FAIL/not executed. No primary or reference historical STRAT-2D output directory exists. Only validation audit/report evidence was created.

### Training, calibration and simulation results

| Item | Result |
| --- | --- |
| Synthetic and regression tests | **138 passed** (137 comprehensive cases plus a dedicated position-checkpoint case) |
| New STRAT-2D-specific cases | 42 cases covering incremental updates, splits, calibration, calendar edges, costs, accounting, corruption and resume |
| Historical fitting/calibration rows | **0 / 0**; admission gate stopped execution |
| Historical backtest/replay rows | **0 / 0**; no model or portfolio run started |
| Historical peak RSS | **Not measured:** no training/backtest child was launched |
| Implemented RSS controls | Windows OS high-water counter, 1 GiB process ceiling, 1.5 GiB available-RAM floor; synthetically tested |
| Historical crash/replay comparison | **Not executed**; preserved for a later admitted run |
| Synthetic restart/replay | **PASS:** identical committed files and logical-state hashes before/after publication faults |
| STRAT-2C immutability | **PASS:** all 47 committed file hashes and mtimes reverified against the completed STRAT-2C baseline |
| Lint/format/whitespace | **PASS** |

The exact failure is at `src/trading/research/safety.py:76` (available RAM check). The 1.25 GiB preparation threshold remains unchanged for its original configuration; STRAT-2D explicitly rejects that exception. Known NumPy timedelta deprecation and runpy module-reimport warnings remain in the test environment.

### Calendar findings available without starting a historical experiment

Inherited from the validated STRAT-2C audit: **301 internal gaps, 23,516 internal missing minutes, 378 expected-open minutes absent under the candidate current calendar, and 16 observed out-of-session candles**. Eight large gaps each span 2,886 minutes. The candidate calendar attributes these eight gaps to weekend closures. The prior full-interval classification counted 25,899 known-closed absent minutes, including leading closure time. These are inherited findings, not a newly executed STRAT-2D audit.

STRAT-2D versions the exact source calendar in America/New_York, marks its 2022 historical validity unverified, and does not apply 2025/2026 dated holiday records retroactively. No verified 2022 holiday schedule is available. Therefore the 378 minutes may represent missing collection data or undocumented closures; their cause is unresolved. DST spring-forward and fall-back behavior passed synthetic tests. No new historical DST scan or detailed interval export was performed after the resource block. Timestamp alignment passed the earlier STRAT-2C validation; STRAT-2D revalidates it when admitted.

### Remaining blockers and exact next phase

**Not ready for broader development-period validation yet.** The immediate next phase is **STRAT-2D validation completion**, before STRAT-2E: restore stable available RAM above 1.5 GiB with import/working-set headroom, allocate new audit/report versions, then run the implemented hard-crash/resume and independent-replay workflow without changing scientific parameters. Preserve this failed preflight report and audit. The default validation tool correctly refuses to overwrite its existing v1 evidence; a retry must use new evidence paths. Do not delete/reset any committed journal.

After the supported baseline passes that engineering validation, proceed to STRAT-2E: verify historical calendar and holiday evidence, investigate/exclude unexpected gaps, validate independent gap-safe episodes, then widen bounded pre-2026 development windows. Legacy batch models, multi-asset execution and statistical performance remain outside validated STRAT-2D v1 support.

Detailed blocked-run diagnostics: `data/research/strat2d-eurusd-2022-validation-evidence-v1/validation-result.json` and `blocked-run-review.json`. Synthetic JUnit: `.tmp/strat2d-synthetic-final-v1.xml` and `.tmp/strat2d-open-position-v1.xml`. Earlier failed synthetic reports were preserved; the final corrected fault timing targets an actual training update.

No existing report, prepared artifact, Phase 5 source file or original configuration was overwritten or deleted. This execution-outcome section is appended to the newly created STRAT-2D report. No holdout access, annual backtest, broker connection, live order, Docker/service startup, commit or push occurred.

## Final major-requirement assessment

This reviewed assessment incorporates the post-stop, read-only preservation check.

| Requirement | Status | Evidence |
| --- | --- | --- |
| calendar_and_quality_engine | PASS | Versioned timezone/holiday/DST rules and blocking policies tested synthetically; no new historical audit admitted. |
| historical_calendar_verification | FAIL | 2022 session/holiday schedules remain unverified. |
| incremental_training_and_inference | PASS | Explicit online_softmax_v1; deterministic updates, calibration, split purging and no-test-fit checks passed. |
| streaming_simulation | PASS | Synthetic next-bar fills, costs, risk/margin bounds, P&L, censorship and duplicate prevention passed. |
| immutable_checkpoint_recovery | PASS | Synthetic before/after-commit crashes, corrupt evidence, model updates and pending/open-position recovery passed. |
| synthetic_independent_replay | PASS | Immutable file hashes and canonical logical-state hashes match independent synthetic replay. |
| real_engineering_validation_and_replay | FAIL | Not started: measured 1.35 GiB available RAM below required 1.5 GiB. |
| resource_safety | PASS | Admission gate stopped before child startup; stricter RAM floor and 1 GiB peak RSS ceiling retained. |
| historical_peak_memory_measurement | FAIL | Unavailable because no historical child started; no preparation RSS reused as a training measurement. |
| strat2c_and_source_preservation | PASS | 47 published file hashes and mtimes verified; no Phase 5 reads/writes or original-config changes. |
| automated_tests_and_static_checks | PASS | 138 tests passed; lint, formatting and whitespace checks passed. |
| broader_development_readiness | FAIL | Complete bounded historical crash/resume and replay validation after restoring RAM headroom, then address calendar verification. |
