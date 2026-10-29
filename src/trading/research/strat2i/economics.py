"""Price-unit cost scenarios and bounded exploratory sufficient statistics."""

import math


def target(gross_bps, sigma, definition):
    if not all(math.isfinite(v) for v in (gross_bps, sigma)) or sigma < 0:
        raise ValueError("Invalid target movement or causal volatility")
    zone = max(
        definition["cost_bps"],
        definition["volatility_multiple"]
        * max(sigma, 1e-6)
        * math.sqrt(definition["horizon_minutes"])
        * 10000,
    )
    return 1 if gross_bps > zone else 2 if gross_bps < -zone else 0


def cashflow(entry, exit_price, side, scenario, quantity=1.0):
    """USD-account reference-mid scenario, EUR base units, no external FX rates."""
    if side not in (1, -1) or not all(
        math.isfinite(v) and v > 0 for v in (entry, exit_price, quantity)
    ):
        raise ValueError("Invalid economic price/quantity/side")
    values = [
        scenario[k] for k in ("spread_bps", "slippage_per_side_bps", "commission_roundtrip_bps")
    ]
    if any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("Invalid cost parameters")
    spread, slip, commission = values
    delta = (spread / 2 + slip) / 10000
    fill_entry = entry * (1 + side * delta)
    fill_exit = exit_price * (1 - side * delta)
    fees = quantity * (fill_entry + fill_exit) * commission / 20000
    gross = side * quantity * (exit_price - entry)
    net = side * quantity * (fill_exit - fill_entry) - fees
    cost = gross - net
    return {
        "gross_usd": gross,
        "net_usd": net,
        "cost_usd": cost,
        "net_bps": net / (quantity * entry) * 10000,
        "gross_pips": gross / quantity / 0.0001,
        "net_pips": net / quantity / 0.0001,
        "cost_pips": cost / quantity / 0.0001,
    }


def stat():
    return {"n": 0, "sum": 0.0, "square": 0.0, "minimum": None, "maximum": None}


def add(s, x):
    if not math.isfinite(x):
        raise ValueError("Nonfinite economic statistic")
    s["n"] += 1
    s["sum"] += x
    s["square"] += x * x
    s["minimum"] = x if s["minimum"] is None else min(x, s["minimum"])
    s["maximum"] = x if s["maximum"] is None else max(x, s["maximum"])


def summarize(s):
    n = s["n"]
    return {
        "n": n,
        "mean": s["sum"] / n if n else None,
        "minimum": s["minimum"],
        "maximum": s["maximum"],
        "std": math.sqrt(max(0, s["square"] / n - (s["sum"] / n) ** 2)) if n else None,
    }


def group(protocol):
    names = ("gross_bps", "gross_pips", "gross_price", "favorable_pips", "adverse_pips", "scaled")
    return {
        "stats": {k: stat() for k in names},
        "directions": [0, 0],
        "sign_hits": 0,
        "label_hits": 0,
        "scenarios": {
            str(v["total_bps"]): {
                "net_bps": stat(),
                "net_pips": stat(),
                "cost_pips": stat(),
                "positive_net": 0,
            }
            for v in protocol["costs"]["scenarios"]
        },
        "histogram_pips": [0] * (len(protocol["pip_histogram_edges"]) + 1),
        "last": None,
        "last_thinned": None,
        "clusters": 0,
        "nonoverlap": 0,
        "target_counts": {t["id"]: [0, 0, 0] for t in protocol["targets"]},
    }


def observe(s, event, protocol):
    p = event["probabilities"]["cal"]
    side = 1 if p[1] >= p[2] else -1
    label = 1 if side == 1 else 2
    entry, exit_price = event["entry_price"], event["exit_price"]
    signed = side * (exit_price - entry)
    high, low = event["path_high"] - entry, event["path_low"] - entry
    favorable, adverse = (max(0, high), max(0, -low)) if side == 1 else (max(0, -low), max(0, high))
    values = {
        "gross_bps": side * event["gross_bps"],
        "gross_pips": signed / 0.0001,
        "gross_price": signed,
        "favorable_pips": favorable / 0.0001,
        "adverse_pips": adverse / 0.0001,
        "scaled": side * event["gross_bps"] / (max(event["sigma"], 1e-6) * math.sqrt(15) * 10000),
    }
    for name, value in values.items():
        add(s["stats"][name], value)
    s["directions"][side == -1] += 1
    s["sign_hits"] += int(signed > 0)
    s["label_hits"] += int(event["label"] == label)
    for scenario in protocol["costs"]["scenarios"]:
        result = cashflow(entry, exit_price, side, scenario)
        values = s["scenarios"][str(scenario["total_bps"])]
        for name in ("net_bps", "net_pips", "cost_pips"):
            add(values[name], result[name])
        values["positive_net"] += int(result["net_usd"] > 0)
    bucket = sum(signed / 0.0001 >= b for b in protocol["pip_histogram_edges"])
    s["histogram_pips"][bucket] += 1
    minute = event["decision"]
    s["clusters"] += int(s["last"] is None or minute > s["last"] + 15)
    if s["last_thinned"] is None or minute >= s["last_thinned"] + 15:
        s["nonoverlap"] += 1
        s["last_thinned"] = minute
    s["last"] = minute
    for definition in protocol["targets"]:
        s["target_counts"][definition["id"]][
            target(event["gross_bps"], event["sigma"], definition)
        ] += 1
