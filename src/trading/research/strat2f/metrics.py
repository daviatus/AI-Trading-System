"""Small deterministic streaming statistics; no ML or plotting dependencies."""

import math

MODELS = ("softmax_raw", "softmax_cal", "majority", "prior", "momentum", "mean_reversion")
THRESHOLDS = (0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60)
EPS = 1e-12


def metric(full=True):
    result = {"n": 0, "loss": 0.0, "brier": 0.0, "cm": [[0] * 3 for _ in range(3)]}
    if full:
        result.update(
            bins=[[0, 0.0, 0] for _ in range(10)],
            class_bins=[[[0, 0.0, 0] for _ in range(10)] for _ in range(3)],
        )
    return result


def validate_probability(p):
    if (
        len(p) != 3
        or not all(math.isfinite(x) and 0 <= x <= 1 for x in p)
        or not math.isclose(sum(p), 1.0, abs_tol=1e-8)
    ):
        raise ValueError("Invalid normalized probabilities")


def update(m, y, p):
    validate_probability(p)
    if y not in (0, 1, 2):
        raise ValueError("Invalid metric target")
    chosen = max(range(3), key=lambda i: p[i])
    m["n"] += 1
    m["loss"] -= math.log(max(EPS, p[y]))
    m["brier"] += sum((p[i] - int(i == y)) ** 2 for i in range(3))
    m["cm"][y][chosen] += 1
    if "bins" in m:
        b = m["bins"][min(9, int(p[chosen] * 10))]
        b[0] += 1
        b[1] += float(p[chosen])
        b[2] += int(chosen == y)
        for i in range(3):
            b = m["class_bins"][i][min(9, int(p[i] * 10))]
            b[0] += 1
            b[1] += float(p[i])
            b[2] += int(i == y)


def ratio(a, b):
    return a / b if b else None


def summarize(m):
    cm, n = m["cm"], m["n"]
    support = [sum(row) for row in cm]
    predicted = [sum(row[i] for row in cm) for i in range(3)]
    recall = [ratio(cm[i][i], support[i]) for i in range(3)]
    result = {
        "n": n,
        "class_counts": support,
        "predicted_counts": predicted,
        "confusion_matrix": cm,
        "accuracy": ratio(sum(cm[i][i] for i in range(3)), n),
        "balanced_accuracy": sum(recall) / 3 if all(v is not None for v in recall) else None,
        "precision": [ratio(cm[i][i], predicted[i]) for i in range(3)],
        "recall": recall,
        "log_loss": ratio(m["loss"], n),
        "brier": ratio(m["brier"], n),
    }
    if "bins" in m:

        def bins(values):
            return [
                {
                    "lower": i / 10,
                    "upper": (i + 1) / 10,
                    "n": b[0],
                    "mean_probability": ratio(b[1], b[0]),
                    "observed_fraction": ratio(b[2], b[0]),
                }
                for i, b in enumerate(values)
            ]

        result["reliability"] = bins(m["bins"])
        result["class_reliability"] = [bins(v) for v in m["class_bins"]]
        result["ece_top_label"] = ratio(sum(abs(b[2] - b[1]) for b in m["bins"]), n)
        result["ece_by_class"] = [
            ratio(sum(abs(b[2] - b[1]) for b in v), n) for v in m["class_bins"]
        ]
    return result


def baselines(class_counts, momentum):
    total = sum(class_counts)
    if not total:
        raise ValueError("Prior requires past training labels")
    majority = max(range(3), key=lambda i: class_counts[i])
    direction = 1 if momentum > 0 else 2 if momentum < 0 else 0
    inverse = 3 - direction if direction else 0

    def onehot(k):
        return [float(i == k) for i in range(3)]

    return {
        "majority": onehot(majority),
        "prior": [n / total for n in class_counts],
        "momentum": onehot(direction),
        "mean_reversion": onehot(inverse),
    }


def feature_stat():
    return {
        "n": 0,
        "missing": 0,
        "mean": 0.0,
        "m2": 0.0,
        "min": None,
        "max": None,
        "clipped": 0,
        "normalized_histogram": [0] * 12,
    }


def feature_update(s, x, scale):
    if not math.isfinite(x):
        s["missing"] += 1
        return
    s["n"] += 1
    delta = x - s["mean"]
    s["mean"] += delta / s["n"]
    s["m2"] += delta * (x - s["mean"])
    s["min"] = x if s["min"] is None else min(x, s["min"])
    s["max"] = x if s["max"] is None else max(x, s["max"])
    z = x / scale
    s["clipped"] += int(abs(z) > 20)
    edges = (-20, -5, -2, -1, -0.25, 0, 0.25, 1, 2, 5, 20)
    bucket = sum(z >= edge for edge in edges)
    s["normalized_histogram"][bucket] += 1


def split_reason(decision, end, finite, opened, target, integrity, left, right):
    if not left <= decision < right:
        return "outside_window"
    if not finite:
        return "nonfinite_predictors"
    if not opened:
        return "out_of_session"
    if not target:
        return "unavailable_label"
    if not integrity:
        return "quality_block"
    if end >= right:
        return "label_boundary"
    return "eligible"


def threshold_stat():
    return {
        "n": 0,
        "correct": 0,
        "long": 0,
        "short": 0,
        "gross_bps": 0.0,
        "net_bps": 0.0,
        "positive_n": 0,
        "positive_gross_sum": 0.0,
        "nonpositive_n": 0,
        "nonpositive_gross_sum": 0.0,
        "net_positive": 0,
        "spaced_signals": 0,
        "next_spaced": None,
    }


def threshold_update(s, side, y, net_bps, decision_ns, horizon_ns):
    gross = net_bps + 2.1
    s["n"] += 1
    s["correct"] += int(y == side)
    s["long" if side == 1 else "short"] += 1
    s["gross_bps"] += gross
    s["net_bps"] += net_bps
    s["net_positive"] += int(net_bps > 0)
    key = "positive" if gross > 0 else "nonpositive"
    s[key + "_n"] += 1
    s[key + "_gross_sum"] += gross
    if s["next_spaced"] is None or decision_ns >= s["next_spaced"]:
        s["spaced_signals"] += 1
        s["next_spaced"] = decision_ns + horizon_ns


def combine(metrics):
    out = metric(False)
    for m in metrics:
        for key in ("n", "loss", "brier"):
            out[key] += m[key]
        for i in range(3):
            for j in range(3):
                out["cm"][i][j] += m["cm"][i][j]
    return out
