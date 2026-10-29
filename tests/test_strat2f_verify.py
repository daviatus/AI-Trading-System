"""Tiny independent-verifier regressions; no historical execution/scientific stack."""

import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

from trading.research.strat2f.verify import verify_events


class VerifierTests(unittest.TestCase):
    def fixture(self):
        event = {
            "sequence": 1,
            "time": "2022-02-02T12:01:00+00:00",
            "opened_at": "2022-02-02T12:00:00+00:00",
            "label_end": "2022-02-02T12:16:00+00:00",
            "label": 1,
            "probabilities": {"softmax_cal": [0.2, 0.6, 0.2]},
            "allowed": True,
        }
        state = {
            "metrics": {
                "test_2022-02": {
                    "softmax_cal": {
                        "n": 1,
                        "cm": [[0, 0, 0], [0, 1, 0], [0, 0, 0]],
                        "loss": -math.log(0.6),
                        "brier": 0.24,
                    }
                }
            },
            "thresholds": {"0.55": {"n": 1}},
        }
        return event, state

    def check(self, event, state):
        with tempfile.TemporaryDirectory(dir=".tmp") as directory:
            path = Path(directory)
            (path / "00000000").mkdir()
            (path / "00000000/events.jsonl").write_text(json.dumps(event) + "\n")
            return verify_events(path, state, lambda: None)

    def test_report_publication_preserves_existing_report(self):
        from unittest.mock import patch

        from trading.research.strat2f import report

        with tempfile.TemporaryDirectory(dir=".tmp") as directory:
            path = Path(directory)
            existing = path / "canonical.md"
            existing.write_text("prior evidence")
            with patch.object(report, "REPORT", existing):
                report.render(
                    {"status": "FAIL", "failure": {"reason": "synthetic resource stop"}}, path
                )
            self.assertEqual(existing.read_text(), "prior evidence")
            self.assertTrue((path / "report.md").exists())

    def test_numerical_totals_against_hand_oracle(self):
        event, state = self.fixture()
        result = self.check(event, state)
        self.assertEqual(result["verified_rows"], 1)
        self.assertEqual(result["threshold_counts"]["0.55"], 1)

    def test_embargo_is_independently_enforced(self):
        event, state = self.fixture()
        event.update(
            time="2022-02-01T00:30:00+00:00",
            opened_at="2022-02-01T00:29:00+00:00",
            label_end="2022-02-01T00:45:00+00:00",
        )
        with self.assertRaisesRegex(ValueError, "purge"):
            self.check(event, state)

    def test_nonfinite_probabilities_fail_closed(self):
        event, state = self.fixture()
        event["probabilities"]["softmax_cal"][0] = float("nan")
        with self.assertRaisesRegex(ValueError, "normalization"):
            self.check(event, state)

    def test_wrong_published_metric_is_rejected(self):
        event, state = self.fixture()
        state = copy.deepcopy(state)
        state["metrics"]["test_2022-02"]["softmax_cal"]["brier"] = 0.9
        with self.assertRaisesRegex(ValueError, "totals"):
            self.check(event, state)


if __name__ == "__main__":
    unittest.main()
