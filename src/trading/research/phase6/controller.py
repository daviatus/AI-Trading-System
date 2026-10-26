"""Standard-library-only bounded controller, immutable evidence and OS job lock."""

import hashlib
import itertools
import json
import math
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from trading.research.safety import ROOT, ResearchSafetyStop, resources

HOLDOUT = "2026-01-01"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def write_json(path, value, *, immutable=False):
    path = Path(path)
    encoded = json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and immutable:
        if path.read_text(encoding="utf-8") != encoded:
            raise ValueError("Frozen evidence differs; select a new Phase 6 output version")
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    os.replace(temporary, path)


def validate_config(config):
    """Reject unsafe settings before scientific imports or filesystem mutation."""
    from datetime import UTC, datetime, timedelta

    output = (ROOT / config["output"]).resolve()
    source = (ROOT / config["source"]).resolve()
    research = (ROOT / "data/research").resolve()
    if not output.is_relative_to(research) or not output.name.startswith("phase6"):
        raise ValueError("Outputs require a separate versioned Phase 6 research directory")
    if output == source or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("Research source/output overlap forbidden")
    if config.get("mode") != "offline_simulation" or config["threads"] != 1:
        raise ValueError("Only single-worker offline simulation is supported")
    floors = ((config["free_floor_gib"], 15), (config["minimum_available_ram_gib"], 1.5))
    if any(not math.isfinite(value) or value < floor for value, floor in floors):
        raise ValueError("Safety floors cannot be reduced")
    worker_ram = config.get("max_worker_ram_gib", 1.0)
    if (
        not isinstance(worker_ram, (int, float))
        or isinstance(worker_ram, bool)
        or not math.isfinite(worker_ram)
        or not 0.125 <= worker_ram <= 2
    ):
        raise ValueError("Worker RAM budget must be finite and between 0.125 and 2 GiB")
    if not 1 <= config["max_iterations"] <= 20:
        raise ValueError("Experiment budget must be bounded at 20 or fewer")
    if not 0 < config["risk_fraction"] <= 0.005 or config["max_positions"] != 1:
        raise ValueError("At most 0.5 percent planned risk and one portfolio position")
    if not set(config["assets"]) <= {"EURUSD", "XAUUSD", "WTI"} or not config["assets"]:
        raise ValueError("Unknown or empty instrument selection")
    if len(set(config["assets"])) != len(config["assets"]):
        raise ValueError("Duplicate instruments")
    if not set(config["leverage_scenarios"]) <= {1, 2, 5, 10, 20, 30}:
        raise ValueError("Unsupported leverage scenario")
    if not 1 <= config["max_rows_per_asset"] <= 600000:
        raise ValueError("Bounded per-asset history is required")
    if not 1 <= config["max_training_rows"] <= 80000:
        raise ValueError("Training sample budget exceeded")
    if not 10 <= config["timeout_seconds"] <= 3600:
        raise ValueError("Per-experiment timeout must be bounded")
    for fold in config["folds"]:
        values = [
            datetime.fromisoformat(fold[k].replace("Z", "+00:00"))
            for k in ("train_start", "train_end", "calibration_end", "tuning_end", "test_end")
        ]
        if any(value.tzinfo is None or value.utcoffset() != timedelta(0) for value in values):
            raise ValueError("Chronological split boundaries must explicitly use UTC")
        if not all(a < b for a, b in itertools.pairwise(values)):
            raise ValueError("Chronological split boundaries must strictly increase")
        cutoff = datetime.fromisoformat(HOLDOUT).replace(tzinfo=UTC)
        if values[-1] > cutoff or fold.get("holdout", False):
            raise ValueError("Previously inspected 2026 holdout is excluded from development")
    return output, source


def experiment_queue(config):
    jobs = []
    for fold, label, model in itertools.product(
        config["folds"], config["labels"], config["models"]
    ):
        scientific = {"fold": fold, "label": label, "model": model}
        identifier = hashlib.sha256(
            canonical({"config": config, **scientific}).encode()
        ).hexdigest()
        jobs.append({"id": identifier[:20], **scientific})
    if len(jobs) > config["max_iterations"]:
        raise ValueError("Configured matrix exceeds the bounded experiment budget")
    return jobs


def source_manifest(config):
    _, source = validate_config(config)
    snapshot = json.loads((source / "snapshot.json").read_text(encoding="utf-8"))
    entries = {}
    # Hash metadata and only monthly partitions capable of entering development.
    for name in ("snapshot.json", "contracts.json", "config.json", "environment.json"):
        entries[name] = digest(source / name)
    for relative, part in sorted(snapshot["parts"].items()):
        if relative.endswith(".parquet") and Path(relative).stem < "2026-01":
            path = (source / relative).resolve()
            if not path.is_relative_to(source):
                raise ValueError("Snapshot path traversal")
            checksum = digest(path)
            if checksum != part["sha256"]:
                raise ValueError("Frozen snapshot checksum mismatch")
            entries[relative] = checksum
    return entries


def code_manifest():
    paths = sorted((ROOT / "src/trading/research/phase6").glob("*.py")) + [
        ROOT / relative
        for relative in (
            "src/trading/research/data.py",
            "src/trading/research/execution.py",
            "src/trading/research/safety.py",
            "src/trading/ctrader/calendar.py",
            "src/trading/data/market_calendar.py",
        )
    ]
    return {str(p.relative_to(ROOT)).replace("\\", "/"): digest(p) for p in paths}


@contextmanager
def controller_lock(output, *, lock_name=".controller.lock"):
    """OS releases the lock even on a crash; never guesses stale PID ownership."""
    output = Path(output)
    if not lock_name or Path(lock_name).name != lock_name:
        raise ValueError("Lock name must be a single filename")
    output.mkdir(parents=True, exist_ok=True)
    with (output / lock_name).open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            kind = "worker" if lock_name == ".worker.lock" else "controller"
            raise RuntimeError(f"Another Phase 6 {kind} is active") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def worker_rss_bytes(pid):
    """Measure a child's resident working set without scientific dependencies.

    A disappeared child returns None. Other measurement failures stop research
    instead of silently disabling the per-experiment RAM limit.
    """
    if not isinstance(pid, int) or pid <= 0:
        raise ResearchSafetyStop("Worker RAM measurement requires a valid process ID")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t)
                for name in (
                    "PeakWorkingSetSize",
                    "WorkingSetSize",
                    "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage",
                    "PagefileUsage",
                    "PeakPagefileUsage",
                )
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(Counters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:  # PID disappeared between poll and measurement.
                return None
            raise ResearchSafetyStop("Worker RAM measurement unavailable")
        try:
            memory = Counters()
            memory.cb = ctypes.sizeof(memory)
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb):
                raise ResearchSafetyStop("Worker RAM measurement unavailable")
            return int(memory.WorkingSetSize)
        finally:
            kernel.CloseHandle(handle)
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise ResearchSafetyStop("Worker RAM measurement unavailable") from None
    raise ResearchSafetyStop("Worker RAM measurement unavailable")


def _stop_worker(process):
    if process is not None:
        try:
            if process.poll() is None:
                process.kill()
            process.wait()
        except ProcessLookupError:
            pass


def _failure_evidence(attempt, entry):
    write_json(
        attempt / "failure.json",
        {key: value for key, value in entry.items() if key not in {"directory"}},
        immutable=True,
    )


def initialize(config):
    output, _ = validate_config(config)
    resources(config)
    write_json(output / "config.json", config, immutable=True)
    write_json(output / "code-manifest.json", code_manifest(), immutable=True)
    manifest = source_manifest(config)
    write_json(output / "source-manifest.json", manifest, immutable=True)
    jobs = experiment_queue(config)
    write_json(output / "queue.json", jobs, immutable=True)
    return output, jobs


def run_queue(config, config_path, *, limit=1, full_batch=False):
    output, _ = validate_config(config)
    if limit < 1 or (limit > 1 and not full_batch):
        raise ValueError("Multiple iterations require explicit --full-batch initiation")
    with controller_lock(output):
        output, jobs = initialize(config)
        state_path = output / "checkpoint.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {"experiments": {}}
        attempted = 0
        for job in jobs:
            prior = state["experiments"].get(job["id"], {})
            if prior.get("status") in {"complete", "rejected", "failed", "timeout"}:
                continue  # Failures are evidence; a retry requires a new version/config.
            if attempted >= min(limit, config["max_iterations"]):
                break
            resources(config)
            number = prior.get("attempt", 0) + 1
            attempt = output / "experiments" / job["id"] / f"attempt-{number:03}"
            attempt.mkdir(parents=True, exist_ok=False)
            entry = {
                "status": "running",
                "attempt": number,
                "directory": str(attempt.relative_to(output)),
                "recovered_interrupted_attempt": prior.get("status") == "running",
            }
            state["experiments"][job["id"]] = entry
            write_json(state_path, state)
            write_json(attempt / "experiment.json", job, immutable=True)
            env = dict(os.environ)
            for key in (
                "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS",
            ):
                env[key] = "1"
            command = [
                sys.executable,
                "-m",
                "trading.research.phase6.runner",
                "worker",
                "--config",
                str(config_path),
                "--job-id",
                job["id"],
                "--attempt",
                str(attempt),
            ]
            process = None
            try:
                with (attempt / "worker.log").open("x", encoding="utf-8") as log:
                    process = subprocess.Popen(command, stdout=log, stderr=log, env=env, cwd=ROOT)
                    started = time.monotonic()
                    while process.poll() is None:
                        if time.monotonic() - started > config["timeout_seconds"]:
                            _stop_worker(process)
                            entry.update(status="timeout", rejection_reason="wall_time_budget")
                            _failure_evidence(attempt, entry)
                            break
                        try:
                            resources(config)
                            rss = worker_rss_bytes(process.pid)
                            if rss is None:
                                if process.poll() is None:
                                    raise ResearchSafetyStop("Worker RAM measurement unavailable")
                                continue
                            entry["peak_worker_rss_bytes"] = max(
                                rss, entry.get("peak_worker_rss_bytes", 0)
                            )
                            budget = int(config.get("max_worker_ram_gib", 1.0) * 1024**3)
                            if rss > budget:
                                entry.update(
                                    status="safety_stopped",
                                    rejection_reason="worker_ram_budget",
                                    worker_rss_bytes=rss,
                                    worker_ram_budget_bytes=budget,
                                )
                                raise ResearchSafetyStop(
                                    "Worker resident RAM exceeded experiment budget"
                                )
                        except ResearchSafetyStop:
                            _stop_worker(process)
                            if entry["status"] != "safety_stopped":
                                entry.update(
                                    status="safety_stopped",
                                    rejection_reason="resource_floor_or_measurement",
                                )
                            _failure_evidence(attempt, entry)
                            raise
                        time.sleep(0.25)
                    else:
                        result = attempt / "diagnostics.json"
                        if process.returncode == 0 and result.exists():
                            evidence = json.loads(result.read_text())
                            entry.update(
                                status="complete" if evidence["accepted"] else "rejected",
                                rejection_reasons=evidence["rejection_reasons"],
                            )
                        else:
                            entry.update(
                                status="failed",
                                rejection_reason="worker_failed",
                                returncode=process.returncode,
                            )
                            _failure_evidence(attempt, entry)
            except OSError as error:
                _stop_worker(process)
                entry.update(
                    status="failed",
                    rejection_reason="worker_os_error",
                    error_type=type(error).__name__,
                    error_number=error.errno,
                )
                _failure_evidence(attempt, entry)
            finally:
                write_json(state_path, state)
            if source_manifest(config) != json.loads((output / "source-manifest.json").read_text()):
                raise ValueError("Source evidence changed during experiment")
            attempted += 1
        return state
