"""Independent price/cost reconciliation and day-block uncertainty, never fitting."""

import json
import math
import random

from trading.research.safety import ROOT
from trading.research.strat2d.journal import Journal, logical_hash
from trading.research.strat2f.analysis import block_indices, interval
from trading.research.strat2h.analysis import events
from trading.research.strat2i import economics as eco


def analyze(state, protocol, guard):
    summaries = {}
    for name, group in sorted(state["groups"].items()):
        guard()
        n = group["stats"]["gross_bps"]["n"]
        summaries[name] = {
            "n": n,
            "stats": {k: eco.summarize(v) for k, v in group["stats"].items()},
            "long_short": group["directions"],
            "sign_hit_rate": group["sign_hits"] / n,
            "class_precision": group["label_hits"] / n,
            "clusters": group["clusters"],
            "nonoverlap": group["nonoverlap"],
            "pip_histogram": group["histogram_pips"],
            "target_counts": group["target_counts"],
            "costs": {
                cost: {
                    "net_bps": eco.summarize(s["net_bps"]),
                    "net_pips": eco.summarize(s["net_pips"]),
                    "cost_pips": eco.summarize(s["cost_pips"]),
                    "coverage_fraction": s["positive_net"] / n,
                }
                for cost, s in group["scenarios"].items()
            },
        }
    daily = [v for k, v in sorted(state["daily"].items()) if k.startswith("evaluation:")]
    uncertainty = {"days": len(daily), "blocks": {}}
    rule = protocol["uncertainty"]
    if len(daily) >= rule["minimum_observed_days"]:
        for length in rule["block_days"]:
            rng = random.Random(rule["seed"] + length)
            results = {str(t): [] for t in ["forced", *protocol["thresholds"]]}
            for repetition in range(rule["replicates"]):
                if repetition % 64 == 0:
                    guard()
                sample = [daily[i] for i in block_indices(len(daily), length, rng)]
                for key, values in results.items():
                    signal_days = sum(d.get(key, {}).get("n", 0) > 0 for d in daily)
                    n = sum(d.get(key, {}).get("n", 0) for d in sample)
                    if n and signal_days >= rule["minimum_signal_days"]:
                        values.append(sum(d.get(key, {}).get("net_bps", 0) for d in sample) / n)
            uncertainty["blocks"][str(length)] = {
                "approximate_nonoverlapping_day_blocks": len(daily) // length,
                "intervals": {
                    k: interval(v) if v else {"status": "INSUFFICIENT_DAYS_OR_SIGNALS"}
                    for k, v in results.items()
                },
            }
    return {
        "groups": summaries,
        "quality": state["quality"],
        "uncertainty": uncertainty,
        "classification": "INCONCLUSIVE",
        "reason": "Unverified calendar/price side/historical fees; zero 0.55 signals and no robust "
        "economic advantage. Development reuse is not confirmation.",
    }


def verify(output, state, protocol, config, guard):
    """Independent input zipper/grid arithmetic, H verifier and exact cash reconciliation."""
    import numpy as np

    from trading.research.strat2d.calendar import Calendar
    from trading.research.strat2d.pipeline import input_batches
    from trading.research.strat2h.analysis import verify as verify_h
    from trading.research.strat2i.pipeline import candle_rows, load_inputs, minute, raw_batches
    from trading.research.strat2i.quality import stamp

    source, _, info, raw, _, h, _ = load_inputs(config, protocol, output, guard)
    cal = Calendar(protocol["calendar"])
    h_journal = Journal(h, json.loads((h / "manifest.json").read_text()), {}, guard)
    h_protocol = json.loads((ROOT / "config/strat2h-diagnostics-protocol-v1.json").read_text())
    h_result = verify_h(h, h_journal.state, h_protocol, config, guard)
    expected = iter(
        r
        for (bars, _, _), _, _ in input_batches(source, info, 0, 0, 1024, guard)
        for r in candle_rows(bars)
    )
    previous, rows, gaps, internal, outside = None, 0, 0, 0, 0
    missing = {"internal": {}, "edge": {}}
    right = minute(config["test_end"])
    cursor = minute(config["start"])
    for frame, _, _ in raw_batches(raw, protocol["raw_parts"], 1024, guard):
        prices = frame[["open", "high", "low", "close"]].to_numpy()
        if (
            not np.isfinite(prices).all()
            or (prices <= 0).any()
            or (prices[:, 1] < prices.max(axis=1)).any()
            or (prices[:, 2] > prices.min(axis=1)).any()
        ):
            raise ValueError("Independent OHLC integrity failure")
        for row in candle_rows(frame):
            if rows % 64 == 0:
                guard()
            m, _, _ = row
            if row != next(expected, None) or m < cursor:
                raise ValueError("Raw/prepared row identity/order failure")
            kind = "edge" if previous is None else "internal"
            if previous is not None and m > cursor:
                gaps += 1
                internal += m - cursor
            for omitted in range(cursor, m):
                category = cal.classify(stamp(omitted))
                counts = missing[kind]
                counts[category] = counts.get(category, 0) + 1
            outside += int(cal.classify(stamp(m)) != "open")
            rows += 1
            previous, cursor = m, m + 1
    if next(expected, None) is not None:
        raise ValueError("Prepared source has extra rows")
    for omitted in range(cursor, right):
        counts = missing["edge"]
        category = cal.classify(stamp(omitted))
        counts[category] = counts.get(category, 0) + 1
    q = state["quality"]
    if (
        (rows, gaps, internal, outside)
        != (q["rows"], q["internal_gaps"], q["internal_minutes"], q["outside"])
        or missing["internal"] != q["internal_missing"]
        or missing["edge"] != q["edge_missing"]
    ):
        raise ValueError("Independent minute-grid reconciliation mismatch")
    n, pip_sum, net_sum, coverage, sequence = 0, 0.0, 0.0, 0, 0
    targets = {t["id"]: [0, 0, 0] for t in protocol["targets"]}
    for e in events(h):
        if sequence % 64 == 0:
            guard()
        sequence += 1
        if e["phase"] != "evaluation":
            continue
        side = 1 if e["probabilities"]["cal"][1] >= e["probabilities"]["cal"][2] else -1
        entry, exit_price = e["entry_price"], e["exit_price"]
        # Independent scalar reference fill/fee formula, not eco.cashflow.
        a = entry * (1 + side * 0.000075)
        b = exit_price * (1 - side * 0.000075)
        net = side * (b - a) - (a + b) * 0.00003
        n += 1
        pip_sum += side * (exit_price - entry) * 10000
        net_sum += net / entry * 10000
        coverage += int(net > 0)
        for definition in protocol["targets"]:
            z = max(
                definition["cost_bps"],
                definition["volatility_multiple"] * max(e["sigma"], 1e-6) * math.sqrt(15) * 10000,
            )
            label = 1 if e["gross_bps"] > z else 2 if e["gross_bps"] < -z else 0
            targets[definition["id"]][label] += 1
    observed = state["groups"]["phase:evaluation"]
    if (
        n != observed["stats"]["gross_pips"]["n"]
        or not math.isclose(pip_sum, observed["stats"]["gross_pips"]["sum"], abs_tol=1e-8)
        or not math.isclose(net_sum, observed["scenarios"]["2.1"]["net_bps"]["sum"], abs_tol=1e-8)
        or coverage != observed["scenarios"]["2.1"]["positive_net"]
        or targets != observed["target_counts"]
        or sequence != state["event_sequence"]
    ):
        raise ValueError("Independent pip/cash/target accounting mismatch")
    return {
        "status": "PASS",
        "raw_prepared_rows_matched": rows,
        "H_numeric_checks": h_result,
        "evaluation_cashflows_recomputed": n,
        "target_counts": targets,
        "quality_grid_reconciled": True,
        "state_hash": logical_hash(state),
    }
