"""Deterministic reference-OHLC portfolio simulation; no broker or database access.

All prices are unverified reference prices. Spread/slippage/commission are separate
scenario debits, rather than invented historical bid/ask fills. Signals are stamped
at a completed candle's close; entry requires the next contiguous observed open at
exactly that timestamp. One shared account is replayed for every portfolio subset.
"""

from collections import Counter
from dataclasses import asdict, dataclass, replace
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from itertools import combinations
from math import isfinite, sqrt

import numpy as np
import pandas as pd

MINUTE = pd.Timedelta(minutes=1)
LEVERAGE_SCENARIOS = (1, 2, 5, 10, 20, 30)
ASSETS = ("EURUSD", "XAUUSD", "WTI")
PORTFOLIO_SUBSETS = tuple(subset for size in range(1, 4) for subset in combinations(ASSETS, size))


@dataclass(frozen=True)
class InstrumentSpec:
    """Quantity is contracts; contract_size converts contracts to USD-quoted units.

    cTrader min/step/maxVolume are hundredths of measurement units, so the adapter
    uses contract_size=1. lotSize is retained as metadata, never multiplied twice.
    Leverage caps are explicit assumptions: leverageId alone cannot establish them.
    """

    asset: str
    price_digits: int
    minimum_quantity: float
    quantity_step: float
    maximum_quantity: float
    leverage_cap: float
    contract_size: float = 1.0
    quote_currency: str = "USD"
    metadata_evidence: str = "unverified_scenario"
    measurement_units: str = "unknown"
    lot_size_units: float | None = None

    def __post_init__(self):
        values = (
            self.minimum_quantity,
            self.quantity_step,
            self.maximum_quantity,
            self.leverage_cap,
            self.contract_size,
        )
        if not all(isfinite(v) and v > 0 for v in values):
            raise ValueError("Instrument quantity, contract size and leverage must be positive")
        if self.minimum_quantity > self.maximum_quantity:
            raise ValueError("Minimum quantity exceeds maximum quantity")
        if not isinstance(self.price_digits, int) or not 0 <= self.price_digits <= 10:
            raise ValueError("Invalid price precision")
        if self.quote_currency != "USD":
            raise ValueError("Only explicitly USD-quoted instruments are supported")
        if self.asset not in ASSETS:
            raise ValueError("Unsupported research instrument")

    @classmethod
    def from_ctrader(cls, asset, metadata, leverage_cap):
        spec = metadata["specifications"]
        return cls(
            asset=asset,
            price_digits=int(spec.get("digits", metadata.get("digits", 5))),
            minimum_quantity=spec["minVolume"] / 100,
            quantity_step=spec["stepVolume"] / 100,
            maximum_quantity=spec["maxVolume"] / 100,
            leverage_cap=leverage_cap,
            measurement_units=spec.get("measurementUnits", "unknown"),
            lot_size_units=spec["lotSize"] / 100 if "lotSize" in spec else None,
            metadata_evidence="frozen_current_contract; leverage_cap_unverified_scenario",
        )


@dataclass(frozen=True)
class CostScenario:
    spread_bps: float = 0.0
    commission_bps_roundtrip: float = 0.0
    slippage_bps_per_side: float = 0.0
    financing_bps_per_day: float = 0.0
    name: str = "unverified_scenario"

    def __post_init__(self):
        values = (
            self.spread_bps,
            self.commission_bps_roundtrip,
            self.slippage_bps_per_side,
            self.financing_bps_per_day,
        )
        if not all(isfinite(v) and v >= 0 for v in values):
            raise ValueError("Cost scenarios must be finite and nonnegative")

    @property
    def roundtrip_fraction(self):
        return (
            self.spread_bps + self.commission_bps_roundtrip + 2 * self.slippage_bps_per_side
        ) / 10000

    def per_unit(self, entry, rollover_days=0):
        """Flat USD debit using entry notional, including each UTC midnight crossed."""
        return entry * (
            self.roundtrip_fraction + rollover_days * self.financing_bps_per_day / 10000
        )


@dataclass(frozen=True)
class SimulationConfig:
    initial_equity: float = 10000.0
    risk_fraction: float = 0.005
    leverage: float = 1.0
    max_positions: int = 1
    margin_closeout_fraction: float = 0.5
    reward_risk: float = 2.0
    bootstrap_seed: int = 20261023

    def __post_init__(self):
        if not isfinite(self.initial_equity) or self.initial_equity <= 0:
            raise ValueError("Positive initial equity required")
        if not isfinite(self.risk_fraction) or not 0 < self.risk_fraction <= 0.005:
            raise ValueError("Planned risk must be positive and at most 0.5%")
        if self.leverage not in LEVERAGE_SCENARIOS:
            raise ValueError("Leverage must be one of 1, 2, 5, 10, 20, 30")
        if self.max_positions != 1:
            raise ValueError("Exactly one portfolio position is supported")
        if not isfinite(self.margin_closeout_fraction) or not (
            0 < self.margin_closeout_fraction <= 1
        ):
            raise ValueError("Margin closeout fraction must be in (0, 1]")
        if not isfinite(self.reward_risk) or self.reward_risk <= 0:
            raise ValueError("Positive reward/risk required")


@dataclass
class SimulationResult:
    trades: pd.DataFrame
    curve: pd.Series
    metrics: dict
    decision_counts: dict
    diagnostics_errors: int = 0


def _utc(value):
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError("Timezone-aware UTC-compatible timestamps required")
    return stamp.tz_convert("UTC")


def _price(value, digits, upwards):
    rounding = ROUND_CEILING if upwards else ROUND_FLOOR
    return float(Decimal(str(value)).quantize(Decimal(1).scaleb(-digits), rounding=rounding))


def _quantity(value, spec):
    # Decimal rounding avoids a quantity larger than the stop-risk budget.
    value = min(value, spec.maximum_quantity)
    step = Decimal(str(spec.quantity_step))
    return float((Decimal(str(value)) / step).to_integral_value(rounding=ROUND_FLOOR) * step)


def _rollovers(start, end):
    return max(0, (end.normalize() - start.normalize()).days)


def _validate_bars(frame):
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("Bars require timezone-aware opened_at index")
    if not frame.index.is_monotonic_increasing or not frame.index.is_unique:
        raise ValueError("Bars must have unique increasing timestamps")
    if not {"open", "high", "low", "close"}.issubset(frame.columns):
        raise ValueError("Missing OHLC columns")
    # Validate one column at a time to avoid an extra full-frame float copy.
    for name in ("open", "high", "low", "close"):
        if not (np.isfinite(frame[name]).all() and frame[name].gt(0).all()):
            raise ValueError("OHLC prices must be finite and positive")
    if not (
        frame.high.ge(frame.open).all()
        and frame.high.ge(frame.close).all()
        and frame.low.le(frame.open).all()
        and frame.low.le(frame.close).all()
        and frame.high.ge(frame.low).all()
    ):
        raise ValueError("Inconsistent OHLC")
    if (frame.index.asi8 % MINUTE.value != 0).any():
        raise ValueError("Bars must align to minute opens")


def _exit_path(
    frame,
    entry_i,
    end,
    entry,
    stop,
    target,
    side,
    horizon,
    quantity,
    equity,
    margin,
    scenario,
    config,
    price_digits,
):
    """Observe one asset sequentially, checking censorship before any unseen gap.

    A missing interval is not filled: an open position is conservatively stopped at
    the first observed post-gap open at the worse of stop/open. Without an observed
    post-gap open inside the evaluation, the last close is an unresolved mark.
    OHLC ambiguity resolves on the adverse path before any favourable target.
    """
    entry_time = frame.index[entry_i]
    marks = []
    previous_time = entry_time - MINUTE
    last_price, last_time = entry, entry_time
    for offset in range(horizon):
        j = entry_i + offset
        if j >= len(frame) or frame.index[j] + MINUTE > end:
            return last_price, last_time, "unresolved_terminal_mark", marks, False
        stamp = frame.index[j]
        bar = frame.iloc[j]
        financing_days = _rollovers(entry_time, stamp + MINUTE)
        fees = scenario.per_unit(entry, financing_days) * quantity
        # Constant entry margin scenario; dynamic broker margin tiers unavailable.
        closeout = entry + side * (config.margin_closeout_fraction * margin - equity + fees) / (
            quantity
        )
        crossed_gap = stamp != previous_time + MINUTE
        if crossed_gap:
            price = min(stop, bar.open) if side == 1 else max(stop, bar.open)
            price = _price(price, price_digits, side == -1)
            marks.append((stamp, equity + side * (price - entry) * quantity - fees))
            return float(price), stamp, "data_gap_stop", marks, False

        adverse = float(bar.low if side == 1 else bar.high)
        stop_hit = adverse <= stop if side == 1 else adverse >= stop
        margin_hit = adverse <= closeout if side == 1 else adverse >= closeout
        target_hit = bar.high >= target if side == 1 else bar.low <= target
        ambiguous = bool(stop_hit and target_hit)
        if stop_hit or margin_hit:
            # The first barrier on an adverse path is the nearer barrier. An
            # opening jump through it executes at the worse observed open.
            barrier = stop
            reason = "stop_loss"
            if margin_hit and (not stop_hit or side * (closeout - stop) > 0):
                barrier, reason = closeout, "margin_closeout"
            price = min(bar.open, barrier) if side == 1 else max(bar.open, barrier)
            price = _price(price, price_digits, side == -1)
            when = stamp if side * (bar.open - barrier) <= 0 else stamp + MINUTE
            marks.append((when, equity + side * (price - entry) * quantity - fees))
            return float(price), when, reason, marks, ambiguous

        marks.append((stamp + MINUTE, equity + side * (adverse - entry) * quantity - fees))
        if target_hit:
            marks.append((stamp + MINUTE, equity + side * (target - entry) * quantity - fees))
            return float(target), stamp + MINUTE, "take_profit", marks, False
        last_price, last_time = float(bar.close), stamp + MINUTE
        marks.append((last_time, equity + side * (last_price - entry) * quantity - fees))
        previous_time = stamp
    last_price = _price(last_price, price_digits, side == -1)
    return last_price, last_time, "horizon", marks, False


def backtest(candidates, bars_by_asset, instruments, costs, config, start, end, diagnostic=None):
    """Replay a single account. Diagnostics receive detached records after decisions.

    Candidates contain decision-time information only: time, asset, direction,
    score, probability, stop_distance, horizon; confidence defaults to zero.
    Arbitration sorts by time, score descending, confidence descending, asset,
    direction, model_id. Exact duplicate decisions are rejected explicitly.
    Quantity is determined by stop risk, never by filling available leverage.
    Insufficient margin or leverage rejects the intended risk-sized trade.
    """
    if not isinstance(config, SimulationConfig):
        raise TypeError("config must be SimulationConfig")
    start, end = _utc(start), _utc(end)
    if start >= end:
        raise ValueError("Evaluation window must increase")
    required = {"time", "asset", "direction", "score", "probability", "stop_distance", "horizon"}
    if not required.issubset(candidates.columns):
        raise ValueError("Missing decision-time opportunity fields")
    for asset, frame in bars_by_asset.items():
        _validate_bars(frame)
        if asset not in instruments or asset not in costs:
            raise ValueError("Missing instrument or cost scenario")
        if instruments[asset].asset != asset:
            raise ValueError("Instrument identity mismatch")
    normalized_bars = {}
    for asset, frame in bars_by_asset.items():
        if str(frame.index.tz) != "UTC":
            frame = frame.copy(deep=False)
            frame.index = frame.index.tz_convert("UTC")
        normalized_bars[asset] = frame
    bars_by_asset = normalized_bars

    candidate_times = pd.DatetimeIndex(candidates.time)
    if len(candidate_times) and candidate_times.tz is None:
        raise ValueError("Signals require timezone-aware timestamps")
    ranked = candidates.copy(deep=False).assign(
        time=pd.to_datetime(candidate_times, utc=True),
        confidence=candidates.get("confidence", 0.0),
        model_id=candidates.get("model_id", "unspecified"),
    )
    ranked = ranked.sort_values(
        [
            "time",
            "score",
            "confidence",
            "asset",
            "direction",
            "model_id",
            "probability",
            "horizon",
            "stop_distance",
        ],
        ascending=[True, False, False, True, True, True, False, True, True],
        kind="stable",
    )
    counts = Counter()
    ledger, marks = [], [(start, config.initial_equity)]
    equity, busy_until = config.initial_equity, start
    diagnostic_errors = 0
    seen = set()
    seen_time = None

    def emit(record):
        nonlocal diagnostic_errors
        if diagnostic is not None:
            # Callback exceptions/mutations cannot change the computed simulation.
            try:
                diagnostic(dict(record))
            except Exception:
                diagnostic_errors += 1

    columns = list(ranked.columns)
    for values in ranked.itertuples(index=False, name=None):
        # One detached candidate at a time; never materialize all row dictionaries.
        row = dict(zip(columns, values, strict=True))
        when, asset = row["time"], row["asset"]
        if when != seen_time:
            seen.clear()
            seen_time = when
        trace = {
            "record_type": "execution_decision",
            "event": "execution_decision",
            "asset": asset,
            "instrument": asset,
            "timestamp": when.isoformat(),
            "model_id": row["model_id"],
            "model_version": row.get("model_version", "unspecified"),
            "research_window": row.get("research_window", row.get("window", "unspecified")),
            "probability": row["probability"],
            "threshold": row.get("threshold"),
            "candidate_signal": row["direction"],
            "existing_position": bool(when < busy_until),
            "accepted": False,
        }
        reason = None
        if not start <= when < end:
            reason = "outside_evaluation_window"
        elif asset not in bars_by_asset:
            reason = "instrument_not_selected"
        elif when < busy_until:
            reason = "portfolio_position_conflict"
        elif equity <= 0:
            reason = "nonpositive_equity"
        else:
            numeric = [row[k] for k in ("score", "probability", "stop_distance", "horizon")]
            if not all(isinstance(v, (int, float)) and isfinite(v) for v in numeric):
                reason = "invalid_signal"
            elif row["direction"] not in (-1, 1) or not 0 <= row["probability"] <= 1:
                reason = "invalid_signal"
            elif row["horizon"] < 1 or row["horizon"] != int(row["horizon"]):
                reason = "invalid_horizon"
            elif row["stop_distance"] <= 0:
                reason = "invalid_stop_distance"
            elif when + int(row["horizon"]) * MINUTE > end:
                reason = "known_evaluation_boundary"
        if reason is None:
            key = (when, asset, row["direction"], row["model_id"])
            if key in seen:
                reason = "duplicate_signal"
            seen.add(key)
        if reason is None:
            frame, spec, scenario = bars_by_asset[asset], instruments[asset], costs[asset]
            i = int(frame.index.searchsorted(when))
            if i >= len(frame) or frame.index[i] != when:
                reason = "missing_next_observed_entry"
            elif i == 0 or frame.index[i - 1] + MINUTE != when:
                reason = "missing_completed_signal_bar"
        if reason is None:
            side = row["direction"]
            entry = _price(float(frame.open.iloc[i]), spec.price_digits, side == 1)
            stop = _price(entry - side * row["stop_distance"], spec.price_digits, side == -1)
            target = _price(
                entry + side * row["stop_distance"] * config.reward_risk,
                spec.price_digits,
                side == -1,
            )
            distance = abs(entry - stop)
            if min(stop, target) <= 0 or distance <= 0 or side * (target - entry) <= 0:
                reason = "invalid_rounded_barriers"
        if reason is None:
            planned_days = _rollovers(when, when + int(row["horizon"]) * MINUTE)
            planned_per_unit = distance + scenario.per_unit(entry, planned_days)
            contracts = _quantity(
                equity * config.risk_fraction / (planned_per_unit * spec.contract_size), spec
            )
            units = contracts * spec.contract_size
            notional = units * entry
            effective_cap = min(config.leverage, spec.leverage_cap)
            margin = notional / effective_cap
            reserved_cost = scenario.per_unit(entry, planned_days) * units
            planned_risk = planned_per_unit * units
            trace.update(
                position_size=contracts,
                measurement_units=units,
                planned_risk_usd=planned_risk,
                planned_risk_fraction=planned_risk / equity,
                available_leverage=config.leverage,
                instrument_leverage_cap=spec.leverage_cap,
                actual_leverage=notional / equity,
                required_margin=margin,
            )
            if contracts < spec.minimum_quantity:
                reason = "quantity_below_minimum"
            elif planned_risk > equity * config.risk_fraction + 1e-9:
                reason = "planned_risk_exceeded"
            elif notional > equity * effective_cap + 1e-9:
                reason = "leverage_limit"
            elif margin + reserved_cost > equity + 1e-9:
                reason = "insufficient_margin"
        if reason is not None:
            counts[reason] += 1
            trace["rejection_reason"] = reason
            trace["reason"] = reason
            emit(trace)
            continue

        # All decisions are complete before any path/outcome is inspected.
        exit_price, exit_time, exit_reason, position_marks, ambiguous = _exit_path(
            frame,
            i,
            end,
            entry,
            stop,
            target,
            side,
            int(row["horizon"]),
            units,
            equity,
            margin,
            scenario,
            config,
            spec.price_digits,
        )
        censored = exit_reason == "unresolved_terminal_mark"
        rollover_days = _rollovers(when, exit_time)
        component_costs = {
            "spread_cost": entry * units * scenario.spread_bps / 10000,
            "commission_cost": entry * units * scenario.commission_bps_roundtrip / 10000,
            "slippage_cost": entry * units * 2 * scenario.slippage_bps_per_side / 10000,
            "financing_cost": entry
            * units
            * rollover_days
            * scenario.financing_bps_per_day
            / 10000,
        }
        fees = sum(component_costs.values())
        gross = side * (exit_price - entry) * units
        pnl = gross - fees
        previous_equity = equity
        equity += pnl
        trade = {
            "asset": asset,
            "entry_time": when,
            "exit_time": exit_time,
            "direction": "long" if side == 1 else "short",
            "quantity": contracts,
            "measurement_units": units,
            "contract_size": spec.contract_size,
            "entry": entry,
            "exit": exit_price,
            "stop_loss": stop,
            "take_profit": target,
            "gross_pnl": gross,
            "cost": fees,
            **component_costs,
            "net_pnl": pnl,
            "equity_before": previous_equity,
            "equity_after": equity,
            "account_return": pnl / previous_equity,
            "planned_risk_usd": planned_risk,
            "planned_risk_fraction": planned_risk / previous_equity,
            "realized_loss_fraction": max(0, -pnl) / previous_equity,
            "required_margin": margin,
            "available_leverage": config.leverage,
            "effective_leverage_cap": effective_cap,
            "actual_leverage": notional / previous_equity,
            "exit_reason": exit_reason,
            "ambiguous": ambiguous,
            "data_gap_exit": exit_reason == "data_gap_stop",
            "unresolved_terminal_mark": censored,
            "completed": not censored,
            "regime": row.get("regime", "unknown"),
            "probability": row["probability"],
            "score": row["score"],
            "model_id": row["model_id"],
            "model_version": row.get("model_version", "unspecified"),
            "threshold": row.get("threshold"),
            "cost_scenario": scenario.name,
            "contract_evidence": spec.metadata_evidence,
        }
        ledger.append(trade)
        marks.extend(position_marks)
        if not position_marks:
            marks.append((exit_time, equity))
        else:
            marks[-1] = (exit_time, equity)
        busy_until = end if censored else exit_time
        counts["accepted"] += 1
        trace.update(accepted=True, rejection_reason=None, reason="accepted")
        emit(trace)
        # Full ledger records contain detached primitives only.
        emit(
            {
                "record_type": "execution_outcome",
                "event": "execution_outcome",
                "reason": exit_reason,
                **trade,
            }
        )

    marks.append((end, equity))
    trades = pd.DataFrame(ledger)
    curve = pd.Series(
        [float(v) for _, v in marks],
        index=pd.DatetimeIndex([t for t, _ in marks], name="timestamp"),
        name="equity_usd",
    )
    observed_days = set()
    for frame in bars_by_asset.values():
        selected = frame.index[(frame.index >= start) & (frame.index < end)]
        observed_days.update(selected.normalize())
    summary = metrics(trades, curve, config, start, end, sorted(observed_days))
    summary["instruments"] = sorted(bars_by_asset)
    summary["decision_counts"] = dict(sorted(counts.items()))
    return SimulationResult(trades, curve, summary, dict(sorted(counts.items())), diagnostic_errors)


def _trade_summary(trades, initial_equity):
    pnl = trades.net_pnl.to_numpy(float) if len(trades) else np.array([], dtype=float)
    completed = trades[trades.completed] if len(trades) else trades
    outcomes = completed.net_pnl.to_numpy(float) if len(completed) else np.array([], dtype=float)
    wins, losses = outcomes[outcomes > 0], outcomes[outcomes < 0]
    n, winning = len(outcomes), int((outcomes > 0).sum())
    interval = None
    if n:
        # Wilson descriptive binomial interval; serial dependence remains a caveat.
        z, p = 1.959963984540054, winning / n
        denominator = 1 + z * z / n
        center = (p + z * z / (2 * n)) / denominator
        radius = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
        interval = [max(0.0, center - radius), min(1.0, center + radius)]
    contribution = np.r_[initial_equity, initial_equity + np.cumsum(pnl)]
    return {
        "trades": n,
        "completed_trades": n,
        "unresolved_positions": len(trades) - n,
        "unresolved_terminal_positions": len(trades) - n,
        "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) else None,
        "profit_factor_undefined_reason": "no_losing_completed_trades" if not len(losses) else None,
        "net_pnl_usd": float(pnl.sum()),
        "net_return_contribution": float(pnl.sum() / initial_equity),
        "win_rate": winning / n if n else None,
        "win_rate_wilson_95_descriptive": interval,
        "average_win_usd": float(wins.mean()) if len(wins) else None,
        "average_loss_usd": float(losses.mean()) if len(losses) else None,
        "average_net_pnl_per_completed_trade": float(outcomes.mean()) if n else None,
        "total_cost_usd": float(trades.cost.sum()) if len(trades) else 0.0,
        "realized_attribution_drawdown": float(
            np.max(1 - contribution / np.maximum.accumulate(contribution))
        ),
        "average_holding_minutes": float(
            (completed.exit_time - completed.entry_time).dt.total_seconds().mean() / 60
        )
        if n
        else None,
        "sample_warning": "fewer_than_100_completed_trades" if n < 100 else None,
    }


def _block_uncertainty(trades, observed_days, seed):
    if not len(trades) or int(trades.completed.sum()) < 30 or len(observed_days) < 20:
        return {
            "method": "five_observed_day_circular_block_bootstrap; descriptive_only",
            "net_pnl_95_interval": None,
            "trade_count_95_interval": None,
            "profit_factor_95_interval": None,
            "reason": "requires_30_completed_trades_and_20_observed_days",
        }
    completed = trades[trades.completed]
    daily_records = pd.DataFrame(
        {
            "net_pnl": completed.net_pnl,
            "trade_count": 1.0,
            "profits": completed.net_pnl.clip(lower=0),
            "losses": -completed.net_pnl.clip(upper=0),
        }
    )
    # Entry-day cohorts keep all completed trades when the exit is at midnight
    # after the final observed bar, and keep zero-opportunity days in the sample.
    daily = daily_records.groupby(completed.entry_time.dt.normalize()).sum()
    day_index = pd.DatetimeIndex(sorted(set(observed_days) | set(daily.index)))
    values = daily.reindex(day_index, fill_value=0).to_numpy(float)
    rng = np.random.default_rng(seed)
    samples = np.empty((256, 4))
    for i in range(len(samples)):
        beginnings = rng.integers(0, len(values), size=(len(values) + 4) // 5)
        ix = ((beginnings[:, None] + np.arange(5)) % len(values)).ravel()[: len(values)]
        samples[i] = values[ix].sum(axis=0)
    valid_pf = (samples[:, 2] > 0) & (samples[:, 3] > 0)
    # Lossless replicates do not imply infinite precision or a finite PF bound.
    enough_pf = (
        valid_pf.mean() >= 0.9
        and completed.net_pnl.gt(0).sum() >= 5
        and completed.net_pnl.lt(0).sum() >= 5
    )
    pf_interval = (
        np.quantile(samples[valid_pf, 2] / samples[valid_pf, 3], [0.025, 0.975]).tolist()
        if enough_pf
        else None
    )
    return {
        "method": "five_observed_day_circular_block_bootstrap; descriptive_only",
        "net_pnl_95_interval": np.quantile(samples[:, 0], [0.025, 0.975]).tolist(),
        "trade_count_95_interval": np.quantile(samples[:, 1], [0.025, 0.975]).tolist(),
        "profit_factor_95_interval": pf_interval,
        "profit_factor_defined_replicates": int(valid_pf.sum()),
        "profit_factor_interval_unavailable_reason": (
            None if enough_pf else "requires_5_wins_5_losses_and_90pct_defined_replicates"
        ),
        "replicates": len(samples),
        "block_days": 5,
        "seed": seed,
        "cohorts": "entry_day_completed_trade_outcomes; zero_trade_observed_days_included",
        "caveat": "fixed_executed_trades; ignores model_selection_and_longer_dependence",
    }


def metrics(trades, curve, config, start, end, observed_days):
    """Cash-inclusive portfolio MTM plus explicitly named closed-PnL attribution.

    Minute marks include the adverse OHLC extreme before a favourable target; this
    is a conservative reference-path scenario, not a tick-verified equity series.
    Per-asset standalone performance comes from singleton portfolio replays.
    """
    result = _trade_summary(trades, config.initial_equity)
    completed = trades[trades.completed] if len(trades) else trades
    trade_days = set(completed.entry_time.dt.normalize()) if len(completed) else set()
    result.update(
        net_return=float(curve.iloc[-1] / config.initial_equity - 1),
        final_equity_usd=float(curve.iloc[-1]),
        maximum_mark_to_market_drawdown=float((1 - curve / curve.cummax()).max()),
        drawdown_method="adverse_first_OHLC_reference_path_including_scenario_costs",
        observed_trading_days=len(observed_days),
        zero_trade_observed_days=len(set(observed_days) - trade_days),
        trades_per_observed_trading_day=len(completed) / len(observed_days)
        if observed_days
        else None,
        trade_day_definition=(
            "dates_with_observed_selected_asset_bars; not_verified_broker_calendar"
        ),
        maximum_concurrent_positions=1 if len(trades) else 0,
        ambiguous_trades=int(trades.ambiguous.sum()) if len(trades) else 0,
        gap_exits=int(trades.data_gap_exit.sum()) if len(trades) else 0,
        margin_closeouts=int(trades.exit_reason.eq("margin_closeout").sum()) if len(trades) else 0,
        statistically_inconclusive=len(completed) < 100 or len(completed) != len(trades),
        uncertainty=_block_uncertainty(trades, observed_days, config.bootstrap_seed),
        cash_baseline_return=0.0,
        acceptance_is_not_profitability_proof=True,
        simulation_only=True,
        configuration=asdict(config),
        by_instrument={},
        by_year={},
        by_regime={},
    )
    if len(trades):
        for field, groups in (
            ("by_instrument", trades.groupby("asset", sort=True)),
            ("by_regime", trades.groupby("regime", sort=True)),
        ):
            result[field] = {
                str(key): _trade_summary(group, config.initial_equity) for key, group in groups
            }
    for year in range(start.year, (end - pd.Timedelta(nanoseconds=1)).year + 1):
        year_start = max(start, pd.Timestamp(year=year, month=1, day=1, tz="UTC"))
        year_end = min(end, pd.Timestamp(year=year + 1, month=1, day=1, tz="UTC"))
        before = curve[curve.index <= year_start]
        baseline = float(before.iloc[-1]) if len(before) else config.initial_equity
        section = curve[(curve.index > year_start) & (curve.index <= year_end)]
        values = np.r_[baseline, section.to_numpy(float)]
        grouped = (
            trades[(trades.exit_time >= year_start) & (trades.exit_time < year_end)]
            if len(trades)
            else trades
        )
        summary = _trade_summary(grouped, baseline if baseline > 0 else config.initial_equity)
        year_days = [day for day in observed_days if year_start.normalize() <= day < year_end]
        n = summary["trades"]
        full_year = year_start == pd.Timestamp(year=year, month=1, day=1, tz="UTC") and (
            year_end == pd.Timestamp(year=year + 1, month=1, day=1, tz="UTC")
        )
        summary.update(
            net_return=float(values[-1] / baseline - 1) if baseline > 0 else None,
            maximum_mark_to_market_drawdown=float(
                np.max(1 - values / np.maximum.accumulate(values))
            ),
            full_evaluation_year=full_year,
            minimum_activity_pass=n >= 100 if full_year else None,
            trades_per_observed_trading_day=n / len(year_days) if year_days else None,
            uncertainty=_block_uncertainty(grouped, year_days, config.bootstrap_seed + year),
        )
        result["by_year"][str(year)] = summary
    return result


def compare_portfolios(
    candidates, bars_by_asset, instruments, costs, config, start, end, diagnostic=None
):
    """Seven independent shared-account replays, never summed asset returns."""
    results = {}
    for subset in PORTFOLIO_SUBSETS:
        name = "+".join(subset)
        selected = candidates[candidates.asset.isin(subset)]
        results[name] = backtest(
            selected,
            {a: bars_by_asset[a] for a in subset},
            instruments,
            costs,
            config,
            start,
            end,
            diagnostic,
        )
    return results


def evaluate_leverage_scenarios(
    candidates, bars_by_asset, instruments, costs, config, start, end, diagnostic=None
):
    """Six availability scenarios with identical risk budget; evaluated sequentially."""
    return {
        str(leverage): backtest(
            candidates,
            bars_by_asset,
            instruments,
            costs,
            replace(config, leverage=leverage),
            start,
            end,
            diagnostic,
        )
        for leverage in LEVERAGE_SCENARIOS
    }
