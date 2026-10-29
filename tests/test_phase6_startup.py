"""Reproduce startup checks and failure reporting without historical preparation."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trading.research import safety
from trading.research.phase6 import controller, runner

GIB = 1024**3
CONFIG = Path("config/phase6-strat2c-eurusd-2022-jan-feb-validation-v1.json")


@pytest.fixture
def config():
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def headroom(monkeypatch, *, ram=2.05, disk=29.54):
    monkeypatch.setattr(safety, "available_memory", lambda: int(ram * GIB))
    monkeypatch.setattr(
        safety.shutil, "disk_usage", lambda path: SimpleNamespace(free=int(disk * GIB))
    )


def isolated_config(config, tmp_path, monkeypatch, *, encoding="utf-8"):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(controller, "ROOT", tmp_path)
    path = tmp_path / "validation.json"
    path.write_text(json.dumps(config), encoding=encoding)
    return path


def arguments(config, path):
    return [
        "prepare",
        "--config",
        str(path),
        "--job-id",
        controller.experiment_queue(config)[0]["id"],
        "--batch-rows",
        "8192",
        "--max-chunks",
        "1",
    ]


def reports(tmp_path):
    return sorted((tmp_path / "data/research/phase6-cli-failures-v1").glob("failure-*.json"))


def test_reported_configuration_and_free_memory_reproduce_fixed_startup(
    config, monkeypatch, tmp_path
):
    assert config["minimum_available_ram_gib"] == 1.25
    assert controller.experiment_queue(config)[0]["id"] == "1d06fff895ee69a12b38"
    path = isolated_config(config, tmp_path, monkeypatch)
    headroom(monkeypatch)
    controller.validate_config(config)
    assert safety.resources(config) == int(29.54 * GIB)
    called = []

    def prepare(actual, job, **kwargs):
        assert actual == config
        assert job["id"] == "1d06fff895ee69a12b38"
        assert kwargs == {"batch_rows": 8192, "max_chunks": 1}
        called.append(True)
        return {
            "EURUSD": {"complete": False, "rows_read": 8192, "rows_written": 8177, "gap_minutes": 0}
        }

    monkeypatch.setattr(runner, "prepare_job", prepare)
    assert runner.cli(arguments(config, path)) == 0
    assert called == [True] and not reports(tmp_path)
    assert not (tmp_path / config["output"]).exists()


@pytest.mark.parametrize("ram,allowed", [(1.25, True), (1.24, False)])
def test_preparation_resource_gate_enforces_the_configured_125_floor(
    config, monkeypatch, ram, allowed
):
    headroom(monkeypatch, ram=ram)
    if allowed:
        safety.resources(config)
    else:
        with pytest.raises(safety.ResearchSafetyStop, match="Available RAM"):
            safety.resources(config)


@pytest.mark.parametrize("change", [{"preparation_only": False}, {"mode": "LIVE"}, {"threads": 2}])
def test_lower_floor_does_not_apply_to_experiments_live_or_parallel(config, change, monkeypatch):
    config.update(change)
    headroom(monkeypatch)
    assert safety.minimum_ram_floor_gib(config) == 1.5
    with pytest.raises(ValueError):
        controller.validate_config(config)
    with pytest.raises(safety.ResearchSafetyStop, match="RAM minimum"):
        safety.resources(config)


@pytest.mark.parametrize("key", ["free_floor_gib", "minimum_available_ram_gib"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 0, True, "1.25"])
def test_invalid_resource_policy_rejected_consistently(config, monkeypatch, key, value):
    config[key] = value
    headroom(monkeypatch)
    with pytest.raises(ValueError, match="Safety floors"):
        controller.validate_config(config)
    with pytest.raises(safety.ResearchSafetyStop):
        safety.resources(config)


def test_preparation_floor_cannot_be_lowered_further(config, monkeypatch):
    config["minimum_available_ram_gib"] = 1.249
    headroom(monkeypatch)
    with pytest.raises(ValueError, match="Safety floors"):
        controller.validate_config(config)
    with pytest.raises(safety.ResearchSafetyStop, match="1.25 GiB"):
        safety.resources(config)


def test_failure_origin_identifies_validator_without_disclosing_payload(config):
    config["minimum_available_ram_gib"] = 1.0
    try:
        controller.validate_config(config)
    except ValueError as error:
        record = runner._failure_record(error, {"stage": "configuration_validation"})
    assert record["reason"] == "Safety floors cannot be reduced"
    assert record["origin"]["file"] == "src/trading/research/phase6/controller.py"
    assert record["origin"]["function"] == "validate_config"
    assert record["origin"]["line"] > 0


def test_startup_failure_writes_unique_evidence_without_initializing_output(
    config, monkeypatch, tmp_path, capsys
):
    config["minimum_available_ram_gib"] = 1.0
    path = isolated_config(config, tmp_path, monkeypatch)
    headroom(monkeypatch)
    for _ in range(2):
        assert runner.cli(arguments(config, path)) == 1
    files = reports(tmp_path)
    assert len(files) == 2
    for file in files:
        record = json.loads(file.read_text())
        assert record["stage"] == "configuration_validation"
        assert record["exception_type"] == "ValueError"
        assert record["reason"] == "Safety floors cannot be reduced"
    output = capsys.readouterr().out
    assert "stage=configuration_validation" in output
    assert "failure_report=data/research/phase6-cli-failures-v1/" in output
    assert "inspect preserved attempt" not in output
    assert not (tmp_path / config["output"]).exists()


def test_resource_stop_retains_exit_two_and_sanitized_measurements(
    config, monkeypatch, tmp_path, capsys
):
    path = isolated_config(config, tmp_path, monkeypatch)
    headroom(monkeypatch, ram=1.24)
    assert runner.cli(arguments(config, path)) == 2
    record = json.loads(reports(tmp_path)[0].read_text())
    assert record["stage"] == "resource_guard"
    assert record["status"] == "safety_stopped"
    assert record["reason"] == "Available RAM 1.24 GiB below research minimum"
    assert "RESEARCH_SAFETY_STOP" in capsys.readouterr().out
    assert not (tmp_path / config["output"]).exists()


def test_wrong_job_is_reported_before_preparation(config, monkeypatch, tmp_path, capsys):
    path = isolated_config(config, tmp_path, monkeypatch)
    headroom(monkeypatch)
    argv = arguments(config, path)
    argv[argv.index("--job-id") + 1] = "00000000000000000000"

    def forbidden(*args, **kwargs):
        pytest.fail("A mismatched job must not reach preparation")

    monkeypatch.setattr(runner, "prepare_job", forbidden)
    assert runner.cli(argv) == 1
    record = json.loads(reports(tmp_path)[0].read_text())
    assert record["stage"] == "preparation_job_selection"
    assert "explicit registered --job-id" in capsys.readouterr().out
    assert not (tmp_path / config["output"]).exists()


def test_bom_config_is_accepted_without_mutating_it(config, monkeypatch, tmp_path):
    path = isolated_config(config, tmp_path, monkeypatch, encoding="utf-8-sig")
    original = path.read_bytes()
    headroom(monkeypatch)
    monkeypatch.setattr(runner, "prepare_job", lambda *args, **kwargs: {})
    assert runner.cli(arguments(config, path)) == 0
    assert path.read_bytes() == original
    assert not reports(tmp_path)


@pytest.mark.parametrize("failure", ["invalid_json", "source_error"])
def test_sensitive_error_payload_is_never_printed_or_persisted(
    config, monkeypatch, tmp_path, capsys, failure
):
    secret = "postgresql://private-user:private-password@private-host/secret-database"
    path = isolated_config(config, tmp_path, monkeypatch)
    headroom(monkeypatch)
    if failure == "invalid_json":
        path.write_text('{"credential": "' + secret)
    else:

        def error(*args, **kwargs):
            raise ValueError(secret)

        monkeypatch.setattr(runner, "prepare_job", error)
    assert runner.cli(arguments(config, path)) == 1
    console = capsys.readouterr()
    evidence = reports(tmp_path)[0].read_text()
    for value in (secret, "private-password", "private-user", "private-host", str(path)):
        assert value not in console.out + console.err + evidence
    assert json.loads(evidence)["exception_type"] in {"JSONDecodeError", "ValueError"}


def test_failure_reporting_does_not_mask_original_error_on_unwritable_disk(
    config, monkeypatch, tmp_path, capsys
):
    config["minimum_available_ram_gib"] = 1.0
    path = isolated_config(config, tmp_path, monkeypatch)

    def denied(*args, **kwargs):
        raise PermissionError("sensitive filesystem detail")

    monkeypatch.setattr(Path, "mkdir", denied)
    assert runner.cli(arguments(config, path)) == 1
    console = capsys.readouterr().out
    assert "Safety floors cannot be reduced" in console
    assert "failure_report=unavailable" in console
    assert "sensitive filesystem detail" not in console
