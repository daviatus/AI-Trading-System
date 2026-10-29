"""Autonomous bounded STRAT-2E frozen strategy execution and calendar audit."""

import argparse
import json
import os

from tools import strat2d_historical_130_validation as harness
from trading.research.safety import ROOT, ResearchSafetyStop
from trading.research.strat2d.journal import Journal, committed_files, logical_hash, write_new
from trading.research.strat2d.pipeline import Monitor, validate_config

CONFIG = harness.CONFIG
AUDIT = ROOT / "data/research/strat2d-strat2e-execution-calendar-evidence-v1"
PRIMARY = "data/research/strat2d-strat2e-execution-calendar-resumed-v1"
REFERENCE = "data/research/strat2d-strat2e-execution-calendar-reference-v1"
PRIOR = ROOT / "data/research/strat2d-eurusd-2022-offline-130-reference-v1"
VERSION = 1


def child(args):
    config = json.loads(CONFIG.read_text())
    ready = AUDIT / (args.child + "-pid.json")
    pending = AUDIT / (args.child + "-pid.pending")
    write_new(pending, {"pid": os.getpid()})
    os.rename(pending, ready)
    monitor = Monitor(config)
    try:
        monitor()
        from trading.research.strat2e.audit import replay

        def fault(stage, generation):
            if stage == args.fault_stage and generation == args.fault_generation:
                monitor()
                write_new(
                    AUDIT / (args.child + "-crash.json"),
                    {"stage": stage, "generation": generation, "metrics": vars(monitor)},
                )
                os._exit(86)

        state = replay(config, args.output, PRIOR, monitor, fault)
        monitor()
        write_new(
            AUDIT / (args.child + "-result.json"),
            {"state": state, "metrics": vars(monitor), "logical_state_hash": logical_hash(state)},
        )
        return 0
    except Exception as error:
        from trading.research.phase6.runner import _failure_record

        write_new(
            AUDIT / (args.child + "-failure.json"),
            {
                "failure": _failure_record(error, {"stage": "strat2e_validation"}),
                "metrics": vars(monitor),
            },
        )
        return 2 if isinstance(error, ResearchSafetyStop) else 1


def inspect(output, config):
    path = ROOT / output
    manifest = json.loads((path / "manifest.json").read_text())
    return Journal(path, manifest, {}, Monitor(config)), committed_files(path)


def parent():
    config = json.loads(CONFIG.read_text())
    source, _ = validate_config(config, PRIMARY)
    if any(p.exists() for p in (AUDIT, ROOT / PRIMARY, ROOT / REFERENCE)):
        raise ValueError("Exclusive evidence paths already exist; preserve them")
    AUDIT.mkdir(parents=True)
    harness.AUDIT = AUDIT
    result = {
        "status": "FAIL",
        "config": config,
        "scope": (
            "Frozen inference/portfolio replay; fitting results verified "
            "from sealed STRAT-2D foundation, not refitted"
        ),
    }
    monitor = None
    try:
        monitor = Monitor(config)
        monitor()
        result["initial_resources"] = vars(monitor)
        before = committed_files(PRIOR)
        harness.preserved_source(source)
        for role, output, generation in (
            ("funnel-crash", PRIMARY, 35),
            ("funnel-resume", PRIMARY, -1),
            ("funnel-repeat", PRIMARY, -1),
            ("funnel-reference", REFERENCE, -1),
        ):
            harness.launch(
                role,
                output,
                config,
                generation,
                module="tools.strat2e_validation",
                extra_args=("--evidence-version", str(VERSION)),
            )
            if role == "funnel-crash":
                journal, prefix = inspect(PRIMARY, config)
                assert journal.generation == 34
                write_new(AUDIT / "interrupted-prefix-hashes.json", prefix)
            elif role == "funnel-resume":
                journal, complete = inspect(PRIMARY, config)
                assert journal.state["complete"]
                assert all(complete[k] == v for k, v in prefix.items())
            elif role == "funnel-repeat":
                assert inspect(PRIMARY, config)[1] == complete
        reference, hashes = inspect(REFERENCE, config)
        assert hashes == complete
        assert logical_hash(reference.state) == logical_hash(journal.state)
        assert before == committed_files(PRIOR)
        assert harness.preserved_source(source) == 47
        result.update(
            status="PASS",
            state=journal.state,
            generation=journal.generation,
            matching_files=len(hashes),
            logical_state_hash=logical_hash(journal.state),
            preserved_2c=47,
            preserved_2d_files=len(before),
        )
        write_new(AUDIT / "reference-hashes.json", hashes)
        write_new(AUDIT / "resumed-hashes.json", complete)
    except Exception as error:
        from trading.research.phase6.runner import _failure_record

        result["failure"] = _failure_record(error, {"stage": "strat2e_historical_parent"})
    finally:
        if monitor is not None:
            result["parent_resource_metrics"] = vars(monitor)
        write_new(AUDIT / "summary.json", result)
    print(json.dumps({"status": result["status"], "evidence": str(AUDIT)}), flush=True)
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child")
    parser.add_argument("--output")
    parser.add_argument("--fault-generation", type=int, default=-1)
    parser.add_argument("--fault-stage", default="before_commit")
    parser.add_argument("--evidence-version", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.evidence_version <= 99:
        parser.error("Evidence version must be 1..99")
    VERSION = args.evidence_version
    AUDIT = ROOT / f"data/research/strat2d-strat2e-execution-calendar-evidence-v{VERSION}"
    PRIMARY = f"data/research/strat2d-strat2e-execution-calendar-resumed-v{VERSION}"
    REFERENCE = f"data/research/strat2d-strat2e-execution-calendar-reference-v{VERSION}"
    raise SystemExit(child(args) if args.child else parent())
