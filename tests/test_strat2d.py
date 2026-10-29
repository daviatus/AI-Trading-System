"""STRAT-2D synthetic chronology, model, calendar, accounting and crash regressions."""

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading.research.phase6.controller import digest
from trading.research.phase6.labels import LabelSpec
from trading.research.phase6.streaming import prepare_stream
from trading.research.strat2d import pipeline
from trading.research.strat2d.calendar import (
    Calendar,
    audit_batch,
    initial_quality,
    label_integrity,
)
from trading.research.strat2d.journal import Journal, committed_files, logical_hash
from trading.research.strat2d.model import OnlineModel
from trading.research.strat2d.portfolio import Portfolio


@pytest.fixture
def config():
    result = json.loads(Path("config/strat2d-eurusd-2022-engineering-v1.json").read_text())
    result["batch_rows"] = 32
    result["embargo_minutes"] = 2
    result["calendar"].update(timezone="UTC", weekly_open_seconds=[[0, 604800]], verified=True)
    result["quality_policy"] = "require_verified_calendar"
    result["portfolio"].update(
        minimum_quantity=1, quantity_step=1, maximum_quantity=10, horizon_minutes=3, threshold=0.34
    )
    return result


@pytest.fixture
def prepared(config, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    index = pd.date_range("2024-01-08T00:00:00Z", periods=240, freq="min", name="opened_at")
    price = 100 + 0.2 * np.sin(np.arange(240) / 4)
    bars = pd.DataFrame(
        {"open": price, "high": price + 0.05, "low": price - 0.05, "close": price, "volume": 1.0},
        index=index,
    )
    raw, source = tmp_path / "raw", tmp_path / "data/research/phase6-synthetic"
    path = raw / "cache/EURUSD/2024-01.parquet"
    path.parent.mkdir(parents=True)
    bars.reset_index().to_parquet(path, index=False)
    (raw / "snapshot.json").write_text(
        json.dumps({"parts": {"cache/EURUSD/2024-01.parquet": {"sha256": digest(path)}}})
    )
    metadata = {
        "specifications": {
            "scheduleTimeZone": "UTC",
            "schedule": [{"startSecond": 0, "endSecond": 604800}],
            "holiday": [],
        }
    }
    prepare_stream(
        raw,
        source,
        "EURUSD",
        index[0].isoformat(),
        (index[-1] + pd.Timedelta(minutes=1)).isoformat(),
        label_spec=LabelSpec(kind="forward", horizon_minutes=3, cost_bps=0),
        metadata=metadata,
        batch_rows=67,
    )
    config["calendar"]["evidence_fingerprint"] = logical_hash(metadata)
    config.update(
        start=index[0].isoformat(),
        train_end=index[80].isoformat(),
        calibration_end=index[160].isoformat(),
        test_end=(index[-1] + pd.Timedelta(minutes=1)).isoformat(),
        prepared=source.relative_to(tmp_path).as_posix(),
        output="data/research/strat2d-test-v1",
    )
    return config, source


def test_incremental_updates_roundtrip_and_batch_independence(config):
    model = OnlineModel(config["model"])
    x = [0.0001] * 5 + [50, 0.0001, 0.001]
    for number in range(10):
        model.fit_one(
            x,
            number % 3,
            f"2024-01-01T00:{number:02}:00+00:00",
            f"2024-01-01T00:{number + 1:02}:00+00:00",
            "2024-02-01T00:00:00+00:00",
        )
        model = OnlineModel(config["model"], json.loads(json.dumps(model.export())))
    other = OnlineModel(config["model"])
    for number in range(10):
        other.fit_one(
            x,
            number % 3,
            f"2024-01-01T00:{number:02}:00+00:00",
            f"2024-01-01T00:{number + 1:02}:00+00:00",
            "2024-02-01T00:00:00+00:00",
        )
    assert model.export() == other.export()
    np.testing.assert_array_equal(model.predict(x), other.predict(x))
    weights = copy.deepcopy(model.export()["weights"])
    model.calibrate_one(x, 1)
    assert model.export()["weights"] == weights


@pytest.mark.parametrize("kind", ["logistic", "trend", "tree", "xgboost", "unknown"])
def test_model_identity_never_silently_substituted(config, kind):
    config["model"]["kind"] = kind
    with pytest.raises(ValueError, match="Unsupported"):
        OnlineModel(config["model"])
    with pytest.raises(ValueError, match="Unsupported"):
        pipeline.validate_config(config)


def test_label_boundary_and_duplicate_training_updates_rejected(config):
    model = OnlineModel(config["model"])
    x = [0.0] * 8
    with pytest.raises(ValueError, match="boundary"):
        model.fit_one(x, 0, "2024-01-01", "2024-02-01", "2024-02-01")
    model.fit_one(x, 0, "2024-01-01", "2024-01-02", "2024-02-01")
    with pytest.raises(ValueError, match="chronological"):
        model.fit_one(x, 0, "2024-01-01", "2024-01-02", "2024-02-01")


@pytest.mark.parametrize(
    "key,value",
    [
        ("minimum_available_ram_gib", 1.25),
        ("free_floor_gib", 14),
        ("threads", 2),
        ("mode", "LIVE"),
        ("preparation_only", True),
        ("batch_rows", 2049),
        ("test_end", "2026-01-02T00:00:00Z"),
        ("train_end", "2023-01-01T00:00:00Z"),
    ],
)
def test_safety_validation_precedes_source_access(config, key, value):
    config[key] = value
    with pytest.raises(ValueError):
        pipeline.validate_config(config)


def test_no_annual_window(config):
    config.update(
        start="2022-01-01T00:00:00Z",
        train_end="2022-04-01T00:00:00Z",
        calibration_end="2022-07-01T00:00:00Z",
        test_end="2023-01-01T00:00:00Z",
    )
    with pytest.raises(ValueError, match="annual"):
        pipeline.validate_config(config)


def test_calendar_timezone_holiday_and_dst(config):
    rules = copy.deepcopy(config["calendar"])
    rules.update(
        timezone="America/New_York",
        holidays=[
            {
                "date": "2024-12-25",
                "timezone": "America/New_York",
                "start_minute": 0,
                "end_minute": 1440,
                "recurring": False,
            }
        ],
    )
    calendar = Calendar(rules)
    assert calendar.classify(pd.Timestamp("2024-12-25T15:00Z")) == "holiday"
    assert calendar.classify(pd.Timestamp("2024-12-26T15:00Z")) == "open"
    with pytest.raises(ValueError, match="UTC"):
        calendar.classify(pd.Timestamp("2024-01-01"))
    index = pd.DatetimeIndex([pd.Timestamp("2024-03-10T06:59Z"), pd.Timestamp("2024-03-10T07:00Z")])
    quality = initial_quality()
    audit_batch(pd.DataFrame(index=index), quality, calendar, index[0], [], 100)
    assert quality["dst_transitions"] == 1
    index = pd.DatetimeIndex([pd.Timestamp("2024-11-03T05:59Z"), pd.Timestamp("2024-11-03T06:01Z")])
    quality = initial_quality()
    audit_batch(pd.DataFrame(index=index), quality, calendar, index[0], [], 100)
    assert quality["dst_transitions"] == 1 and quality["internal_missing_minutes"] == 1


def test_weekend_session_break_and_gap_block_policy(config):
    rules = copy.deepcopy(config["calendar"])
    rules["weekly_open_seconds"] = [[86400 + 60, 86400 + 3600]]  # Monday 00:01..01:00 UTC.
    calendar = Calendar(rules)
    assert calendar.classify(pd.Timestamp("2024-01-07T12:00Z")) == "weekend"
    assert calendar.classify(pd.Timestamp("2024-01-08T00:00Z")) == "session_break"
    start = pd.Timestamp("2024-01-08T00:01Z")
    quality = initial_quality()
    audit_batch(
        pd.DataFrame(index=pd.DatetimeIndex([start, start + pd.Timedelta(minutes=3)])),
        quality,
        calendar,
        start,
        [],
        100,
    )
    assert quality["gap_count"] == 1 and quality["internal_missing_minutes"] == 2
    assert not label_integrity(
        start + pd.Timedelta(minutes=1), start + pd.Timedelta(minutes=4), quality
    )


@pytest.mark.parametrize("spread,slip,commission", [(0, 0, 0), (2, 1, 4)])
def test_next_bar_costs_accounting_and_drawdown(config, spread, slip, commission):
    spec = config["portfolio"]
    spec.update(
        spread_bps=spread, slippage_bps=slip, commission_bps_roundtrip=commission, horizon_minutes=1
    )
    portfolio = Portfolio(spec)
    stamp = pd.Timestamp("2024-01-08T00:01Z")
    events = []
    portfolio.signal(stamp, [0.1, 0.8, 0.1], 1.0, "model", True, events)
    assert portfolio.state["fills"] == 0
    portfolio.on_bar(
        stamp, {"open": 100.0, "low": 99.9, "high": 101.1, "close": 101.0}, True, events
    )
    entry, exit_price = portfolio.fill_price(100.0, 1), portfolio.fill_price(101.0, -1)
    expected = 10 * (exit_price - entry) - 10 * (entry + exit_price) * commission / 20000
    assert portfolio.state["trades"] == 1 and portfolio.state["fills"] == 2
    assert portfolio.state["cash"] == pytest.approx(10000 + expected)
    assert portfolio.state["realized_net"] == pytest.approx(expected)
    assert portfolio.state["unrealized"] == 0 and portfolio.state["maximum_drawdown"] > 0
    with pytest.raises(ValueError, match="Duplicate"):
        portfolio.on_bar(
            stamp, {"open": 100.0, "low": 99.9, "high": 101.1, "close": 101.0}, True, []
        )


def test_gap_censors_position_without_fabricating_fill(config):
    portfolio = Portfolio(config["portfolio"])
    stamp = pd.Timestamp("2024-01-08T00:01Z")
    events = []
    portfolio.signal(stamp, [0.1, 0.8, 0.1], 1.0, "model", True, events)
    portfolio.on_bar(
        stamp, {"open": 100.0, "low": 99.9, "high": 100.1, "close": 100.0}, True, events
    )
    assert portfolio.state["position"] and portfolio.state["trades"] == 0
    portfolio.on_bar(
        stamp + pd.Timedelta(minutes=3),
        {"open": 98.0, "low": 97.0, "high": 99.0, "close": 98.0},
        True,
        events,
    )
    assert portfolio.state["censored"] and portfolio.state["position"]
    assert portfolio.state["fills"] == 1 and portfolio.state["trades"] == 0
    assert portfolio.state["unrealized"] < 0


def test_same_bar_stop_wins_target(config):
    portfolio = Portfolio(config["portfolio"])
    stamp = pd.Timestamp("2024-01-08T00:01Z")
    events = []
    portfolio.signal(stamp, [0.1, 0.8, 0.1], 1.0, "model", True, events)
    portfolio.on_bar(
        stamp, {"open": 100.0, "low": 98.0, "high": 110.0, "close": 100.0}, True, events
    )
    assert next(e for e in events if e["event"] == "trade")["reason"] == "stop_loss"


def test_journal_logical_hash_corruption_and_manifest_rejection(tmp_path):
    manifest, state = {"kind": "synthetic"}, {"row": 0}
    journal = Journal(tmp_path, manifest, state)
    journal.commit({"row": 1}, [{"fill": 1}])
    assert Journal(tmp_path, manifest, state).state == {"row": 1}
    with pytest.raises(ValueError, match="mismatch"):
        Journal(tmp_path, {"kind": "changed"}, state)
    (tmp_path / "00000000/state.json").write_text('{"row":2}')
    with pytest.raises(ValueError, match="corrupt"):
        Journal(tmp_path, manifest, state)


@pytest.mark.parametrize("stage", ["before_commit", "after_commit"])
def test_pipeline_resume_matches_independent_artifact_and_logical_hashes(prepared, tmp_path, stage):
    config, source = prepared
    baseline = {
        p.relative_to(source).as_posix(): digest(p) for p in source.rglob("*") if p.is_file()
    }
    reference = pipeline.run(config, output="data/research/strat2d-reference", guard=lambda: None)
    quality_batches = sum(
        (json.loads(p.read_text())["rows"] + config["batch_rows"] - 1) // config["batch_rows"]
        for p in source.glob("chunks/*/receipt.json")
    )

    def interrupt(when, generation):
        if when == stage and generation == quality_batches + 3:
            raise InterruptedError("synthetic process loss during model update")

    with pytest.raises(InterruptedError):
        pipeline.run(config, guard=lambda: None, fault=interrupt)
    target = tmp_path / config["output"]
    recovered = Journal(target, json.loads((target / "manifest.json").read_text()), {})
    assert recovered.state["phase"] == "train" and recovered.state["model"]["train_rows"] > 0
    completed = pipeline.run(config, guard=lambda: None)
    assert completed["complete"] and completed["model"]["train_rows"] > 0
    assert (
        completed["model"]["calibration_rows"] > 0 and completed["metrics"]["test_labeled_rows"] > 0
    )
    assert logical_hash(completed) == logical_hash(reference)
    left, right = tmp_path / config["output"], tmp_path / "data/research/strat2d-reference"
    assert committed_files(left) == committed_files(right)
    before = committed_files(left)
    pipeline.run(config, guard=lambda: None)
    assert committed_files(left) == before
    assert baseline == {
        p.relative_to(source).as_posix(): digest(p) for p in source.rglob("*") if p.is_file()
    }


def test_future_test_labels_cannot_change_model_or_portfolio(prepared, monkeypatch):
    config, _ = prepared
    original = pipeline.run(config, guard=lambda: None)
    batches = pipeline.input_batches

    def changed(*args, **kwargs):
        for (bars, features, labels), chunk, offset in batches(*args, **kwargs):
            labels = labels.copy()
            eligible = (labels.index >= pd.Timestamp(config["calibration_end"])) & (
                labels.label >= 0
            )
            labels.loc[eligible, "label"] = (labels.loc[eligible, "label"] + 1) % 3
            yield (bars, features, labels), chunk, offset

    monkeypatch.setattr(pipeline, "input_batches", changed)
    mutated = pipeline.run(
        config, output="data/research/strat2d-mutated-test-labels", guard=lambda: None
    )
    assert original["model"] == mutated["model"]
    assert original["portfolio"] == mutated["portfolio"]
    assert original["metrics"] != mutated["metrics"]


def test_unverified_calendar_strict_policy_blocks_all_fitting(prepared):
    config, _ = prepared
    config["calendar"]["verified"] = False
    result = pipeline.run(config, guard=lambda: None)
    assert result["complete"] and result["status"] == "blocked_unverified_calendar"
    assert result["model"]["train_rows"] == result["portfolio"]["fills"] == 0


def test_incompatible_checkpoint_and_resource_stop_preserve_progress(prepared, tmp_path):
    config, _ = prepared
    partial = pipeline.run(config, guard=lambda: None, max_transactions=2)
    target = tmp_path / config["output"]
    before = committed_files(target)
    assert not partial["complete"]
    changed = copy.deepcopy(config)
    changed["model"]["learning_rate"] *= 2
    with pytest.raises(ValueError, match="mismatch"):
        pipeline.run(changed, guard=lambda: None)

    def stop():
        raise RuntimeError("synthetic resource stop")

    with pytest.raises(RuntimeError, match="resource"):
        pipeline.run(config, guard=stop)
    assert committed_files(target) == before


@pytest.mark.parametrize("name", ["state.json", "events.jsonl", "receipt.json"])
def test_each_committed_file_corruption_fails_closed(tmp_path, name):
    journal = Journal(tmp_path, {"test": True}, {})
    journal.commit({"model_updates": 1}, [{"fill_id": 1}])
    (tmp_path / "00000000" / name).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="corrupt"):
        Journal(tmp_path, {"test": True}, {})


def test_missing_transaction_is_not_silently_skipped(tmp_path):
    journal = Journal(tmp_path, {}, {})
    journal.commit({"rows": 1}, [])
    (tmp_path / "00000000").rename(tmp_path / "00000002")
    with pytest.raises(ValueError, match="missing transaction"):
        Journal(tmp_path, {}, {})


def test_calibration_boundary_and_seed_identity(config):
    model = OnlineModel(config["model"])
    with pytest.raises(ValueError, match="boundary"):
        model.calibrate_one([0.0] * 8, 1, "2024-01-01", "2024-02-01", "2024-02-01")
    state = model.export()
    state["seed"] += 1
    with pytest.raises(ValueError, match="seed"):
        OnlineModel(config["model"], state)


def test_margin_budget_includes_entry_commission(config):
    spec = config["portfolio"]
    spec.update(
        spread_bps=0,
        slippage_bps=0,
        commission_bps_roundtrip=1,
        quantity_step=0.001,
        minimum_quantity=0.001,
        maximum_quantity=100000,
    )
    portfolio = Portfolio(spec)
    stamp = pd.Timestamp("2024-01-08T00:01Z")
    portfolio.signal(stamp, [0.1, 0.8, 0.1], 1e-7, "model", True, [])
    # An exact flat bar keeps both directional barriers unresolved.
    portfolio.on_bar(stamp, {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}, True, [])
    assert portfolio.state["reserved_margin"] <= portfolio.state["cash"]


@pytest.mark.parametrize("bad", [float("nan"), -1.0])
def test_bad_portfolio_observation_cannot_mutate_state(config, bad):
    portfolio = Portfolio(config["portfolio"])
    before = copy.deepcopy(portfolio.state)
    with pytest.raises(ValueError, match="integrity"):
        portfolio.on_bar(
            pd.Timestamp("2024-01-08T00:01Z"),
            {"open": bad, "high": 101.0, "low": 99.0, "close": 100.0},
            True,
            [],
        )
    assert portfolio.state == before


def test_pending_order_cannot_fill_across_missing_next_bar(config):
    portfolio = Portfolio(config["portfolio"])
    stamp = pd.Timestamp("2024-01-08T00:01Z")
    portfolio.signal(stamp, [0.1, 0.8, 0.1], 1.0, "model", True, [])
    portfolio.on_bar(
        stamp + pd.Timedelta(minutes=1),
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0},
        True,
        [],
    )
    assert portfolio.state["fills"] == 0 and portfolio.state["pending"] is None


def test_training_resource_floor_is_not_preparation_exception(config, monkeypatch):
    from types import SimpleNamespace

    from trading.research import safety

    monkeypatch.setattr(
        safety.shutil, "disk_usage", lambda path: SimpleNamespace(free=40 * 1024**3)
    )
    monkeypatch.setattr(safety, "available_memory", lambda: int(1.49 * 1024**3))
    with pytest.raises(safety.ResearchSafetyStop, match="Available RAM"):
        pipeline.Monitor(config)()


def test_peak_memory_budget_is_enforced(config, monkeypatch):
    from types import SimpleNamespace

    from trading.research import safety
    from trading.research.phase6 import controller
    from trading.research.strat2d import runtime

    monkeypatch.setattr(
        safety.shutil, "disk_usage", lambda path: SimpleNamespace(free=40 * 1024**3)
    )
    monkeypatch.setattr(safety, "available_memory", lambda: 3 * 1024**3)
    monkeypatch.setattr(controller, "worker_rss_bytes", lambda pid: 100 * 1024**2)
    monkeypatch.setattr(
        runtime,
        "process_memory",
        lambda: {"rss_bytes": 100 * 1024**2, "peak_rss_bytes": 2 * 1024**3},
    )
    with pytest.raises(safety.ResearchSafetyStop, match="RSS budget"):
        pipeline.Monitor(config)()


def test_source_corruption_rejected_without_rewriting_checkpoint(prepared, tmp_path):
    config, source = prepared
    pipeline.run(config, guard=lambda: None, max_transactions=2)
    target = tmp_path / config["output"]
    before = committed_files(target)
    (source / "chunks/00000000/features.parquet").write_bytes(b"synthetic corruption")
    with pytest.raises(ValueError, match="corrupt"):
        pipeline.run(config, guard=lambda: None)
    assert committed_files(target) == before


def test_changed_calendar_requires_separately_prepared_features(prepared, tmp_path):
    config, _ = prepared
    config["calendar"]["weekly_open_seconds"] = [[60, 604800]]
    with pytest.raises(ValueError, match="calendar mismatch"):
        pipeline.run(config, guard=lambda: None)
    assert not (tmp_path / config["output"]).exists()


def test_pending_and_open_position_survive_checkpoint_without_duplicate_fill(config, tmp_path):
    spec = config["portfolio"]
    stamp = pd.Timestamp("2024-01-08T00:01Z")
    bar = {"open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0}
    p = Portfolio(spec)
    p.signal(stamp, [0.1, 0.8, 0.1], 1.0, "frozen-model", True, [])
    journal = Journal(tmp_path, {"test": "portfolio"}, {})
    journal.commit({"portfolio": p.state}, [])
    for number in range(3):
        recovered = Journal(tmp_path, {"test": "portfolio"}, {})
        p = Portfolio(spec, recovered.state["portfolio"])
        events = []
        p.on_bar(stamp + number * pd.Timedelta(minutes=1), bar, True, events)
        recovered.commit({"portfolio": p.state}, events)
    assert p.state["fills"] == 2 and p.state["trades"] == 1
    assert p.state["pending"] is p.state["position"] is None
    assert p.state["last_fill_id"] == 2 and p.state["last_trade_id"] == 1
