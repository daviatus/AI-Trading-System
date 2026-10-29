"""Frozen model, movement units, bounded diagnostics and durable evidence regressions."""

import copy
import json
import math
from pathlib import Path

import pandas as pd
import pytest
from test_strat2d import config as shared_config
from test_strat2d import prepared as shared_prepared

from trading.research.strat2d.journal import Journal, committed_files, logical_hash
from trading.research.strat2d.pipeline import source_manifest
from trading.research.strat2g.core import Candidate, Features, phase_of, safe_observation
from trading.research.strat2h import analysis, pipeline
from trading.research.strat2h.statistics import (
    add_group,
    add_movement,
    describe,
    directed,
    group,
    movement,
    summary,
)

config = shared_config
prepared = shared_prepared


@pytest.fixture
def protocol():
    return json.loads(Path("config/strat2h-diagnostics-protocol-v1.json").read_text())


def event(minute=0, p=(0.2, 0.7, 0.1)):
    return {
        "decision": minute,
        "phase": "evaluation",
        "label": 1,
        "gross_bps": 3.0,
        "entry_price": 1.0,
        "exit_price": 1.0003,
        "path_high": 1.0005,
        "path_low": 0.9999,
        "sigma": 0.0001,
        "features": [0.0] * 12,
        "history_since_reset": 90,
        "probabilities": {v: list(p) for v in ("raw", "cal", "reference", "prior")},
    }


def test_units_mfe_mae_costs_and_volatility_scaling():
    e = event()
    long, short = directed(e, 1), directed(e, 2)
    assert long[:3] == pytest.approx((3.0, 5.0, 1.0))
    assert short[:3] == pytest.approx((-3.0, 1.0, 5.0))
    assert long[3] == pytest.approx(3 / math.sqrt(15))
    s = movement()
    add_movement(s, *long, True)
    d = describe(s, [1.1, 2.1, 3.1, 5.6])
    assert d["net_mean_bps_by_cost"]["2.1"] == pytest.approx(0.9)
    assert d["net_mean_bps_by_cost"]["5.6"] == pytest.approx(-2.6)
    assert d["label_precision"] == 1
    with pytest.raises(ValueError, match="Nonfinite"):
        add_movement(s, float("nan"), 0, 0, 0, False)


def test_bins_gate_clustering_and_no_artificial_orders(protocol):
    s = group(protocol)
    for minute in (0, 1, 15, 40):
        add_group(s, event(minute), protocol)
    d = summary(s, protocol)
    t = d["thresholds"]["0.55"]
    assert t["n"] == 4 and t["clusters"] == 2 and t["nonoverlap_count"] == 3
    assert t["long"] == 4 and t["short"] == 0
    assert d["views"]["cal"]["direction_probability_bins"][14]["n"] == 4
    add_group(s, event(41, (0.6, 0.35, 0.05)), protocol)
    assert summary(s, protocol)["thresholds"]["0.3"]["n"] == 4
    assert summary(s, protocol)["views"]["cal"]["forced_direction"]["n"] == 5


def test_raw_calibration_class_counts_and_probability_validation(protocol):
    s = group(protocol)
    add_group(s, event(), protocol)
    m = summary(s, protocol)["metrics"]["cal"]
    assert m["class_counts"] == [0, 1, 0]
    assert m["log_loss"] == pytest.approx(-math.log(0.7))
    assert m["brier"] == pytest.approx(0.14)
    assert m["class_reliability"][1][7]["observed_fraction"] == 1
    with pytest.raises(ValueError, match="probabilities"):
        add_group(s, event(0, (0.2, 0.7, 0.2)), protocol)


@pytest.mark.parametrize(
    "field,value",
    [
        ("family", "interaction16"),
        ("target", "h5_cost"),
        ("horizon_minutes", 30),
        ("training_rows", 1),
        ("candidate_sha256", "0" * 64),
    ],
)
def test_frozen_candidate_identity_rejects_mismatch(protocol, field, value):
    from trading.research.strat2g.resources import LEGACY

    base = json.loads(LEGACY.read_text())
    g = json.loads(Path("config/strat2g-protocol-v1.json").read_text())
    saved = Candidate("linear12", "h15_cost", g, base["model"]).snapshot()
    identity = copy.deepcopy(protocol["frozen"])
    identity.update(
        candidate_sha256=logical_hash(saved),
        training_rows=0,
        calibration_rows=0,
        log_temperature=0.0,
    )
    pipeline.check_candidate(saved, identity)
    identity[field] = value
    with pytest.raises(ValueError, match="definition/state"):
        pipeline.check_candidate(saved, identity)


def test_temporal_purge_and_embargo_match_frozen_protocol(protocol):
    g = json.loads(Path("config/strat2g-protocol-v1.json").read_text())
    boundary = int(pd.Timestamp("2022-02-15T00:00Z").timestamp()) // 60
    assert phase_of(boundary - 31, boundary, g) == "purged"
    assert phase_of(boundary + 59, boundary + 89, g) == "outside"
    assert phase_of(boundary + 60, boundary + 90, g) == "evaluation"
    assert protocol["splits"] == g["splits"]
    assert protocol["common_label_maturity_minutes"] == 30


def test_calendar_utc_server_and_closing_boundary():
    from trading.research.strat2d.calendar import Calendar
    from trading.research.strat2g.resources import CONFIG

    c = json.loads(CONFIG.read_text())
    calendar = Calendar(c["calendar"])
    for month in (1, 2):
        t = pd.Timestamp(f"2022-{month:02}-10T12:00Z")
        assert t.tz_convert("America/New_York").utcoffset().total_seconds() == -18000
        assert t.tz_convert("Etc/GMT-2").tz_convert("UTC") == t
    assert calendar.classify(pd.Timestamp("2022-02-28T21:59Z")) != "open"
    assert not safe_observation(pd.Timestamp("2022-02-28T21:59Z"), calendar)
    assert not safe_observation(pd.Timestamp("2022-02-27T22:30Z"), calendar)


def test_feature_future_invariance_and_gap_reset():
    a, b = Features(), Features()
    for minute in range(80):
        bar = [1 + minute / 1e6, 1.001 + minute / 1e6, 0.999 + minute / 1e6, 1 + minute / 1e6]
        assert a.push(minute, bar) == b.push(minute, bar)
    frozen = copy.deepcopy(a.state)
    b.push(80, [2, 2.1, 1.9, 2])
    assert a.state == frozen
    a.push(90, [1, 1.01, 0.99, 1])
    assert len(a.state["history"]) == 1
    assert len(b.state["history"]) <= 61


def test_uncertainty_preserves_days_not_overlapping_minutes(protocol):
    from trading.research.strat2f.metrics import metric, update
    from trading.research.strat2h.statistics import add_signal, signal

    p = copy.deepcopy(protocol)
    p["uncertainty"].update(replicates=40, minimum_observed_days=10)
    days = {}
    for i in range(12):
        m = metric(False)
        for y in (0, 1, 2):
            update(m, y, [0.3, 0.4, 0.3])
        s = signal()
        add_signal(s, event(i * 1440), 1)
        days[f"evaluation:2022-02-{i + 15:02}"] = {
            "metrics": {v: copy.deepcopy(m) for v in ("cal", "reference", "prior")},
            "forced": s,
            "thresholds": {str(t): copy.deepcopy(s) for t in p["thresholds"]},
        }
    x = analysis.uncertainty(days, p, "evaluation")
    assert x == analysis.uncertainty(days, p, "evaluation")
    assert x["days"] == 12
    assert x["blocks"]["2"]["loss_vs_reference"]["lower"] == 0
    assert x["blocks"]["5"]["threshold_net:0.55"]["lower"] == pytest.approx(0.9)
    assert analysis.uncertainty({}, p, "evaluation")["status"] == "INSUFFICIENT_DAYS"


def synthetic_frozen(prepared_case, protocol, monkeypatch, tmp_path):
    c, source = prepared_case
    c.update(train_end="2024-01-08T01:40:00+00:00")
    c["portfolio"]["threshold"] = 0.55
    g = json.loads(Path("config/strat2g-protocol-v1.json").read_text())
    g["scope"].update(start=c["start"], end_exclusive=c["test_end"])
    g["splits"] = {
        "train": [c["start"], c["train_end"]],
        "calibration": [c["train_end"], c["calibration_end"]],
        "selection": [c["calibration_end"], "2024-01-08T03:20:00+00:00"],
        "evaluation": ["2024-01-08T03:20:00+00:00", c["test_end"]],
    }
    g["embargo_minutes"] = 2
    h = copy.deepcopy(protocol)
    h.update(splits=g["splits"], scope=g["scope"], embargo_minutes=2)
    models = {
        family: Candidate(family, "h15_cost", g, c["model"])
        for family in ("linear12", "reference8")
    }
    for model in models.values():
        model.model.state["class_counts"] = [2, 3, 4]
    info = source_manifest(source, c, lambda: None)
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    monkeypatch.setattr(pipeline, "frozen", lambda *args: (models, g, info))
    from trading.research.strat2d.model import OnlineModel

    def forbidden(*args, **kwargs):
        raise AssertionError("H must never fit or calibrate")

    monkeypatch.setattr(OnlineModel, "fit_one", forbidden)
    monkeypatch.setattr(OnlineModel, "calibrate_one", forbidden)
    return c, h


@pytest.mark.parametrize("stage", ["before_commit", "after_commit"])
def test_interruption_repeat_resume_corruption_and_replay(
    prepared,
    protocol,
    monkeypatch,
    tmp_path,
    stage,  # noqa: F811
):  # noqa: F811
    c, h = synthetic_frozen(prepared, protocol, monkeypatch, tmp_path)
    out = "data/research/strat2d-h-resumed-v1"
    ref = "data/research/strat2d-h-reference-v1"
    expected = pipeline.run(c, ref, lambda: None, protocol=h)

    def fault(when, generation):
        if when == stage and generation == 3:
            raise InterruptedError("synthetic interruption")

    with pytest.raises(InterruptedError):
        pipeline.run(c, out, lambda: None, fault, h)
    prefix = committed_files(tmp_path / out)
    actual = pipeline.run(c, out, lambda: None, protocol=h)
    files = committed_files(tmp_path / out)
    assert all(files[k] == v for k, v in prefix.items())
    assert actual["complete"] and logical_hash(actual) == logical_hash(expected)
    assert files == committed_files(tmp_path / ref)
    assert actual["max_pending"] <= 31 and actual["max_history"] <= 61
    assert actual["rows"] == 240 and len(actual["groups"]) <= 100
    assert pipeline.run(c, out, lambda: None, protocol=h) == actual
    assert committed_files(tmp_path / out) == files
    (tmp_path / out / "00000000/state.json").write_text("corrupt")
    with pytest.raises(ValueError, match="corrupt"):
        pipeline.run(c, out, lambda: None, protocol=h)


def test_journal_rejects_incompatible_protocol_and_preserves_evidence(tmp_path):
    j = Journal(tmp_path / "journal", {"protocol": "v1"}, {}, lambda: None)
    j.commit({"n": 1}, [])
    before = committed_files(j.output)
    with pytest.raises(ValueError, match="mismatch"):
        Journal(j.output, {"protocol": "v2"}, {}, lambda: None)
    assert committed_files(j.output) == before


def test_confirmation_is_design_only_with_no_threshold_or_scope_expansion(protocol):
    c = json.loads(Path("config/strat2h-confirmation-protocol-v1.json").read_text())
    assert c["frozen_candidate"] == protocol["frozen"]
    assert c["proposed_period"]["access"].startswith("PROHIBITED")
    assert c["training_calibration"]["model_updates"].startswith("none")
    assert c["minimum_evidence"]["observed_utc_days"] >= 40
    assert protocol["production_threshold"] == 0.55
    assert protocol["scope"]["end_exclusive"] == "2022-03-01T00:00:00Z"


def test_break_even_accuracy_requires_gain_loss_magnitudes():
    s = movement()
    add_movement(s, 5, 5, 0, 1, True)
    add_movement(s, -3, 0, 3, -1, False)
    d = describe(s, [2.1])
    assert d["break_even_positive_fraction_by_cost"]["2.1"] == pytest.approx(5.1 / 8)
    assert d["sign_hit_rate"] == 0.5
    assert d["net_mean_bps_by_cost"]["2.1"] == pytest.approx(-1.1)


def test_fixed_statistics_do_not_retain_prediction_history(protocol):
    s = group(protocol)
    for i in range(10):
        add_group(s, event(i), protocol)
    initial_size = len(json.dumps(s))
    for i in range(10, 2000):
        add_group(s, event(i), protocol)
    assert len(json.dumps(s)) < initial_size * 1.15
    assert len(s["views"]["cal"]["direction_bins"]) == 20
    assert len(s["features"]) == 12


def test_independent_verifier_recomputes_raw_path_and_detects_label_corruption(
    prepared,
    protocol,
    monkeypatch,
    tmp_path,  # noqa: F811
):  # noqa: F811
    import trading.research.safety as safety

    c, h = synthetic_frozen(prepared, protocol, monkeypatch, tmp_path)
    monkeypatch.setattr(safety, "ROOT", tmp_path)
    out = "data/research/strat2d-h-verify-v1"
    state = pipeline.run(c, out, lambda: None, protocol=h)
    gpath = tmp_path / "synthetic-g/00000000"
    gpath.mkdir(parents=True)
    h["frozen"]["journal"] = "synthetic-g"
    with (gpath / "events.jsonl").open("x") as handle:
        for e in analysis.events(tmp_path / out):
            if e["phase"] not in ("selection", "evaluation"):
                continue
            clone = copy.deepcopy(e)
            clone.update(
                target="h15_cost",
                probabilities={
                    "linear12_raw": e["probabilities"]["raw"],
                    "linear12_cal": e["probabilities"]["cal"],
                    "reference8_cal": e["probabilities"]["reference"],
                    "prior": e["probabilities"]["prior"],
                },
            )
            handle.write(json.dumps(clone) + "\n")
    assert analysis.verify(tmp_path / out, state, h, c, lambda: None)["status"] == "PASS"
    first = next(p for p in sorted((tmp_path / out).glob("*/events.jsonl")) if p.stat().st_size)
    records = first.read_text().splitlines()
    changed = json.loads(records[0])
    changed["exit_price"] *= 1.001
    records[0] = json.dumps(changed)
    first.write_text("\n".join(records) + "\n")
    with pytest.raises(ValueError, match="OHLC"):
        analysis.verify(tmp_path / out, state, h, c, lambda: None)


def test_resource_stop_precedes_frozen_or_historical_reads(monkeypatch, tmp_path):
    from trading.research.safety import ResearchSafetyStop

    monkeypatch.setattr(pipeline, "validate_config", lambda *args: (tmp_path, tmp_path))

    def forbidden(*args):
        raise AssertionError("Historical artifacts must not be opened after admission fails")

    monkeypatch.setattr(pipeline, "frozen", forbidden)

    def guard():
        raise ResearchSafetyStop("Available memory below unchanged floor")

    with pytest.raises(ResearchSafetyStop, match="unchanged floor"):
        pipeline.run({}, "unused", guard, protocol={})


def test_blocked_report_does_not_invent_peak_rss_or_historical_results(
    protocol, tmp_path, monkeypatch
):
    from trading.research.strat2h import report

    monkeypatch.setattr(report, "ROOT", tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "config/strat2h-diagnostics-protocol-v1.json").write_text(json.dumps(protocol))
    audit = tmp_path / "evidence-v1"
    audit.mkdir()
    (audit / "diagnostics-crash-parent-monitor.json").write_text(
        json.dumps(
            {
                "metrics": {
                    "minimum_available_ram_gib": 1.195037841796875,
                    "minimum_free_disk_gib": 26.765907287597656,
                },
                "worker_peak_rss_bytes": 0,
            }
        )
    )
    result = {
        "status": "BLOCKED",
        "metrics": {},
        "checks": {"resource_admission": "PASS"},
        "protocol_sha256": "0" * 64,
        "evidence": "evidence-v1",
        "failure": {"exception_type": "ResearchSafetyStop", "reason": "RAM below floor"},
    }
    report.render(result, audit)
    text = (tmp_path / "reports/strat-2h-signal-quality-independent-validation-v1.md").read_text(
        "utf-8"
    )
    assert "INCONCLUSIVE" in text and "NOT RUN" in text
    assert "RSS: unavailable MiB" in text
    assert "1.195038 GiB" in text
    assert "Historical diagnostics/recovery/replay are NOT claimed complete" in text
