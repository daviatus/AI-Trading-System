"""Bounded synthetic streaming fixtures; no historical datasets or services."""

import json
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from trading.research.phase6 import runner, streaming
from trading.research.phase6.controller import digest, experiment_queue
from trading.research.phase6.features import FeatureSpec, make_features
from trading.research.phase6.labels import LabelSpec, make_labels, purged_split


def candles(n=240, start="2024-01-31T22:00:00Z"):
    index = pd.date_range(start, periods=n, freq="min", name="opened_at")
    x = np.arange(n)
    close = 100 + 0.1 * np.sin(x / 7) + x * 0.0003
    return pd.DataFrame(
        {
            "open": close - 0.002,
            "high": close + 0.03,
            "low": close - 0.03,
            "close": close,
            "volume": x + 1.0,
        },
        index=index,
    )


def register(source, bars, extra=None):
    source.mkdir(exist_ok=True)
    parts = dict(extra or {})
    for month, frame in bars.groupby(bars.index.strftime("%Y-%m")):
        relative = f"cache/EURUSD/{month}.parquet"
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.reset_index().to_parquet(path, index=False, row_group_size=29)
        parts[relative] = {"sha256": digest(path)}
    (source / "snapshot.json").write_text(json.dumps({"parts": parts}), encoding="utf-8")


def prepare(source, output, bars, **kwargs):
    return streaming.prepare_stream(
        source,
        output,
        "EURUSD",
        bars.index[0].isoformat(),
        (bars.index[-1] + pd.Timedelta(minutes=1)).isoformat(),
        **kwargs,
    )


def read(output):
    chunks = list(streaming.iter_prepared(output))
    return [
        pd.concat([getattr(c, name) for c in chunks]) for name in ("bars", "features", "labels")
    ]


@pytest.mark.parametrize("batch_rows", [1, 17, 67, 8192])
@pytest.mark.parametrize("kind", ["forward", "volatility", "barrier"])
def test_chunk_and_month_edges_match_batch_reference(tmp_path, batch_rows, kind):
    bars = candles(150, "2024-01-31T22:45:00Z")
    source, output = tmp_path / "source", tmp_path / "stream"
    register(source, bars)
    spec = LabelSpec(kind=kind, horizon_minutes=7)
    state = prepare(source, output, bars, batch_rows=batch_rows, label_spec=spec)
    actual_bars, features, labels = read(output)
    expected = make_features(bars)
    sigma = expected["atr_fraction_14" if kind == "barrier" else "volatility_20"]
    expected_labels = make_labels(bars, spec, volatility=sigma)
    assert state["complete"] and state["rows_read"] == state["rows_written"] == len(bars)
    pd.testing.assert_frame_equal(actual_bars, bars, check_freq=False)
    pd.testing.assert_frame_equal(features, expected, check_freq=False, atol=1e-7, rtol=1e-5)
    pd.testing.assert_frame_equal(labels, expected_labels, check_freq=False, atol=1e-7, rtol=1e-5)
    assert features.index.is_unique and features.index.is_monotonic_increasing


def test_restart_and_repeated_resume_are_byte_identical(tmp_path):
    bars = candles()
    source = tmp_path / "source"
    register(source, bars)
    direct, resumed = tmp_path / "direct", tmp_path / "resumed"
    prepare(source, direct, bars, batch_rows=19)
    for _ in range(100):
        state = prepare(source, resumed, bars, batch_rows=19, max_chunks=1)
        if state["complete"]:
            break
    assert state["complete"]
    expected = {
        p.relative_to(direct).as_posix(): digest(p) for p in direct.rglob("*") if p.is_file()
    }
    actual = {
        p.relative_to(resumed).as_posix(): digest(p) for p in resumed.rglob("*") if p.is_file()
    }
    assert actual == expected
    times = {p: p.stat().st_mtime_ns for p in resumed.rglob("*") if p.is_file()}
    prepare(source, resumed, bars, batch_rows=19)
    assert all(p.stat().st_mtime_ns == modified for p, modified in times.items())


@pytest.mark.parametrize("stage", ["parquet", "state", "rename", "checkpoint", "checkpoint_after"])
def test_crash_windows_recover_without_duplicate_output(tmp_path, monkeypatch, stage):
    bars = candles(110)
    source, output, reference = tmp_path / "source", tmp_path / "stream", tmp_path / "reference"
    register(source, bars)
    prepare(source, reference, bars, batch_rows=23)
    prepare(source, output, bars, batch_rows=23, max_chunks=1)
    original = {
        "parquet": pd.DataFrame.to_parquet,
        "state": streaming._write,
        "rename": streaming.os.rename,
        "checkpoint": streaming._checkpoint,
        "checkpoint_after": streaming._checkpoint,
    }[stage]

    def crash(*args, **kwargs):
        if stage in {"parquet", "rename", "checkpoint_after"}:
            original(*args, **kwargs)
        if stage == "state" and args[0].name != "state.json":
            return original(*args, **kwargs)
        raise InterruptedError("synthetic power interruption")

    with monkeypatch.context() as patch:
        if stage == "parquet":
            patch.setattr(pd.DataFrame, "to_parquet", crash)
        elif stage == "rename":
            patch.setattr(streaming.os, "rename", crash)
        else:
            patch.setattr(
                streaming,
                {"state": "_write", "checkpoint": "_checkpoint", "checkpoint_after": "_checkpoint"}[
                    stage
                ],
                crash,
            )
        with pytest.raises(InterruptedError):
            prepare(source, output, bars, batch_rows=23)
    state = prepare(source, output, bars, batch_rows=23)
    assert state["complete"]
    for actual, expected in zip(read(output), read(reference), strict=True):
        pd.testing.assert_frame_equal(actual, expected)
    assert len(read(output)[0]) == len(bars)
    # Partial directories are preserved but never visible through the reader.
    if stage != "checkpoint_after":
        assert list((output / "chunks").glob(".pending-*"))


@pytest.mark.parametrize(
    "target", ["checkpoint", "bars", "state", "receipt", "manifest", "missing_checkpoint"]
)
def test_corruption_fails_closed_and_preserves_evidence(tmp_path, target):
    bars = candles(90)
    source, output = tmp_path / "source", tmp_path / "stream"
    register(source, bars)
    prepare(source, output, bars, batch_rows=20, max_chunks=1)
    if "checkpoint" in target:
        path = output / "stream-checkpoint.json"
    elif target == "manifest":
        path = output / "stream-manifest.json"
    else:
        path = (
            output
            / "chunks/00000000"
            / {
                "bars": "bars.parquet",
                "state": "state.json",
                "receipt": "receipt.json",
            }[target]
        )
    if target == "missing_checkpoint":
        path.rename(output / "preserved-checkpoint.json")
    else:
        path.write_bytes(b"corrupted synthetic evidence")
    evidence = {p: p.read_bytes() for p in output.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="checkpoint|corrupt"):
        prepare(source, output, bars, batch_rows=20)
    assert all(p.read_bytes() == value for p, value in evidence.items())


def test_gaps_and_missing_month_reset_features_and_invalidate_labels(tmp_path):
    bars = pd.concat(
        [
            candles(100, "2024-01-31T22:20:00Z"),
            candles(100, "2024-03-01T00:00:00Z"),
        ]
    )
    bars = bars.drop(bars.index[70:73])
    source, output = tmp_path / "source", tmp_path / "stream"
    register(source, bars)
    spec = LabelSpec(horizon_minutes=5)
    state = prepare(source, output, bars, batch_rows=37, label_spec=spec)
    observed, features, labels = read(output)
    expected = make_features(bars)
    pd.testing.assert_frame_equal(features, expected, check_freq=False, atol=1e-7, rtol=1e-5)
    pd.testing.assert_frame_equal(labels, make_labels(bars, spec), check_freq=False)
    assert state["gap_minutes"] > 28 * 1440
    assert len(observed) == len(bars)
    assert features.loc["2024-03-01T00:01:00Z"].isna().all()


def test_future_perturbation_cannot_change_features_or_purged_training(tmp_path):
    bars = candles()
    changed = bars.copy()
    changed.loc[changed.index[150] :, ["open", "high", "low", "close"]] *= 1.1
    outputs = []
    for name, data in (("original", bars), ("changed", changed)):
        source, output = tmp_path / f"source-{name}", tmp_path / name
        register(source, data)
        prepare(source, output, data, batch_rows=31, label_spec=LabelSpec(horizon_minutes=7))
        outputs.append(read(output))
    pd.testing.assert_frame_equal(outputs[0][1].iloc[:150], outputs[1][1].iloc[:150])
    fold = dict(
        zip(
            ("train_start", "train_end", "calibration_end", "tuning_end", "test_end"),
            [bars.index[i].isoformat() for i in (0, 150, 170, 190, 239)],
            strict=True,
        )
    )
    labels = outputs[0][2]
    masks = purged_split(labels.index, labels.label_end, fold, embargo_minutes=2)
    assert (labels.loc[masks["train"], "label_end"] < bars.index[150]).all()
    pd.testing.assert_frame_equal(
        outputs[0][2].loc[masks["train"]], outputs[1][2].loc[masks["train"]]
    )


@pytest.mark.parametrize("change", ["config", "source", "code", "calendar"])
def test_resume_rejects_changed_identity(tmp_path, monkeypatch, change):
    bars = candles()
    source, output = tmp_path / "source", tmp_path / "stream"
    register(source, bars)
    prepare(source, output, bars, max_chunks=1)
    kwargs = {}
    if change == "config":
        kwargs["batch_rows"] = 123
    elif change == "source":
        register(source, bars * 2)
    elif change == "code":
        monkeypatch.setattr(streaming, "code_manifest", lambda: {"changed": "code"})
    else:
        kwargs["metadata"] = {"changed": "calendar"}
    with pytest.raises(ValueError, match="changed"):
        prepare(source, output, bars, **kwargs)


def test_holdout_and_unregistered_files_are_never_opened_or_hashed(tmp_path, monkeypatch):
    bars = candles(20)
    source, output = tmp_path / "source", tmp_path / "stream"
    register(source, bars, {"cache/EURUSD/2026-01.parquet": {"sha256": "poison"}})
    poison = source / "cache/EURUSD/2026-01.parquet"
    poison.write_bytes(b"never access")
    original = streaming.digest

    def guarded(path):
        assert path != poison
        return original(path)

    monkeypatch.setattr(streaming, "digest", guarded)
    prepare(source, output, bars)
    with pytest.raises(ValueError, match="2026"):
        streaming.prepare_stream(
            tmp_path / "nonexistent",
            tmp_path / "unsafe",
            "EURUSD",
            "2025-12-01T00:00:00Z",
            "2026-01-01T00:01:00Z",
        )
    assert not (tmp_path / "unsafe").exists()


@pytest.mark.parametrize(
    "malformation", ["duplicate", "reversed", "naive", "invalid", "wrong_month"]
)
def test_invalid_source_fails(tmp_path, malformation):
    bars = candles(40)
    source, output = tmp_path / "source", tmp_path / "stream"
    bad = bars.copy()
    if malformation == "duplicate":
        bad.index = pd.DatetimeIndex([*bad.index[:20], *bad.index[19:39]], name="opened_at")
    elif malformation == "reversed":
        bad = bad.iloc[::-1]
    elif malformation == "naive":
        bad.index = bad.index.tz_localize(None)
    elif malformation == "invalid":
        bad.iloc[20, bad.columns.get_loc("high")] = 0
    if malformation == "wrong_month":
        register(source, bars)
        path = source / "cache/EURUSD/2024-01.parquet"
        bad.index += pd.Timedelta(days=31)
        bad.reset_index().to_parquet(path, index=False)
        (source / "snapshot.json").write_text(
            json.dumps({"parts": {"cache/EURUSD/2024-01.parquet": {"sha256": digest(path)}}})
        )
    else:
        register(source, bad)
    with pytest.raises(ValueError):
        prepare(source, output, bars, batch_rows=20)


def test_state_size_is_bounded_by_periods_and_horizon(tmp_path):
    bars = candles(1000)
    source, output = tmp_path / "source", tmp_path / "stream"
    register(source, bars)
    state = prepare(source, output, bars, batch_rows=47, label_spec=LabelSpec(horizon_minutes=11))
    assert len(state["features"]["history"]["data"]) <= 80
    for path in output.glob("chunks/*/state.json"):
        chunk = json.loads(path.read_text())
        if chunk["pending_bars"]:
            assert len(chunk["pending_bars"]["data"]) <= 11
        assert path.stat().st_size < 100_000


def test_prepare_runner_uses_bounded_adapter_and_does_not_execute(tmp_path, monkeypatch):
    config = json.loads((runner.ROOT / "config/phase6a-research-v1.json").read_text())
    source = tmp_path / "source"
    source.mkdir()
    (source / "contracts.json").write_text(json.dumps({a: {} for a in config["assets"]}))
    monkeypatch.setattr(runner, "validate_config", lambda config: (tmp_path / "output", source))
    monkeypatch.setattr(runner, "resources", lambda config: None)
    calls = []

    def adapter(*args, **kwargs):
        calls.append((args, kwargs))
        return {"complete": False}

    monkeypatch.setattr(streaming, "prepare_stream", adapter)
    result = runner.prepare_job(config, experiment_queue(config)[0], batch_rows=50, max_chunks=2)
    assert len(result) == len(calls) == 3
    assert all(k["batch_rows"] == 50 and k["max_chunks"] == 2 for _, k in calls)


def test_feature_engine_sessions_and_reliable_volume_match_reference():
    bars = candles(240, "2024-01-08T00:00:00Z")
    metadata = {
        "specifications": {
            "scheduleTimeZone": "UTC",
            "schedule": [
                {"startSecond": 86400, "endSecond": 86400 + 95 * 60},
                {"startSecond": 86400 + 100 * 60, "endSecond": 2 * 86400},
            ],
            "holiday": [],
        }
    }
    spec = replace(FeatureSpec(), reliable_volume=True)
    engine = streaming.CausalFeatures(spec, metadata)
    actual = pd.concat([engine.transform(bars.iloc[i : i + 23]) for i in range(0, len(bars), 23)])
    pd.testing.assert_frame_equal(
        actual, make_features(bars, spec, metadata), check_freq=False, atol=1e-7, rtol=1e-5
    )


def test_source_checksum_and_missing_registered_file_stop_before_output(tmp_path):
    bars = candles(40)
    source, output = tmp_path / "source", tmp_path / "stream"
    register(source, bars)
    path = source / "cache/EURUSD/2024-01.parquet"
    with path.open("ab") as handle:
        handle.write(b"changed")
    with pytest.raises(ValueError, match="provenance"):
        prepare(source, output, bars)
    assert not output.exists()
    path.rename(source / "preserved-source.parquet")
    with pytest.raises(FileNotFoundError):
        prepare(source, output, bars)
    assert not output.exists()


def test_writer_lock_and_guard_prevent_progress(tmp_path):
    bars = candles(40)
    source, output = tmp_path / "source", tmp_path / "stream"
    register(source, bars)
    with streaming.controller_lock(output, lock_name=".stream.lock"):
        with pytest.raises(RuntimeError, match="active"):
            prepare(source, output, bars)
    assert not (output / "stream-checkpoint.json").exists()

    def stop():
        raise RuntimeError("synthetic resource stop")

    with pytest.raises(RuntimeError, match="resource stop"):
        prepare(source, output, bars, guard=stop)
    assert not (output / "stream-checkpoint.json").exists()


def test_reader_ignores_published_but_uncommitted_chunks(tmp_path, monkeypatch):
    bars = candles(120)
    source, output = tmp_path / "source", tmp_path / "stream"
    register(source, bars)
    prepare(source, output, bars, batch_rows=40, max_chunks=1)
    before = read(output)

    def stop(*args, **kwargs):
        raise InterruptedError("before checkpoint")

    with monkeypatch.context() as patch:
        patch.setattr(streaming, "_checkpoint", stop)
        with pytest.raises(InterruptedError):
            prepare(source, output, bars, batch_rows=40)
    for actual, expected in zip(read(output), before, strict=True):
        pd.testing.assert_frame_equal(actual, expected)
    orphan = output / "chunks/00000001/bars.parquet"
    orphan.write_bytes(b"damaged orphan")
    with pytest.raises(ValueError, match="corrupt"):
        prepare(source, output, bars, batch_rows=40)
    assert orphan.read_bytes() == b"damaged orphan"


@pytest.mark.parametrize("batch_rows", [0, -1, 8193, True])
def test_batch_budget_rejected_before_source_or_output(tmp_path, batch_rows):
    with pytest.raises(ValueError, match="budget"):
        prepare(tmp_path / "no-source", tmp_path / "no-output", candles(1), batch_rows=batch_rows)
    assert not (tmp_path / "no-output").exists()


@pytest.mark.parametrize("missing", ["column", "values"])
def test_missing_volume_and_short_terminal_tail_survive_restart(tmp_path, missing):
    bars = candles(25)
    if missing == "column":
        bars = bars.drop(columns="volume")
    else:
        bars["volume"] = np.nan
    source, output, reference = tmp_path / "source", tmp_path / "stream", tmp_path / "reference"
    register(source, bars)
    prepare(source, reference, bars, batch_rows=11)
    prepare(source, output, bars, batch_rows=11, max_chunks=1)
    state = prepare(source, output, bars, batch_rows=11)
    assert state["rows_written"] == 25 and state["complete"]
    for actual, expected in zip(read(output), read(reference), strict=True):
        pd.testing.assert_frame_equal(actual, expected)
    observed, _, labels = read(output)
    assert observed.volume.isna().all()
    assert labels.label.eq(-1).all() and labels.label_end.isna().all()


def test_infinite_volume_fails_before_chunk_publication(tmp_path):
    bars = candles(40)
    bars["volume"] = np.inf
    source, output = tmp_path / "source", tmp_path / "stream"
    register(source, bars)
    with pytest.raises(ValueError, match="Infinite volume"):
        prepare(source, output, bars)
    assert not (output / "chunks").exists()
