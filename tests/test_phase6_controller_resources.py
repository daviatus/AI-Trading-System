"""Controller RAM, UTC, lock and failure checks without scientific imports."""

import json
import os
from pathlib import Path

import pytest

from trading.research.phase6 import controller
from trading.research.safety import ResearchSafetyStop


@pytest.fixture
def config():
    return json.loads(Path("config/phase6a-research-v1.json").read_text())


@pytest.mark.parametrize("value", [0, -1, 0.01, 3, float("nan"), float("inf"), True])
def test_worker_ram_limits_are_finite_and_bounded(config, value):
    config["max_worker_ram_gib"] = value
    with pytest.raises(ValueError, match="Worker RAM budget"):
        controller.validate_config(config)


@pytest.mark.parametrize("value", ["2026-01-01T00:00:01Z", "2026-01-01T23:59:00Z"])
def test_holdout_cutoff_uses_the_exact_exclusive_instant(config, value):
    config["folds"][-1]["test_end"] = value
    with pytest.raises(ValueError, match="holdout"):
        controller.validate_config(config)
    config["folds"][-1]["test_end"] = "2026-01-01T00:00:00Z"
    controller.validate_config(config)


@pytest.mark.parametrize("value", ["2025-09-01T00:00:00", "2025-09-01T00:00:00+02:00"])
def test_split_boundaries_require_explicit_utc(config, value):
    config["folds"][-1]["test_end"] = value
    with pytest.raises(ValueError, match="UTC"):
        controller.validate_config(config)


@pytest.mark.parametrize("key", ["free_floor_gib", "minimum_available_ram_gib"])
def test_nonfinite_safety_floors_cannot_disable_checks(config, key):
    config[key] = float("nan")
    with pytest.raises(ValueError, match="Safety floors"):
        controller.validate_config(config)


def test_worker_lock_is_independent_and_excludes_other_workers(tmp_path):
    with controller.controller_lock(tmp_path):
        with controller.controller_lock(tmp_path, lock_name=".worker.lock"):
            with pytest.raises(RuntimeError, match="worker is active"):
                with controller.controller_lock(tmp_path, lock_name=".worker.lock"):
                    pytest.fail("Concurrent worker acquired lock")
    with controller.controller_lock(tmp_path, lock_name=".worker.lock"):
        pass
    with pytest.raises(ValueError, match="single filename"):
        with controller.controller_lock(tmp_path, lock_name="../escape"):
            pytest.fail("Lock escaped output directory")


def test_standard_library_rss_measurement():
    assert controller.worker_rss_bytes(os.getpid()) > 0
    assert controller.worker_rss_bytes(2147483647) is None
    with pytest.raises(ResearchSafetyStop, match="valid process ID"):
        controller.worker_rss_bytes(-1)


def isolate_controller(config, tmp_path, monkeypatch):
    monkeypatch.setattr(controller, "ROOT", tmp_path)
    monkeypatch.setattr(controller, "resources", lambda value: 100 * 1024**3)
    monkeypatch.setattr(controller, "source_manifest", lambda value: {"source": "unchanged"})
    monkeypatch.setattr(controller, "code_manifest", lambda: {"code": "unchanged"})
    return controller.initialize(config)


def test_ram_stop_kills_only_worker_and_preserves_checkpoint(config, tmp_path, monkeypatch):
    output, jobs = isolate_controller(config, tmp_path, monkeypatch)

    class Process:
        pid = 12345
        returncode = None

        def __init__(self, *args, **kwargs):
            self.killed = False

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9

        def wait(self):
            return self.returncode

    process = Process()
    monkeypatch.setattr(controller.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(controller, "worker_rss_bytes", lambda pid: 2 * 1024**3)
    with pytest.raises(ResearchSafetyStop, match="RAM exceeded"):
        controller.run_queue(config, "config.json")
    state = json.loads((output / "checkpoint.json").read_text())
    entry = state["experiments"][jobs[0]["id"]]
    assert process.killed
    assert entry["status"] == "safety_stopped"
    assert entry["rejection_reason"] == "worker_ram_budget"
    assert entry["peak_worker_rss_bytes"] == 2 * 1024**3
    failure = json.loads((output / entry["directory"] / "failure.json").read_text())
    assert failure["rejection_reason"] == "worker_ram_budget"
    assert (output / entry["directory"] / "experiment.json").exists()


def test_worker_os_failure_is_preserved_without_sensitive_exception_text(
    config, tmp_path, monkeypatch
):
    output, jobs = isolate_controller(config, tmp_path, monkeypatch)

    def fail(*args, **kwargs):
        raise OSError(13, "SENSITIVE_EXCEPTION_PAYLOAD")

    monkeypatch.setattr(controller.subprocess, "Popen", fail)
    state = controller.run_queue(config, "config.json")
    entry = state["experiments"][jobs[0]["id"]]
    assert entry["status"] == "failed"
    assert entry["rejection_reason"] == "worker_os_error"
    assert entry["error_number"] == 13
    evidence = (output / entry["directory"] / "failure.json").read_text()
    assert "SENSITIVE_EXCEPTION_PAYLOAD" not in evidence
    assert (output / "checkpoint.json").exists()
    # Failed evidence remains final on resume; no implicit retry or overwrite.
    resumed = controller.run_queue(config, "config.json", limit=1)
    assert resumed["experiments"][jobs[0]["id"]] == entry
