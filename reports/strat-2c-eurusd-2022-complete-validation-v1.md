# STRAT-2C EURUSD January-February 2022 consolidated validation

Overall: **PASS**. Job `1d06fff895ee69a12b38`.

| Requirement/check | Status | Evidence |
| --- | --- | --- |
| automated_tests_and_static_checks | PASS | 98 synthetic tests; lint, formatting and diff checks passed |
| fail_closed_resource_and_integrity_guards | PASS | Active per-operation resource gate, OS peak RSS and fail-closed assertions |
| authorized_scope_and_resource_policy | PASS | Only EURUSD Jan-Feb 2022; 8192-row single-thread preparation; 1.25 GiB RAM / 15 GiB disk floors |
| source_and_code_identity | PASS | Only registered January/February EURUSD; source checksums and code match |
| initial_checkpoint_and_receipts | PASS | Verified generations 0 through 1 |
| automatic_bounded_resume | PASS | Completed generation 8 without restarting |
| previous_chunks_unchanged | PASS | Both original chunks and immutable manifest retain hashes and mtimes |
| source_output_rows_and_chronology | PASS | Every emitted bar equals the source row stream; unique ordered UTC minute timestamps |
| causal_feature_continuity | PASS | Batch oracle verified 302 contiguous eligible segments, including chunk/month boundaries |
| label_handling | PASS | Every label/outcome/end matches the horizon-bounded batch oracle, including terminal tail |
| source_gaps_and_month_boundary | PASS | Independent gap totals equal checkpoint; missing-calendar classification reconciles |
| final_checkpoint_and_receipts | PASS | Checkpoint envelope, identity and complete receipt/file chain verified |
| independent_deterministic_replay | PASS | All 47 committed file hashes match independent uninterrupted replay |
| repeated_resume_idempotence | PASS | Completed resume changes no committed hashes or mtimes |
| source_configs_and_production_code_unchanged | PASS | Checksums and source/config mtimes unchanged |
| bounded_memory_and_resource_policy | PASS | 8192-row batches, one numerical worker, active 1.25 GiB/15 GiB floors |

Full bounded-validation evidence:

```json
{
  "started_utc": "2026-10-26T19:35:32.515642+00:00",
  "checks": {
    "automated_tests_and_static_checks": {
      "status": "PASS",
      "details": "98 synthetic tests; lint, formatting and diff checks passed"
    },
    "fail_closed_resource_and_integrity_guards": {
      "status": "PASS",
      "details": "Active per-operation resource gate, OS peak RSS and fail-closed assertions"
    },
    "authorized_scope_and_resource_policy": {
      "status": "PASS",
      "details": "Only EURUSD Jan-Feb 2022; 8192-row single-thread preparation; 1.25 GiB RAM / 15 GiB disk floors"
    },
    "source_and_code_identity": {
      "status": "PASS",
      "details": "Only registered January/February EURUSD; source checksums and code match"
    },
    "initial_checkpoint_and_receipts": {
      "status": "PASS",
      "details": "Verified generations 0 through 1"
    },
    "automatic_bounded_resume": {
      "status": "PASS",
      "details": "Completed generation 8 without restarting"
    },
    "previous_chunks_unchanged": {
      "status": "PASS",
      "details": "Both original chunks and immutable manifest retain hashes and mtimes"
    },
    "source_output_rows_and_chronology": {
      "status": "PASS",
      "details": "Every emitted bar equals the source row stream; unique ordered UTC minute timestamps"
    },
    "causal_feature_continuity": {
      "status": "PASS",
      "details": "Batch oracle verified 302 contiguous eligible segments, including chunk/month boundaries"
    },
    "label_handling": {
      "status": "PASS",
      "details": "Every label/outcome/end matches the horizon-bounded batch oracle, including terminal tail"
    },
    "source_gaps_and_month_boundary": {
      "status": "PASS",
      "details": "Independent gap totals equal checkpoint; missing-calendar classification reconciles"
    },
    "final_checkpoint_and_receipts": {
      "status": "PASS",
      "details": "Checkpoint envelope, identity and complete receipt/file chain verified"
    },
    "independent_deterministic_replay": {
      "status": "PASS",
      "details": "All 47 committed file hashes match independent uninterrupted replay"
    },
    "repeated_resume_idempotence": {
      "status": "PASS",
      "details": "Completed resume changes no committed hashes or mtimes"
    },
    "source_configs_and_production_code_unchanged": {
      "status": "PASS",
      "details": "Checksums and source/config mtimes unchanged"
    },
    "bounded_memory_and_resource_policy": {
      "status": "PASS",
      "details": "8192-row batches, one numerical worker, active 1.25 GiB/15 GiB floors"
    }
  },
  "events": [
    {
      "event": "resume_commit",
      "utc": "2026-10-26T19:35:34.738879+00:00",
      "generation": 2,
      "rows_read": 24576,
      "rows_written": 24561,
      "gap_minutes": 8879,
      "complete": false
    },
    {
      "event": "resume_commit",
      "utc": "2026-10-26T19:35:35.214124+00:00",
      "generation": 3,
      "rows_read": 30066,
      "rows_written": 30051,
      "gap_minutes": 11813,
      "complete": false
    },
    {
      "event": "resume_commit",
      "utc": "2026-10-26T19:35:35.782235+00:00",
      "generation": 4,
      "rows_read": 38258,
      "rows_written": 38243,
      "gap_minutes": 14738,
      "complete": false
    },
    {
      "event": "resume_commit",
      "utc": "2026-10-26T19:35:36.374150+00:00",
      "generation": 5,
      "rows_read": 46450,
      "rows_written": 46435,
      "gap_minutes": 17683,
      "complete": false
    },
    {
      "event": "resume_commit",
      "utc": "2026-10-26T19:35:36.976513+00:00",
      "generation": 6,
      "rows_read": 54642,
      "rows_written": 54627,
      "gap_minutes": 20609,
      "complete": false
    },
    {
      "event": "resume_commit",
      "utc": "2026-10-26T19:35:37.370229+00:00",
      "generation": 7,
      "rows_read": 58683,
      "rows_written": 58668,
      "gap_minutes": 23516,
      "complete": false
    },
    {
      "event": "resume_commit",
      "utc": "2026-10-26T19:35:37.546854+00:00",
      "generation": 8,
      "rows_read": 58683,
      "rows_written": 58683,
      "gap_minutes": 23516,
      "complete": true
    },
    {
      "event": "validation_complete",
      "utc": "2026-10-26T19:35:52.601333+00:00",
      "peak_rss_bytes": 159789056,
      "minimum_available_ram_gib": 1.6798286437988281,
      "minimum_free_disk_gib": 29.479373931884766,
      "guard_calls": 732
    }
  ],
  "config_sha256": "88f7ac3a2fed8876f0b3f19ba9fb5673ab6ec0d6639361b1d6efc2d0a123bc3c",
  "job_id": "1d06fff895ee69a12b38",
  "stream": "C:\\Users\\jager\\ai_trader\\AI-Trading-STRAT-2C\\data\\research\\phase6-strat2c-eurusd-2022-jan-feb-validation-v1\\prepared-streams\\1d06fff895ee69a12b38\\EURUSD",
  "audit_directory": "C:\\Users\\jager\\ai_trader\\AI-Trading-STRAT-2C\\data\\research\\phase6-strat2c-eurusd-2022-validation-audit-v1",
  "reference_directory": "C:\\Users\\jager\\ai_trader\\AI-Trading-STRAT-2C\\data\\research\\phase6-strat2c-eurusd-2022-replay-v1",
  "automated_verification": {
    "junit": "C:\\Users\\jager\\ai_trader\\AI-Trading-STRAT-2C\\.tmp\\strat2c-completion-tests-v1.xml",
    "counts": {
      "tests": 98,
      "errors": 0,
      "failures": 0,
      "skipped": 0
    },
    "static_checks": {
      "ruff_lint": "PASS",
      "ruff_format": "PASS",
      "git_diff_check": "PASS"
    }
  },
  "resources_start": {
    "rss_bytes": 20045824,
    "peak_rss_bytes": 20762624,
    "available_ram_gib": 1.8032341003417969,
    "free_disk_gib": 29.491180419921875
  },
  "initial_state": {
    "cursor": {
      "batch": 2,
      "part": 0
    },
    "rows_read": 16384,
    "rows_written": 16369,
    "gap_minutes": 5944,
    "complete": false
  },
  "initial_generation": 1,
  "final_state": {
    "cursor": {
      "part": 2,
      "batch": 0
    },
    "rows_read": 58683,
    "rows_written": 58683,
    "gap_minutes": 23516,
    "complete": true
  },
  "final_generation": 8,
  "content": {
    "rows": 58683,
    "month_rows": {
      "2022-01": 30066,
      "2022-02": 28617
    },
    "labels": {
      "-1": 7050,
      "0": 26164,
      "2": 12921,
      "1": 12548
    },
    "first_open": "2022-01-02T22:01:00+00:00",
    "last_open": "2022-02-28T23:59:00+00:00",
    "internal_gap_minutes": 23516,
    "internal_gap_count": 301,
    "gap_size_counts": {
      "2-60": 89,
      "1": 204,
      ">60": 8
    },
    "largest_internal_gaps": [
      {
        "start": "2022-01-07T21:55:00+00:00",
        "end_exclusive": "2022-01-09T22:01:00+00:00",
        "minutes": 2886
      },
      {
        "start": "2022-01-14T21:55:00+00:00",
        "end_exclusive": "2022-01-16T22:01:00+00:00",
        "minutes": 2886
      },
      {
        "start": "2022-01-21T21:55:00+00:00",
        "end_exclusive": "2022-01-23T22:01:00+00:00",
        "minutes": 2886
      },
      {
        "start": "2022-01-28T21:55:00+00:00",
        "end_exclusive": "2022-01-30T22:01:00+00:00",
        "minutes": 2886
      },
      {
        "start": "2022-02-04T21:55:00+00:00",
        "end_exclusive": "2022-02-06T22:01:00+00:00",
        "minutes": 2886
      },
      {
        "start": "2022-02-11T21:55:00+00:00",
        "end_exclusive": "2022-02-13T22:01:00+00:00",
        "minutes": 2886
      },
      {
        "start": "2022-02-18T21:55:00+00:00",
        "end_exclusive": "2022-02-20T22:01:00+00:00",
        "minutes": 2886
      },
      {
        "start": "2022-02-25T21:55:00+00:00",
        "end_exclusive": "2022-02-27T22:01:00+00:00",
        "minutes": 2886
      },
      {
        "start": "2022-02-14T22:19:00+00:00",
        "end_exclusive": "2022-02-14T22:24:00+00:00",
        "minutes": 5
      },
      {
        "start": "2022-01-02T22:02:00+00:00",
        "end_exclusive": "2022-01-02T22:06:00+00:00",
        "minutes": 4
      }
    ],
    "missing_calendar_minutes_including_edges": 26277,
    "missing_expected_open_minutes": 378,
    "known_closed_absent_minutes": 25899,
    "calendar_limitation": "Frozen current broker schedule; historical accuracy unverified",
    "month_boundaries": [
      {
        "previous_open": "2022-01-31T23:59:00+00:00",
        "next_open": "2022-02-01T00:00:00+00:00",
        "missing_minutes": 0
      }
    ],
    "batch_reference_feature_segments": 302,
    "maximum_reference_segment_rows": 1435,
    "batch_reference_feature_rows": 58667,
    "chunks": [
      {
        "generation": 0,
        "rows": 8177,
        "first_open": "2022-01-02T22:01:00+00:00",
        "last_open": "2022-01-10T15:44:00+00:00"
      },
      {
        "generation": 1,
        "rows": 8192,
        "first_open": "2022-01-10T15:45:00+00:00",
        "last_open": "2022-01-18T09:53:00+00:00"
      },
      {
        "generation": 2,
        "rows": 8192,
        "first_open": "2022-01-18T09:54:00+00:00",
        "last_open": "2022-01-26T03:18:00+00:00"
      },
      {
        "generation": 3,
        "rows": 5490,
        "first_open": "2022-01-26T03:21:00+00:00",
        "last_open": "2022-01-31T23:44:00+00:00"
      },
      {
        "generation": 4,
        "rows": 8192,
        "first_open": "2022-01-31T23:45:00+00:00",
        "last_open": "2022-02-08T17:01:00+00:00"
      },
      {
        "generation": 5,
        "rows": 8192,
        "first_open": "2022-02-08T17:02:00+00:00",
        "last_open": "2022-02-16T10:38:00+00:00"
      },
      {
        "generation": 6,
        "rows": 8192,
        "first_open": "2022-02-16T10:39:00+00:00",
        "last_open": "2022-02-24T03:56:00+00:00"
      },
      {
        "generation": 7,
        "rows": 4041,
        "first_open": "2022-02-24T03:57:00+00:00",
        "last_open": "2022-02-28T23:44:00+00:00"
      },
      {
        "generation": 8,
        "rows": 15,
        "first_open": "2022-02-28T23:45:00+00:00",
        "last_open": "2022-02-28T23:59:00+00:00"
      }
    ]
  },
  "finished_utc": "2026-10-26T19:35:52.601333+00:00",
  "metrics": {
    "peak_rss_bytes": 159789056,
    "minimum_available_ram_gib": 1.6798286437988281,
    "minimum_free_disk_gib": 29.479373931884766,
    "guard_calls": 732
  },
  "overall_status": "PASS",
  "resources_end": {
    "rss_bytes": 155107328,
    "peak_rss_bytes": 159789056,
    "available_ram_gib": 1.6797904968261719,
    "free_disk_gib": 29.479373931884766
  }
}
```

On resource stop, preserve all files and rerun the original bounded prepare command after the unchanged 1.25 GiB RAM / 15 GiB disk gates pass. Never reset the checkpoint.
On identity/corruption failure, investigate preserved evidence before resuming.

For an autonomous recovery once headroom is restored, rerun from the same worktree:
```powershell
.\.venv\Scripts\python.exe -B -m tools.strat2c_stream_validation
```

No training, portfolio backtest, holdout access, broker/DB connection, collector, Docker, commit or push was performed.

## Reviewed results

**PASS:** 58,683 input rows / 58,683 output rows; nine committed chunks (eight input batches plus the 15-row terminal flush). Seven chunks were added to the original run.

Checkpoint generation **8**, `complete=true`, cursor `{part: 2, batch: 0}`. No pending label tail remains. January has 30,066 rows and February 28,617.

Resume integrity, full receipt-chain verification, completed-run idempotence and all **47 file hashes** against the uninterrupted replay passed.

Peak process working set: **152.4 MiB** (159,789,056 bytes, Windows OS high-water counter). Available RAM started at 1.80 GiB and ended at 1.68 GiB; free disk started at 29.49 GiB and ended at 29.48 GiB. The 1.25 GiB RAM and 15 GiB disk floors were unchanged. These are sampled availability measurements; the working-set peak is an OS cumulative measurement.

## Gap and eligibility interpretation

There are **301 internal source gaps totaling 23,516 missing minutes**. Eight large weekend gaps each span 2,886 minutes; 204 gaps are one minute and 89 span 2?60 minutes. Including the leading 2,761-minute absence, the complete requested interval contains 26,277 absent calendar minutes. No missing prices were imputed.

Against the frozen current broker calendar, **25,899 absent minutes are known closed and 378 are expected open but missing**. Sixteen observed candles fall outside that calendar; their feature rows are unavailable. Historical applicability of this calendar remains unverified, so these classifications require data-quality review.

The January/February boundary is contiguous: 2022-01-31 23:59 UTC to 2022-02-01 00:00 UTC. Causal features were compared to independent bounded batch calculations over 302 contiguous eligible segments (maximum 1,435 rows); the 16 excluded candle rows were checked separately. Every future-only label, label end and outcome matched a horizon-bounded batch calculation. There are **51,633 available labels and 7,050 unavailable labels**; no terminal target was fabricated.

## Execution requirements

| # | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Inspect existing artifacts | PASS | Initial generation 1 inspected; baseline hashes/mtimes saved before resume. |
| 2 | Source identity and initial checkpoint integrity | PASS | Registered selected files, environment/scientific identity, code and receipt chain match. |
| 3 | Automatically resume existing job | PASS | Original stream advanced from generation 1 to 8; no restart. |
| 4 | Process all remaining two-month data | PASS | 58,683 input and emitted rows; seven new committed chunks. |
| 5 | 8192 rows, one numerical worker, 1.25 GiB floor | PASS | Settings preserved; 732 resource observations; no guard failure. |
| 6 | Bounded memory | PASS | Peak working set 152.4 MiB; source/output frames at most 8192 rows; batch feature oracle at most 1435 rows. |
| 7 | Use existing checkpoint implementation | PASS | Existing prepare_stream called repeatedly with max_chunks=1 until complete. |
| 8 | Published chunk immutability | PASS | Both original chunks and manifest retain exact bytes and modification times. |
| 9 | Ordering, causality, labels, counts, gaps, month boundary | PASS | All source rows matched; 302 eligible segments checked; all labels checked; month boundary has zero missing minutes. |
| 10 | Checkpoint and receipt hashes | PASS | Envelope/manifest identity, predecessor chain and all referenced files verified. |
| 11 | Independent replay | PASS | Uninterrupted replay in unique replay-v1 directory; all 47 committed file hashes identical. |
| 12 | Memory, available RAM and disk measurements | PASS | Windows OS peak working set measured; minimum sampled RAM 1.68 GiB, disk 29.48 GiB. |
| 13 | Automatic safe failure behavior | PASS | Fail-closed resource/integrity checks active; synthetic failure tests passed; no real failure occurred. |
| 14 | Preserve progress on transient resource failure | PASS | Failure path tested and recovery command documented; not triggered during this successful run. |
| 15 | Automated tests and static checks | PASS | 98 tests passed; Ruff lint/format and git diff --check passed. |
| 16 | Consolidated per-requirement report | PASS | This report, machine-readable result, before/after hashes and replay hashes saved without replacing prior reports. |

## Blockers before streaming training and portfolio replay

Preparation and its resume/replay checks have no remaining technical failure. The existing model-fitting and portfolio execution paths still consume batch frames; this validation does not implement or validate streaming model training, inference or a durable shared portfolio ledger.

Before those stages: define bounded training/calibration sampling and versioned model artifacts; enforce label-availability purging/embargo at each split; design checkpointed cross-instrument portfolio state; and measure their separate RAM requirements. Keep future labels isolated from causal predictors.

Review the 378 expected-open missing minutes, 16 outside-calendar observations and the unverified historical calendar/price/cost assumptions before making research performance claims. The 2022 two-month preparation results establish no trading profitability or approval.

New files for this validation are the bounded orchestration/verification tool `tools/strat2c_stream_validation.py`, its two synthetic tests, this consolidated report, and uniquely versioned audit/replay artifacts. Production Phase 6 code, both configurations and Phase 5 source checksums/mtimes were preserved. No model training, portfolio backtest, 2026 holdout access, broker connection, service startup, commit or push occurred.
