"""Validate/resume only the authorized EURUSD January-February 2022 preparation.

No model, portfolio, database, broker, collector or Docker operations are invoked.
Production code remains unchanged so existing stream fingerprints stay valid.
"""

import ctypes
import json
import os
import shutil
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from trading.research.phase6.controller import (
    canonical,
    code_manifest,
    controller_lock,
    digest,
    experiment_queue,
    validate_config,
)
from trading.research.safety import ROOT, ResearchSafetyStop, available_memory, resources

CONFIG = ROOT / "config/phase6-strat2c-eurusd-2022-jan-feb-validation-v1.json"
JOB = "1d06fff895ee69a12b38"
PARTS = ["cache/EURUSD/2022-01.parquet", "cache/EURUSD/2022-02.parquet"]
GIB = 1024**3


class ValidationError(ValueError):
    """Fixed, credential-free assertions from this validation tool."""


def require(condition, reason):
    if not condition:
        raise ValidationError(reason)


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical(value) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def process_memory():
    """Windows OS high-water working set, including peaks between guard calls."""
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in (
                "PeakWorkingSetSize",
                "WorkingSetSize",
                "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage",
                "PagefileUsage",
                "PeakPagefileUsage",
            )
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Counters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    value = Counters()
    value.cb = ctypes.sizeof(value)
    require(
        bool(psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(value), value.cb)),
        "Process memory measurement unavailable",
    )
    return {"rss_bytes": int(value.WorkingSetSize), "peak_rss_bytes": int(value.PeakWorkingSetSize)}


def state_at(stream):
    envelope = json.loads((stream / "stream-checkpoint.json").read_text("utf-8"))
    checkpoint = envelope["payload"]
    state = json.loads(
        (stream / "chunks" / f"{checkpoint['generation']:08d}" / "state.json").read_text("utf-8")
    )
    return checkpoint, state


def committed_hashes(stream, generation):
    paths = [stream / "stream-manifest.json", stream / "stream-checkpoint.json"]
    for number in range(generation + 1):
        directory = stream / "chunks" / f"{number:08d}"
        require(
            not directory.is_junction() and not directory.is_symlink(), "Unexpected chunk alias"
        )
        paths.extend(
            directory / name
            for name in (
                "bars.parquet",
                "features.parquet",
                "labels.parquet",
                "state.json",
                "receipt.json",
            )
        )
    return {
        p.relative_to(stream).as_posix(): {"sha256": digest(p), "mtime_ns": p.stat().st_mtime_ns}
        for p in paths
    }


class Validation:
    def __init__(self):
        self.config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
        self.output, self.source = validate_config(self.config)
        self.stream = self.output / "prepared-streams" / JOB / "EURUSD"
        require(
            self.config["assets"] == ["EURUSD"] and self.config["preparation_only"] is True,
            "Unexpected validation scope",
        )
        require(
            self.config["threads"] == 1 and self.config["minimum_available_ram_gib"] == 1.25,
            "Unexpected worker/resource settings",
        )
        self.job = next(j for j in experiment_queue(self.config) if j["id"] == JOB)
        require(
            self.job["fold"]["train_start"] == "2022-01-01T00:00:00Z"
            and self.job["fold"]["test_end"] == "2022-03-01T00:00:00Z",
            "Unexpected interval",
        )
        self.stage = "preflight"
        self.evidence = {
            "started_utc": datetime.now(UTC).isoformat(),
            "checks": {},
            "events": [],
            "config_sha256": digest(CONFIG),
            "job_id": JOB,
            "stream": str(self.stream),
        }
        self.metrics = {
            "peak_rss_bytes": 0,
            "minimum_available_ram_gib": None,
            "minimum_free_disk_gib": None,
            "guard_calls": 0,
        }
        self.audit = self.unique("data/research/phase6-strat2c-eurusd-2022-validation-audit")
        self.report = self.unique("reports/strat-2c-eurusd-2022-complete-validation", suffix=".md")
        self.reference = self.unique("data/research/phase6-strat2c-eurusd-2022-replay")
        self.evidence.update(
            audit_directory=str(self.audit), reference_directory=str(self.reference)
        )

    @staticmethod
    def unique(prefix, suffix=""):
        for version in range(1, 10000):
            path = ROOT / f"{prefix}-v{version}{suffix}"
            if not path.exists():
                return path
        raise ValueError("No unused validation version")

    def check(self, name, details):
        self.evidence["checks"][name] = {"status": "PASS", "details": details}

    def event(self, name, **values):
        record = {"event": name, "utc": datetime.now(UTC).isoformat(), **values}
        self.evidence["events"].append(record)
        print(canonical(record), flush=True)

    def guard(self):
        memory = process_memory()
        ram = available_memory() / GIB
        disk = shutil.disk_usage(ROOT).free / GIB
        self.metrics["peak_rss_bytes"] = max(
            self.metrics["peak_rss_bytes"], memory["peak_rss_bytes"]
        )
        for key, value in (("minimum_available_ram_gib", ram), ("minimum_free_disk_gib", disk)):
            self.metrics[key] = (
                value if self.metrics[key] is None else min(self.metrics[key], value)
            )
        self.metrics["guard_calls"] += 1
        resources(self.config)
        if memory["peak_rss_bytes"] > self.config["max_worker_ram_gib"] * GIB:
            raise ResearchSafetyStop("Validation process exceeded configured 1 GiB RAM budget")

    def run(self):
        junit = ROOT / ".tmp/strat2c-completion-tests-v1.xml"
        suites = ET.parse(junit).getroot().iter("testsuite")
        totals = Counter()
        for suite in suites:
            totals.update(
                {k: int(suite.get(k, "0")) for k in ("tests", "errors", "failures", "skipped")}
            )
        static_path = ROOT / ".tmp/strat2c-completion-static-v1.json"
        static = json.loads(static_path.read_text("utf-8"))
        require(
            totals["tests"] == 98 and totals["errors"] == totals["failures"] == 0,
            "Automated verification did not pass",
        )
        require(
            all(value == "PASS" for value in static.values()), "Static verification did not pass"
        )
        self.evidence["automated_verification"] = {
            "junit": str(junit),
            "counts": dict(totals),
            "static_checks": static,
        }
        self.check(
            "automated_tests_and_static_checks",
            "98 synthetic tests; lint, formatting and diff checks passed",
        )
        self.guard()
        self.check(
            "fail_closed_resource_and_integrity_guards",
            "Active per-operation resource gate, OS peak RSS and fail-closed assertions",
        )
        self.check(
            "authorized_scope_and_resource_policy",
            "Only EURUSD Jan-Feb 2022; 8192-row single-thread preparation; "
            "1.25 GiB RAM / 15 GiB disk floors",
        )
        self.evidence["resources_start"] = {
            **process_memory(),
            "available_ram_gib": available_memory() / GIB,
            "free_disk_gib": shutil.disk_usage(ROOT).free / GIB,
        }
        # Save hashes before any processing. Checkpoint changes during resume are expected;
        # immutable manifest and already published chunk bytes/mtimes must not change.
        checkpoint, initial = state_at(self.stream)
        self.evidence["initial_state"] = {
            k: initial[k]
            for k in ("cursor", "rows_read", "rows_written", "gap_minutes", "complete")
        }
        self.evidence["initial_generation"] = checkpoint["generation"]
        self.baseline = committed_hashes(self.stream, checkpoint["generation"])
        write_new(self.audit / "before-resume-hashes.json", self.baseline)
        manifest = json.loads((self.stream / "stream-manifest.json").read_text("utf-8"))
        require(manifest["code"] == code_manifest(), "Code identity mismatch")
        require([p["path"] for p in manifest["parts"]] == PARTS, "Unexpected source partitions")
        require(
            manifest["batch_rows"] == 8192 and manifest["source"] == str(self.source),
            "Unexpected batch/source identity",
        )
        watched = [
            self.source / name
            for name in ("snapshot.json", "contracts.json", "config.json", "environment.json")
        ]
        watched += [self.source / p for p in PARTS]
        watched += [CONFIG, ROOT / "config/phase6a-research-v1.json"]
        self.source_before = {
            str(p): {"sha256": digest(p), "mtime_ns": p.stat().st_mtime_ns} for p in watched
        }
        write_new(self.audit / "source-before.json", self.source_before)
        for part in manifest["parts"]:
            self.guard()
            require(
                digest(self.source / part["path"]) == part["sha256"], "Source checksum mismatch"
            )
        self.check(
            "source_and_code_identity",
            "Only registered January/February EURUSD; source checksums and code match",
        )
        snapshot = json.loads((self.source / "snapshot.json").read_text("utf-8"))
        self.expected_month_rows = {Path(p).stem: snapshot["parts"][p]["rows"] for p in PARTS}
        require(
            self.expected_month_rows == {"2022-01": 30066, "2022-02": 28617},
            "Unexpected registered source row counts",
        )
        self.expected_rows = sum(self.expected_month_rows.values())

        for name in (
            "OMP_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "MKL_NUM_THREADS",
            "NUMEXPR_NUM_THREADS",
        ):
            os.environ[name] = "1"
        import numpy as np
        import pandas as pd
        import pyarrow as pa

        from trading.research.phase6 import streaming
        from trading.research.phase6.features import FeatureSpec
        from trading.research.phase6.labels import LabelSpec

        self.guard()
        self.np, self.pd, self.streaming = np, pd, streaming
        pa.set_cpu_count(1)
        pa.set_io_thread_count(1)
        self.feature_spec = FeatureSpec(max_rows=self.config["max_rows_per_asset"])
        cost = self.config["costs"]["EURUSD"]
        self.label_spec = LabelSpec(
            **self.job["label"],
            cost_bps=cost["spread_bps"]
            + cost["commission_bps_roundtrip"]
            + 2 * cost["slippage_bps_per_side"],
        )
        self.metadata = json.loads((self.source / "contracts.json").read_text("utf-8"))["EURUSD"]
        expected = {
            **manifest,
            "features": asdict(self.feature_spec),
            "labels": asdict(self.label_spec),
            "metadata": self.metadata,
            "code": code_manifest(),
            "libraries": {
                "numpy": np.__version__,
                "pandas": pd.__version__,
                "arrow": pa.__version__,
            },
        }
        require(
            streaming._hash(expected) == checkpoint["identity"],
            "Current scientific/environment identity mismatch",
        )
        streaming._verify_chain(self.stream, streaming._read_checkpoint(self.stream), self.guard)
        self.check(
            "initial_checkpoint_and_receipts",
            f"Verified generations 0 through {checkpoint['generation']}",
        )

        self.stage = "resume"
        for _ in range(16):
            self.guard()
            state = self.prepare(self.stream, max_chunks=1)
            checkpoint, _ = state_at(self.stream)
            self.event(
                "resume_commit",
                generation=checkpoint["generation"],
                **{k: state[k] for k in ("rows_read", "rows_written", "gap_minutes", "complete")},
            )
            if state["complete"]:
                break
        require(state["complete"], "Bounded invocation budget exhausted before completion")
        require(
            state["rows_read"] == state["rows_written"] == self.expected_rows,
            "Unexpected final row count",
        )
        self.evidence["final_state"] = {
            k: state[k] for k in ("cursor", "rows_read", "rows_written", "gap_minutes", "complete")
        }
        self.evidence["final_generation"] = checkpoint["generation"]
        self.check(
            "automatic_bounded_resume",
            f"Completed generation {checkpoint['generation']} without restarting",
        )
        after = committed_hashes(self.stream, checkpoint["generation"])
        for relative, prior in self.baseline.items():
            if relative != "stream-checkpoint.json":
                require(after[relative] == prior, "An existing immutable chunk/manifest changed")
        self.check(
            "previous_chunks_unchanged",
            "Both original chunks and immutable manifest retain hashes and mtimes",
        )
        write_new(self.audit / "after-resume-hashes.json", after)

        self.stage = "content_validation"
        self.verify_contents(checkpoint, state)

        self.stage = "independent_replay"
        self.guard()
        reference_state = self.prepare(
            self.reference / "prepared-streams" / JOB / "EURUSD", max_chunks=16
        )
        reference_stream = self.reference / "prepared-streams" / JOB / "EURUSD"
        require(reference_state["complete"], "Reference replay did not complete")
        reference_checkpoint = streaming._read_checkpoint(reference_stream)
        streaming._verify_chain(reference_stream, reference_checkpoint, self.guard)
        replay = committed_hashes(reference_stream, reference_checkpoint["generation"])
        require(
            {p: d["sha256"] for p, d in after.items()}
            == {p: d["sha256"] for p, d in replay.items()},
            "Deterministic replay hash mismatch",
        )
        write_new(self.audit / "reference-hashes.json", replay)
        self.check(
            "independent_deterministic_replay",
            f"All {len(after)} committed file hashes match independent uninterrupted replay",
        )

        self.stage = "repeated_resume"
        self.prepare(self.stream, max_chunks=1)
        require(
            committed_hashes(self.stream, checkpoint["generation"]) == after,
            "Completed resume rewrote committed files",
        )
        self.check(
            "repeated_resume_idempotence", "Completed resume changes no committed hashes or mtimes"
        )
        self.guard()
        require(
            self.source_before
            == {
                p: {"sha256": digest(Path(p)), "mtime_ns": Path(p).stat().st_mtime_ns}
                for p in self.source_before
            },
            "Source or configuration modified",
        )
        require(code_manifest() == manifest["code"], "Production code changed during validation")
        self.check(
            "source_configs_and_production_code_unchanged",
            "Checksums and source/config mtimes unchanged",
        )
        self.check(
            "bounded_memory_and_resource_policy",
            "8192-row batches, one numerical worker, active 1.25 GiB/15 GiB floors",
        )
        self.event("validation_complete", **self.metrics)

    def prepare(self, output, max_chunks):
        return self.streaming.prepare_stream(
            self.source,
            output,
            "EURUSD",
            self.job["fold"]["train_start"],
            self.job["fold"]["test_end"],
            feature_spec=self.feature_spec,
            label_spec=self.label_spec,
            metadata=self.metadata,
            batch_rows=8192,
            guard=self.guard,
            max_chunks=max_chunks,
        )

    def verify_contents(self, checkpoint, state):
        """Cross-check bounded output chunks against source, batch features and labels."""
        from trading.research.phase6.features import MINUTE, calendar_open, make_features
        from trading.research.phase6.labels import make_labels

        np, pd = self.np, self.pd
        start, end = (
            pd.Timestamp(self.job["fold"]["train_start"]),
            pd.Timestamp(self.job["fold"]["test_end"]),
        )
        selected = self.streaming._selection(self.source, "EURUSD", start, end)
        source = iter(
            self.streaming._batches(
                self.source, selected, start, end, 8192, {"part": 0, "batch": 0}, self.guard
            )
        )
        source_buffer, source_offset = None, 0

        def expected_rows(count):
            nonlocal source_buffer, source_offset
            parts = []
            while count:
                if source_buffer is None or source_offset == len(source_buffer):
                    source_buffer, _ = next(source)
                    source_offset = 0
                take = min(count, len(source_buffer) - source_offset)
                parts.append(source_buffer.iloc[source_offset : source_offset + take])
                source_offset += take
                count -= take
            return pd.concat(parts) if len(parts) > 1 else parts[0]

        def read(number):
            self.guard()
            directory = self.stream / "chunks" / f"{number:08d}"
            return tuple(
                pd.read_parquet(directory / f"{name}.parquet", use_threads=False)
                for name in ("bars", "features", "labels")
            )

        rows, gaps, gap_count, missing_open, missing_closed = 0, 0, 0, 0, 0
        first_stamp, last_stamp, previous = None, None, None
        month_counts, label_counts = Counter(), Counter()
        gap_histogram, largest_gaps = Counter(), []
        segment_bars, segment_features, segment_size = [], [], 0
        segment_count, maximum_segment, feature_rows = 0, 0, 0
        month_edges, chunk_summary = [], []

        def flush_segment():
            nonlocal \
                segment_bars, \
                segment_features, \
                segment_size, \
                segment_count, \
                maximum_segment, \
                feature_rows
            if not segment_size:
                return
            self.guard()
            bars = pd.concat(segment_bars)
            actual = pd.concat(segment_features)
            expected = make_features(bars, self.feature_spec, self.metadata)
            pd.testing.assert_frame_equal(
                actual, expected, check_freq=False, check_names=False, rtol=1e-5, atol=1e-7
            )
            feature_rows += len(bars)
            segment_count += 1
            maximum_segment = max(maximum_segment, segment_size)
            segment_bars, segment_features, segment_size = [], [], 0

        def gap(left, right):
            nonlocal missing_open, missing_closed
            cursor = left
            while cursor < right:
                self.guard()
                stop = min(right, cursor + 4096 * MINUTE)
                grid = pd.date_range(cursor, stop, freq="min", inclusive="left")
                opened = calendar_open(grid, self.metadata)
                missing_open += int(opened.sum())
                missing_closed += len(grid) - int(opened.sum())
                cursor = stop

        current = read(0)
        for number in range(checkpoint["generation"] + 1):
            self.guard()
            following = read(number + 1) if number < checkpoint["generation"] else None
            bars, features, labels = current
            pd.testing.assert_frame_equal(bars, expected_rows(len(bars)), check_freq=False)
            require(
                features.index.equals(bars.index + MINUTE) and labels.index.equals(features.index),
                "Decision-time/label alignment mismatch",
            )
            require(
                bars.index.is_monotonic_increasing and bars.index.is_unique,
                "Unordered/duplicate output",
            )
            require(len(bars) <= 8192 and len(bars) > 0, "Unexpected output chunk bound")
            require(
                bars.index[0] >= start and bars.index[-1] < end,
                "Output outside authorized interval",
            )
            rows += len(bars)
            month_counts.update(bars.index.strftime("%Y-%m"))
            label_counts.update(str(value) for value in labels.label)
            chunk_summary.append(
                {
                    "generation": number,
                    "rows": len(bars),
                    "first_open": bars.index[0].isoformat(),
                    "last_open": bars.index[-1].isoformat(),
                }
            )
            if first_stamp is None:
                first_stamp = bars.index[0]
                gap(start, first_stamp)
            stamps = bars.index if previous is None else bars.index.insert(0, previous)
            require(previous is None or bars.index[0] > previous, "Chunk ordering violation")
            differences = np.diff(stamps.asi8) // MINUTE.value - 1
            for position in np.flatnonzero(differences > 0):
                minutes = int(differences[position])
                gaps += minutes
                gap_count += 1
                gap_histogram["1" if minutes == 1 else "2-60" if minutes <= 60 else ">60"] += 1
                left, right = stamps[position] + MINUTE, stamps[position + 1]
                gap(left, right)
                largest_gaps.append(
                    {
                        "start": left.isoformat(),
                        "end_exclusive": right.isoformat(),
                        "minutes": minutes,
                    }
                )
                largest_gaps = sorted(largest_gaps, key=lambda g: g["minutes"], reverse=True)[:10]
            for position in np.flatnonzero(stamps[1:].month != stamps[:-1].month):
                month_edges.append(
                    {
                        "previous_open": stamps[position].isoformat(),
                        "next_open": stamps[position + 1].isoformat(),
                        "missing_minutes": int(differences[position]),
                    }
                )
            previous, last_stamp = bars.index[-1], bars.index[-1]

            opened = calendar_open(bars.index, self.metadata)
            require(
                features.iloc[np.flatnonzero(~opened)].isna().all().all(),
                "Closed-session feature leakage",
            )
            breaks = np.r_[
                True, (np.diff(bars.index.asi8) != MINUTE.value) | (opened[1:] != opened[:-1])
            ]
            bounds = np.r_[np.flatnonzero(breaks), len(bars)]
            for left, right in zip(bounds[:-1], bounds[1:], strict=True):
                if not opened[left]:
                    flush_segment()
                    continue
                part, f = bars.iloc[left:right], features.iloc[left:right]
                if segment_size and segment_bars[-1].index[-1] + MINUTE != part.index[0]:
                    flush_segment()
                require(
                    segment_size + len(part) <= 8192,
                    "Reference feature segment exceeds bounded validation budget",
                )
                segment_bars.append(part.copy())
                segment_features.append(f.copy())
                segment_size += len(part)

            # Only a horizon-sized lookahead is supplied to the existing batch label oracle.
            horizon = self.label_spec.horizon_minutes
            future_bars = following[0].iloc[:horizon] if following else bars.iloc[:0]
            future_features = following[1].iloc[:horizon] if following else features.iloc[:0]
            combined = pd.concat([bars, future_bars])
            sigma = pd.concat([features.atr_fraction_14, future_features.atr_fraction_14])
            expected_labels = make_labels(
                combined, self.label_spec, volatility=sigma, metadata=self.metadata
            ).iloc[: len(bars)]
            pd.testing.assert_frame_equal(
                labels, expected_labels, check_freq=False, rtol=1e-10, atol=1e-12
            )
            valid = labels.label >= 0
            require(labels.loc[valid, "label_end"].notna().all(), "Missing valid label end")
            require(
                (labels.loc[valid, "label_end"] > labels.index[valid]).all(),
                "Label end is not future-only",
            )
            require(
                (labels.loc[valid, "label_end"] <= labels.index[valid] + horizon * MINUTE).all(),
                "Label horizon exceeded",
            )
            current = following
        flush_segment()
        gap(last_stamp + MINUTE, end)
        require(source_offset == len(source_buffer), "Source rows left unmatched")
        require(next(source, None) is None, "Unconsumed source batches")
        require(
            rows == self.expected_rows and month_counts == self.expected_month_rows,
            "Source/output row count mismatch",
        )
        require(gaps == state["gap_minutes"], "Independent source gap sum differs from checkpoint")
        missing_all = int((end - start) / MINUTE) - rows
        require(
            missing_open + missing_closed == missing_all,
            "Missing-calendar classification does not reconcile",
        )
        self.evidence["content"] = {
            "rows": rows,
            "month_rows": dict(month_counts),
            "labels": dict(label_counts),
            "first_open": first_stamp.isoformat(),
            "last_open": last_stamp.isoformat(),
            "internal_gap_minutes": gaps,
            "internal_gap_count": gap_count,
            "gap_size_counts": dict(gap_histogram),
            "largest_internal_gaps": largest_gaps,
            "missing_calendar_minutes_including_edges": missing_all,
            "missing_expected_open_minutes": missing_open,
            "known_closed_absent_minutes": missing_closed,
            "calendar_limitation": "Frozen current broker schedule; historical accuracy unverified",
            "month_boundaries": month_edges,
            "batch_reference_feature_segments": segment_count,
            "maximum_reference_segment_rows": maximum_segment,
            "batch_reference_feature_rows": feature_rows,
            "chunks": chunk_summary,
        }
        self.check(
            "source_output_rows_and_chronology",
            "Every emitted bar equals the source row stream; unique ordered UTC minute timestamps",
        )
        self.check(
            "causal_feature_continuity",
            f"Batch oracle verified {segment_count} contiguous eligible segments, "
            "including chunk/month boundaries",
        )
        self.check(
            "label_handling",
            "Every label/outcome/end matches the horizon-bounded batch oracle, "
            "including terminal tail",
        )
        self.check(
            "source_gaps_and_month_boundary",
            "Independent gap totals equal checkpoint; missing-calendar classification reconciles",
        )
        self.streaming._verify_chain(
            self.stream, self.streaming._read_checkpoint(self.stream), self.guard
        )
        self.check(
            "final_checkpoint_and_receipts",
            "Checkpoint envelope, identity and complete receipt/file chain verified",
        )

    def finish(self, error=None):
        self.evidence["finished_utc"] = datetime.now(UTC).isoformat()
        self.evidence["metrics"] = self.metrics
        self.evidence["overall_status"] = "PASS" if error is None else "FAIL"
        self.evidence["resources_end"] = {
            **process_memory(),
            "available_ram_gib": available_memory() / GIB,
            "free_disk_gib": shutil.disk_usage(ROOT).free / GIB,
        }
        if error is not None:
            from trading.research.phase6.runner import _failure_record

            self.evidence["failure"] = _failure_record(error, {"stage": self.stage})
            if isinstance(error, ValidationError):
                self.evidence["failure"]["reason"] = str(error)
            try:
                checkpoint, state = state_at(self.stream)
                self.evidence["preserved_progress"] = {
                    "generation": checkpoint["generation"],
                    **{
                        k: state[k]
                        for k in ("rows_read", "rows_written", "gap_minutes", "complete")
                    },
                }
            except (OSError, ValueError, KeyError):
                pass
        checks = self.evidence["checks"]
        for name in (
            "source_and_code_identity",
            "initial_checkpoint_and_receipts",
            "automatic_bounded_resume",
            "previous_chunks_unchanged",
            "source_output_rows_and_chronology",
            "causal_feature_continuity",
            "label_handling",
            "source_gaps_and_month_boundary",
            "final_checkpoint_and_receipts",
            "independent_deterministic_replay",
            "repeated_resume_idempotence",
            "source_configs_and_production_code_unchanged",
            "bounded_memory_and_resource_policy",
        ):
            if name not in checks:
                checks[name] = {
                    "status": "FAIL",
                    "details": "Not completed; stopped safely at " + self.stage,
                }
        write_new(self.audit / "validation-result.json", self.evidence)
        lines = [
            "# STRAT-2C EURUSD January-February 2022 consolidated validation",
            "",
            f"Overall: **{self.evidence['overall_status']}**. Job `{JOB}`.",
            "",
            "| Requirement/check | Status | Evidence |",
            "| --- | --- | --- |",
        ]
        lines += [
            f"| {name} | {value['status']} | {value['details']} |" for name, value in checks.items()
        ]
        lines += [
            "",
            "Full bounded-validation evidence:",
            "",
            "```json",
            json.dumps(self.evidence, indent=2, allow_nan=False),
            "```",
            "",
            "On resource stop, preserve all files and rerun the original bounded prepare command "
            "after the unchanged 1.25 GiB RAM / 15 GiB disk gates pass. "
            "Never reset the checkpoint.",
            "On identity/corruption failure, investigate preserved evidence before resuming.",
            "",
            "For an autonomous recovery once headroom is restored, rerun from the same worktree:",
            "```powershell",
            ".\\.venv\\Scripts\\python.exe -B -m tools.strat2c_stream_validation",
            "```",
            "",
            "No training, portfolio backtest, holdout access, broker/DB connection, collector, "
            "Docker, commit or push was performed.",
        ]
        self.report.parent.mkdir(parents=True, exist_ok=True)
        with self.report.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
        print(
            canonical(
                {
                    "status": self.evidence["overall_status"],
                    "report": str(self.report),
                    "audit": str(self.audit),
                    "metrics": self.metrics,
                }
            ),
            flush=True,
        )


def main():
    validation = Validation()
    try:
        with controller_lock(validation.output, lock_name=".validation.lock"):
            validation.run()
    except Exception as error:
        validation.finish(error)
        return 2 if isinstance(error, ResearchSafetyStop) else 1
    validation.finish()
    return 0


if __name__ == "__main__":
    sys.exit(main())
