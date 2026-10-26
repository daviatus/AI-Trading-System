"""Safety, leakage and economic invariants for the offline research layer."""

import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from trading.ctrader.calendar import BrokerCalendar
from trading.data.domain import InstrumentCode
from trading.research import data, safety
from trading.research.data import features, session_open
from trading.research.execution import Costs, backtest, outcomes, rank_opportunities, targets
from trading.research.models import (
    apply_calibration,
    fit_model,
    label_end_times,
    purged_masks,
)


def bars(n=200, price=100):
    close = price + np.sin(np.arange(n) / 8) * 0.2 + np.arange(n) * 0.001
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": np.arange(n) % 20 + 1.0,
        },
        index=pd.date_range("2025-01-06T10:00Z", periods=n, freq="min", name="opened_at"),
    )


def metadata():
    return {
        "specifications": {
            "scheduleTimeZone": "America/New_York",
            "schedule": [
                {"startSecond": 64860, "endSecond": 147540},
                {"startSecond": 151260, "endSecond": 233940},
            ],
            "holiday": [],
            "minVolume": 100,
            "stepVolume": 100,
            "maxVolume": 10000000,
        }
    }


def config():
    return {
        "initial_equity": 10000,
        "risk_fraction": 0.0025,
        "daily_loss_fraction": 0.015,
        "max_gross_leverage": 5,
        "max_positions": 1,
        "costs": {
            "XAUUSD": {"spread_bps": 0, "slippage_bps_per_side": 0, "commission_bps_roundtrip": 0}
        },
    }


def candidates(frame, indices, horizon=1):
    return pd.DataFrame(
        {
            "time": frame.index[indices] + pd.Timedelta(minutes=1),
            "asset": "XAUUSD",
            "position": indices,
            "direction": 1,
            "probability": 0.7,
            "confidence": 0.8,
            "score": 0.1,
            "regime": "trending",
            "horizon": horizon,
        }
    )


@pytest.mark.parametrize("override", [":5432", "@main:15432", "?options=unsafe"])
def test_database_target_is_fail_closed(monkeypatch, override):
    url = (
        "postgresql+psycopg://ctrader_demo_test:dummy@host.docker.internal:15432/"
        "ctrader_demo_fe1a7194a87c"
    )
    if override == ":5432":
        url = url.replace(":15432", override)
    elif override.startswith("@"):
        url = url.replace("@host.docker.internal:15432", override)
    else:
        url += override
    monkeypatch.setattr(
        data.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            stdout=json.dumps({"url": url, "identity": [data.DATABASE, data.USER, "fixture"]})
        ),
    )
    with pytest.raises(RuntimeError, match="secrets suppressed"):
        data.isolated_access()


def test_research_disk_floor_cannot_be_bypassed(monkeypatch):
    monkeypatch.setattr(safety.shutil, "disk_usage", lambda _: SimpleNamespace(free=14 * 1024**3))
    with pytest.raises(RuntimeError, match="free disk"):
        data.resources({"free_floor_gib": 15})


def test_features_are_causal_and_reset_across_gaps():
    frame = bars(300)
    original = features(frame)
    changed = frame.copy()
    changed.loc[changed.index[180] :, ["open", "high", "low", "close"]] *= 2
    pd.testing.assert_frame_equal(original.iloc[:180], features(changed).iloc[:180])
    gapped = frame.drop(frame.index[150])
    result = features(gapped)
    assert result.iloc[150:213].isna().all().all()
    pd.testing.assert_frame_equal(result.iloc[213:], features(gapped.iloc[150:]).iloc[63:])


def test_vectorized_calendar_matches_existing_phase4c_calendar():
    meta = metadata()
    index = pd.date_range("2025-03-01", "2025-03-15", freq="17min", tz="UTC")
    calendar = BrokerCalendar({"XAUUSD": meta})
    expected = [calendar.is_open(InstrumentCode.XAUUSD, t.to_pydatetime()) for t in index]
    np.testing.assert_array_equal(session_open(index, meta), expected)


def test_next_bar_entry_and_ambiguous_bar_uses_stop_first():
    frame = bars(4)
    frame.loc[:, ["open", "close"]] = 100.0
    frame.loc[:, "low"] = 99.0
    frame.loc[:, "high"] = 101.0
    frame.iloc[1, frame.columns.get_loc("low")] = 97.0
    frame.iloc[1, frame.columns.get_loc("high")] = 105.0
    path = outcomes(frame, np.ones(4), 2, 2, 2)
    assert path["entry"][0] == 100
    assert path["long"]["gross_per_unit"][0] == -2
    assert path["long"]["ambiguous"][0]


def test_gap_after_entry_cannot_retroactively_cancel_trade():
    frame = bars(10).drop(bars(10).index[2])
    frame.loc[:, ["open", "close"]] = 100.0
    frame.loc[:, "low"] = 99.9
    frame.loc[:, "high"] = 100.1
    path = outcomes(frame, np.ones(len(frame)), 4)
    assert not path["label_valid"][0]
    trades, _, _ = backtest(
        candidates(frame, [0], 4),
        {"XAUUSD": {"bars": frame, "paths": {4: path}}},
        {"XAUUSD": metadata()},
        config(),
        frame.index[0],
        frame.index[-1] + pd.Timedelta(minutes=1),
    )
    assert len(trades) == 1
    assert trades.iloc[0].data_gap_exit
    assert trades.iloc[0].net_pnl < 0


def test_missing_entry_bar_has_no_fill():
    frame = bars(10).drop(bars(10).index[1])
    path = outcomes(frame, np.ones(len(frame)), 2)
    assert not path["fill"][0]


def test_transaction_costs_can_change_a_directional_target_to_no_trade():
    frame = bars(10)
    frame["open"] = np.arange(10) * 0.01 + 100
    frame["close"] = frame["open"]
    frame.loc[:, "low"] = frame.close - 0.001
    frame.loc[:, "high"] = frame.close + 0.001
    path = outcomes(frame, np.ones(10), 2)
    assert targets(path, Costs(0, 0, 0))[0] == 1
    assert targets(path, Costs(100, 0, 0))[0] == 0


def test_ranking_cannot_depend_on_future_outcomes():
    frame = candidates(bars(10), [0, 1])
    first = rank_opportunities(frame)
    frame["future_return"] = [1e6, -1e6]
    pd.testing.assert_frame_equal(first, rank_opportunities(frame).drop(columns="future_return"))


def test_position_steps_daily_loss_and_shared_exposure_are_enforced():
    frame = bars(100)
    frame.loc[:, ["open", "close"]] = 100.0
    frame.loc[:, "high"] = 100.1
    frame.loc[:, "low"] = 95.0
    path = outcomes(frame, np.ones(len(frame)), 1)
    trades, _, result = backtest(
        candidates(frame, list(range(0, 80, 2))),
        {"XAUUSD": {"bars": frame, "paths": {1: path}}},
        {"XAUUSD": metadata()},
        config(),
        frame.index[0],
        frame.index[-1] + pd.Timedelta(minutes=1),
    )
    assert len(trades) > 0
    assert trades.risk_fraction.max() <= 0.0025 + 1e-10
    assert trades.net_pnl.sum() >= -150 - 1e-8
    assert (trades.quantity % 1 == 0).all()
    assert result["net_return"] >= -0.015 - 1e-10
    assert all(trades.entry_time.iloc[1:].to_numpy() >= trades.exit_time.iloc[:-1].to_numpy())


def test_purged_splits_exclude_all_cross_boundary_targets():
    index = pd.date_range("2024-12-30", "2025-01-05", freq="min", tz="UTC")
    fold = {
        "train_end": "2025-01-01",
        "calibration_end": "2025-01-02",
        "tuning_end": "2025-01-03",
        "test_end": "2025-01-05",
    }
    ends = label_end_times(index, 60)
    masks = purged_masks(index, ends, fold)
    assert ends[masks["train"]].max() < pd.Timestamp("2025-01-01T00:00Z")
    assert not (masks["train"] & masks["calibration"]).any()
    assert not (masks["tuning"] & masks["test"]).any()


def test_real_xgboost_and_disjoint_calibration_smoke():
    rng = np.random.default_rng(42)
    index = pd.date_range("2025-01-01", periods=6000, freq="min", tz="UTC")
    f = pd.DataFrame(rng.normal(size=(6000, 3)), index=index, columns=list("abc"))
    labels = np.where(f.a > 0.5, 1, np.where(f.a < -0.5, 2, 0)).astype("int8")
    model, theta, info = fit_model(
        f,
        labels,
        {
            "train": np.arange(6000) < 3000,
            "calibration": (np.arange(6000) >= 3100) & (np.arange(6000) < 4500),
        },
        {"max_depth": 2, "n_estimators": 8, "learning_rate": 0.1},
        {"threads": 1, "seed": 42, "max_training_rows": 2500},
    )
    assert info["train_rows"] == 2500
    assert info["calibration_rows"] == 1400
    assert info["train_last"] < info["calibration_first"]
    probability = apply_calibration(np.tile([0.4, 0.3, 0.3], (10, 1)), theta)
    np.testing.assert_allclose(probability.sum(axis=1), 1)
    assert model.num_boosted_rounds() == 8
