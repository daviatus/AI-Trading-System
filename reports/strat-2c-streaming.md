# STRAT-2C streaming research preparation

## Architecture and scope

The Phase 6 controller registers immutable code/source evidence, runs one bounded
worker and monitors resource floors, RSS and wall time. Its checkpoint tracks
experiments, and interrupted experiments get new attempt directories. The runner's
`load_asset` prunes monthly partitions and reads Arrow batches, then concatenates
the complete fold. Feature/label calculation, model fitting, signal selection and
portfolio execution operate on those in-memory frames. Execution shares a single
position ledger across instruments and cannot safely be restarted independently
for each data chunk. Validation remains development-only; diagnostic sinks are
best-effort observers, so they cannot serve as transaction journals.

The compatible extension is a separate, opt-in preparation path:

1. `streaming.prepare_stream` selects only registered development partitions for
   one asset and interval, verifies checksums and reads projected, single-threaded
   Arrow batches. It never sorts, deduplicates or interpolates prices.
2. `CausalFeatures` carries finite rolling history and recursive EMA seeds across
   batches and months. The existing feature formulas are shared; the existing
   `make_features` interface and default behavior are unchanged. Missing minutes
   and excluded session candles reset indicator state.
3. A bounded pending tail delays target publication until enough future rows are
   available. Existing `make_labels` handles target validity, missing minutes,
   barriers and label-end times. The terminal tail is flushed with unavailable
   outcomes represented by label -1 and NaT, preserving the existing semantics.
4. `iter_prepared` yields committed bars, causal features and future-only labels
   as separate frames. Consumers must keep targets out of prediction/signal inputs
   and apply the existing `purged_split` to label ends for fitting and metrics.

This path does not train or backtest. Existing `run`, worker recovery, models,
execution, validation and diagnostic behavior remain unchanged. In particular,
this work does not make the legacy full experiment runner memory bounded across
arbitrary multi-month folds or checkpoint an open portfolio position.

## Bounded invocation and resume

Use a separate versioned Phase 6 output in the configuration. Obtain the job ID
without initializing or running the research queue:

```powershell
python -c "import json; from pathlib import Path; from trading.research.phase6.controller import experiment_queue; print(experiment_queue(json.loads(Path('config/phase6a-research-v1.json').read_text()))[0]['id'])"
python -m trading.research.phase6.runner prepare --config config/phase6a-research-v1.json --job-id JOB_ID --batch-rows 8192 --max-chunks 1
```

The CLI defaults to one committed input chunk per configured asset per invocation.
The terminal flush also counts as a chunk. Repeat the identical command to resume;
`--max-chunks` may change without changing scientific inputs. Artifacts reside in
`OUTPUT/prepared-streams/JOB_ID/ASSET`. The original experiment checkpoint and
attempt artifacts are independent. The CLI validates offline simulation settings
and checks existing resource floors. It accesses local frozen snapshots only.

The Python API accepts `max_chunks=None` for a caller-controlled complete stream,
and an optional resource guard. Library callers should supply a guard when using
historical data; the CLI always supplies the existing resource gate.

## Checkpoint and publication protocol

`stream-checkpoint.json` is a canonical JSON envelope containing a SHA-256 digest
and payload: schema version, run identity, committed generation and latest receipt
digest. The identity binds selected source checksums, source location, asset,
UTC interval, feature/label specs, calendar metadata, batch size, relevant code
hashes and pandas/NumPy/Arrow versions. Changed inputs require a new output version.
`stream-manifest.json` preserves those identity inputs as readable canonical JSON;
both writers and readers verify its hash against the checkpoint identity.

Each immutable `chunks/00000000` directory contains:

- `bars.parquet`, `features.parquet`, `labels.parquet`;
- `state.json`: source partition/batch cursor, last observed open timestamp,
  rolling/EMA state, pending raw bars/features, row/gap counters and completion;
- `receipt.json`: generation, identity, predecessor receipt digest, emitted row
  count and SHA-256 checksums for every data/state file.

Floating-point state uses JSON round-trip precision, explicit frame dtypes and
nanosecond UTC indices. NaN values become JSON null, never nonstandard JSON tokens.
Input batches are fixed for the bound batch size, making replays reproducible.

An OS lock excludes concurrent writers. Files are written in a unique staging
directory and fsynced; the directory is atomically renamed into place. A flushed
temporary checkpoint then replaces the checkpoint atomically. POSIX directories
are also fsynced. On Windows, file fsync and same-volume atomic replacement are
used; storage hardware and Windows metadata persistence still govern power-loss
durability. Process interruption is covered by fault-injection tests.

Recovery verifies committed receipt/file hashes in bounded memory. It restores
the last committed state and rereads at most the current monthly partition to
skip previously consumed Arrow batches. Staging directories are ignored and
preserved. A published directory ahead of the checkpoint is recomputed and adopted
only when its receipt and file hashes exactly match the replay. Readers follow
the checkpoint, so unpublished/partial chunks never become visible. Repeated
resume after completion validates evidence without rewriting output.

Malformed/missing checkpoints, missing or corrupt committed artifacts, changed
sources and conflicting orphan chunks fail closed. There is no automatic rollback
that could conceal damaged evidence. Investigate preserved files or choose a new
output version. No existing historical data or reports are deleted.

## Memory and ordering

RAM use depends on batch size and indicator/label periods, not the number of
months: at most 8192 input rows, finite feature context (80 rows with defaults),
and at most the label horizon (maximum 1440 rows) of unresolved bars/features.
Label working arrays cover at most batch rows plus horizon; no full-fold calendar
grid or future matrix is allocated. Only one asset is prepared at a time.
Manifest metadata grows with source partition count; checkpoint payload size is
constant and receipt verification reads one generation at a time.

UTC minute alignment, OHLC validity, monthly membership and strictly increasing
unique timestamps are enforced. Unregistered missing months are genuine gaps,
recorded in gap-minute counters; registered missing files fail. Leading/trailing
absence is not imputed or represented by synthetic rows. Month boundaries alone
do not reset indicators or discard targets. Features use only candles already
closed; target lookahead is isolated from feature calculation. Future-only target
end times preserve existing split purging/embargo behavior.

## Automated validation

Validation used the existing research virtual environment with `PYTHONPATH` set
to this worktree's `src` and bytecode writing disabled. All fixtures and JUnit
results were written under this worktree's ignored `.tmp` directory. No packages
were installed and no other worktree was edited.

| Check | Result |
| --- | --- |
| Phase 6 controller/resources/features/models/execution/signals/runner/validation, streaming, research, diagnostics and safety tests | 242 passed in 189.74 seconds |
| Final focused recovery, corruption, state bounds and volume tests, after adding infinite-volume rejection | 18 passed, 31 deselected in 25.37 seconds |
| Ruff lint across `src/trading/research` and the new test module | Passed |
| Ruff formatting across Phase 6 and the new test module | Passed |
| `git diff --check` and CLI `--help` | Passed |

The final focused run includes three new cases, bringing the streaming module to
49 tests. The regression run includes the prior 46 streaming cases. Coverage
includes 1/17/67/8192-row chunks; all three label families; month transitions;
missing minutes and a missing month; session resets; volume absence; short terminal
tails; future perturbation and label-boundary purging; byte-identical interrupted
versus uninterrupted output; repeated resume; checkpoint/manifest/data corruption;
publication fault injection before and after checkpoint replacement; lock/resource
stops; source identity changes; and poison holdout files that must remain unopened.

JUnit results: `.tmp/strat2c-regression-01.xml` and
`.tmp/strat2c-final-focus.xml`. The environment emitted NumPy timedelta deprecation
warnings and the existing runpy module-reimport warning; no test failed in either
reported run. Historical snapshots, the actual 2026 holdout, database/broker
connections, collectors and Docker services were not accessed. No annual backtest
was run, and no changes were committed or pushed.

## Limitations and next validation

Disk use grows with emitted data and preserved crash staging directories. Resume
rehashes selected inputs and committed outputs, trading bounded RAM for additional
I/O. Tiny batch sizes are correct but expensive. Source files must remain immutable
during an invocation; checksums bind the starting evidence, rather than providing
a filesystem snapshot against concurrent external writers. Numerical feature
agreement with the batch reference is tested with float32 tolerances; replay with
identical inputs/environment is tested byte-for-byte. Historical broker calendars,
spreads and source publication/revision times retain existing evidence limitations.

Next: prepare a deliberately small, pre-2026 two-month development slice in a new
output directory, enforce a low RSS ceiling, terminate the process between chunk
commits, resume, and compare committed hashes against uninterrupted preparation.
Do not run the full annual backtest. Connecting model sampling, inference and a
single continuous portfolio ledger to this iterator requires a separate integration.
