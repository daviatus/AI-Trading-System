"""Admission and continuous sampling must enforce H-only opt-in limits."""

import json
from unittest.mock import patch

import pytest

from tools import strat2d_historical_130_validation as harness
from trading.research import safety
from trading.research.strat2d import pipeline, runtime
from trading.research.strat2g import resources as old
from trading.research.strat2h import resources


@pytest.fixture
def profile():
    return json.loads(resources.CONFIG.read_text("utf-8"))


@pytest.mark.parametrize(
    "key,value",
    [
        ("minimum_available_ram_gib", 0.99),
        ("max_worker_ram_gib", 0.3),
        ("threads", 2),
        ("threads", True),
        ("mode", "LIVE"),
        ("free_floor_gib", 14),
        ("test_end", "2026-01-01T00:00:00Z"),
        ("asset", "GBPUSD"),
        ("research_purpose", "training"),
        ("diagnostic_protocol_sha256", "bad"),
        ("prepared", "data/research/phase5-v1"),
        ("batch_rows", 2048),
    ],
)
def test_scope_and_limits_cannot_be_changed(profile, key, value):
    profile[key] = value
    with pytest.raises(ValueError):
        resources.Monitor(profile)


def test_no_global_or_previous_profile_change(profile):
    resources.validate_config(profile)
    for validate in (pipeline.validate_config, old.validate_config):
        with pytest.raises(ValueError):
            validate(profile)
    assert pipeline.resource_floor({}) == 1.5
    assert json.loads(old.CONFIG.read_text())["minimum_available_ram_gib"] == 1.2
    with pytest.raises(ValueError):
        resources.validate_config(profile, "data/research/strat2d-other-v1")


def test_actual_one_gib_floor_rss_and_disk_enforced_every_sample(profile, monkeypatch):
    monkeypatch.setattr(safety, "available_memory", lambda: 1073741824)
    monkeypatch.setattr(
        pipeline.shutil, "disk_usage", lambda _: type("Disk", (), {"free": 20 * 2**30})()
    )
    monkeypatch.setattr(runtime, "process_memory", lambda: {"peak_rss_bytes": 100 * 2**20})
    monitor = resources.Monitor(profile)
    monitor()
    assert monitor.config is profile and monitor.minimum_available_ram_gib == 1
    monkeypatch.setattr(safety, "available_memory", lambda: 1073741823)
    with pytest.raises(safety.ResearchSafetyStop, match="Available RAM"):
        monitor()
    monkeypatch.setattr(safety, "available_memory", lambda: 1073741824)
    monkeypatch.setattr(runtime, "process_memory", lambda: {"peak_rss_bytes": 256 * 2**20 + 1})
    with pytest.raises(safety.ResearchSafetyStop, match="RSS"):
        monitor()
    monkeypatch.setattr(runtime, "process_memory", lambda: {"peak_rss_bytes": 100 * 2**20})
    monitor = resources.Monitor(profile)
    monkeypatch.setattr(
        pipeline.shutil, "disk_usage", lambda _: type("Disk", (), {"free": 14 * 2**30})()
    )
    with pytest.raises(safety.ResearchSafetyStop, match="disk"):
        monitor()


def test_parent_checks_same_profile_before_launch(profile):
    class Stop:
        def __init__(self, config):
            assert config is profile

        def __call__(self):
            raise safety.ResearchSafetyStop("synthetic floor failure")

    with patch.object(harness.subprocess, "Popen") as launch:
        with pytest.raises(safety.ResearchSafetyStop):
            harness.launch("test", "unused", profile, monitor_factory=Stop)
        launch.assert_not_called()
