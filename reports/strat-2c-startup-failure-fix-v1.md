# STRAT-2C preparation startup failure diagnosis and correction

Investigated in `C:\Users\jager\ai_trader\AI-Trading-STRAT-2C`, branch
`feature/strat-2c-multimonth-checkpoint`. No real-data preparation, training,
portfolio backtest, service startup, commit or push was performed.

## Exact cause and reproduction

The failure is a **configuration policy rejection**, not insufficient measured
RAM, a stale job ID, the source junction, or source registration.

Before correction, the runner called `validate_config` before `resources` and job
selection. The controller hardcoded a 1.5 GiB minimum permitted RAM threshold:

```python
floors = ((config["free_floor_gib"], 15), (config["minimum_available_ram_gib"], 1.5))
if any(not math.isfinite(value) or value < floor for value, floor in floors):
    raise ValueError("Safety floors cannot be reduced")
```

The actual validation config contains the intentionally chosen threshold **1.25
GiB**. Therefore its `validate_config` call immediately raised that ValueError,
regardless of whether available RAM was 2.05 GiB or higher. The disk floor is 15
GiB and was valid. No candle reader, source registration check or preparation
writer was reached.

Direct read-only reproduction before edits produced:

```text
validate_config: ValueError: Safety floors cannot be reduced
resources: ResearchSafetyStop: Research RAM minimum cannot be below 1.5 GiB
```

The second line identifies a second policy gate that would reject the config even
if only the controller check were changed. Both checks must use the same policy.
The current corrected source locations are:

- `src/trading/research/phase6/runner.py:711`: configuration validation before
  resource measurement (`resources` at line 713) and preparation/job dispatch.
- `src/trading/research/phase6/controller.py:59`: configured floor validation;
  line 70 retains the ValueError for invalid policy values.
- `src/trading/research/safety.py:17`: shared, narrowly scoped minimum-RAM policy.
- `src/trading/research/safety.py:53`: runtime resource guard using that policy
  and the actual configured threshold.

The config currently has no UTF-8 BOM and decodes successfully. The former BOM
issue was separate; loading now tolerates UTF-8 with or without a BOM without
rewriting the input file (`runner.py:707`).

## Why the CLI hid the cause and evidence was absent

The former module-level generic exception handler printed only the exception
class and this unconditional instruction:

```text
PHASE6_STOPPED ValueError; inspect preserved attempt evidence
```

It intentionally suppressed exception payloads for credential safety, but did not
provide a safe reason, source location or failure stage. It did not write evidence.

Experiment attempts are created by the controller's `run_queue` path. The
`worker-failure.json` writer (`runner.py:199`) belongs to `execute_experiment`,
which preparation never calls. Preparation publishes `stream-checkpoint.json`
only after its own validation, source selection and output initialization. This
failure happened before all of them, so there was no attempt or checkpoint to find.

Read-only evidence inventory examined Phase 6 directories in both:

```text
C:\Users\jager\ai_trader\AI-Trading-STRAT-2C\data\research
C:\Users\jager\ai_trader\AI-Trading-System\data\research
```

The search skipped junctions and Phase 5 datasets. No directory/evidence for job
`1d06fff895ee69a12b38` or the validation output was found. Outside the expected
output, the original worktree contains legacy `phase6a-v1/checkpoint.json`,
`phase6a-v1/diagnostics.json`, experiment artifacts under job
`560ce591165e80aef255`, and old smoke/synthetic test artifacts. Only names and sizes
were inventoried; those files were not opened or used as evidence for this failure.
The junction did not redirect a preparation output; the output directory is absent.

## Correction and diagnostics

The config itself was not changed. Both policy gates now use
`minimum_ram_floor_gib(config)`: **1.25 GiB is permitted only when all three hold**:
`preparation_only is True`, `mode == "offline_simulation"`, and `threads == 1`.
Experiments, Phase 5 callers and all other configurations retain the 1.5 GiB
policy minimum. Thresholds below 1.25 GiB for preparation are still rejected, the
15 GiB disk floor is unchanged, and finite numeric values are enforced consistently.
The runtime guard still stops when measured RAM is below the configured threshold.

The new CLI boundary (`runner.py:861`) retains exit 1 for ordinary failures and
exit 2 for resource stops. It records the stage, exception type, last available
worktree source-code location, command and syntactically valid job ID. Only fixed
public messages and tightly matched resource measurements may be shown. Unknown
exception payloads, locals, configuration contents, paths embedded in errors and
third-party traceback frames are omitted. JSON failures receive a fixed syntax/
encoding explanation. Credential-bearing synthetic errors are tested for redaction.

Future failures, including failures before validation output exists, are written
to unique, exclusively created and flushed files at:

```text
C:\Users\jager\ai_trader\AI-Trading-STRAT-2C\data\research\phase6-cli-failures-v1\failure-<uuid>.json
```

The console prints the exact relative report path plus safe reason/stage/location.
This fixed worktree-local location is independent of an invalid config's output.
Resolved directory paths escaping the worktree are rejected. Existing reports,
checkpoints and attempts are not overwritten. If evidence persistence is denied
or disk space is exhausted, the original sanitized console error remains visible
and says `failure_report=unavailable`. No actual startup failure report was created
by this investigation; synthetic report files are under the isolated test directory.

## Configuration, job ID and source confirmation

Unchanged validation configuration:
`config/phase6-strat2c-eurusd-2022-jan-feb-validation-v1.json`.

- SHA-256 before and after investigation:
  `88f7ac3a2fed8876f0b3f19ba9fb5673ab6ec0d6639361b1d6efc2d0a123bc3c`.
- `minimum_available_ram_gib` remains **1.25**; `free_floor_gib` remains 15.
- Only `EURUSD`, with `preparation_only=true` and offline single-threaded mode.
- Exactly one fold from `2022-01-01T00:00:00Z` to
  `2022-03-01T00:00:00Z` exclusive. The registered Phase 5 cache is M1; its
  snapshot producer queries `timeframe_minutes=1`. No unused timeframe selector
  was added to the config.
- Recomputed first job ID is still **`1d06fff895ee69a12b38`** (15-minute barrier
  label and trend model descriptor; no model is constructed).

An instrumented read-only call to the adapter's `_selection` verified that only
these registered partitions are checksum-read:

| Partition | Registered rows | Verified SHA-256 |
| --- | ---: | --- |
| `cache/EURUSD/2022-01.parquet` | 30066 | `bd722483b4d860db11fc639a327a8b1b9391296a35424d84bf17e0e8fa2128d1` |
| `cache/EURUSD/2022-02.parquet` | 28617 | `9982450f9a5bcafd9d74ea509bf99e18c8e6c2d043396cbc2bf70c6ae5c921e3` |

Total: **58683 registered rows**. No candle rows were loaded. The exact interval
and month pruning exclude 2026 before any holdout candle path is opened/hashed.
The source resolves through the existing junction to the original worktree's
`data/research/phase5-v1`. Selected partition and snapshot/contracts checksums and
modification times remained unchanged. No junction/source files were modified.

The original `config/phase6a-research-v1.json` is also unchanged, with SHA-256
`4de6c4b879753423240f3318099011395c3c2c368e11a2ff47d6afbba92bafbe`.
Job IDs depend on config/scientific descriptors, so these code-only fixes do not
alter the job ID. Streaming identities also bind code hashes, but no validation
checkpoint exists to invalidate. Other existing streaming outputs must not be
forced to resume across a changed code fingerprint.

The final read-only resource gate passed at approximately **1.98 GiB available RAM
and 29.52 GiB free disk**. Those measurements are transient and will be rechecked
by an eventual preparation invocation; they are not a promise of future headroom.

## Files changed for this correction

- `src/trading/research/safety.py`: shared scoped RAM policy and consistent runtime
  validation of finite numeric floors.
- `src/trading/research/phase6/controller.py`: use the shared policy.
- `src/trading/research/phase6/runner.py`: BOM-tolerant config loading, failure
  stage tracking, sanitized CLI diagnostics and unique fallback evidence.
- `tests/test_phase6_startup.py`: 27 new synthetic startup/policy/reporting cases.
- `tests/test_strat2c_validation_config.py`: reflect the user's intentional 1.25
  GiB config change while continuing to verify the other scientific parameters.
- `tests/test_phase6_controller.py`: isolate CLI failure reports in its test fixture.
- This new report. Existing reports were not overwritten. Earlier adapter changes
  remain uncommitted and were not replaced.

## Verification performed

| Check | Result |
| --- | --- |
| Startup/config/controller/controller-resource/runner/research-safety tests | 96 passed in 3.69 seconds |
| Selected synthetic streaming restart, crash-publication, holdout-exclusion, writer/resource-stop, dispatch, provenance and reader tests | 11 passed, 38 deselected in 16.43 seconds |
| Ruff lint and formatting across all changed Python files | Passed |
| `git diff --check` | Passed |
| Read-only config/job/resource and selected-partition registration verification | Passed |

New regressions cover the reported 2.05 GiB RAM / 29.54 GiB disk scenario, exact
1.25 GiB acceptance, below-threshold stops, unchanged experiment floors, invalid
numeric policies, wrong job IDs, BOM inputs, failure reports before output creation,
unique report names, credential redaction and unwritable-disk reporting. The
successful dispatch uses a mocked preparation function; real preparation is never
called. Known NumPy timedelta deprecation and runpy reimport warnings remain.

Tests used the existing research environment with `PYTHONPATH` set to this
worktree's `src`, bytecode writing disabled and pytest caching disabled. Fixtures
and JUnit reports are isolated at `.tmp/strat2c-startup-fix-v1*` and
`.tmp/strat2c-startup-stream-fix-v1*`; no existing test report was overwritten.

## Exact next bounded command (not executed)

From `C:\Users\jager\ai_trader\AI-Trading-STRAT-2C`:

```powershell
.\.venv\Scripts\python.exe -B -m trading.research.phase6.runner prepare --config config/phase6-strat2c-eurusd-2022-jan-feb-validation-v1.json --job-id 1d06fff895ee69a12b38 --batch-rows 8192 --max-chunks 1
```

Expected stream directory:

```text
data/research/phase6-strat2c-eurusd-2022-jan-feb-validation-v1/prepared-streams/1d06fff895ee69a12b38/EURUSD/
```

The directory is still absent. Investigation stops here without executing this
command or any training, portfolio backtest, broker/DB connection or live order.
