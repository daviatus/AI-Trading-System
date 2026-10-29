"""Synthetic checks of the bounded validation oracle; no historical data."""

import json

import numpy as np
import pandas as pd

from tools.strat2c_stream_validation import Validation, committed_hashes, process_memory, state_at
from trading.research.phase6 import streaming
from trading.research.phase6.controller import digest
from trading.research.phase6.features import FeatureSpec
from trading.research.phase6.labels import LabelSpec


def test_bounded_oracle_reconciles_month_boundary_and_terminal_labels(tmp_path):
    source, output = tmp_path / "source", tmp_path / "stream"
    index = pd.date_range("2022-01-31T22:00:00Z", periods=300, freq="min", name="opened_at")
    price = 100 + np.sin(np.arange(300) / 13) * 0.1
    bars = pd.DataFrame(
        {"open": price, "high": price + 0.03, "low": price - 0.03, "close": price, "volume": 1.0},
        index=index,
    )
    parts = {}
    for month, part in bars.groupby(bars.index.strftime("%Y-%m")):
        relative = f"cache/EURUSD/{month}.parquet"
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        part.reset_index().to_parquet(path, index=False)
        parts[relative] = {"sha256": digest(path), "rows": len(part)}
    (source / "snapshot.json").write_text(json.dumps({"parts": parts}))
    spec = FeatureSpec()
    label = LabelSpec(kind="barrier", horizon_minutes=15)
    metadata = {
        "specifications": {
            "scheduleTimeZone": "UTC",
            "holiday": [],
            "schedule": [{"startSecond": 0, "endSecond": 604800}],
        }
    }
    state = streaming.prepare_stream(
        source,
        output,
        "EURUSD",
        "2022-01-01T00:00:00Z",
        "2022-03-01T00:00:00Z",
        feature_spec=spec,
        label_spec=label,
        metadata=metadata,
        batch_rows=8192,
    )
    validator = Validation.__new__(Validation)
    validator.source, validator.stream = source, output
    validator.np, validator.pd, validator.streaming = np, pd, streaming
    validator.guard = lambda: None
    validator.feature_spec, validator.label_spec, validator.metadata = spec, label, metadata
    validator.job = {
        "fold": {"train_start": "2022-01-01T00:00:00Z", "test_end": "2022-03-01T00:00:00Z"}
    }
    validator.expected_rows = 300
    validator.expected_month_rows = {"2022-01": 120, "2022-02": 180}
    validator.evidence = {"checks": {}}
    checkpoint, _ = state_at(output)
    before = committed_hashes(output, checkpoint["generation"])
    validator.verify_contents(checkpoint, state)
    assert committed_hashes(output, checkpoint["generation"]) == before
    content = validator.evidence["content"]
    assert content["rows"] == content["batch_reference_feature_rows"] == 300
    assert content["maximum_reference_segment_rows"] == 300
    assert content["internal_gap_minutes"] == 0
    assert len(content["month_boundaries"]) == 1
    assert all(check["status"] == "PASS" for check in validator.evidence["checks"].values())


def test_os_memory_high_water_is_available():
    value = process_memory()
    assert value["peak_rss_bytes"] >= value["rss_bytes"] > 0
