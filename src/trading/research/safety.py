"""Standard-library-only resource gate, safe to run before scientific imports."""

import ctypes
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class ResearchSafetyStop(RuntimeError):
    """A public, credential-free diagnostic; no research action may bypass it."""


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
    free = shutil.disk_usage(ROOT).free
    ram = available_memory()
    if config["free_floor_gib"] < 15:
        raise ResearchSafetyStop("Research disk floor cannot be below 15 GiB")
    if config.get("minimum_available_ram_gib", 1.5) < 1.5:
        raise ResearchSafetyStop("Research RAM minimum cannot be below 1.5 GiB")
    if free < config["free_floor_gib"] * 1024**3:
        raise ResearchSafetyStop(f"Windows free disk {free / 1024**3:.2f} GiB below research floor")
    if ram < config.get("minimum_available_ram_gib", 1.5) * 1024**3:
        raise ResearchSafetyStop(f"Available RAM {ram / 1024**3:.2f} GiB below research minimum")
    return free


def bootstrap_gate(argv):
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", default="config/phase5-research.json")
    args, _ = parser.parse_known_args(argv)
    config = json.loads((ROOT / args.config).read_text())
    resources(config)
