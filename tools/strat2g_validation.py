"""Autonomous offline STRAT-2G frozen-protocol matrix, recovery and replay."""

import argparse
import json
import os
import time

from tools import strat2d_historical_130_validation as harness
from trading.research.safety import ROOT, ResearchSafetyStop
from trading.research.strat2d.journal import (
    Journal,
    committed_files,
    file_hash,
    logical_hash,
    write_new,
)
from trading.research.strat2g.resources import Monitor, validate_config

CONFIG = harness.CONFIG
VERSION = 1
RESOURCE_PROFILE = "130"
AUDIT = ROOT / "data/research/strat2d-strat2g-evidence-v1"
PRIMARY = "data/research/strat2d-strat2g-resumed-v1"
REFERENCE = "data/research/strat2d-strat2g-reference-v1"


def child(args):
    c = json.loads(CONFIG.read_text())
    pending = AUDIT / (args.child + "-pid.pending")
    write_new(pending, {"pid": os.getpid()})
    os.rename(pending, AUDIT / (args.child + "-pid.json"))
    monitor = Monitor(c)
    started = time.perf_counter()
    try:
        monitor()
        from trading.research.strat2g.pipeline import run

        def fault(stage, generation):
            if stage == args.fault_stage and generation == args.fault_generation:
                monitor()
                write_new(
                    AUDIT / (args.child + "-crash.json"),
                    {"generation": generation, "stage": stage, "metrics": vars(monitor)},
                )
                os._exit(86)

        state = run(c, args.output, monitor, fault)
        monitor()
        write_new(
            AUDIT / (args.child + "-result.json"),
            {
                "complete": state["complete"],
                "logical_state_hash": logical_hash(state),
                "elapsed_seconds": time.perf_counter() - started,
                "metrics": vars(monitor),
            },
        )
        return 0
    except Exception as error:
        from trading.research.phase6.runner import _failure_record

        write_new(
            AUDIT / (args.child + "-failure.json"),
            {
                "failure": _failure_record(error, {"stage": "strat2g_child"}),
                "metrics": vars(monitor),
            },
        )
        return 2 if isinstance(error, ResearchSafetyStop) else 1


def inspect(output, config):
    p = ROOT / output
    return Journal(
        p, json.loads((p / "manifest.json").read_text()), {}, Monitor(config)
    ), committed_files(p)


def parent():
    c = json.loads(CONFIG.read_text())
    source, _ = validate_config(c, PRIMARY)
    if any(p.exists() for p in (AUDIT, ROOT / PRIMARY, ROOT / REFERENCE)):
        raise ValueError("Versioned evidence exists; never reset or overwrite")
    AUDIT.mkdir(parents=True)
    harness.AUDIT = AUDIT
    result = {"status": "FAIL", "config": c, "resource_config_sha256": file_hash(CONFIG)}
    write_new(AUDIT / "resource-config.json", c)
    monitor = Monitor(c)
    protocol = json.loads((ROOT / "config/strat2g-protocol-v1.json").read_text())
    write_new(AUDIT / "protocol.json", protocol)
    result["protocol_sha256"] = file_hash(ROOT / "config/strat2g-protocol-v1.json")
    try:
        monitor()
        protected = [
            "strat2d-eurusd-2022-offline-130-reference-v1",
            "strat2d-strat2e-execution-calendar-reference-v2",
            "strat2d-strat2f-diagnostics-reference-v2",
        ]
        before = {name: committed_files(ROOT / "data/research" / name) for name in protected}
        harness.preserved_source(source)
        for role, target, generation in (
            ("matrix-crash", PRIMARY, 35),
            ("matrix-resume", PRIMARY, -1),
            ("matrix-repeat", PRIMARY, -1),
            ("matrix-reference", REFERENCE, -1),
        ):
            harness.launch(
                role,
                target,
                c,
                generation,
                module="tools.strat2g_validation",
                extra_args=(
                    "--evidence-version",
                    str(VERSION),
                    "--resource-profile",
                    RESOURCE_PROFILE,
                ),
                monitor_factory=Monitor,
            )
            if role == "matrix-crash":
                j, prefix = inspect(PRIMARY, c)
                if j.generation != 34:
                    raise ValueError("Crash commit position mismatch")
                write_new(AUDIT / "prefix-hashes.json", prefix)
            elif role == "matrix-resume":
                j, hashes = inspect(PRIMARY, c)
                if not j.state["complete"] or any(hashes[k] != v for k, v in prefix.items()):
                    raise ValueError("Resume prefix mismatch")
            elif role == "matrix-repeat":
                if hashes != inspect(PRIMARY, c)[1]:
                    raise ValueError("Repeated resume mutation")
        r, reference_hashes = inspect(REFERENCE, c)
        if hashes != reference_hashes or logical_hash(j.state) != logical_hash(r.state):
            raise ValueError("Deterministic replay failure")
        from trading.research.strat2g.analysis import analyze, verify_events

        verification = verify_events(ROOT / PRIMARY, j.state, protocol, monitor)
        write_new(AUDIT / "independent-verification.json", verification)
        monitor()
        analysis = analyze(j.state, protocol)
        monitor()
        write_new(AUDIT / "analysis.json", analysis)
        write_new(AUDIT / "resumed-hashes.json", hashes)
        write_new(AUDIT / "reference-hashes.json", reference_hashes)
        if (
            any(
                before[name] != committed_files(ROOT / "data/research" / name) for name in protected
            )
            or harness.preserved_source(source) != 47
        ):
            raise ValueError("Published evidence mutated")
        result.update(
            status="PASS",
            classification=analysis["classification"],
            matching_files=len(hashes),
            generation=j.generation,
            logical_state_hash=logical_hash(j.state),
            state_path=str((ROOT / PRIMARY / f"{j.generation:08d}/state.json").relative_to(ROOT)),
            preserved={name: len(v) for name, v in before.items()},
            preserved_strat2c=47,
        )
    except Exception as error:
        from trading.research.phase6.runner import _failure_record

        result["failure"] = _failure_record(error, {"stage": "strat2g_parent"})
    finally:
        result["metrics"] = vars(monitor)
        write_new(AUDIT / "summary.json", result)
        from trading.research.strat2g.report import render

        render(result, AUDIT, protocol)
    print(json.dumps({"status": result["status"], "evidence": str(AUDIT)}), flush=True)
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--child")
    p.add_argument("--output")
    p.add_argument("--fault-generation", type=int, default=-1)
    p.add_argument("--fault-stage", default="before_commit")
    p.add_argument("--evidence-version", type=int, default=1)
    p.add_argument("--resource-profile", choices=("130", "120"), default="130")
    args = p.parse_args()
    if not 1 <= args.evidence_version <= 99:
        p.error("Version must be 1..99")
    VERSION = args.evidence_version
    RESOURCE_PROFILE = args.resource_profile
    if RESOURCE_PROFILE == "120":
        from trading.research.strat2g.resources import CONFIG as OPT_IN_CONFIG

        CONFIG = OPT_IN_CONFIG
    AUDIT = ROOT / f"data/research/strat2d-strat2g-evidence-v{VERSION}"
    PRIMARY = f"data/research/strat2d-strat2g-resumed-v{VERSION}"
    REFERENCE = f"data/research/strat2d-strat2g-reference-v{VERSION}"
    raise SystemExit(child(args) if args.child else parent())
