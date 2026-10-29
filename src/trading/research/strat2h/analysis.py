"""Dependent uncertainty and independent streaming checks of diagnostic evidence."""

import json
import math
from collections import deque

from trading.research.strat2d.journal import logical_hash
from trading.research.strat2f.metrics import metric, summarize, update
from trading.research.strat2h.statistics import summary


def events(path):
    for number in range(10000):
        directory = path / f"{number:08d}"
        if not directory.exists():
            return
        with (directory / "events.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                yield json.loads(line)


def combine(values):
    result = metric(False)
    for value in values:
        for key in ("n", "loss", "brier"):
            result[key] += value[key]
        for i in range(3):
            for j in range(3):
                result["cm"][i][j] += value["cm"][i][j]
    return summarize(result)


def uncertainty(daily, protocol, phase):
    import random

    from trading.research.strat2f.analysis import block_indices, interval

    days = [v for k, v in sorted(daily.items()) if k.startswith(phase + ":")]
    definition = protocol["uncertainty"]
    if len(days) < definition["minimum_observed_days"]:
        return {"status": "INSUFFICIENT_DAYS", "days": len(days)}
    result = {"days": len(days), "status": "EXPLORATORY_ONLY", "blocks": {}}
    for block in definition["block_days"]:
        rng = random.Random(definition["seed"] + block)
        values = {
            k: []
            for k in (
                "loss_vs_reference",
                "loss_vs_prior",
                "ba_vs_reference",
                "ba_vs_prior",
                "forced_net",
            )
        }
        values.update({"threshold_net:" + str(t): [] for t in protocol["thresholds"]})
        signal_days = {
            str(t): sum(d["thresholds"][str(t)]["movement"]["n"] > 0 for d in days)
            for t in protocol["thresholds"]
        }
        for _ in range(definition["replicates"]):
            sample = [days[int(i)] for i in block_indices(len(days), block, rng)]
            scores = {
                v: combine(d["metrics"][v] for d in sample) for v in ("cal", "reference", "prior")
            }
            for base in ("reference", "prior"):
                values["loss_vs_" + base].append(
                    scores["cal"]["log_loss"] - scores[base]["log_loss"]
                )
                a, b = scores["cal"]["balanced_accuracy"], scores[base]["balanced_accuracy"]
                if a is not None and b is not None:
                    values["ba_vs_" + base].append(a - b)
            n = sum(d["forced"]["movement"]["n"] for d in sample)
            values["forced_net"].append(
                sum(d["forced"]["movement"]["total"] for d in sample) / n - 2.1
            )
            for t in protocol["thresholds"]:
                key = str(t)
                n = sum(d["thresholds"][key]["movement"]["n"] for d in sample)
                if n and signal_days[key] >= definition["minimum_signal_days"]:
                    values["threshold_net:" + key].append(
                        sum(d["thresholds"][key]["movement"]["total"] for d in sample) / n - 2.1
                    )
        result["blocks"][str(block)] = {
            k: interval(v) if v else {"status": "INSUFFICIENT_SIGNAL_DAYS"}
            for k, v in values.items()
        }
    return result


def analyze(state, protocol, guard=lambda: None):
    guard()
    result = {
        "groups": {k: summary(v, protocol) for k, v in sorted(state["groups"].items())},
        "uncertainty": {},
        "status": "INCONCLUSIVE",
        "reason": "Exploratory data reuse; no independently established economic advantage.",
    }
    for phase in ("selection", "evaluation"):
        guard()
        result["uncertainty"][phase] = uncertainty(state["daily"], protocol, phase)
        guard()
    # Require evidence at the unchanged gate; never select or lower a threshold.
    e = result["groups"].get("phase:evaluation", {})
    signal = e.get("thresholds", {}).get("0.55", {})
    u = result["uncertainty"].get("evaluation", {})
    predictive = u.get("status") == "EXPLORATORY_ONLY"
    for block in u.get("blocks", {}).values():
        for name in ("reference", "prior"):
            loss, accuracy = block["loss_vs_" + name], block["ba_vs_" + name]
            predictive = predictive and loss.get("upper", 1) < 0 and accuracy.get("lower", -1) > 0
    contradicted = u.get("status") == "EXPLORATORY_ONLY"
    for block in u.get("blocks", {}).values():
        for name in ("reference", "prior"):
            contradicted = (
                contradicted
                and block["loss_vs_" + name].get("lower", -1) > 0
                and block["ba_vs_" + name].get("upper", 1) <= 0
            )
    if contradicted:
        result.update(
            status="FAIL", reason="Paired dependent intervals contradict predictive advantage."
        )
    if (
        predictive
        and signal.get("n", 0) >= 200
        and signal.get("net_mean_bps_by_cost", {}).get("2.1", -1) > 0
    ):
        result["reason"] = (
            "Predictive/economic exploratory screens pass; historical calendar remains unverified, "
            "so independent confirmation readiness is unresolved."
        )
    return result


def verify(path, state, protocol, config, guard):
    """Second input pass joins each event to bounded OHLC/features and frozen G events.

    Recalculates probabilities from matrix arithmetic, returns from raw prices,
    path extrema, threshold counts and sums. No fitting or journal publication.
    """
    import numpy as np

    from trading.research.safety import ROOT
    from trading.research.strat2d.calendar import Calendar
    from trading.research.strat2d.pipeline import input_batches, source_manifest
    from trading.research.strat2g.core import Features, safe_observation
    from trading.research.strat2h.pipeline import frozen
    from trading.research.strat2h.resources import validate_config

    models, _, expected_source = frozen(protocol, config, guard)
    source, _ = validate_config(config, path)
    info = source_manifest(source, config, guard)
    if info != expected_source:
        raise ValueError("Verifier source identity mismatch")
    g_events = (
        e for e in events(ROOT / protocol["frozen"]["journal"]) if e["target"] == "h15_cost"
    )

    def input_rows():
        for (bars, features, _), _, _ in input_batches(
            source,
            info,
            0,
            0,
            config["batch_rows"],
            guard,
            feature_columns=config["model"]["features"],
        ):
            original = features[config["model"]["features"]].to_numpy(copy=False)
            for i, stamp in enumerate(bars.index):
                if i % 64 == 0:
                    guard()
                yield (
                    stamp,
                    [float(bars[k].iloc[i]) for k in ("open", "high", "low", "close")],
                    original[i],
                )

    source_rows = iter(input_rows())
    history, engine, calendar = deque(maxlen=31), Features(), Calendar(config["calendar"])
    minute, last = -1, None
    sequence, compared = 0, 0
    metrics, thresholds, bins = {}, {}, {}
    for event in events(path):
        guard()
        sequence += 1
        phase, decision = event["phase"], event["decision"]
        if event["sequence"] != sequence or (last is not None and decision <= last):
            raise ValueError("Diagnostic sequence duplication/skip")
        last = decision
        while minute < event["common_end"] - 1:
            try:
                stamp, bar, original = next(source_rows)
            except StopIteration:
                raise ValueError("Diagnostic path exceeds source") from None
            next_minute = int(stamp.timestamp()) // 60
            if next_minute != minute + 1 or not safe_observation(stamp, calendar):
                history.clear()
                engine.clear()
            minute = next_minute
            if not safe_observation(stamp, calendar):
                continue
            computed = engine.push(minute, bar)
            history.append((minute, bar, original, computed))
        rows = list(history)
        if (
            len(rows) != 31
            or rows[0][0] != decision - 1
            or any(r[0] != decision - 1 + i for i, r in enumerate(rows))
        ):
            raise ValueError("Diagnostic label crosses gap or session exclusion")
        entry, exit_price = rows[1][1][0], rows[15][1][3]
        gross = (exit_price / entry - 1) * 10000
        actual_label = 1 if gross > 2.1 else 2 if gross < -2.1 else 0
        if (
            event["entry_price"] != entry
            or event["exit_price"] != exit_price
            or event["label"] != actual_label
            or event["gross_bps"] != gross
            or event["path_high"] != max(r[1][1] for r in rows[1:16])
            or event["path_low"] != min(r[1][2] for r in rows[1:16])
            or event["features"] != rows[0][3][0]
            or event["sigma"] != rows[0][3][1]
            or event["label_end"] != decision + 15
            or event["common_end"] != decision + 30
            or event["matured_at"] != event["common_end"]
        ):
            raise ValueError("Independent OHLC/feature/label alignment mismatch")
        from datetime import datetime

        left, right = protocol["splits"][phase]
        left, right = (
            int(datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()) // 60
            for t in (left, right)
        )
        if phase != "train":
            left += protocol["embargo_minutes"]
        if not left <= decision < right or event["common_end"] >= right:
            raise ValueError("Independent split/purge/embargo mismatch")
        candidate = models["linear12"]
        vector = np.r_[1.0, event["features"]]
        logits = vector @ candidate.model.weights
        for view, temperature in (
            ("raw", 1),
            ("cal", math.exp(candidate.model.state["log_temperature"])),
        ):
            z = logits / temperature
            p = np.exp(z - max(z))
            p /= p.sum()
            if not np.allclose(p, event["probabilities"][view], rtol=0, atol=1e-14):
                raise ValueError("Independent frozen inference mismatch")
        if phase in ("selection", "evaluation"):
            expected = next(g_events, None)
            if expected is None or any(
                event[k] != expected[k]
                for k in ("phase", "decision", "label", "gross_bps", "label_end", "common_end")
            ):
                raise ValueError("Published G event alignment mismatch")
            for hname, gname in (
                ("raw", "linear12_raw"),
                ("cal", "linear12_cal"),
                ("reference", "reference8_cal"),
                ("prior", "prior"),
            ):
                if event["probabilities"][hname] != expected["probabilities"][gname]:
                    raise ValueError("Published frozen G prediction changed")
            compared += 1
        if phase not in metrics:
            metrics[phase] = {k: metric(True) for k in event["probabilities"]}
            thresholds[phase] = {str(t): [0, 0.0] for t in protocol["thresholds"]}
            bins[phase] = [[0, 0.0] for _ in range(20)]
        for view, p in event["probabilities"].items():
            update(metrics[phase][view], actual_label, p)
        p = event["probabilities"]["cal"]
        side = 1 if p[1] >= p[2] else 2
        signed = gross if side == 1 else -gross
        bucket = min(19, int(max(p[1], p[2]) * 20))
        bins[phase][bucket][0] += 1
        bins[phase][bucket][1] += signed
        for t in protocol["thresholds"]:
            if p[side] >= p[0] and p[side] >= t:
                thresholds[phase][str(t)][0] += 1
                thresholds[phase][str(t)][1] += signed
    if next(g_events, None) is not None or sequence != state["sequence"]:
        raise ValueError("Missing diagnostic or frozen G events")
    for phase, values in metrics.items():
        observed = state["groups"]["phase:" + phase]
        if logical_hash(values) != logical_hash(observed["metrics"]):
            raise ValueError("Independent metric mismatch")
        for t, (n, total) in thresholds[phase].items():
            stat = observed["thresholds"][t]["movement"]
            if n != stat["n"] or total != stat["total"]:
                raise ValueError("Independent threshold accounting mismatch")
        for (n, total), stat in zip(
            bins[phase], observed["views"]["cal"]["direction_bins"], strict=True
        ):
            if n != stat["n"] or total != stat["total"]:
                raise ValueError("Independent probability-bin accounting mismatch")
    return {
        "status": "PASS",
        "events_verified": sequence,
        "frozen_G_predictions_matched": compared,
        "input_max_path": 31,
        "feature_history_max": 61,
        "checks": [
            "source receipts",
            "frozen identity",
            "OHLC paths",
            "causal features",
            "horizon",
            "purge/embargo",
            "raw/cal probabilities",
            "G predictions",
            "class metrics",
            "threshold costs/counts",
            "probability-bin returns",
            "sequence uniqueness",
        ],
    }
