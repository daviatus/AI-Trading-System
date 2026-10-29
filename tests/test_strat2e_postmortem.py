"""Tiny standard-library postmortem tests; no scientific imports or historical execution."""

import json
import tempfile
import unittest
from pathlib import Path

from trading.research.strat2d.journal import Journal, committed_files
from trading.research.strat2e.audit import published_ledger_funnel


class PostmortemTests(unittest.TestCase):
    def test_admission_failure_preserves_resource_metrics_without_worker(self):
        from unittest.mock import patch

        from tools import strat2e_validation as driver
        from trading.research.safety import ResearchSafetyStop

        class FailedMonitor:
            def __init__(self, config):
                self.minimum_available_ram_gib = 1.29
                self.peak_rss_bytes = 16 * 2**20

            def __call__(self):
                raise ResearchSafetyStop("Available RAM below research minimum")

        with tempfile.TemporaryDirectory(dir=".tmp") as directory:
            evidence = Path(directory) / "audit"
            with (
                patch.object(driver, "AUDIT", evidence),
                patch.object(driver, "Monitor", FailedMonitor),
            ):
                self.assertEqual(driver.parent(), 2)
            summary = json.loads((evidence / "summary.json").read_text())
            self.assertEqual(summary["status"], "FAIL")
            self.assertEqual(summary["parent_resource_metrics"]["minimum_available_ram_gib"], 1.29)
            self.assertEqual(len(list(evidence.iterdir())), 1)

    def fixture(self, path, sequence=1):
        journal = Journal(path, {"fixture": "published-probabilities"}, {})
        journal.commit(
            {}, [{"sequence": sequence, "event": "prediction", "probabilities": [0.5, 0.3, 0.2]}]
        )
        return committed_files(path)

    def test_count_published_prediction_without_scientific_imports(self):
        import subprocess
        import sys

        with tempfile.TemporaryDirectory(dir=".tmp") as directory:
            path = Path(directory)
            hashes = self.fixture(path)
            result = published_ledger_funnel(path, hashes, 0.55)
            self.assertEqual(result["confidence_gate_pass"], 0)
            self.assertEqual(result["confidence_gate_fail"], 1)
            self.assertEqual(result["maximum_calibrated_probabilities"], [0.5, 0.3, 0.2])
            self.assertEqual(committed_files(path), hashes)
            # Collection of other suites may already load scientific libraries.
            # Exercise the actual function in a fresh process to test its imports.
            script = (
                "import sys; from pathlib import Path; "
                "from trading.research.strat2d.journal import committed_files; "
                "from trading.research.strat2e.audit import published_ledger_funnel; "
                "p=Path(sys.argv[1]); "
                "r=published_ledger_funnel(p,committed_files(p),0.55); "
                "assert r['confidence_gate_pass']==0; "
                "assert not set(sys.modules)&{'numpy','pandas','pyarrow'}"
            )
            subprocess.run(
                [sys.executable, "-B", "-c", script, str(path)],
                check=True,
                capture_output=True,
                text=True,
            )

    def test_mutated_published_event_rejected(self):
        with tempfile.TemporaryDirectory(dir=".tmp") as directory:
            path = Path(directory)
            hashes = self.fixture(path)
            (path / "00000000/events.jsonl").write_text(json.dumps({"sequence": 1}) + "\n")
            with self.assertRaisesRegex(ValueError, "differs"):
                published_ledger_funnel(path, hashes, 0.55)

    def test_missing_event_sequence_rejected(self):
        with tempfile.TemporaryDirectory(dir=".tmp") as directory:
            path = Path(directory)
            hashes = self.fixture(path, sequence=2)
            with self.assertRaisesRegex(ValueError, "sequence"):
                published_ledger_funnel(path, hashes, 0.55)


if __name__ == "__main__":
    unittest.main()
