"""Controller safety, evidence immutability and deterministic streaming tests."""

import copy
import json
import runpy
import sys
from pathlib import Path

import pytest

from trading.research.phase6 import controller
from trading.research.phase6.instrumentation import JsonlSink, emit
from trading.research.safety import ResearchSafetyStop


@pytest.fixture
def configuration():
    return json.loads(Path("config/phase6a-research-v1.json").read_text())


def test_queue_is_bounded_and_deterministic(configuration):
    jobs = controller.experiment_queue(configuration)
    assert len(jobs) == 16
    assert jobs == controller.experiment_queue(copy.deepcopy(configuration))
    assert len({job["id"] for job in jobs}) == 16
    configuration["max_iterations"] = 10
    with pytest.raises(ValueError, match="budget"):
        controller.experiment_queue(configuration)


@pytest.mark.parametrize(
    "key,value",
    [
        ("output", "data/research/phase5-v1"),
        ("mode", "LIVE"),
        ("threads", 2),
        ("free_floor_gib", 14),
        ("minimum_available_ram_gib", 1),
        ("risk_fraction", 0.006),
        ("max_positions", 2),
        ("max_iterations", 21),
        ("max_training_rows", 80001),
        ("max_rows_per_asset", 600001),
        ("leverage_scenarios", [31]),
        ("assets", ["BTCUSD"]),
        ("assets", ["WTI", "WTI"]),
    ],
)
def test_unsafe_configuration_rejected(configuration, key, value):
    configuration[key] = value
    with pytest.raises(ValueError):
        controller.validate_config(configuration)


def test_holdout_cannot_be_opened(configuration):
    configuration["folds"][0]["test_end"] = "2026-01-01T00:01:00Z"
    with pytest.raises(ValueError, match="holdout"):
        controller.validate_config(configuration)


def test_split_order_rejected(configuration):
    configuration["folds"][0]["train_end"] = configuration["folds"][0]["train_start"]
    with pytest.raises(ValueError, match="Chronological"):
        controller.validate_config(configuration)


def test_immutable_json_does_not_rewrite(tmp_path):
    path = tmp_path / "frozen.json"
    controller.write_json(path, {"x": 1}, immutable=True)
    modified = path.stat().st_mtime_ns
    controller.write_json(path, {"x": 1}, immutable=True)
    assert path.stat().st_mtime_ns == modified
    with pytest.raises(ValueError, match="Frozen"):
        controller.write_json(path, {"x": 2}, immutable=True)


def test_os_lock_excludes_second_controller(tmp_path):
    with controller.controller_lock(tmp_path), pytest.raises(RuntimeError, match="active"):
        with controller.controller_lock(tmp_path):
            pytest.fail("Second controller acquired lock")


def test_stream_bytes_reproduce_and_failures_do_not_escape(tmp_path):
    for name in ("a", "b"):
        with JsonlSink(tmp_path / name, context={"model": "v1"}) as sink:
            sink({"event": "prediction", "probabilities": [0.2, 0.5, 0.3]})
        assert sink.summary()["complete"]
    assert (tmp_path / "a").read_bytes() == (tmp_path / "b").read_bytes()
    with JsonlSink(tmp_path / "budget", max_bytes=2) as sink:
        sink({"large": "record"})
        sink({"another": "record"})
    assert sink.errors == 1
    assert not (tmp_path / "budget").exists()
    assert (tmp_path / "budget.partial").exists()
    emit(lambda record: (_ for _ in ()).throw(OSError("failure")), {"event": "test"})


def test_stream_refuses_prior_evidence(tmp_path):
    target = tmp_path / "observed.jsonl"
    with JsonlSink(target):
        pass
    with pytest.raises(FileExistsError):
        with JsonlSink(target):
            pass


def test_recovery_preserves_attempt_and_records_rejection(configuration, tmp_path, monkeypatch):
    monkeypatch.setattr(controller, "ROOT", tmp_path)
    monkeypatch.setattr(controller, "resources", lambda config: 100 * 1024**3)
    monkeypatch.setattr(controller, "source_manifest", lambda config: {"source": "hash"})
    monkeypatch.setattr(controller, "code_manifest", lambda: {"code": "hash"})
    output, jobs = controller.initialize(configuration)
    first = jobs[0]["id"]
    prior = output / "experiments" / first / "attempt-001"
    prior.mkdir(parents=True)
    (prior / "partial-evidence.json").write_text('{"preserved":true}')
    controller.write_json(
        output / "checkpoint.json", {"experiments": {first: {"status": "running", "attempt": 1}}}
    )

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            attempt = Path(command[command.index("--attempt") + 1])
            controller.write_json(
                attempt / "diagnostics.json",
                {"accepted": False, "rejection_reasons": ["no_eligible_candidate"]},
            )

        def poll(self):
            return 0

    monkeypatch.setattr(controller.subprocess, "Popen", Process)
    state = controller.run_queue(configuration, "config.json")
    assert state["experiments"][first]["status"] == "rejected"
    assert state["experiments"][first]["attempt"] == 2
    assert state["experiments"][first]["recovered_interrupted_attempt"]
    assert (prior / "partial-evidence.json").read_text() == '{"preserved":true}'
    assert len(state["experiments"]) == 1


def test_multiple_jobs_require_explicit_batch(configuration):
    with pytest.raises(ValueError, match="full-batch"):
        controller.run_queue(configuration, "config.json", limit=2)


def test_cli_resource_stop_precedes_science(monkeypatch):
    from trading.research import safety

    monkeypatch.setattr(
        safety, "resources", lambda config: (_ for _ in ()).throw(ResearchSafetyStop("test floor"))
    )
    monkeypatch.setattr(sys, "argv", ["phase6", "run"])
    before = set(sys.modules)
    with pytest.raises(SystemExit) as stopped:
        runpy.run_module("trading.research.phase6.runner", run_name="__main__")
    assert stopped.value.code == 2
    assert not ((set(sys.modules) - before) & {"numpy", "pandas", "scipy", "xgboost"})
