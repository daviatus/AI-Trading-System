"""Small synthetic execution invariants; never read market history or broker data."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal, assert_series_equal

from trading.research.phase6.execution import (
    ASSETS,
    LEVERAGE_SCENARIOS,
    PORTFOLIO_SUBSETS,
    CostScenario,
    InstrumentSpec,
    SimulationConfig,
    backtest,
    compare_portfolios,
    evaluate_leverage_scenarios,
)


def bars(n=8, start="2025-01-06T10:00Z", price=100.0):
    return pd.DataFrame(
        {"open": price, "high": price + 0.1, "low": price - 0.1, "close": price},
        index=pd.date_range(start, periods=n, freq="min", name="opened_at"),
    )


def instrument(asset="EURUSD", **overrides):
    values = dict(
        asset=asset,
        price_digits=2,
        minimum_quantity=1.0,
        quantity_step=1.0,
        maximum_quantity=1000000.0,
        leverage_cap=30,
    )
    return InstrumentSpec(**(values | overrides))


def candidates(frame, indices=(1,), **overrides):
    rows = [
        dict(
            time=frame.index[i],
            asset="EURUSD",
            direction=1,
            score=0.2,
            probability=0.7,
            confidence=0.5,
            stop_distance=1.0,
            horizon=1,
            regime="trend",
            model_id="synthetic-v1",
            threshold=0.6,
        )
        | overrides
        for i in indices
    ]
    return pd.DataFrame(rows)


def run(frame=None, signals=None, spec=None, cost=None, config=None, diagnostic=None, **kwargs):
    frame = bars() if frame is None else frame
    return backtest(
        candidates(frame) if signals is None else signals,
        {"EURUSD": frame},
        {"EURUSD": spec or instrument()},
        {"EURUSD": cost or CostScenario()},
        config or SimulationConfig(),
        kwargs.get("start", frame.index[0]),
        kwargs.get("end", frame.index[-1] + pd.Timedelta(minutes=5)),
        diagnostic,
    )


def test_zero_trade_cash_baseline_and_observed_days():
    frame = bars()
    result = run(frame, candidates(frame).iloc[:0])
    assert result.metrics["trades"] == 0
    assert result.metrics["net_return"] == 0
    assert result.metrics["maximum_mark_to_market_drawdown"] == 0
    assert result.metrics["zero_trade_observed_days"] == 1
    assert result.metrics["win_rate_wilson_95_descriptive"] is None
    assert result.metrics["statistically_inconclusive"]


@pytest.mark.parametrize("side", [1, -1])
def test_ambiguous_bar_is_adverse_first(side):
    frame = bars()
    frame.loc[frame.index[1], ["high", "low"]] = [103.0, 97.0]
    result = run(frame, candidates(frame, direction=side))
    trade = result.trades.iloc[0]
    assert trade.exit == 100 - side
    assert trade.net_pnl == -50
    assert trade.ambiguous
    assert trade.exit_reason == "stop_loss"


@pytest.mark.parametrize("side", [1, -1])
def test_target_and_horizon_exits(side):
    frame = bars()
    frame.loc[frame.index[1], "high" if side == 1 else "low"] = 100 + side * 3
    result = run(frame, candidates(frame, direction=side))
    assert result.trades.iloc[0].exit_reason == "take_profit"
    assert result.trades.iloc[0].net_pnl == 100
    assert not result.trades.iloc[0].ambiguous
    stable = run(bars(), candidates(bars(), direction=side))
    assert stable.trades.iloc[0].exit_reason == "horizon"


def test_signal_timestamp_is_next_contiguous_raw_open():
    frame = bars()
    frame.loc[frame.index[1], ["open", "high", "low", "close"]] = [101, 101.1, 100.9, 101]
    result = run(frame)
    trade = result.trades.iloc[0]
    assert trade.entry_time == frame.index[1]
    assert trade.entry == 101
    missing = run(frame.drop(frame.index[1]), candidates(frame))
    assert missing.decision_counts == {"missing_next_observed_entry": 1}


def test_first_bar_without_completed_predecessor_cannot_fill():
    frame = bars()
    result = run(frame, candidates(frame, indices=(0,)))
    assert result.decision_counts == {"missing_completed_signal_bar": 1}


def test_missing_previous_signal_bar_cannot_fill_after_gap():
    frame = bars().drop(bars().index[0])
    result = run(frame, candidates(frame, indices=(0,)))
    assert result.decision_counts == {"missing_completed_signal_bar": 1}


def test_gap_stop_can_exceed_planned_risk():
    frame = bars().iloc[[0, 1, 4]].copy()
    frame.loc[frame.index[2], ["open", "high", "low", "close"]] = [98, 98.1, 97.9, 98]
    result = run(frame, candidates(frame, horizon=3))
    trade = result.trades.iloc[0]
    assert trade.exit_reason == "data_gap_stop"
    assert trade.exit == 98
    assert trade.planned_risk_fraction <= 0.005
    assert trade.realized_loss_fraction == 0.01
    assert trade.exit_time == frame.index[2]


def test_dataset_exhaustion_is_unresolved_mark_not_manufactured_gap_stop():
    frame = bars(2)
    result = run(frame, candidates(frame, horizon=3))
    trade = result.trades.iloc[0]
    assert trade.unresolved_terminal_mark
    assert not trade.data_gap_exit
    assert trade.exit == 100
    assert not trade.completed
    assert result.metrics["trades"] == 0
    assert result.metrics["unresolved_positions"] == 1


def test_post_evaluation_open_is_not_used_for_gap_or_fill():
    frame = bars().iloc[[0, 1, 7]].copy()
    frame.loc[frame.index[-1], ["open", "high", "low", "close"]] = [50, 50.1, 49.9, 50]
    result = run(frame, candidates(frame, horizon=2), end=frame.index[0] + pd.Timedelta(minutes=4))
    assert result.trades.iloc[0].unresolved_terminal_mark
    assert result.trades.iloc[0].exit == 100


def test_known_end_gate_precedes_future_price_access():
    frame = bars()
    result = run(frame, candidates(frame, horizon=3), end=frame.index[3])
    assert result.decision_counts == {"known_evaluation_boundary": 1}


def test_shared_portfolio_arbitration_and_exit_time_reentry():
    frame = bars()
    signals = pd.concat(
        [
            candidates(frame, indices=(1, 2), asset="EURUSD", score=0.1),
            candidates(frame, asset="XAUUSD", score=0.2),
        ],
        ignore_index=True,
    )
    result = backtest(
        signals,
        {a: frame for a in ("EURUSD", "XAUUSD")},
        {a: instrument(a) for a in ("EURUSD", "XAUUSD")},
        {a: CostScenario() for a in ("EURUSD", "XAUUSD")},
        SimulationConfig(),
        frame.index[0],
        frame.index[-1],
    )
    assert list(result.trades.asset) == ["XAUUSD", "EURUSD"]
    assert result.decision_counts["portfolio_position_conflict"] == 1
    assert result.trades.entry_time.iloc[1] >= result.trades.exit_time.iloc[0]


def test_tied_assets_and_input_reordering_are_deterministic():
    frame = bars()
    signals = pd.concat([candidates(frame, asset=a) for a in ASSETS], ignore_index=True)
    args = (
        {a: frame for a in ASSETS},
        {a: instrument(a) for a in ASSETS},
        {a: CostScenario() for a in ASSETS},
        SimulationConfig(),
        frame.index[0],
        frame.index[-1],
    )
    first = backtest(signals, *args)
    second = backtest(signals.iloc[::-1], *args)
    assert first.trades.asset.tolist() == ["EURUSD"]
    assert_frame_equal(first.trades, second.trades)
    assert_series_equal(first.curve, second.curve)


def test_seven_subsets_replay_position_conflicts_instead_of_summing_returns():
    frame = bars()
    frame.loc[frame.index[1], "high"] = 103
    signals = pd.concat([candidates(frame, asset=a) for a in ASSETS], ignore_index=True)
    results = compare_portfolios(
        signals,
        {a: frame for a in ASSETS},
        {a: instrument(a) for a in ASSETS},
        {a: CostScenario() for a in ASSETS},
        SimulationConfig(),
        frame.index[0],
        frame.index[-1],
    )
    assert len(results) == len(PORTFOLIO_SUBSETS) == 7
    assert all(result.metrics["trades"] == 1 for result in results.values())
    assert results["EURUSD+XAUUSD+WTI"].metrics["net_return"] < sum(
        results[a].metrics["net_return"] for a in ASSETS
    )
    assert "2025" in results["EURUSD"].metrics["by_year"]


def test_cost_components_and_overnight_financing_are_explicit():
    frame = bars(4, start="2025-01-06T23:58Z")
    cost = CostScenario(
        spread_bps=2, commission_bps_roundtrip=3, slippage_bps_per_side=1, financing_bps_per_day=4
    )
    result = run(frame, candidates(frame, horizon=2), cost=cost)
    trade = result.trades.iloc[0]
    assert trade.financing_cost == pytest.approx(100 * trade.quantity * 4 / 10000)
    assert trade.cost == pytest.approx(100 * trade.quantity * 11 / 10000)
    assert trade.net_pnl == -trade.cost
    assert trade.planned_risk_fraction <= 0.005


def test_cost_increase_cannot_improve_fixed_no_move_trade():
    frame = bars()
    cheap = run(frame, cost=CostScenario(spread_bps=1))
    expensive = run(frame, cost=CostScenario(spread_bps=5))
    assert expensive.trades.net_pnl.sum() < cheap.trades.net_pnl.sum()


def test_precision_and_quantity_round_down_within_risk_budget():
    frame = bars(price=100.001)
    result = run(
        frame,
        candidates(frame, stop_distance=1.001),
        spec=instrument(quantity_step=0.1, minimum_quantity=0.1),
    )
    trade = result.trades.iloc[0]
    assert trade.entry == 100.01
    assert trade.stop_loss == 99
    assert trade.planned_risk_usd <= 50
    assert trade.quantity * 10 == pytest.approx(round(trade.quantity * 10))


def test_contract_size_is_applied_once_to_pnl_and_risk():
    frame = bars()
    frame.loc[frame.index[1], "high"] = 103
    result = run(frame, spec=instrument(contract_size=10))
    trade = result.trades.iloc[0]
    assert trade.quantity == 5
    assert trade.measurement_units == 50
    assert trade.net_pnl == 100


def test_frozen_ctrader_units_adapter_and_unverified_cap_evidence():
    spec = InstrumentSpec.from_ctrader(
        "WTI",
        {
            "digits": 2,
            "specifications": {
                "minVolume": 100,
                "stepVolume": 100,
                "maxVolume": 500000,
                "lotSize": 100000,
                "measurementUnits": "barrels",
            },
        },
        leverage_cap=10,
    )
    assert spec.minimum_quantity == spec.quantity_step == 1
    assert spec.maximum_quantity == 5000
    assert spec.contract_size == 1
    assert spec.lot_size_units == 1000
    assert "unverified" in spec.metadata_evidence


def test_quantity_below_minimum_is_rejected():
    result = run(spec=instrument(minimum_quantity=100))
    assert result.decision_counts == {"quantity_below_minimum": 1}


def test_no_maximizing_leverage_and_instrument_specific_caps():
    frame = bars()
    signals = candidates(frame, stop_distance=0.1)
    results = evaluate_leverage_scenarios(
        signals,
        {"EURUSD": frame},
        {"EURUSD": instrument(leverage_cap=2)},
        {"EURUSD": CostScenario()},
        SimulationConfig(),
        frame.index[0],
        frame.index[-1],
    )
    assert tuple(int(x) for x in results) == LEVERAGE_SCENARIOS
    assert all(result.metrics["trades"] == 0 for result in results.values())
    sufficient = evaluate_leverage_scenarios(
        candidates(frame),
        {"EURUSD": frame},
        {"EURUSD": instrument()},
        {"EURUSD": CostScenario()},
        SimulationConfig(),
        frame.index[0],
        frame.index[-1],
    )
    assert {result.trades.quantity.iloc[0] for result in sufficient.values()} == {50}
    assert sufficient["30"].trades.actual_leverage.iloc[0] == 0.5


def test_insufficient_margin_after_cost_reservation_is_explicit():
    # Planned unit risk=.49+.01=.5; 100 units require all capital as margin,
    # leaving nothing for the reserved cost. Quantity is not reduced to use leverage.
    result = run(
        signals=candidates(bars(), stop_distance=0.49),
        cost=CostScenario(spread_bps=1),
    )
    assert result.decision_counts == {"insufficient_margin": 1}


def test_margin_closeout_can_precede_stop_under_stressed_maintenance_scenario():
    frame = bars()
    frame.loc[frame.index[1], "low"] = 99.6
    result = run(
        frame,
        candidates(frame, stop_distance=0.5),
        config=SimulationConfig(margin_closeout_fraction=0.999),
    )
    trade = result.trades.iloc[0]
    assert trade.exit_reason == "margin_closeout"
    assert trade.exit == pytest.approx(99.9)


def test_adverse_intrabar_drawdown_not_only_closing_equity():
    frame = bars()
    frame.loc[frame.index[1], ["high", "low"]] = [103, 99.5]
    result = run(frame)
    assert result.metrics["net_return"] == pytest.approx(0.01)
    assert result.metrics["maximum_mark_to_market_drawdown"] == pytest.approx(0.0025)
    assert result.metrics["realized_attribution_drawdown"] == 0


def test_small_sample_uncertainty_never_disappears_on_a_win():
    frame = bars()
    frame.loc[frame.index[1], "high"] = 103
    result = run(frame)
    assert result.metrics["profit_factor"] is None
    assert result.metrics["statistically_inconclusive"]
    assert result.metrics["win_rate_wilson_95_descriptive"][0] < 0.3
    assert result.metrics["uncertainty"]["net_pnl_95_interval"] is None
    assert result.metrics["uncertainty"]["trade_count_95_interval"] is None
    assert result.metrics["uncertainty"]["profit_factor_95_interval"] is None


def test_block_uncertainty_includes_activity_and_pf_with_zero_trade_days():
    days = pd.date_range("2025-01-06T00:00Z", periods=60, freq="D")
    frame = pd.concat([bars(2, start=day) for day in days])
    traded = [day for i, day in enumerate(days) if i % 3 != 0]
    for i, day in enumerate(traded):
        frame.loc[day + pd.Timedelta(minutes=1), "high" if i % 2 else "low"] = 103 if i % 2 else 98
    signals = candidates(
        frame, indices=tuple(frame.index.get_loc(day + pd.Timedelta(minutes=1)) for day in traded)
    )
    result = run(frame, signals)
    uncertainty = result.metrics["uncertainty"]
    assert result.metrics["trades"] == 40
    assert result.metrics["zero_trade_observed_days"] == 20
    assert uncertainty["trade_count_95_interval"][0] < 40
    assert uncertainty["trade_count_95_interval"][1] > 40
    assert 0 < uncertainty["profit_factor_95_interval"][0]
    assert (
        uncertainty["profit_factor_95_interval"][0] <= uncertainty["profit_factor_95_interval"][1]
    )
    assert uncertainty["profit_factor_defined_replicates"] == 256
    assert uncertainty == run(frame, signals).metrics["uncertainty"]


def test_diagnostics_exceptions_and_record_mutation_cannot_change_decisions():
    frame = bars()
    signals = candidates(frame, indices=(1, 2, 3))
    baseline = run(frame, signals)
    seen = []

    def failing_callback(record):
        seen.append(dict(record))
        record.update(accepted=False, probability=0, equity_after=-100000)
        raise RuntimeError("synthetic logging failure")

    traced = run(frame, signals, diagnostic=failing_callback)
    assert_frame_equal(baseline.trades, traced.trades)
    assert_series_equal(baseline.curve, traced.curve)
    assert baseline.metrics == traced.metrics
    assert traced.diagnostics_errors == len(seen) == 6
    assert {r["record_type"] for r in seen} == {"execution_decision", "execution_outcome"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("risk_fraction", 0.00501),
        ("risk_fraction", 0),
        ("leverage", 3),
        ("leverage", 31),
        ("max_positions", 2),
        ("margin_closeout_fraction", 0),
        ("initial_equity", float("nan")),
    ],
)
def test_invalid_risk_configuration_fails_closed(field, value):
    with pytest.raises(ValueError):
        SimulationConfig(**{field: value})


@pytest.mark.parametrize(
    "kwargs",
    [
        {"spread_bps": -1},
        {"financing_bps_per_day": -1},
        {"slippage_bps_per_side": np.inf},
    ],
)
def test_invalid_cost_scenario_rejected(kwargs):
    with pytest.raises(ValueError):
        CostScenario(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"quote_currency": "EUR"},
        {"minimum_quantity": 0},
        {"quantity_step": 0},
        {"contract_size": -1},
        {"leverage_cap": np.nan},
        {"price_digits": -1},
    ],
)
def test_instrument_metadata_fails_closed(kwargs):
    with pytest.raises(ValueError):
        instrument(**kwargs)


def test_bad_ohlc_or_timestamps_rejected():
    frame = bars()
    bad = frame.copy()
    bad.loc[bad.index[1], "low"] = 101
    with pytest.raises(ValueError, match="Inconsistent OHLC"):
        run(bad)
    with pytest.raises(ValueError, match="unique increasing"):
        run(frame.iloc[::-1], start=frame.index[0], end=frame.index[-1] + pd.Timedelta(minutes=1))
    with pytest.raises(ValueError, match="unique increasing"):
        run(pd.concat([frame, frame.iloc[-1:]]))
    naive = frame.tz_localize(None)
    with pytest.raises(ValueError, match="timezone-aware"):
        run(naive, start=frame.index[0], end=frame.index[-1])


def test_naive_signal_timestamp_rejected():
    frame = bars()
    signals = candidates(frame)
    signals["time"] = signals.time.dt.tz_localize(None)
    with pytest.raises(ValueError, match="timezone-aware"):
        run(frame, signals)


def test_equivalent_local_timezone_bars_replay_in_utc_without_mutating_inputs():
    frame = bars()
    local = frame.tz_convert("Europe/Budapest")
    signals = candidates(frame)
    baseline = run(frame, signals)
    converted = run(
        local, signals, start=frame.index[0], end=frame.index[-1] + pd.Timedelta(minutes=5)
    )
    assert_frame_equal(baseline.trades, converted.trades)
    assert_series_equal(baseline.curve, converted.curve)
    assert str(local.index.tz) == "Europe/Budapest"


def test_instrument_selection_attribution_and_regimes():
    frame = bars()
    signals = candidates(frame, indices=(1, 2), regime="range")
    extra = candidates(frame, indices=(3,), asset="WTI")
    result = run(frame, pd.concat([signals, extra], ignore_index=True))
    assert result.decision_counts["instrument_not_selected"] == 1
    assert result.metrics["by_instrument"]["EURUSD"]["trades"] == 2
    assert result.metrics["by_regime"]["range"]["trades"] == 2


def test_full_year_activity_and_partial_year_distinction():
    frame = bars()
    full = run(
        frame, start=pd.Timestamp("2025-01-01T00:00Z"), end=pd.Timestamp("2026-01-01T00:00Z")
    )
    assert full.metrics["by_year"]["2025"]["full_evaluation_year"]
    assert not full.metrics["by_year"]["2025"]["minimum_activity_pass"]
    partial = run(frame)
    assert partial.metrics["by_year"]["2025"]["minimum_activity_pass"] is None


def test_repeated_execution_is_identical_and_frozen_inputs_unchanged():
    frame = bars()
    signals = candidates(frame, indices=(1, 2))
    original_frame, original_signals = frame.copy(), signals.copy()
    first, second = run(frame, signals), run(frame, signals)
    assert_frame_equal(first.trades, second.trades)
    assert_series_equal(first.curve, second.curve)
    assert_frame_equal(original_frame, frame)
    assert_frame_equal(original_signals, signals)
    assert first.metrics == second.metrics


def test_leverage_cap_changes_margin_without_resizing_a_valid_trade():
    frame = bars()
    low = run(frame, spec=instrument(leverage_cap=2), config=SimulationConfig(leverage=30))
    high = run(frame, spec=instrument(leverage_cap=30), config=SimulationConfig(leverage=30))
    assert low.trades.quantity.iloc[0] == high.trades.quantity.iloc[0]
    assert low.trades.required_margin.iloc[0] > high.trades.required_margin.iloc[0]


def test_max_quantity_reduces_risk_without_exceeding_it():
    result = run(spec=instrument(maximum_quantity=20))
    assert result.trades.quantity.iloc[0] == 20
    assert result.trades.planned_risk_fraction.iloc[0] == 0.002


def test_evaluation_window_and_missing_required_fields_fail_closed():
    frame = bars()
    with pytest.raises(ValueError, match="must increase"):
        run(frame, end=frame.index[0])
    with pytest.raises(ValueError, match="Missing decision-time"):
        run(frame, candidates(frame).drop(columns="stop_distance"))
    with pytest.raises(ValueError, match="identity mismatch"):
        run(frame, spec=replace(instrument(), asset="WTI"))
