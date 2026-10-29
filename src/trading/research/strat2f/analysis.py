"""Paired time-dependent uncertainty from bounded daily sufficient statistics."""

import math
import random

from trading.research.strat2f.metrics import THRESHOLDS, combine, ratio, summarize


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * fraction
    left = int(position)
    return values[left] + (values[min(left + 1, len(values) - 1)] - values[left]) * (
        position - left
    )


def interval(values):
    return {
        "lower": percentile(values, 0.025),
        "upper": percentile(values, 0.975),
        "valid_replicates": len(values),
    }


def block_indices(n, length, rng):
    if not n or length < 1:
        raise ValueError("Positive day count and block length required")
    out = []
    while len(out) < n:
        start = rng.randrange(n)
        out.extend((start + j) % n for j in range(length))
    return out[:n]


def uncertainty(days, seed, repetitions=1000):
    ordered = [days[k] for k in sorted(days)]
    pairs = [
        ("softmax_cal", "prior"),
        ("softmax_raw", "prior"),
        ("softmax_cal", "softmax_raw"),
        ("momentum", "prior"),
        ("mean_reversion", "prior"),
    ]
    result = {}
    for length in (2, 5):
        rng = random.Random(seed + length)
        differences = {a + "-" + b: {"log_loss": [], "balanced_accuracy": []} for a, b in pairs}
        thresholds = {str(t): {"precision": [], "net_bps": []} for t in THRESHOLDS}
        for _ in range(repetitions):
            selected = [ordered[i] for i in block_indices(len(ordered), length, rng)]
            names = {name for pair in pairs for name in pair}
            scores = {
                name: summarize(combine(d["models"][name] for d in selected)) for name in names
            }
            for a, b in pairs:
                for key in ("log_loss", "balanced_accuracy"):
                    if scores[a][key] is not None and scores[b][key] is not None:
                        differences[a + "-" + b][key].append(scores[a][key] - scores[b][key])
            for t in thresholds:
                n = sum(d["thresholds"][t]["n"] for d in selected)
                if n:
                    thresholds[t]["precision"].append(
                        sum(d["thresholds"][t]["correct"] for d in selected) / n
                    )
                    thresholds[t]["net_bps"].append(
                        sum(d["thresholds"][t]["net_bps"] for d in selected) / n
                    )
        result[str(length)] = {
            "day_blocks": len(ordered),
            "paired_differences": {
                pair: {k: interval(v) for k, v in metrics.items()}
                for pair, metrics in differences.items()
            },
            "thresholds": {
                t: {k: interval(v) for k, v in metrics.items()} for t, metrics in thresholds.items()
            },
        }
    return result


def threshold_summary(s, denominator, day_count):
    n = s["n"]
    gain = ratio(s["positive_gross_sum"], s["positive_n"])
    loss = ratio(-s["nonpositive_gross_sum"], s["nonpositive_n"])
    return {
        "n": n,
        "long": s["long"],
        "short": s["short"],
        "precision": ratio(s["correct"], n),
        "signal_fraction": ratio(n, denominator),
        "signals_per_observed_day": ratio(n, day_count),
        "fixed_15m_spacing_count": s["spaced_signals"],
        "gross_barrier_bps": ratio(s["gross_bps"], n),
        "net_label_bps": ratio(s["net_bps"], n),
        "net_positive_fraction": ratio(s["net_positive"], n),
        "conditional_break_even_accuracy": (loss + 2.1) / (gain + loss)
        if gain is not None and loss is not None and gain + loss
        else None,
        "cost_sensitivity_net_bps": {
            str(cost): s["gross_bps"] / n - cost if n else None
            for cost in (1.1, 1.6, 2.1, 3.1, 5.6)
        },
    }


def report_data(state, seed, repetitions=1000):
    metrics = {
        group: {name: summarize(m) for name, m in models.items()}
        for group, models in state["metrics"].items()
    }
    test = metrics["test_2022-02"]
    thresholds = {
        t: threshold_summary(s, test["softmax_cal"]["n"], len(state["days"]))
        for t, s in state["thresholds"].items()
    }
    ci = uncertainty(state["days"], seed, repetitions)
    return {
        "classification": "INCONCLUSIVE — insufficient evidence or unresolved uncertainty",
        "metrics": metrics,
        "thresholds": thresholds,
        "uncertainty": ci,
        "temperature": math.exp(state["model"]["log_temperature"]),
        "statistical_policy": (
            "February already inspected development data; no threshold chosen; "
            "day-block intervals exploratory; historical calendar unverified"
        ),
    }
