"""Explicit STRAT-2G-only 1.20 GiB policy; shared defaults are unchanged.

The legacy configuration is validated for scope/path rules. The new configuration
is independently checked against an exact operational-only delta, then monitored
at its actual 1.20 GiB floor using the existing native runtime probes.
"""

import json
import time
from pathlib import Path

from trading.research.safety import ROOT
from trading.research.strat2d.journal import file_hash
from trading.research.strat2d.pipeline import Monitor as BaseMonitor
from trading.research.strat2d.pipeline import validate_config as legacy_validate

PROFILE = "strat2g-eurusd-2022-offline-120-v1"
CONFIG = ROOT / "config/strat2g-eurusd-2022-offline-120-v1.json"
LEGACY = ROOT / "config/strat2d-eurusd-2022-offline-130-validation-v1.json"
PROTOCOL_SHA256 = "c0514b40d6802b9ed14293852882ca5e8213fdb377a588000786eee591c35dcb"


def validate_config(config, output=None):
    if config.get("resource_profile") != PROFILE:
        return legacy_validate(config, output)
    original = json.loads(LEGACY.read_text(encoding="utf-8"))
    operational = {
        "version",
        "output",
        "resource_profile",
        "minimum_available_ram_gib",
        "research_purpose",
        "research_protocol_sha256",
    }
    if (
        {k: v for k, v in config.items() if k not in operational}
        != {k: v for k, v in original.items() if k not in operational}
        or config.get("minimum_available_ram_gib") != 1.20
        or type(config.get("threads")) is not int
        or config.get("max_worker_ram_gib") != 0.25
        or config.get("research_purpose") != "strat2g_frozen_protocol_v1"
        or config.get("research_protocol_sha256") != PROTOCOL_SHA256
        or file_hash(ROOT / "config/strat2g-protocol-v1.json") != PROTOCOL_SHA256
    ):
        raise ValueError("Invalid scope, protocol or limits for opt-in STRAT-2G 1.20 GiB profile")
    selected = Path(output or config["output"])
    if not selected.name.startswith("strat2d-strat2g-") or "-v" not in selected.name:
        raise ValueError("1.20 GiB profile is restricted to versioned STRAT-2G outputs")
    # This invokes only legacy scope/path validation, not a resource admission.
    # Every admission/runtime sample uses config below, including its 1.20 floor.
    return legacy_validate(original, output or config["output"])


class Monitor(BaseMonitor):
    """Reuse unchanged sampling, disk/RSS/time limits and fail-closed comparisons."""

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
