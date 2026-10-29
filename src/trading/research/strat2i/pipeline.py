"""Three bounded audit stages with atomic cursors; never fits or executes a model."""

import importlib.metadata
import json
import math
from datetime import UTC, datetime
from pathlib import Path

from trading.research.safety import ROOT
from trading.research.strat2d.journal import Journal, committed_files, file_hash
from trading.research.strat2h.resources import CONFIG, validate_config
from trading.research.strat2i import economics as eco
from trading.research.strat2i import quality as qa

PROTOCOL = ROOT / "config/strat2i-data-quality-economic-research-v1.json"


def minute(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()) // 60


def load_inputs(config, protocol, output, guard):
    guard()
    source, target = validate_config(config, output)
    if not Path(output).name.startswith("strat2d-strat2g-strat2h-strat2i-"):
        raise ValueError("Dedicated versioned STRAT-2I output required")
    if (
        file_hash(CONFIG) != protocol["resource_profile"]["sha256"]
        or protocol["scope"]["start"] != config["start"]
        or protocol["scope"]["end_exclusive"] != config["test_end"]
    ):
        raise ValueError("I resource or scope mismatch")
    for name, expected in protocol["unchanged_protocols"].items():
        if file_hash(ROOT / name) != expected:
            raise ValueError("Frozen research protocol changed")
    from trading.research.strat2d.pipeline import source_manifest

    info = source_manifest(source, config, guard)
    manifest = json.loads((source / "stream-manifest.json").read_text("utf-8"))
    expected_parts = protocol["raw_parts"]
    if expected_parts != ["cache/EURUSD/2022-01.parquet", "cache/EURUSD/2022-02.parquet"]:
        raise ValueError("Raw partition whitelist mismatch")
    if [p["path"] for p in manifest["parts"]] != expected_parts:
        raise ValueError("Prepared/raw partition identity mismatch")
    raw = ROOT / protocol["raw_source_junction"]
    if raw.resolve() != Path(manifest["source"]).resolve():
        raise ValueError("Phase 5 junction identity mismatch")
    raw_identity = {}
    for part in manifest["parts"]:
        guard()
        path = raw / part["path"]
        if file_hash(path) != part["sha256"]:
            raise ValueError("Read-only Phase 5 partition checksum mismatch")
        raw_identity[part["path"]] = {"sha256": part["sha256"], "mtime_ns": path.stat().st_mtime_ns}
    h = ROOT / protocol["frozen_H"]["path"]
    if (
        file_hash(h / "manifest.json") != protocol["frozen_H"]["manifest_sha256"]
        or file_hash(h / "00000059/receipt.json") != protocol["frozen_H"]["head_receipt_sha256"]
    ):
        raise ValueError("Frozen H journal identity mismatch")
    hj = Journal(h, json.loads((h / "manifest.json").read_text("utf-8")), {}, guard)
    hfiles = committed_files(h)
    if (
        hj.generation != 59
        or not hj.state["complete"]
        or hfiles != json.loads((ROOT / protocol["frozen_H"]["saved_hashes"]).read_text())
    ):
        raise ValueError("Frozen H checkpoint/file hashes mismatch")
    return source, target, info, raw, raw_identity, h, hfiles


def raw_batches(raw, parts, batch_rows, guard, part_start=0, offset=0):
    import pandas as pd
    import pyarrow.parquet as pq

    for number in range(part_start, len(parts)):
        reader = pq.ParquetFile(raw / parts[number])
        seen = 0
        try:
            for batch in reader.iter_batches(batch_size=batch_rows, use_threads=False):
                guard()
                frame = batch.to_pandas(use_threads=False)
                if not isinstance(frame.index, pd.DatetimeIndex):
                    frame = frame.set_index("opened_at")
                if (
                    frame.index.tz is None
                    or str(frame.index.tz) not in ("UTC", "Etc/UTC", "UTC+00:00")
                    or frame.index.hasnans
                    or any(v.value % 60_000_000_000 for v in frame.index)
                ):
                    raise ValueError("Source timestamp timezone/alignment/missing-value defect")
                skip = max(0, offset - seen) if number == part_start else 0
                seen += len(frame)
                if skip >= len(frame):
                    continue
                yield frame.iloc[skip:], number, seen
        finally:
            reader.close()


def candle_rows(frame):
    for stamp, *values in frame[["open", "high", "low", "close", "volume"]].itertuples(name=None):
        prices = [float(v) for v in values[:4]]
        v = float(values[4]) if values[4] is not None else float("nan")
        yield int(stamp.timestamp()) // 60, prices, v if math.isfinite(v) else None


def initial():
    return {
        "version": "strat2i-audit-checkpoint-v1",
        "stage": "raw",
        "part": 0,
        "offset": 0,
        "chunk": 0,
        "prepared_offset": 0,
        "prepared_rows": 0,
        "prepared_chain": None,
        "feature_age": 0,
        "prepared_previous": None,
        "feature_cross_gap_violations": 0,
        "feature_warmup_checked": 0,
        "quality": qa.initial(),
        "h_generation": 0,
        "event_sequence": 0,
        "groups": {},
        "daily": {},
        "complete": False,
    }


def keys(event):
    d = datetime.fromtimestamp(event["decision"] * 60, UTC)
    phase = event["phase"]
    band = (
        "00_07" if d.hour < 7 else "07_13" if d.hour < 13 else "13_17" if d.hour < 17 else "17_24"
    )
    p = event["probabilities"]["cal"]
    chosen = max(range(3), key=lambda k: p[k])
    volatility = sum(event["sigma"] * 10000 >= e for e in (0.5, 1, 2))
    return [
        "phase:" + phase,
        "month:" + d.strftime("%Y-%m"),
        "week:" + d.strftime("%G-W%V"),
        f"session:{phase}:{band}",
        f"volatility:{phase}:{volatility}",
        f"prediction:{phase}:{chosen}",
        f"confidence:{phase}:{int(max(p[1:]) * 20)}",
    ]


def economic_event(state, event, protocol):
    from trading.research.strat2f.metrics import validate_probability

    if event["sequence"] != state["event_sequence"] + 1:
        raise ValueError("Frozen prediction sequence gap/duplicate")
    state["event_sequence"] = event["sequence"]
    p = event["probabilities"]["cal"]
    validate_probability(p)
    if abs(event["gross_bps"] - (event["exit_price"] / event["entry_price"] - 1) * 10000) > 1e-12:
        raise ValueError("Price/bps conversion mismatch")
    group_keys = keys(event)
    side = 1 if p[1] >= p[2] else 2
    for threshold in protocol["thresholds"]:
        if p[side] >= max(p[0], threshold):
            group_keys.append(f"threshold:{event['phase']}:{threshold}")
    for key in group_keys:
        if key not in state["groups"]:
            if len(state["groups"]) >= protocol["budgets"]["groups"]:
                raise ValueError("Economic group budget exceeded")
            state["groups"][key] = eco.group(protocol)
        eco.observe(state["groups"][key], event, protocol)
    if event["phase"] in ("selection", "evaluation"):
        day = datetime.fromtimestamp(event["decision"] * 60, UTC).date().isoformat()
        key = event["phase"] + ":" + day
        if key not in state["daily"]:
            if len(state["daily"]) >= 64:
                raise ValueError("Economic day budget exceeded")
            state["daily"][key] = {}
        for threshold in ["forced", *protocol["thresholds"]]:
            if threshold != "forced" and p[side] < max(p[0], threshold):
                continue
            name = str(threshold)
            value = state["daily"][key].setdefault(name, {"n": 0, "net_bps": 0.0, "net_pips": 0.0})
            flow = eco.cashflow(
                event["entry_price"],
                event["exit_price"],
                1 if side == 1 else -1,
                protocol["costs"]["scenarios"][1],
            )
            value["n"] += 1
            value["net_bps"] += flow["net_bps"]
            value["net_pips"] += flow["net_pips"]


def run(config, output, guard, fault=None, protocol=None):
    protocol = json.loads(PROTOCOL.read_text("utf-8")) if protocol is None else protocol
    source, output, info, raw, raw_identity, h, hfiles = load_inputs(
        config, protocol, output, guard
    )
    import pyarrow as pa

    from trading.research.phase6.controller import controller_lock
    from trading.research.strat2d.calendar import Calendar
    from trading.research.strat2d.pipeline import input_batches

    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    calendar = Calendar(protocol["calendar"])
    manifest = {
        "version": "strat2i-readonly-audit-v1",
        "environment": {
            name: importlib.metadata.version(name) for name in ("numpy", "pandas", "pyarrow")
        },
        "protocol": protocol,
        "resource": config,
        "source": info,
        "raw_identity": raw_identity,
        "H_files": hfiles,
        "code": {p.name: file_hash(p) for p in Path(__file__).parent.glob("*.py")},
        "dependencies": {
            str(p.relative_to(ROOT)): file_hash(p)
            for folder in ("strat2d", "strat2g", "strat2h")
            for p in (ROOT / "src/trading/research" / folder).glob("*.py")
        },
    }
    left, right = minute(config["start"]), minute(config["test_end"])
    with controller_lock(output, lock_name=".strat2i.lock"):
        journal = Journal(output, manifest, initial(), guard)
        state = journal.state
        if state["complete"]:
            return state
        if state["stage"] == "raw":
            for frame, part, offset in raw_batches(
                raw,
                protocol["raw_parts"],
                config["batch_rows"],
                guard,
                state["part"],
                state["offset"],
            ):
                events = []
                for i, (m, prices, volume) in enumerate(candle_rows(frame)):
                    if i % 64 == 0:
                        guard()
                    qa.observe(
                        state["quality"],
                        m,
                        prices,
                        volume,
                        calendar,
                        events,
                        left,
                        right,
                        protocol["audit"]["jump_bps_warning"],
                        guard,
                    )
                state.update(part=part, offset=offset)
                journal.commit(state, events, fault)
            events = []
            qa.missing(
                state["quality"],
                state["quality"]["previous"][0] + 1,
                right,
                calendar,
                events,
                False,
                guard,
            )
            state.update(stage="prepared", offset=0)
            journal.commit(state, events, fault)
        if state["stage"] == "prepared":
            for (bars, features, _), chunk, offset in input_batches(
                source, info, state["chunk"], state["prepared_offset"], config["batch_rows"], guard
            ):
                for i, (m, prices, volume) in enumerate(candle_rows(bars)):
                    if i % 64 == 0:
                        guard()
                    allowed = calendar.classify(qa.stamp(m)) == "open"
                    if state["prepared_previous"] != m - 1 or not allowed:
                        state["feature_age"] = 0
                    if allowed:
                        state["feature_age"] += 1
                        if state["feature_age"] < 64:
                            state["feature_warmup_checked"] += 1
                            finite = all(
                                math.isfinite(float(features[k].iloc[i]))
                                for k in config["model"]["features"]
                            )
                            state["feature_cross_gap_violations"] += int(finite)
                    state["prepared_previous"] = m
                    state["prepared_rows"] += 1
                    state["prepared_chain"] = qa.row_digest(
                        state["prepared_chain"], m, prices, volume
                    )
                state.update(chunk=chunk, prepared_offset=offset)
                journal.commit(state, [], fault)
            if (
                state["prepared_chain"] != state["quality"]["chain"]
                or state["prepared_rows"] != state["quality"]["rows"]
                or state["feature_cross_gap_violations"]
            ):
                raise ValueError("Raw/prepared equality or causal feature warmup failed")
            state["stage"] = "economics"
            journal.commit(state, [], fault)
        if state["stage"] == "economics":
            for generation in range(state["h_generation"], protocol["frozen_H"]["generation"] + 1):
                guard()
                with (h / f"{generation:08d}/events.jsonl").open(encoding="utf-8") as handle:
                    for i, line in enumerate(handle):
                        if i % 64 == 0:
                            guard()
                        economic_event(state, json.loads(line), protocol)
                state["h_generation"] = generation + 1
                journal.commit(state, [], fault)
            state.update(stage="complete", complete=True)
            journal.commit(state, [], fault)
        for name, identity in raw_identity.items():
            guard()
            if (
                file_hash(raw / name) != identity["sha256"]
                or (raw / name).stat().st_mtime_ns != identity["mtime_ns"]
            ):
                raise ValueError("Read-only source changed during audit")
        return state
