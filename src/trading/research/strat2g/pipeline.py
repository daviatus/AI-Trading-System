"""Bounded chronological experiment matrix with common delayed label maturity."""

import json
from pathlib import Path

from trading.research.safety import ROOT
from trading.research.strat2d.journal import Journal, file_hash, logical_hash
from trading.research.strat2f.metrics import baselines, metric, threshold_stat, update
from trading.research.strat2g.core import (
    Candidate,
    Features,
    net_bps,
    phase_of,
    safe_observation,
    select,
    target,
)
from trading.research.strat2g.resources import validate_config

PROTOCOL = ROOT / "config/strat2g-protocol-v1.json"


def bump(s, name, amount=1):
    s["counts"][name] = s["counts"].get(name, 0) + amount


def initial(protocol, base):
    return {
        "schema": "strat2g-checkpoint-v1",
        "chunk": 0,
        "offset": 0,
        "complete": False,
        "rows": 0,
        "last_minute": None,
        "features": Features().state,
        "pending": [],
        "counts": {},
        "models": {
            t["id"]: {
                m["id"]: Candidate(m["id"], t["id"], protocol, base["model"]).snapshot()
                for m in protocol["models"]
            }
            for t in protocol["targets"]
        },
        "metrics": {
            p: {t["id"]: {} for t in protocol["targets"]} for p in ("selection", "evaluation")
        },
        "sessions": {},
        "signals": {},
        "daily": {},
        "selection_days": [],
        "choices": None,
        "event_seq": 0,
        "max_history": 0,
        "max_pending": 0,
        "frozen_model_hash": None,
    }


def model_objects(s, protocol, config):
    return {
        t["id"]: {
            m["id"]: Candidate(
                m["id"], t["id"], protocol, config["model"], s["models"][t["id"]][m["id"]]
            )
            for m in protocol["models"]
        }
        for t in protocol["targets"]
    }


def score(container, name, label, p, full=True):
    if name not in container:
        container[name] = metric(full)
    update(container[name], label, p)


def signal(s, phase, target_id, name, label, probabilities, gross, minute, horizon):
    from trading.research.strat2f.metrics import threshold_update

    if phase != "evaluation":
        return
    entry = s["signals"].setdefault(target_id, {})
    if name not in entry:
        entry[name] = threshold_stat()
    side = 1 if probabilities[1] >= probabilities[2] else 2
    if probabilities[side] >= max(probabilities[0], 0.55):
        threshold_update(entry[name], side, label, net_bps(gross, side), minute, horizon)


def resolve(s, rows, protocol, config, models, events):
    row = rows[0]
    if row["new"] is None or row["reference"] is None:
        bump(s, "common_path_predictor_warmup")
        return
    decision, common_end = row["minute"] + 1, rows[30]["minute"] + 1
    phase = phase_of(decision, common_end, protocol)
    bump(s, phase)
    if phase in ("outside", "purged"):
        return
    import pandas as pd

    stamp = pd.Timestamp(decision * 60, unit="s", tz="UTC")
    day = stamp.date().isoformat()
    if phase == "selection" and day not in s["selection_days"]:
        s["selection_days"].append(day)
    if phase == "evaluation":
        if s["choices"] is None:
            s["choices"] = {
                t["id"]: select(s["metrics"]["selection"][t["id"]], s["selection_days"], protocol)
                for t in protocol["targets"]
            }
        if day not in s["daily"]:
            if len(s["daily"]) >= protocol["budgets"]["daily_records"]:
                raise ValueError("Daily metadata budget exceeded")
            s["daily"][day] = {t["id"]: {} for t in protocol["targets"]}
    if phase in ("selection", "evaluation"):
        if s["frozen_model_hash"] is None:
            s["frozen_model_hash"] = logical_hash(s["models"])
    for definition in protocol["targets"]:
        target_id = definition["id"]
        y, gross, neutral = target(rows, definition)
        family_models = models[target_id]
        horizon = definition["horizon"]
        end = pd.Timestamp((row["minute"] + horizon + 1) * 60, unit="s", tz="UTC").isoformat()
        if phase in ("train", "calibration"):
            right = protocol["splits"][phase][1].replace("Z", "+00:00")
            for candidate in family_models.values():
                vector = candidate.vector(row)
                if phase == "train":
                    candidate.model.fit_one(vector, y, stamp.isoformat(), end, right)
                else:
                    candidate.model.calibrate_one(vector, y, stamp.isoformat(), end, right)
                s["models"][target_id][candidate.family] = candidate.snapshot()
            continue
        probabilities = {}
        for name, candidate in family_models.items():
            vector = candidate.vector(row)
            raw = candidate.model.softmax(candidate.model.vector(vector) @ candidate.model.weights)
            cal = candidate.model.predict(vector)
            probabilities[name + "_raw"] = raw.tolist()
            probabilities[name + "_cal"] = cal.tolist()
            signal(s, phase, target_id, name, y, cal, gross, decision, horizon)
        reference = family_models["reference8"].model
        probabilities.update(baselines(reference.state["class_counts"], row["reference"][1]))
        for name, p in probabilities.items():
            score(s["metrics"][phase][target_id], name, y, p)
            if phase == "evaluation" and (name.endswith("_cal") or name == "prior"):
                score(s["daily"][day][target_id], name, y, p, False)
                band = (
                    "utc_00_07"
                    if stamp.hour < 7
                    else "utc_07_13"
                    if stamp.hour < 13
                    else "utc_13_17"
                    if stamp.hour < 17
                    else "utc_17_24"
                )
                score(
                    s["sessions"].setdefault(target_id, {}).setdefault(band, {}), name, y, p, False
                )
        s["event_seq"] += 1
        events.append(
            {
                "sequence": s["event_seq"],
                "phase": phase,
                "target": target_id,
                "decision": decision,
                "label_end": row["minute"] + horizon + 1,
                "common_end": common_end,
                "matured_at": rows[-1]["minute"] + 1,
                "label": y,
                "gross_bps": gross,
                "neutral_bps": neutral,
                "entry_price": rows[1]["bar"][0],
                "exit_price": rows[horizon]["bar"][3],
                "sigma_at_decision": row["sigma"],
                "probabilities": probabilities,
            }
        )


def run(config, output, guard, fault=None, protocol=None):
    protocol = json.loads(PROTOCOL.read_text()) if protocol is None else protocol
    source, target_path = validate_config(config, output)
    if (
        protocol["scope"]["start"] != config["start"]
        or protocol["scope"]["end_exclusive"] != config["test_end"]
        or len(protocol["targets"]) * len(protocol["models"]) != 9
    ):
        raise ValueError("Protocol scope or matrix budget mismatch")
    if protocol["signals"]["threshold"] != 0.55 or config["portfolio"]["threshold"] != 0.55:
        raise ValueError("Production confidence gate must remain unchanged")
    costs = (
        config["portfolio"]["spread_bps"]
        + 2 * config["portfolio"]["slippage_bps"]
        + config["portfolio"]["commission_bps_roundtrip"]
    )
    if (
        abs(costs - 2.1) > 1e-12
        or protocol["costs"]["total_bps"] != 2.1
        or any(t["neutral_cost_bps"] != 2.1 for t in protocol["targets"])
    ):
        raise ValueError("Economic protocol/cost scenario mismatch")
    guard()
    import numpy as np
    import pandas as pd
    import pyarrow as pa

    from trading.research.phase6.controller import controller_lock
    from trading.research.strat2d.calendar import Calendar
    from trading.research.strat2d.pipeline import input_batches, source_manifest

    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    info = source_manifest(source, config, guard)
    manifest = {
        "version": "strat2g-experiment-journal-v1",
        "protocol": protocol,
        "resource_config": config,
        "source": info,
        "environment": {"numpy": np.__version__, "pandas": pd.__version__, "arrow": pa.__version__},
        "code": {p.name: file_hash(p) for p in Path(__file__).parent.glob("*.py")},
        "dependencies": {
            str(p.relative_to(ROOT)): file_hash(p)
            for folder in ("strat2d", "strat2f")
            for p in (ROOT / "src/trading/research" / folder).glob("*.py")
        },
    }
    calendar = Calendar(config["calendar"])
    with controller_lock(target_path, lock_name=".strat2g.lock"):
        journal = Journal(target_path, manifest, initial(protocol, config), guard)
        s = journal.state
        if s["complete"]:
            return s
        engine = Features(s["features"])
        models = model_objects(s, protocol, config)
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
                    raise ValueError("Nonchronological experiment input")
                gap = s["last_minute"] is not None and minute != s["last_minute"] + 1
                s["last_minute"], s["rows"] = minute, s["rows"] + 1
                allowed = safe_observation(opened, calendar)
                if gap or not allowed:
                    bump(s, "unobservable_pending_rows", len(s["pending"]))
                    s["pending"].clear()
                    engine.clear()
                if not allowed:
                    bump(s, "session_buffer_excluded")
                    continue
                bar = [float(bars[k].iloc[i]) for k in ("open", "high", "low", "close")]
                computed = engine.push(minute, bar)
                reference = originals[i].tolist() if np.isfinite(originals[i]).all() else None
                row = {
                    "minute": minute,
                    "bar": bar,
                    "reference": reference,
                    "new": computed[0] if computed else None,
                    "sigma": computed[1] if computed else 0.0,
                }
                s["pending"].append(row)
                s["max_history"] = max(s["max_history"], len(engine.state["history"]))
                s["max_pending"] = max(s["max_pending"], len(s["pending"]))
                if len(s["pending"]) == 31:
                    resolve(s, s["pending"], protocol, config, models, events)
                    s["pending"].pop(0)
                if len(s["pending"]) > 31 or len(engine.state["history"]) > 61:
                    raise ValueError("Bounded research buffer exceeded")
            s.update(chunk=chunk, offset=offset)
            if (
                s["frozen_model_hash"] is not None
                and logical_hash(s["models"]) != s["frozen_model_hash"]
            ):
                raise ValueError("Model changed after calibration")
            journal.commit(s, events, fault)
        s["tail_unavailable"] = len(s["pending"])
        s["complete"] = True
        if s["choices"] is None:
            s["choices"] = {t["id"]: None for t in protocol["targets"]}
        if logical_hash(s["models"]) != s["frozen_model_hash"]:
            raise ValueError("Frozen model checkpoint mismatch")
        journal.commit(s, [], fault)
        return s
