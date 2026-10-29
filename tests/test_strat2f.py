"""Causal admissions, metric oracles, calibration gradients and bounded replay."""

import copy
import json
import math
import random
from pathlib import Path

import numpy as np
import pytest
from test_strat2d import config, prepared  # noqa: F401

from trading.research.strat2d import pipeline as foundation
from trading.research.strat2d.journal import committed_files, logical_hash
from trading.research.strat2d.model import OnlineModel
from trading.research.strat2f import pipeline
from trading.research.strat2f.analysis import block_indices, uncertainty
from trading.research.strat2f.metrics import (
    MODELS,
    THRESHOLDS,
    baselines,
    feature_stat,
    feature_update,
    metric,
    split_reason,
    summarize,
    threshold_stat,
    threshold_update,
    update,
)


def test_metric_independent_closed_form_oracles():
    m = metric()
    for y in range(3):
        update(m, y, [1 / 3] * 3)
    s = summarize(m)
    assert s["log_loss"] == pytest.approx(math.log(3))
    assert s["brier"] == pytest.approx(2 / 3)
    assert s["balanced_accuracy"] == pytest.approx(1 / 3)
    assert s["ece_top_label"] == pytest.approx(0)
    assert s["confusion_matrix"] == [[1, 0, 0], [1, 0, 0], [1, 0, 0]]
    assert s["precision"][1] is None
    perfect = metric()
    for y in range(3):
        update(perfect, y, [float(i == y) for i in range(3)])
    assert summarize(perfect)["brier"] == 0
    assert summarize(perfect)["balanced_accuracy"] == 1
    assert sum(b["n"] for b in summarize(perfect)["reliability"]) == 3


@pytest.mark.parametrize("p", [[0.5, 0.5, 0.5], [float("nan"), 0, 1], [-1, 1, 1], [1, 0]])
def test_invalid_probability_fails_closed(p):
    with pytest.raises(ValueError):
        update(metric(), 1, p)


@pytest.mark.parametrize("momentum,direction,inverse", [(1.0, 1, 2), (-1.0, 2, 1), (0.0, 0, 0)])
def test_training_only_baselines(momentum, direction, inverse):
    b = baselines([6, 3, 1], momentum)
    assert b["prior"] == [0.6, 0.3, 0.1]
    assert b["majority"] == [1.0, 0.0, 0.0]
    assert b["momentum"][direction] == 1
    assert b["mean_reversion"][inverse] == 1
    assert all(sum(p) == pytest.approx(1) for p in b.values())


def test_calibration_gradient_and_stable_softmax(config):  # noqa: F811
    model = OnlineModel(config["model"])
    x = [0.0001] * 5 + [50.0, 0.0001, 0.001]
    model.weights[:] = np.arange(27).reshape(9, 3) / 50
    model.state["log_temperature"] = 0.2
    z = model.vector(x) @ model.weights
    t = 0.2

    def objective(a):
        p = model.softmax(z / math.exp(a))
        return -math.log(p[1])

    numeric = (objective(t + 1e-5) - objective(t - 1e-5)) / 2e-5
    scaled = z / math.exp(t)
    p = model.softmax(scaled)
    analytic = float(scaled[1] - p @ scaled)
    assert analytic == pytest.approx(numeric, abs=1e-9)
    model.calibrate_one(x, 1)
    assert model.state["log_temperature"] == pytest.approx(t - 0.005 * analytic)
    assert np.isfinite(model.softmax(np.array([10000.0, -10000.0, 0.0]))).all()
    assert model.softmax(np.array([10000.0, -10000.0, 0.0])).sum() == pytest.approx(1)


def test_purge_embargo_and_label_alignment_admission():
    assert split_reason(10, 20, True, True, True, True, 10, 20) == "label_boundary"
    assert split_reason(9, 15, True, True, True, True, 10, 20) == "outside_window"
    assert split_reason(10, 19, True, True, True, True, 10, 20) == "eligible"
    assert split_reason(10, 19, True, True, True, False, 10, 20) == "quality_block"


def test_feature_statistics_bound_and_normalization():
    s = feature_stat()
    for v in [1.0, 2.0, 3.0]:
        feature_update(s, v, 1.0)
    assert s["mean"] == 2.0 and s["m2"] == 2.0
    feature_update(s, float("nan"), 1.0)
    assert s["missing"] == 1
    for _ in range(20000):
        feature_update(s, 21.0, 1.0)
    assert s["clipped"] == 20000
    assert len(s["normalized_histogram"]) == 12
    assert sum(s["normalized_histogram"]) == s["n"]


def test_threshold_net_costs_and_causal_cooldown():
    s = threshold_stat()
    threshold_update(s, 1, 1, 1.0, 0, 15)
    threshold_update(s, 2, 0, -3.0, 1, 15)
    threshold_update(s, 2, 2, 2.0, 15, 15)
    assert s["n"] == 3 and s["correct"] == 2
    assert s["gross_bps"] == pytest.approx(6.3)
    assert s["net_bps"] == 0 and s["spaced_signals"] == 2
    assert s["next_spaced"] == 30


def test_day_block_bootstrap_preserves_within_block_and_is_deterministic():
    idx = block_indices(10, 2, random.Random(8))
    assert all(idx[i + 1] == (idx[i] + 1) % 10 for i in range(0, 10, 2))
    days = {}
    for i in range(6):
        m = metric(False)
        for y in range(3):
            update(m, y, [0.5, 0.25, 0.25])
        days[str(i)] = {
            "models": {name: copy.deepcopy(m) for name in MODELS},
            "thresholds": {str(t): threshold_stat() for t in THRESHOLDS},
        }
    a = uncertainty(days, 7, repetitions=30)
    assert a == uncertainty(days, 7, repetitions=30)
    assert a["2"]["paired_differences"]["softmax_cal-prior"]["log_loss"]["lower"] == 0
    assert a["2"]["thresholds"]["0.55"]["precision"]["valid_replicates"] == 0


@pytest.mark.parametrize("stage", ["before_commit", "after_commit"])
def test_full_diagnostics_resume_and_reconstruction(prepared, tmp_path, stage):  # noqa: F811
    c, source = prepared
    foundation_output = "data/research/strat2d-f-base"
    old = foundation.run(c, output=foundation_output, guard=lambda: None)
    prior = tmp_path / foundation_output
    reference = tmp_path / "data/research/strat2d-f-reference"
    expected = pipeline.run(c, "data/research/strat2d-f-reference", lambda: None, prior=prior)
    target = tmp_path / "data/research/strat2d-f-resumed"

    def fault(when, generation):
        if when == stage and generation == 3:
            raise InterruptedError("synthetic failure")

    with pytest.raises(InterruptedError):
        pipeline.run(c, "data/research/strat2d-f-resumed", lambda: None, fault, prior)
    actual = pipeline.run(c, "data/research/strat2d-f-resumed", lambda: None, prior=prior)
    assert actual["complete"] and logical_hash(actual) == logical_hash(expected)
    assert actual["model"] == old["model"]
    assert committed_files(target) == committed_files(reference)
    before = committed_files(target)
    pipeline.run(c, "data/research/strat2d-f-resumed", lambda: None, prior=prior)
    assert committed_files(target) == before
    assert len(actual["days"]) <= 64 and len(actual["trace"]) <= 64


@pytest.mark.parametrize(
    "key,value", [("cost_bps", 1.0), ("horizon_minutes", 30), ("kind", "forward")]
)
def test_economic_label_identity_cannot_silently_change(key, value):
    c = json.loads(Path("config/strat2d-eurusd-2022-offline-130-validation-v1.json").read_text())
    labels = {
        "kind": "barrier",
        "horizon_minutes": 15,
        "stop_multiple": 2.0,
        "reward_risk": 3.0,
        "cost_bps": 2.1,
    }
    pipeline.validate_label_policy(c, labels)
    labels[key] = value
    with pytest.raises(ValueError, match="policy"):
        pipeline.validate_label_policy(c, labels)
