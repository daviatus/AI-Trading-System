"""Fixed paired day-block analysis; final outcomes never choose a model."""

import random

from trading.research.strat2f.analysis import block_indices, interval
from trading.research.strat2f.metrics import combine, summarize


def analyze(s, protocol):
    metrics = {
        phase: {
            t: {name: summarize(m) for name, m in models.items()} for t, models in targets.items()
        }
        for phase, targets in s["metrics"].items()
    }
    uncertainty = {}
    for t, chosen in s["choices"].items():
        if chosen is None or not s["daily"]:
            uncertainty[t] = {"status": "insufficient selection evidence"}
            continue
        days = [s["daily"][k][t] for k in sorted(s["daily"])]
        estimates = {}
        for length in protocol["uncertainty"]["circular_adjacent_day_lengths"]:
            rng = random.Random(protocol["seed"] + length)
            values = {
                baseline: {"log_loss": [], "balanced_accuracy": []}
                for baseline in ("reference8_cal", "prior")
            }
            for _ in range(protocol["uncertainty"]["repetitions"]):
                selected = [days[i] for i in block_indices(len(days), length, rng)]
                scores = {
                    name: summarize(combine(day[name] for day in selected))
                    for name in {chosen + "_cal", "reference8_cal", "prior"}
                }
                for baseline in values:
                    for metric_name in values[baseline]:
                        a, b = scores[chosen + "_cal"][metric_name], scores[baseline][metric_name]
                        if a is not None and b is not None:
                            values[baseline][metric_name].append(a - b)
            estimates[str(length)] = {
                baseline: {name: interval(v) for name, v in data.items()}
                for baseline, data in values.items()
            }
        uncertainty[t] = {"days": len(days), "selected": chosen, "intervals": estimates}
    improved = []
    for t, u in uncertainty.items():
        if "intervals" not in u:
            continue
        good = True
        for comparisons in u["intervals"].values():
            for values in comparisons.values():
                if (
                    values["log_loss"]["upper"] is None
                    or values["log_loss"]["upper"] >= 0
                    or values["balanced_accuracy"]["lower"] is None
                    or values["balanced_accuracy"]["lower"] <= 0
                ):
                    good = False
        if good:
            improved.append(t)
    classification = (
        "PASS — exploratory predictive improvement demonstrated, "
        "independent confirmation still required"
        if improved
        else "INCONCLUSIVE — insufficient evidence to establish reliable improvement"
    )
    return {
        "classification": classification,
        "improved_targets": improved,
        "metrics": metrics,
        "selection_choices": s["choices"],
        "uncertainty": uncertainty,
        "sessions": {
            t: {
                group: {name: summarize(m) for name, m in models.items()}
                for group, models in groups.items()
            }
            for t, groups in s["sessions"].items()
        },
    }


def verify_events(output, state, protocol, guard):
    """Independent direct formulas on streamed events, without model implementation."""
    import json
    import math

    actual = {}
    sequence, previous = 0, None
    for generation in range(10000):
        path = output / f"{generation:08d}/events.jsonl"
        if not path.exists():
            break
        with path.open() as f:
            for line in f:
                if sequence % 128 == 0:
                    guard()
                e = json.loads(line)
                sequence += 1
                if (
                    e["sequence"] != sequence
                    or not e["decision"] <= e["label_end"] <= e["common_end"] == e["matured_at"]
                ):
                    raise ValueError("Prediction event chronology or target maturity mismatch")
                if previous is not None and e["decision"] < previous:
                    raise ValueError("Out-of-order prediction evidence")
                previous = e["decision"]
                from trading.research.strat2g.core import phase_of

                definition = next(t for t in protocol["targets"] if t["id"] == e["target"])
                gross = (e["exit_price"] / e["entry_price"] - 1) * 10000
                neutral = max(
                    definition["neutral_cost_bps"],
                    definition["volatility_multiple"]
                    * e["sigma_at_decision"]
                    * math.sqrt(definition["horizon"])
                    * 10000,
                )
                if (
                    phase_of(e["decision"], e["common_end"], protocol) != e["phase"]
                    or e["label_end"] - e["decision"] != definition["horizon"]
                    or abs(gross - e["gross_bps"]) > 1e-8
                    or abs(neutral - e["neutral_bps"]) > 1e-8
                ):
                    raise ValueError("Independent price units, horizon or split mismatch")
                for name, p in e["probabilities"].items():
                    key = (e["phase"], e["target"], name)
                    a = actual.setdefault(
                        key, {"n": 0, "loss": 0.0, "brier": 0.0, "cm": [[0] * 3 for _ in range(3)]}
                    )
                    if (
                        len(p) != 3
                        or not all(math.isfinite(x) and 0 <= x <= 1 for x in p)
                        or abs(sum(p) - 1) > 1e-8
                    ):
                        raise ValueError("Independent probability integrity failure")
                    y, pred = e["label"], p.index(max(p))
                    # Label direction and neutral-zone definition are independently checked.
                    expected = (
                        1
                        if e["gross_bps"] > e["neutral_bps"]
                        else 2
                        if e["gross_bps"] < -e["neutral_bps"]
                        else 0
                    )
                    if y != expected:
                        raise ValueError("Independent cost-aware label mismatch")
                    a["n"] += 1
                    a["loss"] -= math.log(max(p[y], 1e-12))
                    a["brier"] += sum((p[i] - int(i == y)) ** 2 for i in range(3))
                    a["cm"][y][pred] += 1
    for (phase, t, name), a in actual.items():
        m = state["metrics"][phase][t][name]
        if (
            a["n"] != m["n"]
            or a["cm"] != m["cm"]
            or abs(a["loss"] - m["loss"]) > 1e-8
            or abs(a["brier"] - m["brier"]) > 1e-8
        ):
            raise ValueError("Independent metric totals mismatch")
    from trading.research.strat2g.core import select

    expected_choices = {
        t["id"]: select(state["metrics"]["selection"][t["id"]], state["selection_days"], protocol)
        for t in protocol["targets"]
    }
    if expected_choices != state["choices"] or sequence != state["event_seq"]:
        raise ValueError("Selection provenance or event count mismatch")
    return {
        "status": "PASS",
        "events": sequence,
        "metric_groups": len(actual),
        "selection_recomputed_without_final_outcomes": True,
    }
