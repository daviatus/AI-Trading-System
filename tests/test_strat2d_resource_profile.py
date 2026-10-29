"""Explicit scoped exception must never weaken other training workflows."""

import copy
import json
from pathlib import Path

import pytest

from trading.research import safety
from trading.research.strat2d import pipeline, runtime


@pytest.fixture
def profile():
    return json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "config/strat2d-eurusd-2022-offline-130-validation-v1.json"
        ).read_text()
    )


def test_profile_is_explicit_and_ordinary_defaults_unchanged(profile):
    pipeline.validate_config(profile)
    assert pipeline.resource_floor(profile) == 1.30
    assert safety.minimum_ram_floor_gib(profile) == 1.5
    ordinary = copy.deepcopy(profile)
    ordinary.pop("resource_profile")
    with pytest.raises(ValueError):
        pipeline.validate_config(ordinary)
    with pytest.raises(ValueError):
        pipeline.Monitor(ordinary)
    ordinary["minimum_available_ram_gib"] = 1.5
    pipeline.validate_config(ordinary)


@pytest.mark.parametrize(
    "key,value",
    [
        ("resource_profile", "unknown"),
        ("minimum_available_ram_gib", 1.29),
        ("max_worker_ram_gib", 0.3),
        ("batch_rows", 2048),
        ("asset", "GBPUSD"),
        ("mode", "LIVE"),
        ("threads", 2),
        ("threads", True),
        ("start", "2022-01-02T00:00:00Z"),
        ("test_end", "2026-03-01T00:00:00Z"),
        ("train_end", "2022-01-16T00:00:00Z"),
        ("calibration_end", "2022-02-02T00:00:00Z"),
        ("prepared", "data/research/other"),
        ("free_floor_gib", 14),
        ("preparation_only", True),
        ("quality_policy", "anything"),
    ],
)
def test_exception_rejects_other_scope_or_weaker_limits(profile, key, value):
    profile[key] = value
    with pytest.raises(ValueError):
        pipeline.validate_config(profile)
    with pytest.raises(ValueError):
        pipeline.Monitor(profile)


def test_model_exception_is_not_general(profile):
    profile["model"]["kind"] = "xgboost"
    with pytest.raises(ValueError):
        pipeline.validate_config(profile)


def test_monitor_floor_rss_and_measurements_fail_closed(profile, monkeypatch):
    monkeypatch.setattr(safety, "available_memory", lambda: int(1.31 * 2**30))
    monkeypatch.setattr(
        pipeline.shutil, "disk_usage", lambda _: type("Disk", (), {"free": 20 * 2**30})()
    )
    monkeypatch.setattr(runtime, "process_memory", lambda: {"peak_rss_bytes": 100 * 2**20})
    monitor = pipeline.Monitor(profile)
    monitor()
    monkeypatch.setattr(safety, "available_memory", lambda: int(1.29 * 2**30))
    with pytest.raises(safety.ResearchSafetyStop, match="Available RAM"):
        monitor()
    assert monitor.minimum_available_ram_gib < 1.30
    monkeypatch.setattr(safety, "available_memory", lambda: int(1.31 * 2**30))
    monkeypatch.setattr(runtime, "process_memory", lambda: {"peak_rss_bytes": 257 * 2**20})
    with pytest.raises(safety.ResearchSafetyStop, match="RSS"):
        monitor()
