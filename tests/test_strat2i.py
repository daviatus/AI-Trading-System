"""Synthetic data/cost integrity and durable bounded I audit regressions."""

import copy
import json
from pathlib import Path

import pandas as pd
import pytest
from test_strat2d import config as shared_config
from test_strat2d import prepared as shared_prepared

from trading.research.strat2d.calendar import Calendar
from trading.research.strat2d.journal import committed_files, file_hash, logical_hash
from trading.research.strat2i import economics as eco
from trading.research.strat2i import pipeline
from trading.research.strat2i import quality as qa

config = shared_config
prepared = shared_prepared


@pytest.fixture
def protocol():
    return json.loads(Path("config/strat2i-data-quality-economic-research-v1.json").read_text())


def cal():
    return Calendar(
        {
            "version": "synthetic-v1",
            "verified": False,
            "timezone": "UTC",
            "weekly_open_seconds": [[0, 604800]],
            "holidays": [],
        }
    )


def test_duplicate_conflict_and_order_fail_closed():
    s = qa.initial()
    qa.observe(s, 10, [1, 1.01, 0.99, 1], 1, cal(), [], 10, 100)
    for m, prices, message in [
        (10, [1, 1.01, 0.99, 1], "duplicates"),
        (10, [1, 1.01, 0.99, 1.001], "conflicts"),
        (9, [1, 1.01, 0.99, 1], "Out-of-order"),
    ]:
        with pytest.raises(ValueError, match=message):
            qa.observe(copy.deepcopy(s), m, prices, 1, cal(), [], 0, 100)


@pytest.mark.parametrize(
    "prices",
    [
        [0, 1, 1, 1],
        [-1, 1, -1, 1],
        [1, 1, 0.9, 1.1],
        [1, 1.1, 1.05, 1],
        [1, float("nan"), 0.9, 1],
        [1, float("inf"), 0.9, 1],
    ],
)
def test_invalid_ohlc_rejected(prices):
    with pytest.raises(ValueError, match="OHLC"):
        qa.observe(qa.initial(), 10, prices, 1, cal(), [], 10, 100)


def test_gap_edge_and_overlapping_label_categories():
    s, events = qa.initial(), []
    for m in (12, 13, 20, 21):
        qa.observe(s, m, [1, 1.01, 0.99, 1], 0, cal(), events, 10, 30)
    qa.missing(s, 22, 30, cal(), events, False)
    assert s["internal_gaps"] == 1 and s["internal_minutes"] == 6
    assert s["edge_minutes"] == 10
    assert s["internal_missing"] == {"open": 6}
    assert s["edge_missing"] == {"open": 10}
    assert s["gap_label_blocks"] == {"5": 2, "15": 2, "30": 2}
    assert s["rows"] + s["internal_minutes"] + s["edge_minutes"] == 20


def test_session_boundaries_dst_and_version(protocol):
    calendar = Calendar(protocol["calendar"])
    assert not calendar.rules["verified"]
    assert calendar.classify(pd.Timestamp("2022-02-28T21:58Z")) == "open"
    assert calendar.classify(pd.Timestamp("2022-02-28T21:59Z")) != "open"
    assert calendar.classify(pd.Timestamp("2022-02-28T22:01Z")) == "open"
    assert calendar.classify(pd.Timestamp("2022-02-27T22:00Z")) != "open"
    assert calendar.classify(pd.Timestamp("2022-02-27T22:01Z")) == "open"
    for t in ("2022-01-10T12:00Z", "2022-02-10T12:00Z"):
        assert pd.Timestamp(t).tz_convert(calendar.zone).utcoffset().total_seconds() == -18000
    with pytest.raises(ValueError, match="UTC"):
        calendar.classify(pd.Timestamp("2022-01-10T12:00"))


def test_timestamp_alignment_and_timezone_before_rows(tmp_path):
    for n, index in enumerate(
        (
            pd.date_range("2022-01-10", periods=3, freq="min"),
            pd.date_range("2022-01-10T00:00:01Z", periods=3, freq="min"),
        )
    ):
        frame = pd.DataFrame({"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}, index=index)
        frame.index.name = "opened_at"
        path = tmp_path / f"bad{n}.parquet"
        frame.to_parquet(path)
        with pytest.raises(ValueError, match="timestamp"):
            list(pipeline.raw_batches(tmp_path, [path.name], 2, lambda: None))


@pytest.mark.parametrize("side", [1, -1])
def test_price_pips_and_cash_accounting(protocol, side):
    scenario = protocol["costs"]["scenarios"][1]
    flow = eco.cashflow(1.1, 1.1 + side * 0.001, side, scenario, 100000)
    assert flow["gross_pips"] == pytest.approx(10)
    assert flow["gross_usd"] == pytest.approx(100)
    assert flow["net_usd"] + flow["cost_usd"] == pytest.approx(flow["gross_usd"])
    assert flow["net_usd"] < flow["gross_usd"]
    flat = eco.cashflow(1.1, 1.1, side, scenario, 100000)
    assert flat["cost_pips"] == pytest.approx(2.31)
    assert flat["net_bps"] == pytest.approx(-2.1)
    assert (
        eco.cashflow(1.1, 1.1 + side * 0.001, side, protocol["costs"]["scenarios"][-1])["net_bps"]
        < flow["net_bps"]
    )


def test_targets_strict_neutral_and_volatility_costs(protocol):
    d = protocol["targets"][0]
    assert eco.target(2.1, 0.00001, d) == 0
    assert eco.target(2.10001, 0.00001, d) == 1
    assert eco.target(-2.10001, 0.00001, d) == 2
    assert eco.target(2.5, 0.00001, protocol["targets"][1]) == 0
    assert eco.target(3, 0.001, protocol["targets"][2]) == 0
    with pytest.raises(ValueError):
        eco.target(float("nan"), 0.001, d)


def test_frozen_protocol_and_resource_scope(protocol):
    from trading.research.strat2h.resources import CONFIG, validate_config

    for name, sha in protocol["unchanged_protocols"].items():
        assert file_hash(name) == sha
    assert (
        protocol["targets"]
        == json.loads(Path("config/strat2i-data-quality-economic-research-v1.json").read_text())[
            "targets"
        ]
    )
    c = json.loads(CONFIG.read_text())
    validate_config(c, "data/research/strat2d-strat2g-strat2h-strat2i-test-v1")
    c["test_end"] = "2026-01-01T00:00:00Z"
    with pytest.raises(ValueError):
        validate_config(c)


def test_fixed_economic_state_and_future_outcomes_not_features(protocol):
    from test_strat2h import event

    e = event()
    s = eco.group(protocol)
    for _ in range(10):
        eco.observe(s, e, protocol)
    size = len(json.dumps(s))
    features = e["features"][:]
    e["exit_price"] *= 1.01
    for _ in range(2000):
        eco.observe(s, e, protocol)
    assert len(json.dumps(s)) < size * 1.25
    assert e["features"] == features


@pytest.mark.parametrize("stage", ["before_commit", "after_commit"])
@pytest.mark.parametrize("fault_phase", ["raw", "economics"])
def test_synthetic_full_audit_resume_and_immutable_replay(
    prepared, protocol, monkeypatch, tmp_path, stage, fault_phase
):
    from test_strat2h import synthetic_frozen

    from trading.research.strat2d.pipeline import source_manifest
    from trading.research.strat2h import pipeline as hp

    hprotocol = json.loads(Path("config/strat2h-diagnostics-protocol-v1.json").read_text())
    c, hprotocol = synthetic_frozen(prepared, hprotocol, monkeypatch, tmp_path)
    hpath = tmp_path / "data/research/strat2d-h-synthetic-v1"
    hp.run(c, hpath.relative_to(tmp_path).as_posix(), lambda: None, protocol=hprotocol)
    hfiles = committed_files(hpath)
    p = copy.deepcopy(protocol)
    p["raw_parts"] = ["cache/EURUSD/2024-01.parquet"]
    p["calendar"] = c["calendar"]
    p["frozen_H"]["generation"] = (len(hfiles) - 1) // 3 - 1
    info = source_manifest(prepared[1], c, lambda: None)
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    monkeypatch.setattr(
        pipeline,
        "load_inputs",
        lambda config, protocol, output, guard: (
            prepared[1],
            tmp_path / output,
            info,
            tmp_path / "raw",
            {},
            hpath,
            hfiles,
        ),
    )
    reference = "data/research/strat2d-i-reference-v1"
    resumed = "data/research/strat2d-i-resumed-v1"
    expected = pipeline.run(c, reference, lambda: None, protocol=p)
    fault_generation = 3
    if fault_phase == "economics":
        fault_generation = next(
            int(path.parent.name)
            for path in sorted((tmp_path / reference).glob("[0-9]*/state.json"))
            if json.loads(path.read_text())["event_sequence"] > 0
        )

    def fault(when, generation):
        if when == stage and generation == fault_generation:
            raise InterruptedError("synthetic crash")

    with pytest.raises(InterruptedError):
        pipeline.run(c, resumed, lambda: None, fault, p)
    prefix = committed_files(tmp_path / resumed)
    actual = pipeline.run(c, resumed, lambda: None, protocol=p)
    assert actual["complete"] and actual["quality"]["rows"] == 240
    assert actual["feature_cross_gap_violations"] == 0
    assert logical_hash(actual) == logical_hash(expected)
    files = committed_files(tmp_path / resumed)
    assert files == committed_files(tmp_path / reference)
    assert all(files[k] == v for k, v in prefix.items())
    pipeline.run(c, resumed, lambda: None, protocol=p)
    assert committed_files(tmp_path / resumed) == files
    (tmp_path / resumed / "00000000/state.json").write_text("corrupt")
    with pytest.raises(ValueError, match="corrupt"):
        pipeline.run(c, resumed, lambda: None, protocol=p)


def test_whole_day_uncertainty_deterministic_and_sparse_cells(protocol):
    from trading.research.strat2i.analysis import analyze

    s = {
        "quality": {},
        "groups": {},
        "daily": {
            f"evaluation:2022-02-{15 + i:02}": {"forced": {"n": 3, "net_bps": 3.0, "net_pips": 3.3}}
            for i in range(12)
        },
    }
    p = copy.deepcopy(protocol)
    p["uncertainty"]["replicates"] = 30
    a, b = analyze(s, p, lambda: None), analyze(s, p, lambda: None)
    assert a == b
    assert a["uncertainty"]["blocks"]["5"]["approximate_nonoverlapping_day_blocks"] == 2
    assert a["uncertainty"]["blocks"]["2"]["intervals"]["forced"]["lower"] == 1
    assert a["uncertainty"]["blocks"]["2"]["intervals"]["0.55"]["status"].startswith("INSUFFICIENT")


def test_admission_failure_preserves_evidence_without_raw_access(tmp_path, monkeypatch):
    from tools import strat2i_validation as driver
    from trading.research.safety import ResearchSafetyStop
    from trading.research.strat2i import report

    class Stop:
        def __init__(self, config):
            self.minimum_available_ram_gib = 0.99
            self.minimum_free_disk_gib = 20

        def __call__(self):
            raise ResearchSafetyStop("Synthetic available RAM 0.99 GiB below floor")

    monkeypatch.setattr(driver, "AUDIT", tmp_path / "evidence-v1")
    monkeypatch.setattr(driver, "PRIMARY", str(tmp_path / "primary"))
    monkeypatch.setattr(driver, "REFERENCE", str(tmp_path / "reference"))
    monkeypatch.setattr(
        driver, "validate_config", lambda *args: (tmp_path / "source", tmp_path / "primary")
    )
    monkeypatch.setattr(driver, "Monitor", Stop)
    monkeypatch.setattr(report, "render", lambda *args: None)
    assert driver.parent() == 2
    evidence = json.loads((driver.AUDIT / "summary.json").read_text())
    assert evidence["status"] == "BLOCKED"
    assert evidence["metrics"]["minimum_available_ram_gib"] == 0.99
    assert not list(driver.AUDIT.glob("*-pid.json"))
