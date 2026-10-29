"""Bounded, chronological, transactional training/calibration/inference/simulation."""

import importlib.metadata
import json
import math
import os
import shutil
import time
from itertools import zip_longest
from pathlib import Path

from trading.research.phase6.controller import controller_lock
from trading.research.safety import ROOT, ResearchSafetyStop
from trading.research.strat2d.journal import Journal, file_hash, logical_hash


def resource_floor(config):
    """Explicit offline engineering exception; ordinary workflows retain 1.5 GiB."""
    profile = config.get("resource_profile")
    if profile is None:
        return 1.5
    expected = {
        "resource_profile": "strat2d-eurusd-2022-offline-130-v1",
        "mode": "offline_simulation",
        "threads": 1,
        "asset": "EURUSD",
        "start": "2022-01-01T00:00:00Z",
        "train_end": "2022-01-15T00:00:00Z",
        "calibration_end": "2022-02-01T00:00:00Z",
        "test_end": "2022-03-01T00:00:00Z",
        "prepared": "data/research/phase6-strat2c-eurusd-2022-jan-feb-validation-v1/"
        "prepared-streams/1d06fff895ee69a12b38/EURUSD",
        "minimum_available_ram_gib": 1.30,
        "max_worker_ram_gib": 0.25,
        "batch_rows": 1024,
        "quality_policy": "observed_contiguous_engineering",
    }
    if (
        any(config.get(k) != v for k, v in expected.items())
        or type(config.get("threads")) is not int
        or config.get("preparation_only", False)
        or config.get("model", {}).get("kind") != "online_softmax_v1"
    ):
        raise ValueError("Invalid scope or limits for explicit STRAT-2D 1.30 GiB profile")
    return 1.30


def validate_config(config, output=None):
    """Standard-library bootstrap rejects unsafe scope before scientific imports."""
    from datetime import datetime

    if config.get("mode") != "offline_simulation" or config.get("threads") != 1:
        raise ValueError("STRAT-2D is single-threaded offline simulation only")
    if config.get("preparation_only", False):
        raise ValueError("Preparation-only RAM exceptions cannot authorize STRAT-2D")
    if config.get("asset") != "EURUSD":
        raise ValueError("STRAT-2D v1 supports EURUSD only")
    floors = (
        (config["minimum_available_ram_gib"], resource_floor(config)),
        (config["free_floor_gib"], 15),
    )
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < floor for v, floor in floors):
        raise ValueError("STRAT-2D resource profile RAM floor and 15 GiB free disk required")
    if type(config["batch_rows"]) is not int or not 1 <= config["batch_rows"] <= 2048:
        raise ValueError("STRAT-2D batch size must be 1..2048")
    if not 1 <= config["max_quality_intervals"] <= 4096:
        raise ValueError("Quality metadata must be bounded")
    if (
        not 0.125 <= config["max_worker_ram_gib"] <= 1
        or not 10 <= config["timeout_seconds"] <= 1800
    ):
        raise ValueError("Invalid STRAT-2D process limits")
    stamps = [
        datetime.fromisoformat(config[k].replace("Z", "+00:00"))
        for k in ("start", "train_end", "calibration_end", "test_end")
    ]
    if any(s.tzinfo is None or s.utcoffset().total_seconds() != 0 for s in stamps):
        raise ValueError("Explicit UTC split boundaries required")
    if (
        not all(a < b for a, b in zip(stamps, stamps[1:], strict=False))
        or stamps[-1].isoformat() > "2026-01-01T00:00:00+00:00"
    ):
        raise ValueError("Invalid splits or forbidden 2026 holdout")
    if (stamps[-1] - stamps[0]).total_seconds() > 90 * 86400:
        raise ValueError("STRAT-2D v1 engineering windows are limited to 90 days; no annual run")
    if (
        any(s.second or s.microsecond for s in stamps)
        or type(config["embargo_minutes"]) is not int
        or config["embargo_minutes"] < 0
    ):
        raise ValueError("Whole-minute boundaries and nonnegative embargo required")
    if config["quality_policy"] not in {
        "require_verified_calendar",
        "observed_contiguous_engineering",
    }:
        raise ValueError("Explicit data-quality admission policy required")
    if config["model"]["kind"] != "online_softmax_v1":
        raise ValueError("Unsupported model; existing Phase 6 kinds are not substituted")
    source = (ROOT / config["prepared"]).resolve()
    target = (ROOT / (output or config["output"])).resolve()
    research = (ROOT / "data/research").resolve()
    if not target.is_relative_to(research) or not target.name.startswith("strat2d-"):
        raise ValueError("STRAT-2D outputs require a new versioned research directory")
    if (
        not source.is_relative_to(research)
        or target == source
        or target.is_relative_to(source)
        or source.is_relative_to(target)
    ):
        raise ValueError("Prepared source/output overlap or source escape")
    return source, target


class Monitor:
    """Explicit profile resource floors plus RSS ceiling at bounded intervals."""

    def __init__(self, config):
        if (
            config.get("preparation_only", False)
            or config["minimum_available_ram_gib"] < resource_floor(config)
            or config["free_floor_gib"] < 15
            or not math.isfinite(config["minimum_available_ram_gib"])
            or not math.isfinite(config["free_floor_gib"])
        ):
            raise ValueError("STRAT-2D resource floors remain 1.5 GiB RAM / 15 GiB disk")
        self.config, self.started = config, time.monotonic()
        self.peak_rss_bytes, self.minimum_available_ram_gib, self.minimum_free_disk_gib = (
            0,
            None,
            None,
        )
        self.calls = 0

    def __call__(self):
        from trading.research.safety import available_memory
        from trading.research.strat2d.runtime import process_memory

        free = shutil.disk_usage(ROOT).free
        ram = available_memory() / 1024**3
        memory = process_memory()
        self.peak_rss_bytes = max(self.peak_rss_bytes, memory["peak_rss_bytes"])
        self.minimum_available_ram_gib = min(ram, self.minimum_available_ram_gib or ram)
        self.minimum_free_disk_gib = min(
            free / 1024**3, self.minimum_free_disk_gib or free / 1024**3
        )
        self.calls += 1
        if free < self.config["free_floor_gib"] * 1024**3:
            raise ResearchSafetyStop(
                f"Windows free disk {free / 1024**3:.2f} GiB below research floor"
            )
        if ram < self.config["minimum_available_ram_gib"]:
            raise ResearchSafetyStop(f"Available RAM {ram:.2f} GiB below research minimum")
        if self.peak_rss_bytes > self.config["max_worker_ram_gib"] * 1024**3:
            raise ResearchSafetyStop("STRAT-2D process RSS budget exceeded")
        if time.monotonic() - self.started > self.config["timeout_seconds"]:
            raise ResearchSafetyStop("STRAT-2D wall-time budget exceeded")


def source_manifest(source, config, guard):
    from datetime import datetime

    from trading.research.strat2d.prepared import verify

    manifest = json.loads((source / "stream-manifest.json").read_text("utf-8"))

    def stamp(s):
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    if (
        manifest["asset"] != config["asset"]
        or stamp(manifest["start"]) != stamp(config["start"])
        or stamp(manifest["end"]) != stamp(config["test_end"])
        or manifest["end"][:10] > "2026-01-01"
    ):
        raise ValueError("Prepared source scope/holdout mismatch before candle access")
    specs = manifest["metadata"]["specifications"]
    rules = config["calendar"]
    expected_windows = [[w["startSecond"], w["endSecond"]] for w in specs["schedule"]]
    if (
        rules["timezone"] != specs["scheduleTimeZone"]
        or rules["weekly_open_seconds"] != expected_windows
        or rules.get("evidence_fingerprint") != logical_hash(manifest["metadata"])
    ):
        raise ValueError(
            "Prepared feature calendar mismatch; reprepare separately before changing rules"
        )
    from datetime import date, timedelta

    applicable = []
    left, right = stamp(config["start"]).date(), stamp(config["test_end"]).date()
    for holiday in specs.get("holiday", []):
        day = date(1970, 1, 1) + timedelta(days=int(holiday["holidayDate"]))
        if holiday["isRecurring"] or left - timedelta(days=1) <= day <= right + timedelta(days=1):
            a, b = holiday.get("startSecond", 0), holiday.get("endSecond", 0)
            applicable.append(
                {
                    "date": day.isoformat(),
                    "timezone": holiday["scheduleTimeZone"],
                    "start_minute": a // 60,
                    "end_minute": 1440 if a == b == 0 else (b + 59) // 60,
                    "recurring": holiday["isRecurring"],
                }
            )
    if rules.get("holidays", []) != applicable:
        raise ValueError("Prepared feature holiday rules mismatch; reprepare separately")
    return verify(source, guard)


def input_batches(source, source_info, chunk, offset, batch_rows, guard, *, feature_columns=None):
    import pandas as pd
    import pyarrow.parquet as pq

    from trading.research.strat2d.prepared import utc_index, validate_candles

    minute = pd.Timedelta(minutes=1)

    for generation in range(chunk, source_info["generation"] + 1):
        paths = [
            source / f"chunks/{generation:08d}/{name}.parquet"
            for name in ("bars", "features", "labels")
        ]
        readers = [pq.ParquetFile(path) for path in paths]
        try:
            projections = [None, None, None]
            if feature_columns is not None:
                projections = [
                    ["opened_at", "open", "high", "low", "close"],
                    list(dict.fromkeys(["decision_time", *feature_columns, "atr_fraction_14"])),
                    ["decision_time", "label", "label_end"],
                ]
                # Volume is preserved in the public default reader; the pipeline
                # never uses it for this explicitly price-only model/simulator.
            iterators = [
                p.iter_batches(batch_size=batch_rows, use_threads=False, columns=cols)
                for p, cols in zip(readers, projections, strict=True)
            ]
            seen = 0
            for triple in zip_longest(*iterators):
                if any(batch is None for batch in triple):
                    raise ValueError("Misaligned prepared Arrow batches")
                count = triple[0].num_rows
                if generation == chunk and seen + count <= offset:
                    seen += count
                    continue
                guard()
                frames = []
                for name, batch in zip(
                    ("opened_at", "decision_time", "decision_time"), triple, strict=True
                ):
                    frame = batch.to_pandas(use_threads=False)
                    if not isinstance(frame.index, pd.DatetimeIndex):
                        frame = frame.set_index(name)
                    frame.index = utc_index(frame.index)
                    frames.append(frame)
                bars, features, labels = frames
                validate_candles(bars, max_rows=batch_rows)
                if bars.index[0] < pd.Timestamp(source_info["start"]) or bars.index[
                    -1
                ] >= pd.Timestamp(source_info["end"]):
                    raise ValueError("Prepared observations outside declared interval")
                if not features.index.equals(bars.index + minute) or not labels.index.equals(
                    features.index
                ):
                    raise ValueError("Prepared feature/label decision alignment mismatch")
                if not labels.label.isin([-1, 0, 1, 2]).all():
                    raise ValueError("Unsupported label codes")
                if not labels.label_end.isna().equals(labels.label.eq(-1)):
                    raise ValueError("Unavailable label/end mismatch")
                skip = max(0, offset - seen) if generation == chunk else 0
                seen += count
                next_chunk, next_offset = generation, seen
                if seen == readers[0].metadata.num_rows:
                    next_chunk, next_offset = generation + 1, 0
                yield [f if not skip else f.iloc[skip:] for f in frames], next_chunk, next_offset
                del frames, bars, features, labels, triple, frame, batch
        finally:
            for reader in readers:
                reader.close()


def run(config, *, output=None, max_transactions=None, guard=None, fault=None):
    """Each transaction commits the complete cursor/model/accounting state together."""
    source, target = validate_config(config, output)
    if max_transactions is not None and (type(max_transactions) is not int or max_transactions < 1):
        raise ValueError("Positive bounded transaction count required")
    guard = guard or Monitor(config)
    guard()
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

    from trading.research.strat2d.calendar import (
        MINUTE,
        Calendar,
        audit_batch,
        audit_gap,
        initial_quality,
    )
    from trading.research.strat2d.model import OnlineModel
    from trading.research.strat2d.portfolio import Portfolio, initial_portfolio

    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    guard()
    source_info = source_manifest(source, config, guard)
    code = {p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
    dependency_names = (
        "src/trading/research/phase6/streaming.py",
        "src/trading/research/phase6/features.py",
        "src/trading/research/phase6/controller.py",
        "src/trading/research/safety.py",
    )
    # Tests may use an isolated data ROOT while scientific modules stay in the worktree.
    # Resolve code dependencies from this installed source package, never from data.
    actual_root = Path(__file__).resolve().parents[4]
    dependencies = {name: file_hash(actual_root / name) for name in dependency_names}
    try:
        timezone_version = importlib.metadata.version("tzdata")
    except importlib.metadata.PackageNotFoundError:
        timezone_version = "system-zoneinfo-unversioned"
    scientific = {k: v for k, v in config.items() if k not in {"output", "version"}}
    manifest = {
        "schema_version": 1,
        "scientific": scientific,
        "source": source_info,
        "code": code,
        "dependencies": dependencies,
        "environment": {
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "arrow": pa.__version__,
            "tzdata": timezone_version,
        },
    }
    calendar = Calendar(config["calendar"])
    model = OnlineModel(config["model"])
    Portfolio(config["portfolio"])
    initial = {
        "schema_version": 1,
        "phase": "quality",
        "chunk": 0,
        "offset": 0,
        "complete": False,
        "model": model.export(),
        "quality": initial_quality(),
        "portfolio": initial_portfolio(config["portfolio"]),
        "event_seq": 0,
        "counts": {},
        "training_weights_hash": None,
        "frozen_model_hash": None,
        "metrics": {"test_labeled_rows": 0, "log_loss_sum": 0.0, "brier_sum": 0.0},
        "status": "running",
        "seed": config["model"]["seed"],
    }
    boundaries = [
        pd.Timestamp(config[k]) for k in ("start", "train_end", "calibration_end", "test_end")
    ]
    maximum = config["max_quality_intervals"]
    class_targets = np.eye(3)
    effective_starts = [
        boundaries[0],
        boundaries[1] + pd.Timedelta(minutes=config["embargo_minutes"]),
        boundaries[2] + pd.Timedelta(minutes=config["embargo_minutes"]),
    ]
    with controller_lock(target, lock_name=".strat2d.lock"):
        journal = Journal(target, manifest, initial, guard)
        state = journal.state
        transactions = 0
        while not state["complete"]:
            phase = state["phase"]
            model = OnlineModel(config["model"], state["model"])
            portfolio = Portfolio(config["portfolio"], state["portfolio"])
            blocks = [
                (pd.Timestamp(b["start"]).value, pd.Timestamp(b["end"]).value)
                for b in state["quality"]["blocks"]
            ]
            for (bars, features, labels), chunk, offset in input_batches(
                source,
                source_info,
                state["chunk"],
                state["offset"],
                config["batch_rows"],
                guard,
                feature_columns=config["model"]["features"],
            ):
                events = []
                if phase == "quality":
                    audit_batch(bars, state["quality"], calendar, boundaries[0], events, maximum)
                else:
                    values = features[config["model"]["features"]].to_numpy(copy=False)
                    ends = pd.DatetimeIndex(labels.label_end)
                    label_values = labels.label.to_numpy(copy=False)
                    prices = {
                        key: bars[key].to_numpy(copy=False)
                        for key in ("open", "high", "low", "close")
                    }
                    atr = features.atr_fraction_14.to_numpy(copy=False)
                    for number, opened in enumerate(bars.index):
                        if number % 128 == 0:
                            guard()
                        decision = features.index[number]
                        x, label, label_end = (
                            values[number],
                            int(label_values[number]),
                            ends[number],
                        )
                        ready = bool(np.isfinite(x).all()) and calendar.classify(opened) == "open"
                        target_ok = label >= 0 and not pd.isna(label_end)
                        if target_ok:
                            target_ok = not any(
                                opened.value < right and label_end.value > left
                                for left, right in blocks
                            )
                        if phase in {"train", "calibration"}:
                            i = 0 if phase == "train" else 1
                            left = effective_starts[i]
                            if (
                                ready
                                and target_ok
                                and left <= decision < boundaries[i + 1]
                                and label_end < boundaries[i + 1]
                            ):
                                if phase == "train":
                                    model.fit_one(
                                        x,
                                        label,
                                        decision.isoformat(),
                                        label_end.isoformat(),
                                        boundaries[1].isoformat(),
                                    )
                                else:
                                    model.calibrate_one(
                                        x,
                                        label,
                                        decision.isoformat(),
                                        label_end.isoformat(),
                                        boundaries[2].isoformat(),
                                    )
                            continue
                        if phase != "test":
                            raise ValueError("Unknown transaction phase")
                        bar = {key: float(a[number]) for key, a in prices.items()}
                        if boundaries[2] <= opened < boundaries[3]:
                            portfolio.on_bar(
                                opened, bar, calendar.classify(opened) == "open", events
                            )
                        if not effective_starts[2] <= decision < boundaries[3]:
                            continue
                        if ready:
                            probability = model.predict(x)
                            events.append(
                                {
                                    "event": "prediction",
                                    "time": decision.isoformat(),
                                    "probabilities": probability.tolist(),
                                    "model_version": state["frozen_model_hash"],
                                }
                            )
                            horizon = config["portfolio"]["horizon_minutes"]
                            allowed = decision + horizon * MINUTE <= boundaries[3]
                            if (
                                allowed
                                and not portfolio.state["censored"]
                                and not portfolio.state["position"]
                            ):
                                allowed = calendar.entry_window_open(decision, horizon)
                            distance = (
                                float(atr[number])
                                * bar["close"]
                                * config["portfolio"]["stop_multiple"]
                            )
                            portfolio.signal(
                                decision,
                                probability,
                                distance,
                                state["frozen_model_hash"],
                                allowed,
                                events,
                            )
                            if target_ok and label_end < boundaries[3]:
                                metric = state["metrics"]
                                metric["test_labeled_rows"] += 1
                                metric["log_loss_sum"] -= float(
                                    np.log(max(probability[label], 1e-12))
                                )
                                metric["brier_sum"] += float(
                                    np.square(probability - class_targets[label]).sum()
                                )
                state.update(
                    chunk=chunk, offset=offset, model=model.export(), portfolio=portfolio.state
                )
                if (
                    phase in {"calibration", "test"}
                    and logical_hash(state["model"]["weights"]) != state["training_weights_hash"]
                ):
                    raise ValueError("Model coefficients changed outside training")
                if phase == "test" and logical_hash(state["model"]) != state["frozen_model_hash"]:
                    raise ValueError("Frozen model/calibrator changed during evaluation")
                for event in events:
                    state["event_seq"] += 1
                    event["sequence"] = state["event_seq"]
                state["counts"][phase] = state["counts"].get(phase, 0) + len(bars)
                journal.commit(state, events, fault)
                transactions += 1
                if max_transactions is not None and transactions >= max_transactions:
                    return state
                events.clear()
                del bars, features, labels
                if phase != "quality":
                    del values, ends, label_values, prices, atr, x
            # Phase boundaries are transactions too; a crash cannot freeze only half a model.
            events = []
            if phase == "quality":
                audit_gap(
                    state["quality"],
                    pd.Timestamp(state["quality"]["last_open"]) + pd.Timedelta(minutes=1),
                    boundaries[3],
                    calendar,
                    events,
                    internal=False,
                    maximum=maximum,
                )
                if (
                    config["quality_policy"] == "require_verified_calendar"
                    and not config["calendar"]["verified"]
                ):
                    state.update(complete=True, phase="done", status="blocked_unverified_calendar")
                else:
                    state["phase"] = "train"
            elif phase == "train":
                if model.state["train_rows"] == 0:
                    raise ValueError("No eligible training rows")
                state["training_weights_hash"] = logical_hash(state["model"]["weights"])
                state["phase"] = "calibration"
            elif phase == "calibration":
                if model.state["calibration_rows"] == 0:
                    raise ValueError("No eligible calibration rows")
                state["frozen_model_hash"] = logical_hash(state["model"])
                state["phase"] = "test"
            elif phase == "test":
                state["portfolio"]["pending"] = (
                    None  # No fictitious fill beyond the configured end.
                )
                state.update(
                    complete=True,
                    phase="done",
                    status=(
                        "engineering_complete_censored"
                        if state["portfolio"]["censored"]
                        else "engineering_complete"
                    ),
                )
            else:
                raise ValueError("Invalid checkpoint phase")
            state.update(chunk=0, offset=0)
            events.append({"event": "phase_boundary", "phase": phase, "next_phase": state["phase"]})
            for event in events:
                state["event_seq"] += 1
                event["sequence"] = state["event_seq"]
            journal.commit(state, events, fault)
            transactions += 1
            if max_transactions is not None and transactions >= max_transactions:
                return state
        return state
