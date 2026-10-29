"""Bounded read-only diagnostics and immutable replay of frozen historical inference.

Target admission is diagnostic only. Future labels/exclusion blocks never gate orders.
The original model, strategy parameters, portfolio implementation and artifacts are untouched.
"""

import math
from collections import Counter

from trading.research.strat2d.journal import Journal, file_hash, logical_hash


def count(mapping, key, amount=1):
    mapping[key] = mapping.get(key, 0) + amount


def signal_reason(state, probability, distance, allowed, threshold):
    """Ordered, mutually exclusive explanation of the existing silent signal gate."""
    if state["censored"]:
        return "censored"
    if state["position"]:
        return "position_busy"
    if state["pending"]:
        return "pending_busy"
    if not allowed:
        return "session_or_end_boundary"
    if not math.isfinite(distance) or distance <= 0:
        return "invalid_stop_distance"
    directional = max(probability[1:])
    if directional < threshold:
        return "below_directional_threshold"
    if directional < probability[0]:
        return "no_trade_dominates"
    return "pending_order"


def observe_probability(stats, probability, raw):
    count(stats, "predictions")
    for name, values in (("calibrated", probability), ("uncalibrated", raw)):
        index = max(range(3), key=lambda i: values[i])
        count(stats, f"{name}_winner_{index}")
        for i in range(3):
            key = f"{name}_max_{i}"
            stats[key] = max(stats.get(key, 0), float(values[i]))
        count(stats, f"{name}_direction_ge_055", int(max(values[1:]) >= 0.55))


def published_ledger_funnel(output, expected_hashes, threshold):
    """Postmortem log inspection only: no candles, fitting, inference or simulation.

    This remains useful after resource admission rejects a new historical worker.
    Maximum probabilities are inspected from already-published predictions, never
    regenerated. Canonical checkpoint/receipt files must match the prior audit.
    """
    import json

    from trading.research.strat2d.journal import committed_files

    actual = committed_files(output)
    if actual != expected_hashes:
        raise ValueError("Published foundation differs from its saved audit hashes")
    events, winners = Counter(), Counter()
    maxima, probability_pass, previous = [0.0] * 3, 0, 0
    for generation in range(10000):
        path = output / f"{generation:08d}/events.jsonl"
        if not path.exists():
            break
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                event = json.loads(line)
                if event["sequence"] != previous + 1:
                    raise ValueError("Published event sequence gap or duplicate")
                previous = event["sequence"]
                events[event["event"]] += 1
                if event["event"] == "prediction":
                    p = event["probabilities"]
                    if len(p) != 3 or not math.isclose(sum(p), 1, abs_tol=1e-8):
                        raise ValueError("Invalid published probabilities")
                    maxima = [max(a, b) for a, b in zip(maxima, p, strict=True)]
                    winners[str(max(range(3), key=lambda i: p[i]))] += 1
                    probability_pass += int(max(p[1:]) >= max(threshold, p[0]))
    return {
        "kind": "published_ledger_postmortem_not_new_execution",
        "verified_files": len(actual),
        "events": dict(events),
        "class_winners": dict(winners),
        "maximum_calibrated_probabilities": maxima,
        "confidence_gate_pass": probability_pass,
        "confidence_gate_fail": events["prediction"] - probability_pass,
        "threshold": threshold,
    }


def replay(config, target, prior, guard, fault=None):
    import numpy as np
    import pandas as pd
    import pyarrow as pa

    from trading.research.phase6.controller import controller_lock
    from trading.research.strat2d.calendar import MINUTE, Calendar
    from trading.research.strat2d.model import OnlineModel
    from trading.research.strat2d.pipeline import input_batches, source_manifest, validate_config
    from trading.research.strat2d.portfolio import Portfolio, initial_portfolio

    source, output = validate_config(config, target)
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    guard()
    source_info = source_manifest(source, config, guard)
    manifest_path = prior / "manifest.json"
    import json

    old_manifest = json.loads(manifest_path.read_text())
    old = Journal(prior, old_manifest, {}, guard)
    if not old.state["complete"] or old.state["status"] != "engineering_complete":
        raise ValueError("Completed uncensored historical foundation required")
    expected = {k: v for k, v in config.items() if k not in {"version", "output"}}
    if old_manifest["scientific"] != expected or old_manifest["source"] != source_info:
        raise ValueError("Frozen model configuration/source mismatch")
    from pathlib import Path

    for name, digest in old_manifest["code"].items():
        if file_hash(Path(__file__).parents[1] / "strat2d" / name) != digest:
            raise ValueError("Foundation code identity changed")
    model = OnlineModel(config["model"], old.state["model"])
    if logical_hash(model.export()) != old.state["frozen_model_hash"]:
        raise ValueError("Frozen model hash mismatch")
    calendar = Calendar(config["calendar"])
    bounds = [
        pd.Timestamp(config[k]) for k in ("start", "train_end", "calibration_end", "test_end")
    ]
    blocks = [
        (pd.Timestamp(b["start"]).value, pd.Timestamp(b["end"]).value)
        for b in old.state["quality"]["blocks"]
    ]
    manifest = {
        "version": "strat2e-frozen-execution-audit-v1",
        "config": expected,
        "source": source_info,
        "foundation_receipt": old.previous,
        "foundation_identity": old.identity,
        "audit_code": file_hash(__file__),
    }
    initial = {
        "chunk": 0,
        "offset": 0,
        "complete": False,
        "counts": {},
        "signals": {},
        "probabilities": {},
        "events": {},
        "portfolio": initial_portfolio(config["portfolio"]),
        "calendar_days": {},
        "last_open": None,
        "internal_gaps": 0,
        "internal_missing_minutes": 0,
        "event_seq": 0,
    }
    with controller_lock(output, lock_name=".strat2e.lock"):
        journal = Journal(output, manifest, initial, guard)
        state = journal.state
        if state["complete"]:
            return state
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
            portfolio = Portfolio(config["portfolio"], state["portfolio"])
            xs = features[config["model"]["features"]].to_numpy(copy=False)
            ends = pd.DatetimeIndex(labels.label_end)
            for i, opened in enumerate(bars.index):
                if i % 128 == 0:
                    guard()
                decision = features.index[i]
                count(state["counts"], "input_rows")
                local = opened.tz_convert(calendar.zone)
                day = state["calendar_days"].setdefault(
                    local.date().isoformat(),
                    {
                        "first_utc": opened.isoformat(),
                        "last_utc": opened.isoformat(),
                        "rows": 0,
                        "out_of_session": 0,
                    },
                )
                day["last_utc"] = opened.isoformat()
                day["rows"] += 1
                is_open = calendar.classify(opened) == "open"
                day["out_of_session"] += int(not is_open)
                if state["last_open"] is not None:
                    missing = int((opened - pd.Timestamp(state["last_open"])) / MINUTE) - 1
                    state["internal_gaps"] += int(missing > 0)
                    state["internal_missing_minutes"] += max(0, missing)
                state["last_open"] = opened.isoformat()
                finite = bool(np.isfinite(xs[i]).all())
                ready = finite and is_open
                label, end = int(labels.label.iloc[i]), ends[i]
                target = label >= 0 and not pd.isna(end)
                integrity = target and not any(
                    opened.value < b and end.value > a for a, b in blocks
                )
                for phase, left, right in (
                    ("train", bounds[0], bounds[1]),
                    ("calibration", bounds[1] + config["embargo_minutes"] * MINUTE, bounds[2]),
                    ("test", bounds[2] + config["embargo_minutes"] * MINUTE, bounds[3]),
                ):
                    if left <= decision < right:
                        counts = state["counts"]
                        count(counts, phase + "_window_rows")
                        reason = (
                            "nonfinite_predictors"
                            if not finite
                            else "out_of_session"
                            if not is_open
                            else "unavailable_label"
                            if not target
                            else "label_quality_block"
                            if not integrity
                            else "label_split_boundary"
                            if end >= right
                            else "eligible"
                        )
                        count(counts, phase + "_" + reason)
                if bounds[2] <= opened < bounds[3]:
                    bar = {k: float(bars[k].iloc[i]) for k in ("open", "high", "low", "close")}
                    portfolio.on_bar(opened, bar, is_open, events)
                    count(state["counts"], "simulation_bars")
                if (
                    not bounds[2] + config["embargo_minutes"] * MINUTE <= decision < bounds[3]
                    or not ready
                ):
                    continue
                probability = model.predict(xs[i])
                raw = model.softmax(model.vector(xs[i]) @ model.weights)
                observe_probability(state["probabilities"], probability, raw)
                horizon = config["portfolio"]["horizon_minutes"]
                allowed = decision + horizon * MINUTE <= bounds[3]
                if allowed and not portfolio.state["censored"] and not portfolio.state["position"]:
                    allowed = calendar.entry_window_open(decision, horizon)
                distance = (
                    float(features.atr_fraction_14.iloc[i])
                    * float(bars.close.iloc[i])
                    * config["portfolio"]["stop_multiple"]
                )
                reason = signal_reason(
                    portfolio.state,
                    probability,
                    distance,
                    allowed,
                    config["portfolio"]["threshold"],
                )
                count(state["signals"], reason)
                portfolio.signal(
                    decision, probability, distance, old.state["frozen_model_hash"], allowed, events
                )
                if reason == "pending_order" and portfolio.state["pending"] is None:
                    raise ValueError("Diagnostic gate differs from production signal")
            for event in events:
                state["event_seq"] += 1
                event["sequence"] = state["event_seq"]
                count(state["events"], event["event"])
            state.update(chunk=chunk, offset=offset, portfolio=portfolio.state)
            journal.commit(state, events, fault)
        state["complete"] = True
        if (
            state["counts"].get("train_eligible") != old.state["model"]["train_rows"]
            or state["counts"].get("calibration_eligible") != old.state["model"]["calibration_rows"]
        ):
            raise ValueError("Admission diagnostics diverge from historical fitting")
        if state["portfolio"] != old.state["portfolio"]:
            raise ValueError("Frozen execution differs from validated portfolio")
        journal.commit(state, [], fault)
        return state


def synthetic_job(output, spec, guard=lambda: None, fault=None):
    """Tiny explicit long-profit/short-loss fixture, never historical signal injection."""
    import pandas as pd

    from trading.research.strat2d.portfolio import Portfolio, initial_portfolio

    manifest = {"fixture": "synthetic-long-short-open-recovery-v1", "spec": spec}
    journal = Journal(output, manifest, {"cursor": 0, "portfolio": initial_portfolio(spec)}, guard)
    state = journal.state
    for i in range(state["cursor"], 8):
        guard()
        portfolio, events = Portfolio(spec, state["portfolio"]), []
        stamp = pd.Timestamp("2022-01-10T12:00Z") + i * pd.Timedelta(minutes=1)
        close = 104.0 if i == 2 else 102.0 if i == 6 else 100.0
        bar = {"open": 100.0, "high": max(close, 100.1), "low": min(close, 99.9), "close": close}
        portfolio.on_bar(stamp, bar, True, events)
        if i in (0, 4):
            portfolio.signal(
                stamp + pd.Timedelta(minutes=1),
                [0.01, 0.98 if i == 0 else 0.01, 0.01 if i == 0 else 0.98],
                1.0,
                "synthetic-only",
                True,
                events,
            )
        state.update(cursor=i + 1, portfolio=portfolio.state)
        journal.commit(state, events, fault)
    return state
