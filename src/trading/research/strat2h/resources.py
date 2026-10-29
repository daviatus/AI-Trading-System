"""Explicit H-only 1.00 GiB engineering profile; no shared policy changes."""

import json
import time
from pathlib import Path

from trading.research.safety import ROOT
from trading.research.strat2d.journal import file_hash, logical_hash
from trading.research.strat2g import resources as previous

PROFILE = "strat2h-eurusd-2022-offline-100-v1"
CONFIG = ROOT / f"config/{PROFILE}.json"


def validate_config(config, output=None):
    if config.get("resource_profile") != PROFILE:
        return previous.validate_config(config, output)
    original = json.loads(previous.CONFIG.read_text("utf-8"))
    expected = dict(original)
    expected.update(
        version=PROFILE,
        resource_profile=PROFILE,
        minimum_available_ram_gib=1.0,
        output="data/research/strat2d-strat2g-strat2h-offline-100-v1",
        research_purpose="strat2h_frozen_engineering_diagnostics_v1",
        diagnostic_protocol_sha256=file_hash(ROOT / "config/strat2h-diagnostics-protocol-v1.json"),
        confirmation_protocol_sha256=file_hash(
            ROOT / "config/strat2h-confirmation-protocol-v1.json"
        ),
    )
    if logical_hash(config) != logical_hash(expected):
        raise ValueError("Invalid frozen scope or limits for opt-in STRAT-2H 1.00 GiB profile")
    selected = Path(output or config["output"])
    if not selected.name.startswith("strat2d-strat2g-strat2h-") or "-v" not in selected.name:
        raise ValueError("1.00 GiB policy requires versioned STRAT-2H diagnostic output")
    # Scope/path validation only; all real measurements below use actual config.
    return previous.validate_config(original, output or config["output"])


class Monitor(previous.Monitor):
    def __init__(self, config):
        if config.get("resource_profile") != PROFILE:
            super().__init__(config)
            return
        validate_config(config)
        self.config, self.started = config, time.monotonic()
        self.peak_rss_bytes = 0
        self.minimum_available_ram_gib = None
        self.minimum_free_disk_gib = None
        self.calls = 0
