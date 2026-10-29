# STRAT-2C bounded real-data validation preparation v1

Prepared on 2026-10-26 in `AI-Trading-STRAT-2C`, branch
`feature/strat-2c-multimonth-checkpoint`. Real-data preparation has **not** run.
The validation output directory does not exist yet. No commit or push was made.

## Configuration and exact first invocation

Configuration: `config/phase6-strat2c-eurusd-2022-jan-feb-validation-v1.json`.
From `C:\Users\jager\ai_trader\AI-Trading-STRAT-2C`, once resource prerequisites
are satisfied and execution is separately requested:

```powershell
.\.venv\Scripts\python.exe -B -m trading.research.phase6.runner prepare --config config/phase6-strat2c-eurusd-2022-jan-feb-validation-v1.json --job-id 667cf7921233a4616ee7 --batch-rows 8192 --max-chunks 1
```

Expected per-asset output:

```text
data/research/phase6-strat2c-eurusd-2022-jan-feb-validation-v1/
  prepared-streams/667cf7921233a4616ee7/EURUSD/
    .stream.lock
    stream-manifest.json
    stream-checkpoint.json
    chunks/00000000/{bars.parquet,features.parquet,labels.parquet,state.json,receipt.json}
```

This job selects the existing first label (15-minute barrier, stop multiple 2,
reward/risk 3) and first model descriptor (`trend`, `phase6a-v1`). The model
descriptor is identifier metadata; preparation never constructs or trains a model.
At most one input chunk is committed by this invocation. If source row validation
succeeds, expect 8192 input rows, 8177 emitted rows and a 15-row pending target
tail; the first chunk is not the complete two-month validation.

## Every configuration change

The original `config/phase6a-research-v1.json` remains byte-for-byte unchanged
(SHA-256 `4de6c4b879753423240f3318099011395c3c2c368e11a2ff47d6afbba92bafbe`).
Only these five top-level keys differ in the separate configuration:

| Key | Change and reason |
| --- | --- |
| `version` | `phase6-strat2c-eurusd-2022-jan-feb-validation-v1`; separate validation identity |
| `output` | `data/research/phase6-strat2c-eurusd-2022-jan-feb-validation-v1`; unused worktree-local destination |
| `assets` | Only `EURUSD`, as requested |
| `folds` | One fold named `streaming-validation-2022-jan-feb`; `train_start=2022-01-01T00:00:00Z`, `test_end=2022-03-01T00:00:00Z` (exclusive) |
| `preparation_only` | Added as `true`; disallows other Phase 6 runner commands and experiment entrypoints |

The fold's required interior boundaries are `train_end=2022-01-15T00:00:00Z`,
`calibration_end=2022-02-01T00:00:00Z`, and
`tuning_end=2022-02-15T00:00:00Z`. These strictly increasing UTC values satisfy the
existing validator. Preparation uses only `train_start`/`test_end`; the interior
values do not represent a training/selection exercise.

All scientific values are inherited unchanged: seed, model and label definitions,
cost scenarios, features, risk, leverage, thresholds, embargo, resource floors and
budgets. The complete model/label lists remain present for compatibility with
`experiment_queue`: eight possible job descriptors, of which the command selects
exactly one. No matrix or experiment is executed. The selected EURUSD label cost
remains 2.1 roundtrip basis points (1 spread + 0.6 commission + twice 0.25 slippage).
Inherited evidence-limit statements about historical model evaluations do not
constitute new evidence from this preparation-only validation.

## Source selection, M1 provenance and read-only junction

The source path remains `data/research/phase5-v1`, a Windows directory junction to:

```text
C:\Users\jager\ai_trader\AI-Trading-System\data\research\phase5-v1
```

The resolved output is inside the STRAT-2C worktree, is not a junction into the
source, and has no ancestor/descendant overlap with the resolved source. Both
`controller.validate_config` and `streaming.prepare_stream` resolve paths before
rejecting overlaps. The source is treated as read-only; its ACLs/junction were not
changed. No source path is passed to a writer.

The Phase 5 snapshot writer registers `cache/ASSET/YYYY-MM.parquet` entries with
row counts, interval bounds and SHA-256 checksums after querying
`timeframe_minutes=1` and `source_provider='ctrader_open_api_demo'`. There is no
separate timeframe setting in the preparation config: this registered cache is
the existing M1 source. At execution, candle validation requires whole-minute UTC
timestamps and valid OHLC; missing minutes are gaps, never interpolation.

Read-only preflight called the adapter's actual `_selection` with the exact UTC
interval, instrumented its digest calls to reject any other candle path, and
confirmed that it selects **only** these registered entries:

| Partition | Rows | Bytes | SHA-256 verified against snapshot |
| --- | ---: | ---: | --- |
| `cache/EURUSD/2022-01.parquet` | 30066 | 551066 | `bd722483b4d860db11fc639a327a8b1b9391296a35424d84bf17e0e8fa2128d1` |
| `cache/EURUSD/2022-02.parquet` | 28617 | 548446 | `9982450f9a5bcafd9d74ea509bf99e18c8e6c2d043396cbc2bf70c6ae5c921e3` |

Total registered rows: 58683. Only the two selected files were checksum-read;
their Parquet footers were inspected without loading row groups or candle values.
Both report `timestamp[ns, tz=UTC]`, required OHLC columns, and matching row counts.
The EURUSD calendar metadata also constructs successfully without broker access.
Full candle integrity, chronological order and session/label compatibility remain
execution-time checks, not claims from this metadata-only inspection.

Checksums, sizes and modification times of both partitions and source
`snapshot.json`, `config.json`, `contracts.json`, `environment.json` were unchanged
across preflight. No source data was written, deleted or re-registered.

## Safety enforcement

Previously, any otherwise valid research config could be supplied to `run` or
`smoke`. To enforce this validation's scope, the optional `preparation_only`
boolean is now validated and checked before command dispatch. With this config,
`audit`, `plan`, `run`, `worker` and `smoke` reject before resource checks, source
enumeration, model construction, worker launch or output mutation. The controller's
`initialize`/`run_queue`, runner audit/smoke, and both experiment execution
entrypoints also reject direct Python calls with this flag. Existing configs that
omit the flag retain their behavior. This is an application guard, not an OS
sandbox against someone deliberately modifying code/configuration.

The permitted `prepare` branch calls only local contracts loading and the streaming
adapter. It does not call `initialize`, `source_manifest`, `protected_manifest`,
the snapshot writer, broker/DB clients, collectors, Docker, model fitting or
portfolio backtesting. Module imports define existing data/calendar functions;
they do not invoke their network/service operations.

The global development cutoff rejects any interval ending after 2026-01-01 before
snapshot/candle access. The selected interval ends in March 2022, and monthly
pruning occurs before a file is resolved, hashed or opened. Thus registered 2026
partitions cannot be opened by this invocation. No holdout contents were inspected.
Do not substitute an `audit`, `plan`, `run` or snapshot command.

## Resource assessment and blockers

Configured floors remain **15 GiB free disk** and **1.5 GiB available physical
RAM**. Windows measurements during preflight were:

| Measurement | Free disk GiB | Available RAM GiB | Resource gate |
| --- | ---: | ---: | --- |
| Before scientific imports | 29.73 | 1.49 | Blocked on RAM |
| After scientific imports | 29.73 | 1.41 | Blocked on RAM |
| Final recheck before scientific imports | 29.71 | 1.76 | Passed |
| Final recheck after scientific imports | 29.71 | 1.69 | Passed |

**The latest resource gate passes, with only about 0.19 GiB RAM headroom after
imports.** Earlier measurements failed, so available RAM is a transient execution
blocker if it drops below the floor again. Recheck before execution and free memory
if necessary; do not lower floors. The command checks resources before scientific imports and
again on preparation entry, source batches, receipt validation and terminal flush.
These are point-in-time measurements and will be repeated during execution.

The adapter retains at most 8192 input rows, 80 default rolling-context rows, and
15 pending rows for this label. Data is processed single-threaded, one asset at a
time. The inherited 1 GiB worker RSS budget and 1800-second worker timeout belong
to the experiment controller; **they are not enforced by the in-process prepare
command**. Its limits are structural row/context bounds, `--max-chunks`, and the
resource guard. Runtime RSS still needs observation in the planned validation.

Disk headroom is approximately 14.71 GiB above the floor. No validation output
has been created. The local `.venv` is Python 3.12.10 and imports the current
worktree's research code and required scientific libraries. It lacks pytest/ruff;
tests/lint use existing development tools read-only from the original worktree,
with `PYTHONPATH` directed to this worktree and bytecode/cache writing disabled.
No installation is required to execute preparation. No fixed execution blocker
was found by metadata/configuration inspection; candle validation remains deferred.

## Lightweight and synthetic verification

| Check | Result |
| --- | --- |
| New validation-config/command-guard tests plus existing controller, controller-resource and runner tests | 63 passed in 12.36 seconds |
| Selected synthetic streaming holdout-exclusion, restart, publication interruption, writer/resource-stop, dispatch and provenance tests | 11 passed, 38 deselected in 41.08 seconds |
| Ruff lint and formatting of changed Python modules and the new test module | Passed |
| `git diff --check`; original config diff; unused validation-output check | Passed |
| Actual `_selection`, registered checksums and two Parquet footers, without loading candle rows | Passed |

All 74 selected tests passed. The known NumPy timedelta deprecation warnings and
the existing runpy module-reimport warning remain. Test fixtures/JUnit results are
isolated under `.tmp/strat2c-config-2022-v1-tests*` and
`.tmp/strat2c-config-2022-v1-stream-tests*`; no research outputs were initialized.
The first-run dispatch test replaces `prepare_job` with a mock, so it does not run
real-data preparation. No model training or portfolio backtesting test was run.

Changes in this preparation task are limited to the new validation JSON, this
report, `tests/test_strat2c_validation_config.py`, and small opt-in command guards
in `phase6/controller.py` and `phase6/runner.py`. Earlier streaming-adapter changes
remain uncommitted and were not replaced.

## Planned interruption/resume and reference comparison (not executed)

1. Recheck resource floors, unused output paths and the two registered checksums.
   Keep source files, code, scientific arguments, Python environment and batch size
   identical through the entire test.
2. Run the exact first command above once. Inspect only its checkpoint/receipt and
   summary counters. Repeat `--max-chunks 1` to resume the same asset directory.
3. During a later invocation, interrupt only the preparation process with Ctrl+C.
   Do not edit/delete its lock, checkpoint, committed chunks or staging directories.
   Resume with the same command until `complete=true`. Recovery must verify the
   receipt chain, ignore incomplete staging and reconcile a published directory
   ahead of the checkpoint by deterministic replay. Each invocation is bounded.
4. After execution is separately requested, create an exclusive new reference
   config derived from this validation config, changing **only version and output**
   to a fresh `...-reference-v1` namespace. Recompute its job ID with
   `experiment_queue`; changing the outer config changes the job ID. Keep its
   `preparation_only=true`. Run its `prepare` once uninterrupted with
   `--batch-rows 8192 --max-chunks 16`. Based on the registered rows, expect eight
   input chunks plus one terminal flush (nine commits), assuming candle validation
   passes. This is still only the selected two-month preparation.
5. Compare the two **per-asset stream directories**, each beneath its own job ID.
   `stream-manifest.json`, `stream-checkpoint.json` and every checkpoint-referenced
   chunk's bars/features/labels/state/receipt must have identical SHA-256 hashes.
   Hash one file at a time using the existing bounded `digest`; do not concatenate
   full datasets. The adapter identity excludes output/version/job-directory names,
   so those outer reference changes do not prevent exact stream-byte comparison.
   Ignore `.stream.lock`, `.pending-*` and temporary checkpoint/manifest files:
   interrupted staging is intentionally preserved and never considered output.
6. Verify both completion flags, `rows_read == rows_written == 58683`, unique
   chronological timestamps via `iter_prepared` one chunk at a time, equal gap
   counters and no duplicate emission. Repeat resume after completion and confirm
   that committed file bytes and modification times do not change. Rehash the two
   input partitions and source metadata to confirm continued source immutability.

No annual backtest, live orders, broker/DB connection, collector or Docker service
is part of this plan. Do not run the real-data preparation until separately
requested and the RAM gate passes.
