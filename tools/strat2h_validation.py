"""Bounded STRAT-2H frozen diagnostics, hard crash, resume and independent replay."""

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
from trading.research.strat2g.resources import CONFIG
from trading.research.strat2h.resources import Monitor, validate_config

RESOURCE_PROFILE = "120"
VERSION = 1
AUDIT = ROOT / "data/research/strat2d-strat2g-strat2h-evidence-v1"
PRIMARY = "data/research/strat2d-strat2g-strat2h-resumed-v1"
REFERENCE = "data/research/strat2d-strat2g-strat2h-reference-v1"
PROTOCOL = ROOT / "config/strat2h-diagnostics-protocol-v1.json"


def inspect(output, config):
    path = ROOT / output
    return Journal(
        path, json.loads((path / "manifest.json").read_text("utf-8")), {}, Monitor(config)
    ), committed_files(path)


def child(args):
    config = json.loads(CONFIG.read_text("utf-8"))
    pending = AUDIT / (args.child + "-pid.pending")
    write_new(pending, {"pid": os.getpid()})
    os.rename(pending, AUDIT / (args.child + "-pid.json"))
    guard = Monitor(config)
    started = time.perf_counter()
    try:
        guard()
        from trading.research.strat2h.pipeline import run

        if args.child == "verification":
            from trading.research.strat2h.analysis import analyze, verify

            journal, _ = inspect(args.output, config)
            protocol = json.loads(PROTOCOL.read_text("utf-8"))
            verification = verify(ROOT / args.output, journal.state, protocol, config, guard)
            write_new(AUDIT / "independent-verification.json", verification)
            write_new(AUDIT / "analysis.json", analyze(journal.state, protocol, guard))
            state = journal.state
        else:

            def fault(stage, generation):
                if stage == args.fault_stage and generation == args.fault_generation:
                    guard()
                    write_new(
                        AUDIT / (args.child + "-crash.json"),
                        {"generation": generation, "stage": stage, "metrics": vars(guard)},
                    )
                    os._exit(86)

            state = run(config, args.output, guard, fault)
        guard()
        write_new(
            AUDIT / (args.child + "-result.json"),
            {
                "complete": state["complete"],
                "logical_state_hash": logical_hash(state),
                "elapsed_seconds": time.perf_counter() - started,
                "metrics": vars(guard),
            },
        )
        return 0
    except Exception as error:
        from trading.research.phase6.runner import _failure_record

        write_new(
            AUDIT / (args.child + "-failure.json"),
            {"failure": _failure_record(error, {"stage": "strat2h_child"}), "metrics": vars(guard)},
        )
        return 2 if isinstance(error, ResearchSafetyStop) else 1


def parent():
    config = json.loads(CONFIG.read_text("utf-8"))
    source, _ = validate_config(config, PRIMARY)
    if any(p.exists() for p in (AUDIT, ROOT / PRIMARY, ROOT / REFERENCE)):
        raise ValueError("Versioned evidence exists; select a fresh evidence version")
    AUDIT.mkdir(parents=True)
    harness.AUDIT = AUDIT
    result = {
        "status": "FAIL",
        "checks": {},
        "primary": PRIMARY,
        "reference": REFERENCE,
        "resource_config": str(CONFIG.relative_to(ROOT)),
        "resource_sha256": file_hash(CONFIG),
        "protocol_sha256": file_hash(PROTOCOL),
        "evidence": str(AUDIT.relative_to(ROOT)),
    }
    write_new(AUDIT / "resource-config.json", config)
    for name in ("diagnostics-protocol", "confirmation-protocol", "calendar-review"):
        write_new(
            AUDIT / (name + ".json"),
            json.loads((ROOT / f"config/strat2h-{name}-v1.json").read_text("utf-8")),
        )
    guard = Monitor(config)
    protected = {}
    try:
        guard()
        result["admission"] = dict(vars(guard))
        result["checks"]["resource_admission"] = "PASS"
        # Prior journals and reports are protected by complete before/after maps.
        for name in (
            "strat2d-eurusd-2022-offline-130-reference-v1",
            "strat2d-strat2e-execution-calendar-reference-v2",
            "strat2d-strat2f-diagnostics-reference-v2",
            "strat2d-strat2g-resumed-v2",
            "strat2d-strat2g-reference-v2",
        ):
            protected[name] = committed_files(ROOT / "data/research" / name)
        old_reports = {
            str(p.relative_to(ROOT)): file_hash(p)
            for letter in ("c", "d", "e", "f", "g")
            for p in (ROOT / "reports").glob(f"strat-2{letter}-*.md")
        }
        original_config = ROOT / "config/phase6a-research-v1.json"
        original_hash = file_hash(original_config)
        harness.preserved_source(source)
        write_new(
            AUDIT / "protected-before.json",
            {
                "journals": protected,
                "reports": old_reports,
                "original_config_sha256": original_hash,
            },
        )
        for role, target_path, generation in (
            ("diagnostics-crash", PRIMARY, 35),
            ("diagnostics-resume", PRIMARY, -1),
            ("diagnostics-repeat", PRIMARY, -1),
            ("diagnostics-reference", REFERENCE, -1),
        ):
            harness.launch(
                role,
                target_path,
                config,
                generation,
                module="tools.strat2h_validation",
                extra_args=(
                    "--evidence-version",
                    str(VERSION),
                    "--resource-profile",
                    RESOURCE_PROFILE,
                ),
                monitor_factory=Monitor,
            )
            journal, files = inspect(target_path, config)
            if role == "diagnostics-crash":
                if journal.generation != 34 or not any((ROOT / PRIMARY).glob(".pending-*")):
                    raise ValueError("Hard-crash position/staging mismatch")
                prefix = files
                write_new(AUDIT / "prefix-hashes.json", prefix)
            elif role == "diagnostics-resume":
                if not journal.state["complete"] or any(files[k] != v for k, v in prefix.items()):
                    raise ValueError("Resume changed committed prefix")
                hashes, state_hash = files, logical_hash(journal.state)
            elif role == "diagnostics-repeat":
                if files != hashes or logical_hash(journal.state) != state_hash:
                    raise ValueError("Repeated resume changed evidence")
            elif files != hashes or logical_hash(journal.state) != state_hash:
                raise ValueError("Independent replay mismatch")
        write_new(AUDIT / "resumed-hashes.json", hashes)
        write_new(AUDIT / "reference-hashes.json", files)
        harness.launch(
            "verification",
            PRIMARY,
            config,
            module="tools.strat2h_validation",
            extra_args=("--evidence-version", str(VERSION), "--resource-profile", RESOURCE_PROFILE),
            monitor_factory=Monitor,
        )
        result.update(
            generation=journal.generation,
            rows=journal.state["rows"],
            events=journal.state["sequence"],
            counts=journal.state["counts"],
            buffers={
                "max_pending": journal.state["max_pending"],
                "max_history": journal.state["max_history"],
            },
            matching_files=len(files),
            logical_state_hash=state_hash,
        )
        result["checks"].update(
            frozen_inference="PASS",
            bounded_paths="PASS",
            hard_crash_resume="PASS",
            immutable_prefix="PASS",
            repeated_resume="PASS",
            replay="PASS",
            independent_verification="PASS",
        )
        result["status"] = "PASS"
    except Exception as error:
        from trading.research.phase6.runner import _failure_record

        result["failure"] = _failure_record(error, {"stage": "strat2h_parent"})
        result["status"] = "BLOCKED" if isinstance(error, ResearchSafetyStop) else "FAIL"
    finally:
        try:
            if protected and (
                any(
                    committed_files(ROOT / "data/research" / name) != files
                    for name, files in protected.items()
                )
                or any(file_hash(ROOT / p) != value for p, value in old_reports.items())
                or file_hash(original_config) != original_hash
            ):
                raise ValueError("Prior published evidence changed")
            if protected:
                result["preserved_strat2c_files"] = harness.preserved_source(source)
                result["checks"]["prior_evidence_preserved"] = "PASS"
        except Exception:
            result["status"] = "FAIL"
            result["checks"]["prior_evidence_preserved"] = "FAIL"
        result["metrics"] = vars(guard)
        write_new(AUDIT / "summary.json", result)
        from trading.research.strat2h.report import render

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
    parser.add_argument("--resource-profile", choices=("120", "100"), default="120")
    args = parser.parse_args()
    RESOURCE_PROFILE = args.resource_profile
    if RESOURCE_PROFILE == "100":
        from trading.research.strat2h.resources import CONFIG as H_CONFIG

        CONFIG = H_CONFIG
    if not 1 <= args.evidence_version <= 99:
        parser.error("Version must be 1..99")
    VERSION = args.evidence_version
    AUDIT = ROOT / f"data/research/strat2d-strat2g-strat2h-evidence-v{VERSION}"
    PRIMARY = f"data/research/strat2d-strat2g-strat2h-resumed-v{VERSION}"
    REFERENCE = f"data/research/strat2d-strat2g-strat2h-reference-v{VERSION}"
    raise SystemExit(child(args) if args.child else parent())
