"""Chronological bounded reconstruction, diagnostics and immutable checkpoints."""

import json
import math
from pathlib import Path

from trading.research.safety import ROOT
from trading.research.strat2d.journal import Journal, file_hash, logical_hash
from trading.research.strat2d.pipeline import validate_config
from trading.research.strat2f.metrics import (
    MODELS,
    THRESHOLDS,
    baselines,
    feature_stat,
    feature_update,
    metric,
    split_reason,
    threshold_stat,
    threshold_update,
    update,
)

PRIOR = ROOT / "data/research/strat2d-eurusd-2022-offline-130-reference-v1"
POLICY = {
    "version": "strat2f-diagnostics-v1",
    "thresholds": list(THRESHOLDS),
    "momentum_feature": "return_5",
    "bins": 10,
    "daily_budget": 64,
    "bootstrap_repetitions": 1000,
    "bootstrap_day_lengths": [2, 5],
    "cost_bps": 2.1,
    "softmax_loss_floor": 1e-12,
    "uncertainty": "paired circular adjacent observed-day blocks; descriptive only",
}


def initial(config):
    from trading.research.strat2d.model import OnlineModel

    return {
        "chunk": 0,
        "offset": 0,
        "complete": False,
        "rows": 0,
        "event_seq": 0,
        "model": OnlineModel(config["model"]).export(),
        "counts": {},
        "features": {},
        "metrics": {},
        "days": {},
        "thresholds": {str(t): threshold_stat() for t in THRESHOLDS},
        "trace": [],
        "train_optimizer": metric(False),
        "calibration_optimizer": metric(False),
        "inference": {
            "n": 0,
            "max_cal": [0.0, 0.0, 0.0],
            "max_raw": [0.0, 0.0, 0.0],
            "normalization_error": 0.0,
            "max_abs_logit": 0.0,
            "confidence_histogram": [0] * 10,
        },
        "last_open": None,
        "maximum_label_horizon_minutes": 0.0,
    }


def increment(state, key):
    state["counts"][key] = state["counts"].get(key, 0) + 1


def validate_label_policy(config, labels):
    costs = (
        config["portfolio"]["spread_bps"]
        + 2 * config["portfolio"]["slippage_bps"]
        + config["portfolio"]["commission_bps_roundtrip"]
    )
    expected = {
        "kind": "barrier",
        "horizon_minutes": 15,
        "stop_multiple": 2.0,
        "reward_risk": 3.0,
        "cost_bps": 2.1,
    }
    if not math.isclose(costs, POLICY["cost_bps"]) or any(
        labels.get(k) != v for k, v in expected.items()
    ):
        raise ValueError("Diagnostic economic/label scenario differs from approved barrier policy")


def run(config, target, guard, fault=None, prior=PRIOR):
    source, output = validate_config(config, target)
    guard()
    import numpy as np
    import pandas as pd
    import pyarrow as pa

    from trading.research.phase6.controller import controller_lock
    from trading.research.strat2d.calendar import MINUTE, Calendar
    from trading.research.strat2d.model import OnlineModel
    from trading.research.strat2d.pipeline import input_batches, source_manifest

    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    guard()
    info = source_manifest(source, config, guard)
    if config.get("resource_profile"):
        validate_label_policy(
            config, json.loads((source / "stream-manifest.json").read_text())["labels"]
        )
    old_manifest = json.loads((prior / "manifest.json").read_text())
    old = Journal(prior, old_manifest, {}, guard)
    if any(
        old_manifest["environment"][k] != v
        for k, v in {
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "arrow": pa.__version__,
        }.items()
    ):
        raise ValueError("Scientific runtime version differs from foundation")
    scientific = {k: v for k, v in config.items() if k not in {"output", "version"}}
    if (
        not old.state["complete"]
        or old_manifest["scientific"] != scientific
        or old_manifest["source"] != info
    ):
        raise ValueError("Foundation identity or completion mismatch")
    for name, digest in old_manifest["code"].items():
        if file_hash(Path(__file__).parents[1] / "strat2d" / name) != digest:
            raise ValueError("Foundation implementation changed")
    for name, digest in old_manifest["dependencies"].items():
        if file_hash(ROOT / name) != digest:
            raise ValueError("Foundation dependency changed")
    manifest = {
        "policy": POLICY,
        "scientific": scientific,
        "source": info,
        "foundation_receipt": old.previous,
        "environment": old_manifest["environment"],
        "code": {p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob("*.py"))},
    }
    cal = Calendar(config["calendar"])
    boundaries = [
        pd.Timestamp(config[k]) for k in ("start", "train_end", "calibration_end", "test_end")
    ]
    starts = [
        boundaries[0],
        boundaries[1] + config["embargo_minutes"] * MINUTE,
        boundaries[2] + config["embargo_minutes"] * MINUTE,
    ]
    blocks = [
        (pd.Timestamp(b["start"]).value, pd.Timestamp(b["end"]).value)
        for b in old.state["quality"]["blocks"]
    ]
    with controller_lock(output, lock_name=".strat2f.lock"):
        journal = Journal(output, manifest, initial(config), guard)
        s = journal.state
        if s["complete"]:
            return s
        model = OnlineModel(config["model"], s["model"])
        for (bars, features, labels), chunk, offset in input_batches(
            source, info, s["chunk"], s["offset"], config["batch_rows"], guard
        ):
            values = features[config["model"]["features"]].to_numpy(copy=False)
            label_values = labels.label.to_numpy(copy=False)
            ends = pd.DatetimeIndex(labels.label_end)
            long_net = labels.long_net_return.to_numpy(copy=False)
            short_net = labels.short_net_return.to_numpy(copy=False)
            events = []
            for i, opened in enumerate(bars.index):
                if i % 128 == 0:
                    guard()
                if s["last_open"] is not None and opened <= pd.Timestamp(s["last_open"]):
                    raise ValueError("Diagnostic input duplicate or out of order")
                s["last_open"] = opened.isoformat()
                s["rows"] += 1
                decision = features.index[i]
                if decision != opened + MINUTE:
                    raise ValueError("Feature/label decision alignment error")
                phase_index = next(
                    (j for j in range(3) if starts[j] <= decision < boundaries[j + 1]), None
                )
                if phase_index is None:
                    increment(s, "outside_windows")
                    continue
                phase = ("train", "calibration", "test")[phase_index]
                x, label, end = values[i], int(label_values[i]), ends[i]
                if phase not in s["features"]:
                    s["features"][phase] = {k: feature_stat() for k in config["model"]["features"]}
                fstats = s["features"][phase]
                for k, v, scale in zip(
                    config["model"]["features"], x, config["model"]["scales"], strict=True
                ):
                    feature_update(fstats[k], float(v), scale)
                finite = bool(np.isfinite(x).all())
                is_open = cal.classify(opened) == "open"
                target_ok = label >= 0 and not pd.isna(end)
                if target_ok:
                    if end < decision:
                        raise ValueError("Target information precedes decision")
                    horizon = float((end - decision) / MINUTE)
                    if horizon > 15:
                        raise ValueError("Unexpected label horizon")
                    s["maximum_label_horizon_minutes"] = max(
                        s["maximum_label_horizon_minutes"], horizon
                    )
                integrity = target_ok and not any(
                    opened.value < b and end.value > a for a, b in blocks
                )
                reason = split_reason(
                    decision,
                    end,
                    finite,
                    is_open,
                    target_ok,
                    integrity,
                    starts[phase_index],
                    boundaries[phase_index + 1],
                )
                increment(s, phase + "_" + reason)
                if phase == "test" and finite and is_open:
                    p = model.predict(x)
                    raw = model.softmax(model.vector(x) @ model.weights)
                    diag = s["inference"]
                    diag["n"] += 1
                    diag["max_cal"] = [
                        max(a, float(b)) for a, b in zip(diag["max_cal"], p, strict=True)
                    ]
                    diag["max_raw"] = [
                        max(a, float(b)) for a, b in zip(diag["max_raw"], raw, strict=True)
                    ]
                    diag["normalization_error"] = max(
                        diag["normalization_error"], abs(float(p.sum()) - 1)
                    )
                    diag["max_abs_logit"] = max(
                        diag["max_abs_logit"], float(np.abs(model.vector(x) @ model.weights).max())
                    )
                    diag["confidence_histogram"][min(9, int(float(p.max()) * 10))] += 1
                if reason != "eligible":
                    continue
                if phase == "train":
                    update(s["train_optimizer"], label, model.predict(x).tolist())
                    model.fit_one(
                        x, label, decision.isoformat(), end.isoformat(), boundaries[1].isoformat()
                    )
                elif phase == "calibration":
                    update(s["calibration_optimizer"], label, model.predict(x).tolist())
                    model.calibrate_one(
                        x, label, decision.isoformat(), end.isoformat(), boundaries[2].isoformat()
                    )
                else:
                    if logical_hash(model.export()) != old.state["frozen_model_hash"]:
                        raise ValueError("Reconstructed model differs before test inference")
                    probabilities = {
                        "softmax_raw": raw.tolist(),
                        "softmax_cal": p.tolist(),
                        **baselines(model.state["class_counts"], float(x[1])),
                    }
                    session = (
                        "utc_00_07"
                        if opened.hour < 7
                        else "utc_07_13"
                        if opened.hour < 13
                        else "utc_13_17"
                        if opened.hour < 17
                        else "utc_17_24"
                    )
                    day_key = decision.date().isoformat()
                    if day_key not in s["days"] and len(s["days"]) >= POLICY["daily_budget"]:
                        raise ValueError("Daily diagnostic metadata budget exceeded")
                    if day_key not in s["days"]:
                        s["days"][day_key] = {
                            "models": {k: metric(False) for k in MODELS},
                            "thresholds": {str(t): threshold_stat() for t in THRESHOLDS},
                        }
                    day = s["days"][day_key]
                    for name, probability in probabilities.items():
                        for group in ("test_2022-02", session):
                            group_metrics = s["metrics"].setdefault(group, {})
                            if name not in group_metrics:
                                group_metrics[name] = metric()
                            update(group_metrics[name], label, probability)
                        update(day["models"][name], label, probability)
                    distance_ok = float(x[3]) > 0
                    allowed = (
                        distance_ok
                        and decision + 15 * MINUTE <= boundaries[3]
                        and cal.entry_window_open(decision, 15)
                    )
                    side = 1 if p[1] >= p[2] else 2
                    net = float(long_net[i] if side == 1 else short_net[i]) * 10000
                    if not math.isfinite(net):
                        raise ValueError("Admissible economic target is nonfinite")
                    for threshold in THRESHOLDS:
                        if allowed and p[side] >= max(p[0], threshold):
                            for stats in (
                                s["thresholds"][str(threshold)],
                                day["thresholds"][str(threshold)],
                            ):
                                threshold_update(
                                    stats, side, label, net, decision.value, (15 * MINUTE).value
                                )
                    s["event_seq"] += 1
                    events.append(
                        {
                            "sequence": s["event_seq"],
                            "time": decision.isoformat(),
                            "opened_at": opened.isoformat(),
                            "label_end": end.isoformat(),
                            "label": label,
                            "probabilities": probabilities,
                            "allowed": allowed,
                            "side_net_bps": net,
                        }
                    )
            s.update(chunk=chunk, offset=offset, model=model.export())
            s["trace"].append(
                {
                    "rows": s["rows"],
                    "train_rows": model.state["train_rows"],
                    "calibration_rows": model.state["calibration_rows"],
                    "log_temperature": model.state["log_temperature"],
                    "weight_norm": float(np.linalg.norm(model.weights)),
                    "next_learning_rate": config["model"]["learning_rate"]
                    / math.sqrt(1 + model.state["train_rows"] * 0.0001),
                    "train_loss_sum": s["train_optimizer"]["loss"],
                }
            )
            journal.commit(s, events, fault)
            del bars, features, labels, values, ends, long_net, short_net, events
        if logical_hash(model.export()) != old.state["frozen_model_hash"]:
            raise ValueError("Full chronological model reconstruction mismatch")
        if (
            s["metrics"]["test_2022-02"]["softmax_cal"]["n"]
            != old.state["metrics"]["test_labeled_rows"]
        ):
            raise ValueError("Evaluation row count differs from foundation")
        s["complete"] = True
        journal.commit(s, [], fault)
        return s
