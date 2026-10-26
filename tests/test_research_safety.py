"""Run with unittest under memory pressure; no scientific imports or DB access."""

import contextlib
import io
import runpy
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from trading.research import safety

GIB = 1024**3


class ResourceTests(unittest.TestCase):
    def check(self, disk, ram, **configuration):
        config = {"free_floor_gib": 15, "minimum_available_ram_gib": 1.5, **configuration}
        with (
            patch.object(safety.shutil, "disk_usage", return_value=SimpleNamespace(free=disk)),
            patch.object(safety, "available_memory", return_value=ram),
        ):
            return safety.resources(config)

    def test_disk_pressure_stops_research(self):
        with self.assertRaisesRegex(safety.ResearchSafetyStop, "free disk"):
            self.check(14 * GIB, 4 * GIB)

    def test_memory_pressure_stops_research(self):
        with self.assertRaisesRegex(safety.ResearchSafetyStop, "Available RAM"):
            self.check(40 * GIB, GIB // 3)

    def test_safe_headroom_passes(self):
        self.assertEqual(self.check(40 * GIB, 3 * GIB), 40 * GIB)

    def test_disk_floor_cannot_be_lowered(self):
        with self.assertRaisesRegex(safety.ResearchSafetyStop, "disk floor"):
            self.check(40 * GIB, 4 * GIB, free_floor_gib=1)

    def test_ram_floor_cannot_be_lowered(self):
        with self.assertRaisesRegex(safety.ResearchSafetyStop, "RAM minimum"):
            self.check(40 * GIB, 4 * GIB, minimum_available_ram_gib=0)

    def test_cli_stops_before_scientific_imports(self):
        before = set(sys.modules)
        with (
            patch.object(safety, "resources", side_effect=safety.ResearchSafetyStop("test gate")),
            patch.object(sys, "argv", ["research", "all"]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_module("trading.research.runner", run_name="__main__")
        self.assertEqual(stopped.exception.code, 2)
        added = set(sys.modules) - before
        self.assertFalse({"numpy", "pandas", "xgboost", "scipy", "sqlalchemy"} & added)


if __name__ == "__main__":
    unittest.main()
