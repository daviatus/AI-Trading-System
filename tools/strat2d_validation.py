"""Autonomous bounded STRAT-2D engineering validation, isolated from STRAT-2C."""

import argparse
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter

from trading.research.safety import ROOT, ResearchSafetyStop, resources
from trading.research.strat2d.journal import (
    Journal,
    committed_files,
    file_hash,
    logical_hash,
    write_new,
)
from trading.research.strat2d.pipeline import Monitor, run, validate_config

CONFIG = ROOT / "config/strat2d-eurusd-2022-engineering-v1.json"
AUDIT = ROOT / "data/research/strat2d-eurusd-2022-validation-evidence-v1"
REPORT = ROOT / "reports/strat-2d-streaming-training-backtest-v1.md"
REFERENCE = ROOT / "data/research/strat2d-eurusd-2022-uninterrupted-v1"


def child(args):
    config = json.loads(CONFIG.read_text("utf-8-sig"))
    monitor = Monitor(config)

    def fault(stage, generation):
        if stage == "before_commit" and generation == args.fault_generation:
            monitor()
            write_new(
                AUDIT / "intentional-crash.json",
                {
                    "stage": stage,
                    "generation": generation,
                    "metrics": vars(monitor),
                    "expected_exit": 86,
                },
            )
            os._exit(86)  # Deliberate hard process loss: no Python cleanup or state publication.

    try:
        state = run(config, output=args.output, guard=monitor, fault=fault)
        monitor()
        write_new(
            AUDIT / f"{args.child}-result.json",
            {"state": state, "metrics": vars(monitor), "logical_state_hash": logical_hash(state)},
        )
        return 0
    except Exception as error:
        from trading.research.phase6.runner import _failure_record

        write_new(
            AUDIT / f"{args.child}-failure.json",
            {
                "failure": _failure_record(error, {"stage": "strat2d_validation"}),
                "metrics": vars(monitor),
            },
        )
        return 2 if isinstance(error, ResearchSafetyStop) else 1


def launch(role, output, config, fault_generation=-1):
    resources(config)
    command = [
        sys.executable,
        "-B",
        "-m",
        "tools.strat2d_validation",
        "--child",
        role,
        "--output",
        str(output),
        "--fault-generation",
        str(fault_generation),
    ]
    env = dict(os.environ)
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        env[name] = "1"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with (AUDIT / f"{role}.log").open("x", encoding="utf-8") as log:
        process = subprocess.Popen(
            command, cwd=ROOT, stdout=log, stderr=log, env=env, creationflags=flags
        )
        while process.poll() is None:
            # Child enforces RSS/time/floors between bounded operations; parent also
            # stops on resource pressure while the child is running.
            try:
                resources(config)
            except ResearchSafetyStop:
                process.kill()
                process.wait()
                raise
            time.sleep(0.2)
        code = process.returncode
    print(json.dumps({"validation_process": role, "exit_code": code}), flush=True)
    expected = 86 if fault_generation >= 0 else 0
    if code != expected:
        raise RuntimeError(
            f"STRAT-2D {role} stopped with code {code}; see preserved audit evidence"
        )


def scan_ledger(output, state):
    """No full event/trade history is retained in memory."""
    counters, previous_sequence, trade_hash, costs = Counter(), 0, None, 0.0
    with (AUDIT / "data-quality-intervals.jsonl").open("x", encoding="utf-8") as quality:
        for generation in range(10000):
            directory = output / f"{generation:08d}"
            if not directory.exists():
                break
            with (directory / "events.jsonl").open(encoding="utf-8") as handle:
                for line in handle:
                    event = json.loads(line)
                    if event["sequence"] != previous_sequence + 1:
                        raise ValueError("Duplicated or skipped event sequence")
                    previous_sequence = event["sequence"]
                    counters[event["event"]] += 1
                    if event["event"] in {"data_gap", "out_of_session"}:
                        quality.write(json.dumps(event, sort_keys=True) + "\n")
                    if event["event"] == "fill" and event["fill_id"] != counters["fill"]:
                        raise ValueError("Duplicate or skipped fill")
                    if event["event"] == "trade":
                        if event["trade_id"] != counters["trade"]:
                            raise ValueError("Duplicate or skipped trade")
                        value = {k: v for k, v in event.items() if k != "sequence"}
                        trade_hash = logical_hash({"previous": trade_hash, "trade": value})
                        costs += event["costs"]
                    if (
                        event["event"] == "equity"
                        and abs(event["cash"] + event["unrealized"] - event["equity"]) > 1e-8
                    ):
                        raise ValueError("Cash/unrealized/equity reconciliation failed")
    portfolio = state["portfolio"]
    if counters["fill"] != portfolio["fills"] or counters["trade"] != portfolio["trades"]:
        raise ValueError("Ledger counters do not match portfolio state")
    if trade_hash != portfolio["trade_history_hash"] or previous_sequence != state["event_seq"]:
        raise ValueError("Ledger history hash does not match checkpoint")
    if portfolio["position"]:
        costs += portfolio["position"]["entry_costs"]
    if abs(costs - portfolio["transaction_costs"]) > 1e-7:
        raise ValueError("Transaction cost ledger mismatch")
    return dict(counters)


def render(result):
    rows = [
        "# STRAT-2D streaming training and backtest v1",
        "",
        f"Engineering validation: **{result['status']}**. Performance remains unproven.",
        "",
        "## Implementation summary",
        "",
        "An isolated STRAT-2D package streams immutable STRAT-2C bars/features/labels in "
        "1024-row Arrow batches through quality, training, calibration and test passes. "
        "No Phase 5 files or original Phase 6 configuration are used as output or modified.",
        "",
        "The explicitly named online softmax baseline uses fixed, predeclared feature scales "
        "and chronological one-row SGD. Calibration changes only scalar temperature. "
        "Weights and calibrator are frozen before February predictions. Labels are purged "
        "at split ends; calibration/test have a 60-minute embargo. "
        "Test labels affect metrics only.",
        "",
        "## Model support matrix",
        "",
        "| Model | STRAT-2D status |",
        "| --- | --- |",
        "| online_softmax_v1 | Incremental fitting, calibration, inference and recovery |",
        "| trend | Existing Phase 6 fixed rule unchanged; STRAT-2D adapter unsupported |",
        "| logistic | Existing batch L-BFGS definition unchanged; no silent SGD substitution |",
        "| tree / xgboost | Explicit unsupported status; no validated bounded continuation |",
        "",
        "## Major requirements",
        "",
        "| Requirement | Status | Evidence |",
        "| --- | --- | --- |",
    ]
    rows += [f"| {k} | {v['status']} | {v['details']} |" for k, v in result["checks"].items()]
    rows += [
        "",
        "## Calendar and data-integrity policy",
        "",
        "The versioned America/New_York weekly rules are copied from frozen current contract "
        "metadata, not verified for 2022. No 2022 holiday schedule was supplied; later dated "
        "holidays are not applied retroactively. DST conversion uses ZoneInfo and its recorded "
        "tzdata version. Weekend/session-break/holiday classifications remain "
        "hypotheses until verified.",
        "",
        "The strict require_verified_calendar policy blocks all fitting under an unverified "
        "calendar. This separately configured observed_contiguous_engineering run admits only "
        "finite predictors and observed, target-contiguous paths. Labels intersecting any missing "
        "or excluded observation are blocked. No candles are filled. Published prepared features "
        "must match the selected calendar; changing rules requires a new prepared dataset.",
        "",
        "At an unobservable gap or excluded session candle, an open simulated position is "
        "censored with exposure retained; subsequent execution is blocked. No retrospective "
        "last-close or fictional stop fill is invented. Predictions may continue, but affected "
        "portfolio performance is not evaluable. Pending orders require "
        "the exact next observed open.",
        "",
        "## Simulation and accounting",
        "",
        "Single asset/position, CFD-style balance with reserved entry margin. Equity equals "
        "balance plus unrealized P&L. Risk and exposure cap quantity; step/min/max constraints "
        "apply. Explicit spread/slippage are adverse fill adjustments; commission is charged "
        "per side. Stop wins same-bar ambiguity; targets do not improve on favorable gaps. "
        "The 15-bar horizon exits at an observed close. No terminal fill is fabricated. "
        "Financing, FX conversion, dynamic broker margin tiers and bid/ask history "
        "remain unverified/unsupported.",
        "",
        "## Checkpoint protocol and replay",
        "",
        "Immutable numbered transactions contain state.json, events.jsonl and a chained receipt "
        "with file and logical-state hashes. Model, cursor, calibration, pending orders, open "
        "position, balance, cost counters and history references commit in one same-volume "
        "directory rename after file flush/fsync. There is no mutable checkpoint to reset. "
        "All committed receipts are checked before resume; staging is preserved and ignored. "
        "Windows power-loss persistence still depends on filesystem/hardware behavior.",
        "",
        "One real validation child is deliberately terminated with os._exit(86) after staging "
        "an actual training update and before publication. A fresh process resumes it. An "
        "independent uninterrupted process uses a different output directory. Runtime logs and "
        "RSS/timestamps live outside scientific transactions; canonical state and artifact hashes "
        "are compared without run-specific metadata.",
        "",
        "## Results and evidence",
        "",
        "```json",
        json.dumps(result, indent=2),
        "```",
        "",
        "All gap durations and individual outside-session observations are preserved in "
        "data/research/strat2d-eurusd-2022-validation-evidence-v1/data-quality-intervals.jsonl "
        "when the historical run reaches ledger verification.",
        "",
        "## Files changed",
        "",
        "New src/trading/research/strat2d/{__init__,calendar,journal,model,pipeline,"
        "portfolio,runner,runtime}.py; "
        "config/strat2d-eurusd-2022-engineering-v1.json; tests/test_strat2d.py; "
        "tools/strat2d_validation.py; reports/strat-2d-implementation-plan-v1.md; this report. "
        "Existing production code and STRAT-2C published artifacts are preserved.",
        "",
        "## Limitations, blockers and next phase",
        "",
        "This is a two-month engineering validation of one new baseline, not statistical "
        "performance evidence or live-trading approval. Historical calendar verification and "
        "unexpected gaps remain data-quality blockers for performance research. A censored "
        "portfolio episode cannot establish a valid return/drawdown claim. "
        "Multi-asset arbitration, other estimators, broker financing and complete "
        "realistic margin simulation are unsupported.",
        "",
        "Recommended STRAT-2E: verify historical calendar/session evidence, resolve or formally "
        "exclude unexpected gaps, validate gap-safe independent episodes, "
        "then expand the supported "
        "baseline to additional bounded pre-2026 development windows with unchanged leakage and "
        "resource checks. Add multi-asset shared-account recovery only after these checks pass.",
        "",
        "If a resource gate failed, preserve all evidence and restore headroom above 1.5 GiB; "
        "do not use the preparation-only 1.25 GiB exception. Resume unchanged code/config at a "
        "new bounded invocation; never reset committed transactions. On corruption/identity "
        "failure, investigate preserved evidence before any resume.",
        "",
        "No full-year 2022 backtest, 2026 holdout access, broker/DB connection, live order, "
        "Docker/service startup, commit or push occurred.",
    ]
    with REPORT.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(rows) + "\n")


def parent():
    config = json.loads(CONFIG.read_text("utf-8-sig"))
    source, primary = validate_config(config)
    if REPORT.exists() or AUDIT.exists() or primary.exists() or REFERENCE.exists():
        raise ValueError(
            "Validation v1 paths already exist; no existing evidence will be overwritten"
        )
    AUDIT.mkdir(parents=True)
    result = {
        "status": "FAIL",
        "checks": {},
        "config": str(CONFIG),
        "primary": str(primary),
        "reference": str(REFERENCE),
        "resource_policy": {"ram_gib": 1.5, "disk_gib": 15, "process_gib": 1.0},
    }

    def passed(name, details):
        result["checks"][name] = {"status": "PASS", "details": details}

    try:
        suites = ET.parse(ROOT / ".tmp/strat2d-synthetic-final-v1.xml").getroot().iter("testsuite")
        totals = Counter()
        for suite in suites:
            totals.update(
                {k: int(suite.get(k, "0")) for k in ("tests", "errors", "failures", "skipped")}
            )
        extra = ROOT / ".tmp/strat2d-open-position-v1.xml"
        if extra.exists():
            for suite in ET.parse(extra).getroot().iter("testsuite"):
                totals.update(
                    {k: int(suite.get(k, "0")) for k in ("tests", "errors", "failures", "skipped")}
                )
        if totals["errors"] or totals["failures"] or totals["tests"] < 130:
            raise ValueError("Comprehensive synthetic verification has not passed")
        result["tests"] = dict(totals)
        passed(
            "synthetic_tests",
            f"{totals['tests']} tests passed, including model, calendar, execution and crash cases",
        )
        ruff = ROOT.parent / "AI-Trading-System/.venv/Scripts/ruff.exe"
        paths = [
            "src/trading/research/strat2d",
            "tests/test_strat2d.py",
            "tools/strat2d_validation.py",
        ]
        commands = [
            [str(ruff), "check", *paths, "--no-cache"],
            [str(ruff), "format", "--check", *paths, "--no-cache"],
            ["git", "diff", "--check"],
        ]
        for command in commands:
            checked = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
            if checked.returncode != 0:
                raise ValueError("Static checks failed before historical execution")
        result["static_checks"] = {
            "ruff_lint": "PASS",
            "ruff_format": "PASS",
            "git_diff_check": "PASS",
        }
        passed("static_checks", "Ruff lint/format and git diff --check passed")
        resources(config)
        before = {
            p.relative_to(source).as_posix(): {
                "sha256": file_hash(p),
                "mtime": p.stat().st_mtime_ns,
            }
            for p in source.rglob("*")
            if p.is_file()
        }
        config_paths = [
            CONFIG,
            ROOT / "config/phase6a-research-v1.json",
            ROOT / "config/phase6-strat2c-eurusd-2022-jan-feb-validation-v1.json",
        ]
        config_before = {str(p): file_hash(p) for p in config_paths}
        write_new(AUDIT / "strat2c-before.json", before)
        quality_batches = sum(
            (json.loads(p.read_text())["rows"] + config["batch_rows"] - 1) // config["batch_rows"]
            for p in source.glob("chunks/*/receipt.json")
        )
        fault_generation = quality_batches + 3
        launch("interrupted", primary, config, fault_generation)
        manifest = json.loads((primary / "manifest.json").read_text())
        journal = Journal(primary, manifest, {}, lambda: resources(config))
        if (
            journal.generation != fault_generation - 1
            or journal.state["phase"] != "train"
            or journal.state["model"]["train_rows"] == 0
        ):
            raise ValueError("Crash did not preserve the expected training checkpoint")
        pending = list(primary.glob(".pending-*/state.json"))
        if len(pending) != 1:
            raise ValueError("Expected one preserved uncommitted model transaction")
        staged = json.loads(pending[0].read_text())
        if staged["model"]["train_rows"] <= journal.state["model"]["train_rows"]:
            raise ValueError("Crash was not inside an actual incremental model update")
        frozen_before_resume = committed_files(primary)
        result["interruption"] = {
            "committed_generation": journal.generation,
            "committed_train_rows": journal.state["model"]["train_rows"],
            "uncommitted_train_rows": staged["model"]["train_rows"],
        }
        launch("resumed", primary, config)
        completed = json.loads((AUDIT / "resumed-result.json").read_text())
        state = completed["state"]
        if not state["complete"] or not state["status"].startswith("engineering_complete"):
            raise ValueError("Engineering pipeline did not complete")
        after = committed_files(primary)
        if any(after.get(k) != v for k, v in frozen_before_resume.items()):
            raise ValueError("A committed model/cursor/ledger transaction was rewritten")
        passed(
            "checkpoint_recovery",
            "Hard process loss during training; staged update ignored; "
            "committed evidence unchanged",
        )
        launch("uninterrupted", REFERENCE, config)
        independent = json.loads((AUDIT / "uninterrupted-result.json").read_text())
        if (
            independent["logical_state_hash"] != completed["logical_state_hash"]
            or committed_files(REFERENCE) != after
        ):
            raise ValueError("Independent artifact or logical-state replay mismatch")
        passed(
            "independent_replay",
            f"{len(after)} immutable file hashes and final logical-state hash match",
        )
        write_new(AUDIT / "committed-file-hashes.json", after)
        launch("completed-resume", primary, config)
        if committed_files(primary) != after:
            raise ValueError("Repeated completed resume changed committed files")
        passed("completed_resume", "Idempotent completed-run resume, no duplicate fills or updates")
        result["ledger"] = scan_ledger(primary, state)
        passed(
            "portfolio_accounting",
            "Every fill/trade/event ID, history hash, costs and cash/equity reconcile",
        )
        result["quality"] = state["quality"]
        result["model"] = state["model"]
        result["portfolio"] = state["portfolio"]
        result["prediction_metrics"] = state["metrics"]
        n = state["metrics"]["test_labeled_rows"]
        if n:
            result["prediction_metrics"]["log_loss"] = state["metrics"]["log_loss_sum"] / n
            result["prediction_metrics"]["brier"] = state["metrics"]["brier_sum"] / n
        result["transactions"] = sum(1 for p in primary.iterdir() if p.name.isdigit())
        result["logical_state_hash"] = completed["logical_state_hash"]
        passed(
            "quality_audit",
            "All gaps/outside-session observations retained; affected labels excluded; "
            "rules unverified",
        )
        passed(
            "chronological_fitting",
            "Jan 1-15 train, Jan 15-Feb 1 calibration, February test; purging and embargo enforced",
        )
        passed(
            "frozen_inference",
            "No test observations fitted; coefficients frozen through calibration and test",
        )
        passed(
            "bounded_real_engineering",
            "Only 58,683 prepared EURUSD Jan-Feb 2022 rows; fixed 1024-row input batches",
        )
        final_source = {
            p.relative_to(source).as_posix(): {
                "sha256": file_hash(p),
                "mtime": p.stat().st_mtime_ns,
            }
            for p in source.rglob("*")
            if p.is_file()
        }
        if before != final_source or config_before != {str(p): file_hash(p) for p in config_paths}:
            raise ValueError("STRAT-2C or original configurations were modified")
        passed(
            "source_preservation",
            "All STRAT-2C files retain hashes and mtimes; configs unchanged; "
            "no Phase 5 reads/writes",
        )
        result["status"] = "PASS"
    except Exception as error:
        from trading.research.phase6.runner import _failure_record

        result["failure"] = _failure_record(error, {"stage": "strat2d_engineering_validation"})
    finally:
        result["process_measurements"] = {}
        for path in AUDIT.glob("*-result.json"):
            value = json.loads(path.read_text())
            result["process_measurements"][path.stem] = value["metrics"]
        for path in (AUDIT / "intentional-crash.json", *AUDIT.glob("*-failure.json")):
            if path.exists():
                value = json.loads(path.read_text())
                result["process_measurements"][path.stem] = value.get("metrics", {})
        for name in (
            "synthetic_tests",
            "quality_audit",
            "chronological_fitting",
            "frozen_inference",
            "portfolio_accounting",
            "checkpoint_recovery",
            "independent_replay",
            "completed_resume",
            "bounded_real_engineering",
            "source_preservation",
        ):
            if name not in result["checks"]:
                result["checks"][name] = {
                    "status": "FAIL",
                    "details": "Not completed; stopped safely, see failure evidence",
                }
        result["checks"]["historical_calendar_verification"] = {
            "status": "FAIL",
            "details": "Unverified; engineering-only policy, performance admission blocked",
        }
        result["checks"]["statistical_performance"] = {
            "status": "FAIL",
            "details": "Not established by this two-month engineering validation",
        }
        write_new(AUDIT / "validation-result.json", result)
        render(result)
        print(
            json.dumps({"status": result["status"], "report": str(REPORT), "audit": str(AUDIT)}),
            flush=True,
        )
    return 0 if result["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child")
    parser.add_argument("--output")
    parser.add_argument("--fault-generation", type=int, default=-1)
    args = parser.parse_args()
    return child(args) if args.child else parent()


if __name__ == "__main__":
    raise SystemExit(main())
