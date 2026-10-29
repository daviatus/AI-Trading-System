"""Offline STRAT-2F reconstruction, crash/resume, replay and verification."""

import argparse
import json
import os
import time

from tools import strat2d_historical_130_validation as harness
from trading.research.safety import ROOT, ResearchSafetyStop
from trading.research.strat2d.journal import Journal, committed_files, logical_hash, write_new
from trading.research.strat2d.pipeline import Monitor, validate_config

CONFIG = harness.CONFIG
VERSION = 1
AUDIT = ROOT / "data/research/strat2d-strat2f-diagnostics-evidence-v1"
PRIMARY = "data/research/strat2d-strat2f-diagnostics-resumed-v1"
REFERENCE = "data/research/strat2d-strat2f-diagnostics-reference-v1"
PRIOR = ROOT / "data/research/strat2d-eurusd-2022-offline-130-reference-v1"


def child(args):
    config = json.loads(CONFIG.read_text())
    ready = AUDIT / (args.child + "-pid.json")
    staging = AUDIT / (args.child + "-pid.pending")
    write_new(staging, {"pid": os.getpid()})
    os.rename(staging, ready)
    monitor = Monitor(config)
    started = time.perf_counter()
    try:
        monitor()
        from trading.research.strat2f.pipeline import run

        def fault(stage, generation):
            if stage == args.fault_stage and generation == args.fault_generation:
                monitor()
                write_new(
                    AUDIT / (args.child + "-crash.json"),
                    {"generation": generation, "stage": stage, "metrics": vars(monitor)},
                )
                os._exit(86)

        state = run(config, args.output, monitor, fault)
        monitor()
        write_new(
            AUDIT / (args.child + "-result.json"),
            {
                "complete": state["complete"],
                "logical_state_hash": logical_hash(state),
                "metrics": vars(monitor),
                "elapsed_seconds": time.perf_counter() - started,
            },
        )
        return 0
    except Exception as error:
        from trading.research.phase6.runner import _failure_record

        write_new(
            AUDIT / (args.child + "-failure.json"),
            {
                "failure": _failure_record(error, {"stage": "strat2f_child"}),
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
        raise ValueError("Exclusive output paths exist; preserve evidence")
    AUDIT.mkdir(parents=True)
    harness.AUDIT = AUDIT
    result = {"status": "FAIL", "config": config}
    monitor = Monitor(config)
    try:
        monitor()
        originals = committed_files(PRIOR)
        epath = ROOT / "data/research/strat2d-strat2e-execution-calendar-reference-v2"
        e_original = committed_files(epath)
        harness.preserved_source(source)
        for role, target, generation in (
            ("diagnostics-crash", PRIMARY, 35),
            ("diagnostics-resume", PRIMARY, -1),
            ("diagnostics-repeat", PRIMARY, -1),
            ("diagnostics-reference", REFERENCE, -1),
        ):
            harness.launch(
                role,
                target,
                config,
                generation,
                module="tools.strat2f_validation",
                extra_args=("--evidence-version", str(VERSION)),
            )
            if role == "diagnostics-crash":
                journal, prefix = inspect(PRIMARY, config)
                if journal.generation != 34:
                    raise ValueError("Unexpected crash commit position")
                write_new(AUDIT / "prefix-hashes.json", prefix)
            elif role == "diagnostics-resume":
                journal, hashes = inspect(PRIMARY, config)
                if not journal.state["complete"] or any(hashes[k] != v for k, v in prefix.items()):
                    raise ValueError("Resume prefix or completion failure")
            elif role == "diagnostics-repeat":
                if hashes != inspect(PRIMARY, config)[1]:
                    raise ValueError("Repeated resume mutated outputs")
        reference, reference_hashes = inspect(REFERENCE, config)
        if hashes != reference_hashes or logical_hash(journal.state) != logical_hash(
            reference.state
        ):
            raise ValueError("Independent replay mismatch")
        from trading.research.strat2f.verify import verify_events

        verified = verify_events(ROOT / PRIMARY, journal.state, monitor)
        write_new(AUDIT / "independent-verification.json", verified)
        from trading.research.strat2f.analysis import report_data

        monitor()
        analysis = report_data(journal.state, config["model"]["seed"])
        monitor()
        write_new(AUDIT / "analysis.json", analysis)
        write_new(AUDIT / "resumed-hashes.json", hashes)
        write_new(AUDIT / "reference-hashes.json", reference_hashes)
        if (
            originals != committed_files(PRIOR)
            or e_original != committed_files(epath)
            or harness.preserved_source(source) != 47
        ):
            raise ValueError("Existing evidence modified")
        result.update(
            status="PASS",
            classification=analysis["classification"],
            generation=journal.generation,
            matching_files=len(hashes),
            logical_state_hash=logical_hash(journal.state),
            preserved_2c=47,
            preserved_2d=len(originals),
            preserved_2e=len(e_original),
            counts=journal.state["counts"],
            state_path=str(
                (ROOT / PRIMARY / f"{journal.generation:08d}/state.json").relative_to(ROOT)
            ),
        )
    except Exception as error:
        from trading.research.phase6.runner import _failure_record

        result["failure"] = _failure_record(error, {"stage": "strat2f_parent"})
    finally:
        result["metrics"] = vars(monitor)
        write_new(AUDIT / "summary.json", result)
        xml = ROOT / ".tmp/strat2f-tests-v1.xml"
        if xml.exists():
            with (AUDIT / "tests.xml").open("xb") as handle:
                handle.write(xml.read_bytes())
        for name in ("strat2f-focused-tests-v2.xml", "strat2f-focused-tests-v3.xml"):
            path = ROOT / ".tmp" / name
            if path.exists():
                with (AUDIT / name).open("xb") as handle:
                    handle.write(path.read_bytes())
        from trading.research.strat2f.report import render

        render(result, AUDIT)
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
        parser.error("Version must be 1..99")
    VERSION = args.evidence_version
    AUDIT = ROOT / f"data/research/strat2d-strat2f-diagnostics-evidence-v{VERSION}"
    PRIMARY = f"data/research/strat2d-strat2f-diagnostics-resumed-v{VERSION}"
    REFERENCE = f"data/research/strat2d-strat2f-diagnostics-reference-v{VERSION}"
    raise SystemExit(child(args) if args.child else parent())
