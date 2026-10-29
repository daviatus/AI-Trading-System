"""Configuration/dispatch checks only; never prepare historical candles."""

import json
from pathlib import Path

import pytest

from trading.research.phase6 import controller, runner

CONFIG = Path("config/phase6-strat2c-eurusd-2022-jan-feb-validation-v1.json")


@pytest.fixture
def config():
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def forbidden(*args, **kwargs):
    pytest.fail("Configuration validation must not start research or access services")


def test_validation_preserves_science_and_has_only_requested_scope(config):
    original = json.loads(Path("config/phase6a-research-v1.json").read_text(encoding="utf-8"))
    changed = {k for k in original.keys() | config.keys() if original.get(k) != config.get(k)}
    assert changed == {
        "version",
        "output",
        "assets",
        "folds",
        "preparation_only",
        "minimum_available_ram_gib",
    }
    assert config["assets"] == ["EURUSD"]
    assert config["source"] == "data/research/phase5-v1"
    assert config["preparation_only"] is True
    assert config["mode"] == "offline_simulation"
    assert config["threads"] == 1
    assert config["free_floor_gib"] == 15
    assert config["minimum_available_ram_gib"] == 1.25
    assert len(config["folds"]) == 1
    fold = config["folds"][0]
    assert fold["train_start"] == "2022-01-01T00:00:00Z"
    assert fold["test_end"] == "2022-03-01T00:00:00Z"
    output, source = controller.validate_config(config)
    assert output.is_relative_to(controller.ROOT)
    assert not output.is_relative_to(source) and not source.is_relative_to(output)
    assert len(controller.experiment_queue(config)) == 8  # Metadata, no model construction.


@pytest.mark.parametrize("command", ["audit", "plan", "smoke", "run", "worker"])
def test_cli_rejects_every_nonprepare_command_before_resources(config, monkeypatch, command):
    monkeypatch.setattr(runner, "resources", forbidden)
    for name in ("audit", "initialize", "run_queue", "smoke", "execute_experiment", "prepare_job"):
        monkeypatch.setattr(runner, name, forbidden)
    with pytest.raises(ValueError, match="Preparation-only"):
        runner.main([command, "--config", str(CONFIG)])


@pytest.mark.parametrize(
    "entrypoint", ["initialize", "run_queue", "audit", "smoke", "execute", "execute_internal"]
)
def test_python_experiment_entrypoints_reject_before_any_mutation(
    config, monkeypatch, tmp_path, entrypoint
):
    monkeypatch.setattr(controller, "resources", forbidden)
    monkeypatch.setattr(runner, "resources", forbidden)
    monkeypatch.setattr(controller, "source_manifest", forbidden)
    calls = {
        "initialize": lambda: controller.initialize(config),
        "run_queue": lambda: controller.run_queue(config, CONFIG),
        "audit": lambda: runner.audit(config),
        "smoke": lambda: runner.smoke(config, tmp_path / "smoke"),
        "execute": lambda: runner.execute_experiment(config, {}, tmp_path / "attempt"),
        "execute_internal": lambda: runner._execute_experiment(config, {}, tmp_path / "attempt"),
    }
    with pytest.raises(ValueError, match="Preparation-only"):
        calls[entrypoint]()
    assert not list(tmp_path.iterdir())


def test_prepare_cli_dispatch_is_bounded_and_cannot_start_experiments(config, monkeypatch):
    monkeypatch.setattr(runner, "resources", lambda config: None)
    for name in ("audit", "initialize", "run_queue", "smoke", "execute_experiment"):
        monkeypatch.setattr(runner, name, forbidden)
    job = controller.experiment_queue(config)[0]
    called = []

    def prepare(actual_config, actual_job, *, batch_rows, max_chunks):
        assert actual_config == config and actual_job == job
        assert batch_rows == 8192 and max_chunks == 1
        called.append(True)
        return {
            "EURUSD": {"complete": False, "rows_read": 8192, "rows_written": 8177, "gap_minutes": 0}
        }

    monkeypatch.setattr(runner, "prepare_job", prepare)
    assert (
        runner.main(
            [
                "prepare",
                "--config",
                str(CONFIG),
                "--job-id",
                job["id"],
                "--batch-rows",
                "8192",
                "--max-chunks",
                "1",
            ]
        )
        == 0
    )
    assert called == [True]


def test_preparation_flag_requires_boolean_and_is_opt_in(config):
    config["preparation_only"] = "true"
    with pytest.raises(ValueError, match="boolean"):
        controller.validate_config(config)
    del config["preparation_only"]
    for command in ("audit", "plan", "run", "smoke", "worker", "prepare"):
        controller.validate_command(config, command)
