"""Isolated, opt-in two-month offline validation with monitored hard-crash replay."""

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time

from trading.research.safety import ROOT, ResearchSafetyStop
from trading.research.strat2d.journal import (
    Journal,
    committed_files,
    file_hash,
    logical_hash,
    write_new,
)
from trading.research.strat2d.pipeline import Monitor, run, validate_config

CONFIG = ROOT / "config/strat2d-eurusd-2022-offline-130-validation-v1.json"
AUDIT = ROOT / "data/research/strat2d-eurusd-2022-historical-130-evidence-v1"
REPORT = ROOT / "reports/strat-2d-eurusd-2022-historical-validation-v1.md"
REFERENCE = "data/research/strat2d-eurusd-2022-offline-130-reference-v1"


def child(args):
    config = json.loads(CONFIG.read_text())
    ready = AUDIT / (args.child + "-pid.json")
    pending = AUDIT / (args.child + "-pid.pending")
    write_new(pending, {"pid": os.getpid()})
    os.rename(pending, ready)
    monitor = Monitor(config)

    def fault(stage, generation):
        if stage == args.fault_stage and generation == args.fault_generation:
            monitor()
            write_new(
                AUDIT / (args.child + "-crash.json"),
                {
                    "stage": stage,
                    "generation": generation,
                    "metrics": vars(monitor),
                    "expected_exit": 86,
                },
            )
            os._exit(86)

    try:
        state = run(config, output=args.output, guard=monitor, fault=fault)
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
                "failure": _failure_record(error, {"stage": "offline_130_validation"}),
                "metrics": vars(monitor),
            },
        )
        return 2 if isinstance(error, ResearchSafetyStop) else 1


def launch(
    role,
    output,
    config,
    generation=-1,
    stage="before_commit",
    *,
    module=None,
    extra_args=(),
    monitor_factory=Monitor,
):
    monitor = monitor_factory(config)
    monitor()
    env = dict(os.environ)
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        env[name] = "1"
    command = [
        sys.executable,
        "-B",
        "-m",
        module or "tools.strat2d_historical_130_validation",
        "--child",
        role,
        "--output",
        output,
        "--fault-generation",
        str(generation),
        "--fault-stage",
        stage,
        *extra_args,
    ]
    from ctypes import wintypes

    from trading.research.strat2d.runtime import _windows_api

    counters, kernel, psapi = _windows_api()
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    worker = None
    peak = 0
    try:
        with (AUDIT / (role + ".log")).open("x", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            try:
                while process.poll() is None:
                    monitor()
                    if worker is None:
                        ready = AUDIT / (role + "-pid.json")
                        if not ready.exists():
                            time.sleep(0.1)
                            continue
                        pid = json.loads(ready.read_text())["pid"]
                        worker = kernel.OpenProcess(0x0411, False, pid)
                        if not worker:
                            raise ResearchSafetyStop("Worker monitor handle unavailable")
                    value = counters()
                    value.cb = ctypes.sizeof(value)
                    if not psapi.GetProcessMemoryInfo(worker, ctypes.byref(value), value.cb):
                        if process.poll() is not None:
                            break
                        raise ResearchSafetyStop("Worker RSS measurement unavailable")
                    peak = max(peak, int(value.PeakWorkingSetSize))
                    if peak > config["max_worker_ram_gib"] * 1024**3:
                        raise ResearchSafetyStop("External worker RSS ceiling exceeded")
                    time.sleep(0.1)
            except BaseException:
                if worker is not None:
                    kernel.TerminateProcess(worker, 2)
                if process.poll() is None:
                    process.kill()
                process.wait()
                raise
            code = process.wait()
        expected = 86 if generation >= 0 else 0
        if code != expected:
            raise ResearchSafetyStop(f"Child {role} stopped with exit {code}; preserved evidence")
    finally:
        if worker is not None:
            kernel.CloseHandle(worker)
        write_new(
            AUDIT / (role + "-parent-monitor.json"),
            {"metrics": vars(monitor), "worker_peak_rss_bytes": peak},
        )
    print(json.dumps({"role": role, "exit": code, "peak_rss_mib": peak / 1024**2}), flush=True)


def preserved_source(source):
    original = json.loads(
        (
            ROOT
            / "data/research/phase6-strat2c-eurusd-2022-validation-audit-v1"
            / "after-resume-hashes.json"
        ).read_text()
    )
    for name, expected in original.items():
        path = source / name
        if file_hash(path) != expected["sha256"] or path.stat().st_mtime_ns != expected["mtime_ns"]:
            raise ValueError("Published STRAT-2C artifact changed")
    return len(original)


def inspect(output):
    path = ROOT / output
    manifest = json.loads((path / "manifest.json").read_text())
    config = json.loads(CONFIG.read_text())
    monitor = Monitor(config)
    journal = Journal(path, manifest, {}, monitor)
    return journal, committed_files(path)


def parent():
    config = json.loads(CONFIG.read_text())
    source, primary = validate_config(config)
    if any(p.exists() for p in (AUDIT, REPORT, primary, ROOT / REFERENCE)):
        raise ValueError(
            "Versioned validation paths exist; preserve evidence and use a new version"
        )
    AUDIT.mkdir(parents=True)
    result = {
        "status": "FAIL",
        "checks": {},
        "config": config,
        "primary": str(primary),
        "reference": REFERENCE,
    }
    checks = result["checks"]
    try:
        gate = Monitor(config)
        gate()
        result["initial_resources"] = vars(gate)
        checks["resource_admission"] = "PASS"
        result["strat2c_preserved_before"] = preserved_source(source)
        from trading.research.strat2d.pipeline import source_manifest

        result["source"] = source_manifest(source, config, gate)
        checks["source_integrity"] = "PASS"
        output = config["output"]
        launch("training-crash", output, config, 65)
        first, first_files = inspect(output)
        assert first.generation == 64 and first.state["phase"] == "train"
        assert any(primary.glob(".pending-*"))
        result["training_crash"] = {
            "committed_generation": first.generation,
            "phase": first.state["phase"],
        }
        write_new(AUDIT / "before-resume-hashes.json", first_files)
        launch("simulation-crash", output, config, 220, "after_commit")
        second, second_files = inspect(output)
        assert second.generation == 220 and second.state["phase"] == "test"
        assert all(second_files[k] == v for k, v in first_files.items())
        result["simulation_crash"] = {
            "committed_generation": second.generation,
            "phase": second.state["phase"],
        }
        launch("resume", output, config)
        complete, hashes = inspect(output)
        assert complete.state["complete"] and complete.state["status"] == "engineering_complete"
        assert all(hashes[k] == v for k, v in second_files.items())
        result["committed_generation"] = complete.generation
        result["state"] = complete.state
        checks["hard_crash_resume_and_immutable_prefix"] = "PASS"
        launch("repeat-resume", output, config)
        assert inspect(output)[1] == hashes
        checks["repeated_resume"] = "PASS"
        launch("reference", REFERENCE, config)
        reference, reference_hashes = inspect(REFERENCE)
        assert hashes == reference_hashes
        assert logical_hash(reference.state) == logical_hash(complete.state)
        result["matching_committed_files"] = len(hashes)
        result["logical_state_hash"] = logical_hash(complete.state)
        write_new(AUDIT / "resumed-hashes.json", hashes)
        write_new(AUDIT / "reference-hashes.json", reference_hashes)
        checks["independent_replay_all_committed_bytes"] = "PASS"
        import tools.strat2d_validation as ledger

        ledger.AUDIT = AUDIT
        result["ledger_events"] = ledger.scan_ledger(primary, complete.state)
        assert complete.state["model"]["train_rows"] > 0
        assert logical_hash(complete.state["model"]) == complete.state["frozen_model_hash"]
        assert (
            logical_hash(complete.state["model"]["weights"])
            == complete.state["training_weights_hash"]
        )
        assert all(v == 58683 for v in complete.state["counts"].values())
        checks["model_portfolio_ledger_invariants"] = "PASS"
        result["strat2c_preserved_after"] = preserved_source(source)
        checks["strat2c_preservation"] = "PASS"
        result["status"] = "PASS"
    except Exception as error:
        from trading.research.phase6.runner import _failure_record

        result["failure"] = _failure_record(error, {"stage": "historical_130_validation"})
        result["strat2c_preserved_after"] = preserved_source(source)
    finally:
        measurements = []
        for path in AUDIT.glob("*.json"):
            if path.name.endswith(
                ("-result.json", "-crash.json", "-failure.json", "-parent-monitor.json")
            ):
                record = json.loads(path.read_text())
                metrics = record.get("metrics", {})
                measurements.append(
                    {
                        "file": path.name,
                        "peak_rss_bytes": max(
                            metrics.get("peak_rss_bytes", 0), record.get("worker_peak_rss_bytes", 0)
                        ),
                        "minimum_available_ram_gib": metrics.get("minimum_available_ram_gib"),
                        "minimum_free_disk_gib": metrics.get("minimum_free_disk_gib"),
                    }
                )
        result["measurements"] = measurements
        write_new(AUDIT / "summary.json", result)
        render(result)
    print(json.dumps({"status": result["status"], "report": str(REPORT)}), flush=True)
    return 0 if result["status"] == "PASS" else 2


def render(result):
    rows = [
        "# STRAT-2D EURUSD January-February 2022 historical validation v1",
        "",
        f"Engineering validation: **{result['status']}**. "
        "Statistical performance remains unproven.",
        "",
        "## Resource configuration",
        "",
        "Explicit opt-in config: `config/strat2d-eurusd-2022-offline-130-validation-v1.json`. "
        "Profile `strat2d-eurusd-2022-offline-130-v1`: minimum physical available RAM 1.30 GiB, "
        "maximum worker peak RSS 256 MiB, minimum free disk 15 GiB, single-threaded offline "
        "execution, 1024-row batches. Ordinary workflows retain 1.5 GiB. The profile rejects "
        "other sources, dates, models, thread counts and limits. Child guards run between bounded "
        "operations; the parent polls physical RAM, disk and worker RSS every 0.1 seconds and "
        "kills only its validation child on a guard failure. This is sampled monitoring, not an "
        "OS-enforced memory reservation. All published transactions and failed staging "
        "are preserved.",
        "",
        "## Data coverage and exclusions",
        "",
        "Only the sealed 58,683-row EURUSD M1 preparation for 2022-01-01 UTC through "
        "2022-03-01 UTC exclusive is read. Training ends January 15, calibration ends February 1, "
        "and test ends March 1. Calibration/test have a 60-minute embargo; future-label paths "
        "and split-end labels are purged. No source candles are filled or altered. "
        "Finite predictors and observed target-contiguous paths are required.",
        "",
        "Historical calendar uncertainty remains explicit: America/New_York current-contract "
        "weekly hours are unverified for 2022; holiday evidence is absent. Calendar-based gap "
        "categories are hypotheses. Unexpected missing/out-of-session observations exclude "
        "affected label paths. An open position encountering unobservable data is censored, "
        "with exposure retained and later execution blocked. No invented gap fill is used.",
        "",
        "## Training, simulation, recovery and replay",
        "",
        "Model/scientific parameters are unchanged: online_softmax_v1, fixed scales, seed, "
        "learning rate and regularization; calibration freezes before test. Portfolio retains "
        "next-bar execution, costs and sizing. Ledger scans check sequential events/fills/trades, "
        "cash + unrealized = equity, cost totals and trade-history hashes. Historical zero fills "
        "do not validate real-data trade execution; forced-signal synthetic tests cover that path.",
        "",
        "The driver kills itself with os._exit(86) before training generation 65 publication, "
        "resumes and crashes after test generation 220 publication, then resumes to completion. "
        "A repeated completed resume must change no committed bytes. An independent uninterrupted "
        "run compares every manifest/state/event/receipt SHA-256 and final canonical state. "
        "Parent logs, memory metadata and orphan staging are intentionally outside "
        "committed comparisons.",
        "",
        "## PASS/FAIL checks",
        "",
        "| Requirement | Status |",
        "| --- | --- |",
    ]
    for name in (
        "resource_admission",
        "source_integrity",
        "hard_crash_resume_and_immutable_prefix",
        "repeated_resume",
        "independent_replay_all_committed_bytes",
        "model_portfolio_ledger_invariants",
        "strat2c_preservation",
    ):
        rows.append(f"| {name} | {result['checks'].get(name, 'NOT COMPLETED')} |")
    rows += [
        "",
        "## Measurements and complete evidence",
        "",
        "```json",
        json.dumps(result, indent=2),
        "```",
        "",
        "## Tests, failures and remaining blockers",
        "",
        "Synthetic test/static-check results are recorded in the adjacent evidence test log and "
        "tests.xml (report supplement below). Any actual failure and last metrics appear above. "
        "No guard was weakened during execution; incomplete stages are not claimed as passing. "
        "New profile validation changes code fingerprints: prior runs remain preserved and are "
        "not silently migrated. No Phase 5/STRAT-2C data, existing reports or checkpoints "
        "are modified.",
        "",
        "Historical calendar verification, missing data resolution and meaningful trade coverage "
        "remain blockers for performance research. This small two-month engineering run is not "
        "profitability evidence or authorization for live trading. Windows power-loss durability "
        "still depends on filesystem/hardware. No holdout access, annual backtest, broker/live "
        "connection, Docker/service startup, source change, commit or push is permitted.",
        "",
        "Reproduction (requires unused v1 output paths; preserve existing evidence):",
        "",
        "```powershell",
        r".\.venv\Scripts\python.exe -B -m tools.strat2d_historical_130_validation",
        "```",
        "",
    ]
    with REPORT.open("x", encoding="utf-8") as handle:
        handle.write("\n".join(rows))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monitor-check", action="store_true")
    parser.add_argument("--child")
    parser.add_argument("--output")
    parser.add_argument("--fault-generation", type=int, default=-1)
    parser.add_argument("--fault-stage", default="before_commit")
    args = parser.parse_args()
    if args.monitor_check:
        config = json.loads(CONFIG.read_text())
        launch(
            "monitor-reference",
            "data/research/strat2d-eurusd-2022-offline-130-monitored-reference-v1",
            config,
        )
        reference, hashes = inspect(REFERENCE)
        measured, measured_hashes = inspect(
            "data/research/strat2d-eurusd-2022-offline-130-monitored-reference-v1"
        )
        if hashes != measured_hashes or logical_hash(reference.state) != logical_hash(
            measured.state
        ):
            raise ValueError("External-monitor reference differs from independent reference")
        write_new(
            AUDIT / "external-monitor-comparison.json",
            {"status": "PASS", "matching_files": len(hashes)},
        )
        raise SystemExit(0)
    raise SystemExit(child(args) if args.child else parent())
