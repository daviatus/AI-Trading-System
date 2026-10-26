"""Bounded deterministic JSONL observability with no feedback into decisions.

Schema v1: one record per completed-bar prediction, signal gate or execution
gate/trade. Context supplies experiment/model/version/window/calibration IDs.
Timestamps are UTC ISO8601. Unknown observations are null, never reconstructed.
All three probabilities are recorded, including neutral and rejected decisions.
Callbacks receive copies. Exceptions disable only the sink, invalidate diagnostic
completeness, and cannot alter a signal, fill, size, equity or ranking.
"""

import json
from collections import Counter
from pathlib import Path


class JsonlSink:
    """Stream records without retaining history; a byte budget bounds disk use."""

    def __init__(self, path, *, context=None, max_bytes=128 * 1024**2, guard=None):
        self.path = Path(path)
        self.partial = self.path.with_suffix(self.path.suffix + ".partial")
        self.context = dict(context or {})
        self.max_bytes = max_bytes
        self.guard = guard
        self.counts = Counter()
        self.events = Counter()
        self.predictions = Counter()
        self.probability_sums = [0.0, 0.0, 0.0]
        self.probability_histograms = [[0] * 10 for _ in range(3)]
        self.rows = self.bytes = self.errors = 0
        self.error_type = None
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() or self.partial.exists():
            raise FileExistsError("Diagnostics evidence already exists; use a new attempt")
        self.handle = self.partial.open("x", encoding="utf-8", newline="\n")
        return self

    def __call__(self, record):
        # Never allow user-provided dict mutation to affect the trading engine.
        if self.errors:
            return
        try:
            value = {"schema_version": 1, **self.context, **dict(record)}
            encoded = (
                json.dumps(
                    value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str
                )
                + "\n"
            )
            size = len(encoded.encode("utf-8"))
            if self.bytes + size > self.max_bytes:
                raise OSError("Diagnostic byte budget exhausted")
            if self.guard and self.rows % 1024 == 0:
                self.guard()
            self.handle.write(encoded)
            self.bytes += size
            self.rows += 1
            self.counts[str(value.get("reason", value.get("event", "unknown")))] += 1
            self.events[str(value.get("event", value.get("record_type", "unknown")))] += 1
            if value.get("event") == "prediction":
                self.predictions["rows"] += 1
                self.predictions["candidate_signals"] += bool(value.get("candidate_signal"))
                self.predictions["accepted_signals"] += bool(value.get("accepted"))
                probabilities = value.get("probabilities")
                if probabilities is not None:
                    self.predictions["available_predictions"] += 1
                    for i, probability in enumerate(probabilities):
                        self.probability_sums[i] += probability
                        self.probability_histograms[i][min(9, int(probability * 10))] += 1
        except Exception as error:
            self.errors += 1
            self.error_type = type(error).__name__

    def __exit__(self, kind, value, traceback):
        if self.handle:
            self.handle.close()
        if kind is None and not self.errors:
            self.partial.replace(self.path)

    def summary(self):
        return {
            "rows": self.rows,
            "bytes": self.bytes,
            "counts": dict(sorted(self.counts.items())),
            "complete": not self.errors,
            "errors": self.errors,
            "error_type": self.error_type,
            "events": dict(sorted(self.events.items())),
            "prediction_funnel": dict(sorted(self.predictions.items())),
            "mean_class_probabilities": [
                value / self.predictions["available_predictions"] for value in self.probability_sums
            ]
            if self.predictions["available_predictions"]
            else None,
            "class_probability_histograms_10_bins": self.probability_histograms,
        }


def emit(sink, record):
    """Best-effort observation only; return value must never inform decisions."""
    if sink is not None:
        try:
            sink(dict(record))
        except Exception:
            pass
