"""Isolated opt-in resource admission must be consistent and fail closed."""

import copy
import json
from unittest.mock import patch

import pytest

from tools import strat2d_historical_130_validation as harness
from trading.research import safety
from trading.research.strat2d import pipeline as legacy
from trading.research.strat2d import runtime
from trading.research.strat2g import resources


@pytest.fixture
def profile():
    return json.loads(resources.CONFIG.read_text(encoding="utf-8"))


def test_only_g_accepts_explicit120_and_legacy_floors_are_unchanged(profile):
    resources.validate_config(profile)
    monitor = resources.Monitor(profile)
    assert monitor.config is profile
    assert monitor.config["minimum_available_ram_gib"] == 1.2
    with pytest.raises(ValueError):
        legacy.validate_config(profile)
    with pytest.raises(ValueError):
        legacy.Monitor(profile)
    previous = json.loads(resources.LEGACY.read_text(encoding="utf-8"))
    assert legacy.resource_floor(previous) == 1.3
    assert previous["minimum_available_ram_gib"] == 1.3
    assert legacy.resource_floor({}) == 1.5
    assert safety.minimum_ram_floor_gib(profile) == 1.5
    plain = copy.deepcopy(profile)
    plain.pop("resource_profile")
    with pytest.raises(ValueError):
        resources.Monitor(plain)


@pytest.mark.parametrize(
    "key,value",
    [
        ("minimum_available_ram_gib", 1.19),
        ("minimum_available_ram_gib", 1.3),
        ("max_worker_ram_gib", 0.30),
        ("free_floor_gib", 14),
        ("threads", 2),
        ("threads", True),
        ("mode", "LIVE"),
        ("asset", "GBPUSD"),
        ("start", "2022-02-01T00:00:00Z"),
        ("test_end", "2026-01-01T00:00:00Z"),
        ("research_purpose", "other"),
        ("research_protocol_sha256", "wrong"),
        ("batch_rows", 2048),
        ("prepared", "data/research/phase5-v1"),
        ("preparation_only", True),
        ("resource_profile", "unknown"),
    ],
)
def test_misuse_or_weaker_limits_rejected(profile, key, value):
    profile[key] = value
    with pytest.raises(ValueError):
        resources.validate_config(profile)
    with pytest.raises(ValueError):
        resources.Monitor(profile)


def test_only_strat2g_output_paths_admitted(profile):
    with pytest.raises(ValueError):
        resources.validate_config(profile, "data/research/strat2d-other-v1")
    with pytest.raises(ValueError):
        resources.validate_config(profile, profile["prepared"])


def test_actual120_is_used_at_every_runtime_sample(profile, monkeypatch):
    monkeypatch.setattr(safety, "available_memory", lambda: int(1.21 * 2**30))
    monkeypatch.setattr(
        legacy.shutil, "disk_usage", lambda _: type("Disk", (), {"free": 20 * 2**30})()
    )
    monkeypatch.setattr(runtime, "process_memory", lambda: {"peak_rss_bytes": 100 * 2**20})
    monitor = resources.Monitor(profile)
    monitor()
    assert monitor.minimum_available_ram_gib > 1.20
    monkeypatch.setattr(safety, "available_memory", lambda: int(1.19 * 2**30))
    with pytest.raises(safety.ResearchSafetyStop, match="Available RAM"):
        monitor()
    assert monitor.minimum_available_ram_gib < 1.2
    monkeypatch.setattr(safety, "available_memory", lambda: int(1.21 * 2**30))
    monkeypatch.setattr(runtime, "process_memory", lambda: {"peak_rss_bytes": 257 * 2**20})
    with pytest.raises(safety.ResearchSafetyStop, match="RSS"):
        monitor()


def test_parent_launcher_uses_explicit_same_monitor(profile, tmp_path):
    seen = []

    class StopBeforeLaunch:
        def __init__(self, c):
            seen.append(c)

        def __call__(self):
            raise safety.ResearchSafetyStop("synthetic admission stop")

    with patch.object(harness.subprocess, "Popen") as process:
        with pytest.raises(safety.ResearchSafetyStop):
            harness.launch("synthetic", str(tmp_path), profile, monitor_factory=StopBeforeLaunch)
        process.assert_not_called()
    assert seen == [profile]
