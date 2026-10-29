"""Read-only frozen inference with delayed, censored paths and atomic diagnostics."""

import json
from datetime import UTC, datetime
from pathlib import Path

from trading.research.safety import ROOT
from trading.research.strat2d.journal import Journal, file_hash, logical_hash
from trading.research.strat2g.core import Candidate, Features, phase_of, safe_observation, target
from trading.research.strat2h.resources import validate_config
from trading.research.strat2h.statistics import add_group, add_signal, group, signal

PROTOCOL = ROOT / "config/strat2h-diagnostics-protocol-v1.json"


def check_candidate(saved, identity):
    if (
        identity["family"] != "linear12"
        or identity["target"] != "h15_cost"
        or identity["horizon_minutes"] != 15
        or saved["spec"] != identity["spec"]
        or saved["schema"] != identity["model_schema"]
        or saved["state"]["train_rows"] != identity["training_rows"]
        or saved["state"]["calibration_rows"] != identity["calibration_rows"]
        or saved["state"]["log_temperature"] != identity["log_temperature"]
        or logical_hash(saved) != identity["candidate_sha256"]
    ):
        raise ValueError("Frozen candidate definition/state mismatch")


def frozen(protocol, config, guard):
    identity = protocol["frozen"]
    path = ROOT / identity["journal"]
    generation = identity["generation"]
    for name, key in (
        ("manifest.json", "manifest_sha256"),
        (f"{generation:08d}/receipt.json", "receipt_sha256"),
        (f"{generation:08d}/state.json", "state_sha256"),
    ):
        if file_hash(path / name) != identity[key]:
            raise ValueError("Frozen candidate artifact identity mismatch")
    manifest = json.loads((path / "manifest.json").read_text("utf-8"))
    journal = Journal(path, manifest, {}, guard)
    s = journal.state
    if (
        journal.generation != generation
        or not s["complete"]
        or s["choices"]["h15_cost"] != "linear12"
    ):
        raise ValueError("Frozen selection or completion mismatch")
    gprotocol = manifest["protocol"]
    if file_hash(ROOT / "config/strat2g-protocol-v1.json") != identity["protocol_sha256"]:
        raise ValueError("G frozen protocol mismatch")
    if any(
        protocol[k] != gprotocol[k]
        for k in ("scope", "splits", "embargo_minutes", "common_label_maturity_minutes")
    ):
        raise ValueError("Frozen split/horizon mismatch")
    if protocol["production_threshold"] != 0.55 or config["portfolio"]["threshold"] != 0.55:
        raise ValueError("Confidence gate mismatch")
    for name, expected in manifest["code"].items():
        if file_hash(ROOT / "src/trading/research/strat2g" / name) != expected:
            raise ValueError("Frozen G implementation changed")
    for name, expected in manifest["dependencies"].items():
        if file_hash(ROOT / name) != expected:
            raise ValueError("Frozen G dependency changed")
    models = {}
    for family, key in (("linear12", "candidate_sha256"), ("reference8", "reference_sha256")):
        saved = s["models"]["h15_cost"][family]
        if family == "linear12":
            check_candidate(saved, identity)
        if logical_hash(saved) != identity[key]:
            raise ValueError("Frozen model identity mismatch")
        models[family] = Candidate(family, "h15_cost", gprotocol, config["model"], saved)
    if protocol["cost_components"] != gprotocol["costs"]:
        raise ValueError("Frozen cost assumptions changed")
    return models, gprotocol, manifest["source"]


def initial():
    return {
        "schema": "strat2h-diagnostics-checkpoint-v1",
        "chunk": 0,
        "offset": 0,
        "rows": 0,
        "complete": False,
        "last_minute": None,
        "run_length": 0,
        "features": Features().state,
        "pending": [],
        "counts": {},
        "groups": {},
        "daily": {},
        "sequence": 0,
        "max_history": 0,
        "max_pending": 0,
    }


def bump(s, key, n=1):
    s["counts"][key] = s["counts"].get(key, 0) + n


def resolve(s, rows, protocol, gprotocol, models, events):
    import numpy as np

    row = rows[0]
    if row["new"] is None or row["reference"] is None:
        bump(s, "common_path_predictor_warmup")
        return
    decision, common_end = row["minute"] + 1, rows[30]["minute"] + 1
    phase = phase_of(decision, common_end, gprotocol)
    bump(s, phase)
    if phase in ("outside", "purged"):
        return
    definition = next(t for t in gprotocol["targets"] if t["id"] == "h15_cost")
    y, gross, neutral = target(rows, definition)
    candidate, reference = models["linear12"], models["reference8"]
    x = candidate.model.vector(row["new"])
    raw = candidate.model.softmax(x @ candidate.model.weights).tolist()
    calibrated = candidate.model.predict(row["new"]).tolist()
    counts = candidate.model.state["class_counts"]
    priors = [n / sum(counts) for n in counts]
    s["sequence"] += 1
    event = {
        "sequence": s["sequence"],
        "phase": phase,
        "decision": decision,
        "label_end": row["minute"] + 16,
        "common_end": common_end,
        "matured_at": rows[-1]["minute"] + 1,
        "label": y,
        "gross_bps": gross,
        "neutral_bps": neutral,
        "entry_price": rows[1]["bar"][0],
        "exit_price": rows[15]["bar"][3],
        "path_high": max(r["bar"][1] for r in rows[1:16]),
        "path_low": min(r["bar"][2] for r in rows[1:16]),
        "features": row["new"],
        "sigma": row["sigma"],
        "history_since_reset": row["history_since_reset"],
        "probabilities": {
            "raw": raw,
            "cal": calibrated,
            "reference": reference.model.predict(row["reference"]).tolist(),
            "prior": priors,
        },
    }
    if not np.isfinite([gross, neutral, event["path_high"], event["path_low"]]).all():
        raise ValueError("Nonfinite economic path")
    stamp = datetime.fromtimestamp(decision * 60, UTC)
    day, week = stamp.date().isoformat(), stamp.strftime("%G-W%V")
    session = (
        "00_07"
        if stamp.hour < 7
        else "07_13"
        if stamp.hour < 13
        else "13_17"
        if stamp.hour < 17
        else "17_24"
    )
    regime = sum(row["sigma"] * 10000 >= e for e in protocol["volatility_sigma20_bps_edges"])
    keys = [
        f"phase:{phase}",
        f"month:{day[:7]}",
        f"week:{week}",
        f"session:{phase}:{session}",
        f"volatility:{phase}:{regime}",
        f"gap_age:{phase}:{'under120' if row['history_since_reset'] < 120 else '120plus'}",
    ]
    for key in keys:
        if key not in s["groups"]:
            if len(s["groups"]) >= protocol["budgets"]["groups"]:
                raise ValueError("Group metadata budget exceeded")
            s["groups"][key] = group(protocol)
        add_group(s["groups"][key], event, protocol)
    if phase in ("selection", "evaluation"):
        from trading.research.strat2f.metrics import metric, update

        key = phase + ":" + day
        if key not in s["daily"]:
            if len(s["daily"]) >= protocol["budgets"]["days"]:
                raise ValueError("Daily metadata budget exceeded")
            s["daily"][key] = {
                "metrics": {v: metric(False) for v in ("cal", "reference", "prior")},
                "thresholds": {str(t): signal() for t in protocol["thresholds"]},
                "forced": signal(),
            }
        d = s["daily"][key]
        for view in d["metrics"]:
            update(d["metrics"][view], y, event["probabilities"][view])
        side = 1 if calibrated[1] >= calibrated[2] else 2
        add_signal(d["forced"], event, side)
        for threshold, stat in d["thresholds"].items():
            if calibrated[side] >= max(calibrated[0], float(threshold)):
                add_signal(stat, event, side)
    events.append(event)


def run(config, output, guard, fault=None, protocol=None):
    protocol = json.loads(PROTOCOL.read_text("utf-8")) if protocol is None else protocol
    source, output = validate_config(config, output)
    guard()
    models, gprotocol, expected_source = frozen(protocol, config, guard)
    import numpy as np
    import pandas as pd
    import pyarrow as pa

    from trading.research.phase6.controller import controller_lock
    from trading.research.strat2d.calendar import Calendar
    from trading.research.strat2d.pipeline import input_batches, source_manifest

    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    info = source_manifest(source, config, guard)
    if info != expected_source:
        raise ValueError("Prepared source differs from frozen G source")
    manifest = {
        "version": "strat2h-frozen-diagnostics-journal-v1",
        "protocol": protocol,
        "resource_config": config,
        "source": info,
        "code": {p.name: file_hash(p) for p in Path(__file__).parent.glob("*.py")},
        "dependencies": {
            str(p.relative_to(ROOT)): file_hash(p)
            for folder in ("strat2d", "strat2f", "strat2g")
            for p in (ROOT / "src/trading/research" / folder).glob("*.py")
        },
        "environment": {"numpy": np.__version__, "pandas": pd.__version__, "arrow": pa.__version__},
    }
    before = {k: logical_hash(v.snapshot()) for k, v in models.items()}
    with controller_lock(output, lock_name=".strat2h.lock"):
        journal = Journal(output, manifest, initial(), guard)
        s = journal.state
        if s["complete"]:
            return s
        engine, calendar = Features(s["features"]), Calendar(config["calendar"])
        for (bars, features, _), chunk, offset in input_batches(
            source,
            info,
            s["chunk"],
            s["offset"],
            config["batch_rows"],
            guard,
            feature_columns=config["model"]["features"],
        ):
            events = []
            originals = features[config["model"]["features"]].to_numpy(copy=False)
            for i, opened in enumerate(bars.index):
                if i % 64 == 0:
                    guard()
                minute = int(opened.timestamp()) // 60
                if s["last_minute"] is not None and minute <= s["last_minute"]:
                    raise ValueError("Nonchronological H input")
                gap = s["last_minute"] is not None and minute != s["last_minute"] + 1
                s["last_minute"], s["rows"] = minute, s["rows"] + 1
                allowed = safe_observation(opened, calendar)
                if gap or not allowed:
                    bump(s, "unobservable_pending_rows", len(s["pending"]))
                    s["pending"].clear()
                    engine.clear()
                    s["run_length"] = 0
                if not allowed:
                    bump(s, "session_buffer_excluded")
                    continue
                s["run_length"] += 1
                computed = engine.push(
                    minute, [float(bars[k].iloc[i]) for k in ("open", "high", "low", "close")]
                )
                s["pending"].append(
                    {
                        "minute": minute,
                        "bar": [float(bars[k].iloc[i]) for k in ("open", "high", "low", "close")],
                        "reference": originals[i].tolist()
                        if np.isfinite(originals[i]).all()
                        else None,
                        "new": computed[0] if computed else None,
                        "sigma": computed[1] if computed else 0,
                        "history_since_reset": s["run_length"],
                    }
                )
                s["max_history"] = max(s["max_history"], len(engine.state["history"]))
                s["max_pending"] = max(s["max_pending"], len(s["pending"]))
                if len(s["pending"]) == 31:
                    resolve(s, s["pending"], protocol, gprotocol, models, events)
                    s["pending"].pop(0)
                if len(s["pending"]) > 31 or len(engine.state["history"]) > 61:
                    raise ValueError("H causal buffer exceeded")
            if len(events) > protocol["budgets"]["event_batch"]:
                raise ValueError("H event batch exceeded")
            s.update(chunk=chunk, offset=offset)
            journal.commit(s, events, fault)
        if {k: logical_hash(v.snapshot()) for k, v in models.items()} != before:
            raise ValueError("Frozen model mutated during diagnostics")
        s.update(complete=True, tail_unavailable=len(s["pending"]), model_hashes=before)
        journal.commit(s, [], fault)
        return s
