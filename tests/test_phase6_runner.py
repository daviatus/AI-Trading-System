"""Offline runner provenance, decision clocks and selected feature integration."""

import json

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from trading.research.phase6 import controller, runner
from trading.research.phase6.controller import digest
from trading.research.phase6.labels import LabelSpec, make_labels, purged_split
from trading.research.phase6.signals import generate_signals
from trading.research.safety import ResearchSafetyStop


def candles(n=8, start="2024-01-08T09:59:00Z"):
    index = pd.date_range(start, periods=n, freq="min", name="opened_at")
    return pd.DataFrame({"open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0}, index=index)


def register(source, frame, relative="cache/EURUSD/2024-01.parquet", extra=None):
    path = source / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.reset_index().to_parquet(path, index=False)
    parts = {relative: {"sha256": digest(path)}}
    parts.update(extra or {})
    (source / "snapshot.json").write_text(json.dumps({"parts": parts}), encoding="utf-8")
    return path


def loader_fold():
    return {"train_start": "2024-01-01T00:00:00Z", "test_end": "2025-01-01T00:00:00Z"}


def test_loader_reads_only_registered_development_asset_partitions(tmp_path, monkeypatch):
    source = tmp_path / "frozen-source"
    frame = candles()
    path = register(
        source,
        frame,
        extra={
            "cache/EURUSD/2026-01.parquet": {"sha256": "must-not-be-hashed"},
            "cache/XAUUSD/2024-01.parquet": {"sha256": "other-asset-must-not-be-read"},
        },
    )
    # An unregistered file lies inside the development date range. A glob-based
    # loader would try to parse it; the frozen manifest is the authority.
    unregistered = path.parent / "2024-02.parquet"
    unregistered.write_bytes(b"unregistered poison, not parquet")
    holdout = path.parent / "2026-01.parquet"
    holdout.write_bytes(b"holdout poison, never parse or hash")
    other_asset = source / "cache/XAUUSD/2024-01.parquet"
    other_asset.parent.mkdir()
    other_asset.write_bytes(b"other asset poison")
    opened, hashed = [], []
    original_parquet, original_digest = pq.ParquetFile, runner.digest

    def observed_parquet(candidate, *args, **kwargs):
        opened.append(candidate.resolve())
        return original_parquet(candidate, *args, **kwargs)

    def observed_digest(candidate):
        hashed.append(candidate.resolve())
        return original_digest(candidate)

    monkeypatch.setattr(pq, "ParquetFile", observed_parquet)
    monkeypatch.setattr(runner, "digest", observed_digest)
    monkeypatch.setattr(runner, "resources", lambda config: None)
    actual = runner.load_asset(source, "EURUSD", loader_fold(), {"max_rows_per_asset": 100})
    pd.testing.assert_frame_equal(actual, frame, check_freq=False)
    assert opened == [path.resolve()]
    assert hashed == [path.resolve()]
    assert holdout.read_bytes() == b"holdout poison, never parse or hash"
    assert unregistered.read_bytes() == b"unregistered poison, not parquet"


def test_registered_checksum_mismatch_fails_before_parquet_parse(tmp_path, monkeypatch):
    source = tmp_path / "frozen-source"
    path = register(source, candles())
    with path.open("ab") as handle:
        handle.write(b"changed-after-snapshot")
    monkeypatch.setattr(runner, "resources", lambda config: None)

    def forbidden_parse(*args, **kwargs):
        pytest.fail("Changed frozen evidence must fail before parsing")

    monkeypatch.setattr(pq, "ParquetFile", forbidden_parse)
    with pytest.raises(ValueError, match="provenance"):
        runner.load_asset(source, "EURUSD", loader_fold(), {"max_rows_per_asset": 100})
    assert path.read_bytes().endswith(b"changed-after-snapshot")


def test_row_budget_stops_before_history_concatenation(tmp_path, monkeypatch):
    source = tmp_path / "frozen-source"
    register(source, candles())
    monkeypatch.setattr(runner, "resources", lambda config: None)

    def forbidden_concat(*args, **kwargs):
        pytest.fail("Row budget must be checked before history concatenation")

    monkeypatch.setattr(pd, "concat", forbidden_concat)
    with pytest.raises(ResearchSafetyStop, match="row budget"):
        runner.load_asset(source, "EURUSD", loader_fold(), {"max_rows_per_asset": 4})


def test_loader_rejects_holdout_boundary_before_snapshot_read(tmp_path):
    nonexistent = tmp_path / "no-source-or-snapshot"
    fold = {"train_start": "2024-01-01T00:00:00Z", "test_end": "2026-01-01T00:01:00Z"}
    with pytest.raises(ValueError, match="2026"):
        runner.load_asset(nonexistent, "EURUSD", fold, {"max_rows_per_asset": 100})
    assert not nonexistent.exists()


def test_loader_does_not_invent_a_timezone_for_naive_source(tmp_path, monkeypatch):
    source = tmp_path / "frozen-source"
    frame = candles()
    frame.index = frame.index.tz_localize(None)
    register(source, frame)
    monkeypatch.setattr(runner, "resources", lambda config: None)
    with pytest.raises(ValueError, match="explicit source timezone"):
        runner.load_asset(source, "EURUSD", loader_fold(), {"max_rows_per_asset": 100})


def ready_features(index):
    return pd.DataFrame(
        {
            "atr_fraction_14": 0.001,
            "reference_close": 100.0,
            "volatility_ratio": 1.0,
            "ema_trend_spread": 0.002,
            "return_1": 0.001,
        },
        index=index,
    )


def signals(features, mask, *, feature_columns=None, diagnostic=None):
    return generate_signals(
        "EURUSD",
        features,
        np.tile([0.1, 0.8, 0.1], (len(features), 1)),
        mask,
        threshold=0.55,
        horizon=2,
        stop_multiple=2,
        profile=np.full((3, 2), 0.001),
        model_id="logistic",
        window="evaluation",
        feature_columns=feature_columns,
        diagnostic=diagnostic,
    )


def test_decision_clock_masks_apply_embargo_without_future_label_eligibility():
    source = candles()
    labels = make_labels(source, LabelSpec(horizon_minutes=2))
    fold = {
        "train_start": "2024-01-08T09:00:00Z",
        "train_end": "2024-01-08T09:30:00Z",
        "calibration_end": "2024-01-08T10:00:00Z",
        "tuning_end": "2024-01-08T10:04:00Z",
        "test_end": "2024-01-08T10:08:00Z",
    }
    tuning = runner._window_mask(labels.index, fold, "tuning", embargo=1)
    test = runner._window_mask(labels.index, fold, "test", embargo=1)
    np.testing.assert_array_equal(np.flatnonzero(tuning), [1, 2, 3])
    np.testing.assert_array_equal(np.flatnonzero(test), [5, 6, 7])
    purged = purged_split(labels.index, labels.label_end, fold, embargo_minutes=1)
    assert (test & ~purged["test"]).any()
    assert labels.label.iloc[7] == -1
    actual = signals(ready_features(labels.index), test)
    # The final current features are available even though a future label cannot
    # be observed at dataset end. Execution, rather than a target mask, resolves it.
    assert actual.time.tolist() == labels.index[test].tolist()
    assert labels.index[7] in actual.time.tolist()


def test_unselected_indicator_nans_do_not_reject_selected_model_features():
    index = pd.date_range("2024-01-08T10:00:00Z", periods=4, freq="min")
    features = ready_features(index)
    baseline = signals(features, np.ones(len(features), dtype=bool), feature_columns=["return_1"])
    features["unused_long_window_indicator"] = np.nan
    events = []
    selected = signals(
        features,
        np.ones(len(features), dtype=bool),
        feature_columns=["return_1"],
        diagnostic=events.append,
    )
    pd.testing.assert_frame_equal(selected, baseline)
    assert len(selected) == 4
    assert all(event["reason"] == "accepted" for event in events)
    features.loc[index[0], "return_1"] = np.nan
    events.clear()
    result = signals(
        features,
        np.ones(len(features), dtype=bool),
        feature_columns=["return_1"],
        diagnostic=events.append,
    )
    assert len(result) == 3
    assert events[0]["reason"] == "incomplete_features"


def test_causal_sizing_context_remains_required_with_feature_selection():
    index = pd.date_range("2024-01-08T10:00:00Z", periods=2, freq="min")
    features = ready_features(index)
    features.loc[index[0], "reference_close"] = np.nan
    result = signals(features, np.ones(len(features), dtype=bool), feature_columns=["return_1"])
    assert result.time.tolist() == [index[1]]


def test_code_manifest_includes_reused_calendar_execution_and_resource_dependencies():
    manifest = controller.code_manifest()
    dependencies = {
        "src/trading/research/data.py",
        "src/trading/research/execution.py",
        "src/trading/research/safety.py",
        "src/trading/ctrader/calendar.py",
        "src/trading/data/market_calendar.py",
    }
    assert dependencies <= set(manifest)
    for relative in dependencies:
        assert manifest[relative] == digest(controller.ROOT / relative)
