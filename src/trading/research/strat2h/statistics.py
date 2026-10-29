"""Bounded sufficient statistics; observations and equity curves never accumulate."""

import math

from trading.research.strat2f.metrics import metric, summarize, update, validate_probability


def movement():
    return dict(
        n=0,
        total=0.0,
        square=0.0,
        absolute=0.0,
        positive=0,
        label_hit=0,
        favorable=0.0,
        adverse=0.0,
        scaled=0.0,
        minimum=None,
        maximum=None,
    )


def add_movement(s, gross, favorable, adverse, scaled, label_hit):
    if not all(math.isfinite(v) for v in (gross, favorable, adverse, scaled)):
        raise ValueError("Nonfinite movement")
    s["n"] += 1
    s["total"] += gross
    s["square"] += gross * gross
    s["absolute"] += abs(gross)
    s["positive"] += int(gross > 0)
    s["label_hit"] += int(label_hit)
    s["favorable"] += favorable
    s["adverse"] += adverse
    s["scaled"] += scaled
    s["minimum"] = gross if s["minimum"] is None else min(gross, s["minimum"])
    s["maximum"] = gross if s["maximum"] is None else max(gross, s["maximum"])


def describe(s, costs):
    n = s["n"]
    if not n:
        return {"n": 0}
    mean = s["total"] / n
    wins = s["positive"]
    gain = (s["absolute"] + s["total"]) / (2 * wins) if wins else None
    loss = (s["absolute"] - s["total"]) / (2 * (n - wins)) if n > wins else None
    return {
        "n": n,
        "mean_signed_bps": mean,
        "standard_deviation_bps": math.sqrt(max(0, s["square"] / n - mean * mean)),
        "mean_absolute_bps": s["absolute"] / n,
        "sign_hit_rate": s["positive"] / n,
        "label_precision": s["label_hit"] / n,
        "mean_favorable_bps": s["favorable"] / n,
        "mean_adverse_bps": s["adverse"] / n,
        "mean_volatility_scaled_signed_move": s["scaled"] / n,
        "minimum_bps": s["minimum"],
        "maximum_bps": s["maximum"],
        "net_mean_bps_by_cost": {str(c): mean - c for c in costs},
        "mean_positive_move_bps": gain,
        "mean_nonpositive_loss_bps": loss,
        "break_even_positive_fraction_by_cost": {
            str(c): (loss + c) / (gain + loss)
            if gain is not None and loss is not None and gain + loss > 0
            else None
            for c in costs
        },
    }


def signal():
    return {
        "movement": movement(),
        "long": 0,
        "short": 0,
        "clusters": 0,
        "nonoverlap_count": 0,
        "last": None,
        "last_nonoverlap": None,
        "wins_after_base_cost": 0,
    }


def add_signal(s, event, side):
    gross, favorable, adverse, scaled = directed(event, side)
    add_movement(s["movement"], gross, favorable, adverse, scaled, event["label"] == side)
    minute = event["decision"]
    s["long" if side == 1 else "short"] += 1
    s["wins_after_base_cost"] += int(gross > 2.1)
    if s["last"] is None or minute > s["last"] + 15:
        s["clusters"] += 1
    if s["last_nonoverlap"] is None or minute >= s["last_nonoverlap"] + 15:
        s["nonoverlap_count"] += 1
        s["last_nonoverlap"] = minute
    s["last"] = minute


def directed(event, side):
    entry = event["entry_price"]
    high = (event["path_high"] / entry - 1) * 10000
    low = (event["path_low"] / entry - 1) * 10000
    signed = event["gross_bps"] * (1 if side == 1 else -1)
    favorable = max(0, high if side == 1 else -low)
    adverse = max(0, -low if side == 1 else high)
    scaled = signed / (max(event["sigma"], 1e-6) * math.sqrt(15) * 10000)
    return signed, favorable, adverse, scaled


def group(protocol, full=True):
    return {
        "metrics": {view: metric(full) for view in ("raw", "cal", "reference", "prior")},
        "views": {
            view: {
                "max_probability": [0.0] * 3,
                "sum_probability": [0.0] * 3,
                "predicted_class": [movement() for _ in range(3)],
                "direction_bins": [movement() for _ in range(20)],
                "return_histograms_by_class": [
                    [0] * (len(protocol["return_histogram_bps_edges"]) + 1) for _ in range(3)
                ],
                "forced_direction": signal(),
            }
            for view in ("raw", "cal")
        },
        "thresholds": {str(t): signal() for t in protocol["thresholds"]},
        "features": [{"n": 0, "sum": 0.0, "square": 0.0, "clipped": 0} for _ in range(12)],
        "near_gap_count": 0,
    }


def add_group(s, event, protocol):
    y, gross = event["label"], event["gross_bps"]
    for view, p in event["probabilities"].items():
        update(s["metrics"][view], y, p)
    for j, x in enumerate(event["features"]):
        if not math.isfinite(x) or abs(x) > 5:
            raise ValueError("Invalid frozen feature vector")
        f = s["features"][j]
        f["n"] += 1
        f["sum"] += x
        f["square"] += x * x
        f["clipped"] += int(abs(x) == 5)
    s["near_gap_count"] += int(event["history_since_reset"] < 120)
    for view in ("raw", "cal"):
        p = event["probabilities"][view]
        validate_probability(p)
        v = s["views"][view]
        chosen = max(range(3), key=lambda i: p[i])
        side = 1 if p[1] >= p[2] else 2
        values = directed(event, side)
        for i in range(3):
            v["max_probability"][i] = max(v["max_probability"][i], p[i])
            v["sum_probability"][i] += p[i]
        # Predicted-class returns are UNSIGNED underlying price movements.
        add_movement(
            v["predicted_class"][chosen],
            gross,
            max(0, (event["path_high"] / event["entry_price"] - 1) * 10000),
            max(0, (1 - event["path_low"] / event["entry_price"]) * 10000),
            gross / (max(event["sigma"], 1e-6) * math.sqrt(15) * 10000),
            chosen == y,
        )
        bucket = min(19, int(max(p[1], p[2]) * 20))
        add_movement(v["direction_bins"][bucket], *values, y == side)
        hist = sum(gross >= e for e in protocol["return_histogram_bps_edges"])
        v["return_histograms_by_class"][chosen][hist] += 1
        add_signal(v["forced_direction"], event, side)
        if view == "cal":
            for threshold, stat in s["thresholds"].items():
                if p[side] >= max(p[0], float(threshold)):
                    add_signal(stat, event, side)


def summary(s, protocol):
    costs = protocol["cost_scenarios_bps"]

    def signal_summary(x, n):
        result = {k: v for k, v in x.items() if k not in ("movement", "last", "last_nonoverlap")}
        result.update(describe(x["movement"], costs))
        result["frequency_per_1000_predictions"] = x["movement"]["n"] / n * 1000 if n else None
        result["base_cost_coverage_fraction"] = (
            x["wins_after_base_cost"] / x["movement"]["n"] if x["movement"]["n"] else None
        )
        return result

    n = s["metrics"]["cal"]["n"]
    result = {
        "metrics": {k: summarize(v) for k, v in s["metrics"].items()},
        "near_gap_count": s["near_gap_count"],
        "views": {},
        "thresholds": {t: signal_summary(x, n) for t, x in s["thresholds"].items()},
    }
    result["features"] = [
        {
            "n": f["n"],
            "mean": f["sum"] / f["n"],
            "standard_deviation": math.sqrt(
                max(0, f["square"] / f["n"] - (f["sum"] / f["n"]) ** 2)
            ),
            "clipped_fraction": f["clipped"] / f["n"],
        }
        if f["n"]
        else {"n": 0}
        for f in s["features"]
    ]
    for view, v in s["views"].items():
        result["views"][view] = {
            "max_probability": v["max_probability"],
            "mean_probability": [p / n if n else None for p in v["sum_probability"]],
            "predicted_class_returns": [describe(x, costs) for x in v["predicted_class"]],
            "forced_direction": signal_summary(v["forced_direction"], n),
            "direction_probability_bins": [
                dict(lower=i / 20, upper=(i + 1) / 20, **describe(x, costs))
                for i, x in enumerate(v["direction_bins"])
            ],
            "return_histograms_by_predicted_class": v["return_histograms_by_class"],
        }
    return result
