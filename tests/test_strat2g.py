"""Predeclared targets, causal features, stable models and immutable experiments."""

import copy
import json
import math
from pathlib import Path

import pandas as pd
import pytest
from test_strat2d import config, prepared  # noqa: F401

from trading.research.strat2d.calendar import Calendar
from trading.research.strat2d.journal import committed_files, logical_hash
from trading.research.strat2f.metrics import metric, update
from trading.research.strat2g import pipeline
from trading.research.strat2g.analysis import verify_events
from trading.research.strat2g.core import (
    Candidate,
    Features,
    phase_of,
    safe_observation,
    select,
    target,
)


@pytest.fixture
def protocol():
    return json.loads(Path("config/strat2g-protocol-v1.json").read_text())


def series(n=120):
    return [
        [1.1 + i * 0.00001, 1.1001 + i * 0.00001, 1.0999 + i * 0.00001, 1.10002 + i * 0.00001]
        for i in range(n)
    ]


def test_future_prices_do_not_change_features_and_state_is_bounded():
    a, b = Features(), Features()
    outputs = []
    for i, bar in enumerate(series()):
        x = a.push(i, bar)
        if i < 80:
            y = b.push(i, bar)
            assert x == y
            outputs.append(x)
    for i, bar in enumerate(series(50), 80):
        b.push(i, [v * 2 for v in bar])
    assert len(a.state["history"]) <= 61 and len(b.state["history"]) <= 61
    assert len(a.state["completed"]) == 2
    clone = Features(copy.deepcopy(a.state))
    assert a.push(120, series(121)[120]) == clone.push(120, series(121)[120])
    a.push(130, series()[0])
    assert len(a.state["history"]) == 1
    with pytest.raises(ValueError, match="Duplicate"):
        a.push(130, series()[0])


def test_multitimeframe_uses_only_fully_completed_five_minute_bars():
    f = Features()
    for i in range(2, 5):
        f.push(i, [1.0, 1.01, 0.99, 1.0])
    assert f.state["completed"] == []
    for i in range(5, 10):
        f.push(i, [1.0, 1.01, 0.99, 1.0])
    assert f.state["completed"] == [1.0]
    f.push(10, [1.0, 1.5, 0.99, 1.4])
    assert f.state["completed"] == [1.0]


@pytest.mark.parametrize("direction", [1, -1])
def test_cost_aware_target_price_units(protocol, direction):
    rows = [{"minute": i, "bar": [1.1, 1.1005, 1.0995, 1.1], "sigma": 0.0001} for i in range(31)]
    rows[5]["bar"][3] = 1.1 * (1 + direction * 0.0003)
    y, gross, neutral = target(rows, protocol["targets"][0])
    assert y == (1 if direction == 1 else 2)
    assert gross == pytest.approx(direction * 3.0) and neutral == 2.1
    rows[5]["bar"][3] = 1.1 * (1 + direction * 0.0001)
    assert target(rows, protocol["targets"][0])[0] == 0
    rows[3]["minute"] = 99
    with pytest.raises(ValueError, match="path"):
        target(rows, protocol["targets"][0])


def test_volatility_neutral_zone_and_purge_embargo(protocol):
    rows = [{"minute": i, "bar": [1.0, 1.01, 0.99, 1.001], "sigma": 0.001} for i in range(31)]
    y, gross, neutral = target(rows, protocol["targets"][2])
    assert neutral == pytest.approx(0.5 * 0.001 * math.sqrt(30) * 10000)
    assert y == 0
    boundary = int(pd.Timestamp("2022-02-15T00:00Z").timestamp()) // 60
    assert phase_of(boundary - 31, boundary, protocol) == "purged"
    assert phase_of(boundary + 59, boundary + 89, protocol) == "outside"
    assert phase_of(boundary + 60, boundary + 90, protocol) == "evaluation"


def test_conservative_session_boundaries(protocol):
    c = json.loads(Path("config/strat2d-eurusd-2022-offline-130-validation-v1.json").read_text())
    cal = Calendar(c["calendar"])
    assert not safe_observation(pd.Timestamp("2022-01-10T21:45Z"), cal)
    assert not safe_observation(pd.Timestamp("2022-01-07T21:00Z"), cal)
    assert not safe_observation(pd.Timestamp("2022-01-09T22:30Z"), cal)
    assert safe_observation(pd.Timestamp("2022-01-10T12:00Z"), cal)


@pytest.mark.parametrize("family", ["reference8", "linear12", "interaction16"])
def test_model_schema_stability_checkpoint_identity(protocol, family):
    base = json.loads(
        Path("config/strat2d-eurusd-2022-offline-130-validation-v1.json").read_text()
    )["model"]
    c = Candidate(family, "h5_cost", protocol, base)
    row = {"reference": [0.0001] * 5 + [50.0, 0.0001, 0.001], "new": [0.1] * 12}
    x = c.vector(row)
    for i in range(20):
        c.model.fit_one(
            x,
            i % 3,
            f"2022-01-03T00:{i:02}:00+00:00",
            f"2022-01-03T01:{i:02}:00+00:00",
            "2022-01-15T00:00:00+00:00",
        )
    restored = Candidate(family, "h5_cost", protocol, base, c.snapshot())
    assert c.model.predict(x).tolist() == restored.model.predict(x).tolist()
    assert sum(c.model.predict(x)) == pytest.approx(1.0)
    bad = copy.deepcopy(c.snapshot())
    bad["schema"] = "unknown"
    with pytest.raises(ValueError, match="Incompatible"):
        Candidate(family, "h5_cost", protocol, base, bad)


def test_selection_has_no_final_outcome_input_and_prefers_simple(protocol):
    p = copy.deepcopy(protocol)
    p["selection"].update(minimum_rows=1, minimum_observed_days=1)
    scores = {}
    for name in ["reference8", "linear12", "interaction16"]:
        m = metric()
        update(m, 0, [0.5, 0.25, 0.25])
        scores[name + "_cal"] = m
    assert select(scores, ["2022-02-02"], p) == "reference8"
    scores["linear12_cal"]["loss"] -= 0.004
    assert select(scores, ["2022-02-02"], p) == "reference8"
    scores["linear12_cal"]["loss"] -= 0.02
    assert select(scores, ["2022-02-02"], p) == "linear12"
    assert select(scores, [], p) is None


@pytest.mark.parametrize("stage", ["before_commit", "after_commit"])
def test_complete_matrix_interruption_replay_and_selection(prepared, protocol, tmp_path, stage):  # noqa: F811
    c, source = prepared
    c["train_end"] = "2024-01-08T01:40:00+00:00"
    c["portfolio"]["threshold"] = 0.55
    p = copy.deepcopy(protocol)
    p["scope"].update(start=c["start"], end_exclusive=c["test_end"])
    p["splits"] = {
        "train": [c["start"], c["train_end"]],
        "calibration": [c["train_end"], c["calibration_end"]],
        "selection": [c["calibration_end"], "2024-01-08T03:20:00+00:00"],
        "evaluation": ["2024-01-08T03:20:00+00:00", c["test_end"]],
    }
    p["embargo_minutes"] = 2
    p["selection"].update(minimum_rows=2, minimum_observed_days=1)
    ref = tmp_path / "data/research/strat2d-g-reference"
    out = tmp_path / "data/research/strat2d-g-resume"
    expected = pipeline.run(c, "data/research/strat2d-g-reference", lambda: None, protocol=p)

    def fault(when, g):
        if when == stage and g == 3:
            raise InterruptedError("synthetic failure")

    with pytest.raises(InterruptedError):
        pipeline.run(c, "data/research/strat2d-g-resume", lambda: None, fault, p)
    actual = pipeline.run(c, "data/research/strat2d-g-resume", lambda: None, protocol=p)
    assert logical_hash(actual) == logical_hash(expected)
    assert committed_files(out) == committed_files(ref)
    assert actual["max_history"] <= 61 and actual["max_pending"] <= 31
    assert verify_events(out, actual, p, lambda: None)["status"] == "PASS"
    before = committed_files(out)
    pipeline.run(c, "data/research/strat2d-g-resume", lambda: None, protocol=p)
    assert committed_files(out) == before
    changed = copy.deepcopy(p)
    changed["version"] = "incompatible"
    with pytest.raises(ValueError, match="mismatch"):
        pipeline.run(c, "data/research/strat2d-g-resume", lambda: None, protocol=changed)


def test_target_future_after_horizon_and_cost_sensitivity(protocol):
    from trading.research.strat2g.core import net_bps

    rows = [{"minute": i, "bar": [1.0, 1.01, 0.99, 1.001], "sigma": 0.001} for i in range(31)]
    before = target(rows, protocol["targets"][0])
    rows[30]["bar"][3] = 1.2
    assert target(rows, protocol["targets"][0]) == before
    assert net_bps(3.0, 1) == pytest.approx(0.9)
    assert net_bps(3.0, 2) == pytest.approx(-5.1)
    assert net_bps(3.0, 1, 3.1) == pytest.approx(-0.1)
    with pytest.raises(ValueError):
        net_bps(3.0, 0)


def test_cost_protocol_mismatch_stops_before_any_data_read(protocol):
    c = json.loads(Path("config/strat2d-eurusd-2022-offline-130-validation-v1.json").read_text())
    p = copy.deepcopy(protocol)
    p["costs"]["total_bps"] = 1.0
    with pytest.raises(ValueError, match="cost"):
        pipeline.run(c, "data/research/strat2d-g-never-written-v1", lambda: None, protocol=p)
