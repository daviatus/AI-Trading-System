"""Independent numerical oracles, deterministic execution and hard process recovery."""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from trading.research.strat2d.calendar import Calendar
from trading.research.strat2d.journal import Journal, committed_files, logical_hash
from trading.research.strat2d.portfolio import Portfolio
from trading.research.strat2e.audit import signal_reason, synthetic_job


@pytest.fixture
def spec():
    config = json.loads(Path("config/strat2d-eurusd-2022-engineering-v1.json").read_text())
    result = copy.deepcopy(config["portfolio"])
    result.update(minimum_quantity=1, quantity_step=1, maximum_quantity=10, horizon_minutes=3)
    return result


@pytest.mark.parametrize("side", [1, -1])
@pytest.mark.parametrize("outcome", ["target", "stop", "horizon_profit", "horizon_loss"])
@pytest.mark.parametrize("costs", [False, True])
def test_long_short_exit_numerical_oracle(spec, side, outcome, costs):
    spec.update(
        spread_bps=2 if costs else 0,
        slippage_bps=1 if costs else 0,
        commission_bps_roundtrip=4 if costs else 0,
        horizon_minutes=2,
    )
    p, events = Portfolio(spec), []
    stamp = pd.Timestamp("2022-01-10T12:01Z")
    p.signal(
        stamp,
        [0.01, 0.98 if side == 1 else 0.01, 0.98 if side == -1 else 0.01],
        1.0,
        "fixture",
        True,
        events,
    )
    assert p.state["fills"] == 0
    p.on_bar(stamp, dict(open=100.0, high=100.05, low=99.95, close=100.0), True, events)
    assert p.state["position"]["side"] == side
    entry = 100 * (1 + side * (2 if costs else 0) / 10000)
    assert p.state["position"]["entry"] == pytest.approx(entry)
    assert p.state["position"]["quantity"] == 10
    assert (
        p.state["reserved_margin"] + 10 * entry * spec["commission_bps_roundtrip"] / 20000
        <= spec["initial_equity"]
    )
    if outcome == "target":
        reference = entry + side * 3
        close = entry + side * 3.1
    elif outcome == "stop":
        reference = entry - side
        close = entry - side * 1.1
    else:
        reference = 100 + side * (0.3 if outcome == "horizon_profit" else -0.3)
        close = reference
    p.on_bar(
        stamp + pd.Timedelta(minutes=1),
        dict(open=100.0, high=max(100.05, close), low=min(99.95, close), close=close),
        True,
        events,
    )
    exit_price = reference * (1 - side * (2 if costs else 0) / 10000)
    fees = 10 * (entry + exit_price) * (4 if costs else 0) / 20000
    net = side * 10 * (exit_price - entry) - fees
    expected_cost = 10 * (abs(entry - 100) + abs(exit_price - reference)) + fees
    assert p.state["fills"] == 2 and p.state["trades"] == 1
    assert p.state["cash"] == pytest.approx(10000 + net)
    assert p.state["realized_net"] == pytest.approx(net)
    assert p.state["transaction_costs"] == pytest.approx(expected_cost)
    assert p.state["equity"] == p.state["cash"] and p.state["unrealized"] == 0
    assert p.state["reserved_margin"] == 0 and p.state["position"] is None
    assert (net > 0) == (outcome in ["target", "horizon_profit"])
    trade = next(e for e in events if e["event"] == "trade")
    assert trade["net_pnl"] == pytest.approx(net)
    assert trade["costs"] == pytest.approx(expected_cost)


def test_capital_floor_and_pending_session_rejection(spec):
    spec["initial_equity"] = 10
    p, events = Portfolio(spec), []
    stamp = pd.Timestamp("2022-01-10T12:01Z")
    p.signal(stamp, [0.01, 0.98, 0.01], 1, "fixture", True, events)
    p.on_bar(stamp, dict(open=100.0, high=100.1, low=99.9, close=100.0), True, events)
    assert p.state["rejections"] == {"quantity_below_minimum": 1}
    assert p.state["cash"] == 10 and p.state["fills"] == 0
    p.signal(stamp + pd.Timedelta(minutes=1), [0.01, 0.01, 0.98], 1, "fixture", True, events)
    p.on_bar(
        stamp + pd.Timedelta(minutes=1),
        dict(open=100.0, high=100.1, low=99.9, close=100.0),
        False,
        events,
    )
    assert p.state["rejections"]["unavailable_next_bar"] == 1
    assert p.state["pending"] is None and p.state["fills"] == 0


@pytest.mark.parametrize("gap,opened", [(3, True), (1, False)])
def test_gap_and_session_boundary_censor_exposure_without_exit(spec, gap, opened):
    p, events = Portfolio(spec), []
    stamp = pd.Timestamp("2022-01-10T12:01Z")
    p.signal(stamp, [0.01, 0.98, 0.01], 1, "fixture", True, events)
    p.on_bar(stamp, dict(open=100.0, high=100.1, low=99.9, close=100.0), True, events)
    p.on_bar(
        stamp + pd.Timedelta(minutes=gap),
        dict(open=98.0, high=98.1, low=97.9, close=98.0),
        opened,
        events,
    )
    assert p.state["censored"] and p.state["position"]
    assert p.state["fills"] == 1 and p.state["trades"] == 0
    assert p.state["cash"] + p.state["unrealized"] == p.state["equity"]


@pytest.mark.parametrize("stage", ["before_commit", "after_commit"])
def test_actual_hard_exit_with_open_position_matches_independent_replay(tmp_path, spec, stage):
    reference, recovered = tmp_path / "reference", tmp_path / "resumed"
    expected = synthetic_job(reference, spec)
    source = (
        "import os,json;from trading.research.strat2e.audit import synthetic_job;"
        "synthetic_job("
        + repr(str(recovered))
        + ",json.loads("
        + repr(json.dumps(spec))
        + "),fault="
        "lambda stage,g:os._exit(86) if stage==" + repr(stage) + " and g==1 else None)"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", source],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 86, result.stderr
    manifest = json.loads((recovered / "manifest.json").read_text())
    interrupted = Journal(recovered, manifest, {})
    assert (
        interrupted.state["portfolio"]["position"]
        if stage == "after_commit"
        else interrupted.state["portfolio"]["pending"]
    )
    prefix = committed_files(recovered)
    actual = synthetic_job(recovered, spec)
    assert actual["portfolio"]["fills"] == 4 and actual["portfolio"]["trades"] == 2
    assert logical_hash(actual) == logical_hash(expected)
    assert committed_files(recovered) == committed_files(reference)
    assert all(committed_files(recovered)[k] == v for k, v in prefix.items())
    assert synthetic_job(recovered, spec) == actual
    assert committed_files(recovered) == committed_files(reference)


@pytest.mark.parametrize(
    "p,allowed,distance,reason",
    [
        ([0.6, 0.2, 0.2], True, 1, "below_directional_threshold"),
        ([0.5, 0.4, 0.1], True, 1, "no_trade_dominates"),
        ([0.1, 0.8, 0.1], False, 1, "session_or_end_boundary"),
        ([0.1, 0.8, 0.1], True, 0, "invalid_stop_distance"),
        ([0.1, 0.8, 0.1], True, 1, "pending_order"),
    ],
)
def test_funnel_explanation_matches_real_signal(spec, p, allowed, distance, reason):
    if reason == "no_trade_dominates":
        spec["threshold"] = 0.34
    portfolio = Portfolio(spec)
    assert signal_reason(portfolio.state, p, distance, allowed, spec["threshold"]) == reason
    if sum(p) == pytest.approx(1):
        portfolio.signal(pd.Timestamp("2022-01-10T12:01Z"), p, distance, "fixture", allowed, [])
        assert bool(portfolio.state["pending"]) == (reason == "pending_order")


def test_calendar_2022_offset_and_weekly_edges():
    c = json.loads(Path("config/strat2d-eurusd-2022-engineering-v1.json").read_text())
    cal = Calendar(c["calendar"])
    assert not cal.rules["verified"]
    for stamp in ["2022-01-02T22:00Z", "2022-02-06T22:00Z"]:
        t = pd.Timestamp(stamp)
        assert t.tz_convert(cal.zone).hour == 17
        assert t.tz_convert(cal.zone).utcoffset().total_seconds() == -18000
        assert cal.classify(t) == "weekend"
        assert cal.classify(t + pd.Timedelta(minutes=1)) == "open"
    assert cal.classify(pd.Timestamp("2022-01-07T21:54Z")) == "open"
    assert cal.classify(pd.Timestamp("2022-01-07T21:55Z")) == "weekend"
    assert (
        pd.Timestamp("2022-03-13T07:00Z").tz_convert(cal.zone).utcoffset().total_seconds() == -14400
    )
