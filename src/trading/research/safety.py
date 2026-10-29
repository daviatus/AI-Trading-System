"""Standard-library-only resource gate, safe to run before scientific imports."""

import ctypes
import json
import math
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class ResearchSafetyStop(RuntimeError):
    """A public, credential-free diagnostic; no research action may bypass it."""


def minimum_ram_floor_gib(config):
    """Bounded offline preparation may use 1.25 GiB; experiments still need 1.5."""
    preparation = (
        config.get("preparation_only") is True
        and config.get("mode") == "offline_simulation"
        and config.get("threads") == 1
    )
    return 1.25 if preparation else 1.5


def available_memory():
    if os.name != "nt":
        pages = os.sysconf("SC_AVPHYS_PAGES")
        return pages * os.sysconf("SC_PAGE_SIZE")

    class Memory(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (key, ctypes.c_ulonglong)
            for key in (
                "total_physical",
                "available_physical",
                "total_page",
                "available_page",
                "total_virtual",
                "available_virtual",
                "available_extended",
            )
        ]

    memory = Memory()
    memory.length = ctypes.sizeof(memory)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        raise ResearchSafetyStop("Physical RAM measurement unavailable")
    return memory.available_physical


def resources(config):
    disk_floor = config["free_floor_gib"]
    ram_floor = config.get("minimum_available_ram_gib", 1.5)
    minimum_ram = minimum_ram_floor_gib(config)
    if (
        not isinstance(disk_floor, (int, float))
        or isinstance(disk_floor, bool)
        or not math.isfinite(disk_floor)
        or disk_floor < 15
    ):
        raise ResearchSafetyStop("Research disk floor cannot be below 15 GiB")
    if (
        not isinstance(ram_floor, (int, float))
        or isinstance(ram_floor, bool)
        or not math.isfinite(ram_floor)
        or ram_floor < minimum_ram
    ):
        raise ResearchSafetyStop(f"Research RAM minimum cannot be below {minimum_ram:g} GiB")
    free = shutil.disk_usage(ROOT).free
    ram = available_memory()
    if free < disk_floor * 1024**3:
        raise ResearchSafetyStop(f"Windows free disk {free / 1024**3:.2f} GiB below research floor")
    if ram < ram_floor * 1024**3:
        raise ResearchSafetyStop(f"Available RAM {ram / 1024**3:.2f} GiB below research minimum")
    return free


def bootstrap_gate(argv):
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", default="config/phase5-research.json")
    args, _ = parser.parse_known_args(argv)
    config = json.loads((ROOT / args.config).read_text())
    resources(config)
