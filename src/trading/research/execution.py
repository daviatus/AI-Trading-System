"""Offline adverse-first OHLC execution and a shared single-position USD ledger."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trading.research.data import MINUTE, session_open


@dataclass(frozen=True)
class Costs:
    spread_bps: float
    slippage_bps_per_side: float
    commission_bps_roundtrip: float

    @property
    def fraction(self):
        values = (self.spread_bps, self.slippage_bps_per_side, self.commission_bps_roundtrip)
        if min(values) < 0:
            raise ValueError("Costs cannot be negative")
        return (
            self.spread_bps + 2 * self.slippage_bps_per_side + self.commission_bps_roundtrip
        ) / 10000


def permitted_entries(index, horizon, metadata):
    """Known schedule only: no UTC overnight, broker-session break or swap crossing."""
    entry = index + pd.Timedelta(minutes=1)
    last = entry + pd.Timedelta(minutes=horizon - 1)
    local = entry.tz_convert(metadata["specifications"]["scheduleTimeZone"])
    seconds = ((local.dayofweek + 1) % 7) * 86400 + local.hour * 3600 + local.minute * 60
    permitted = np.zeros(len(index), bool)
    for w in metadata["specifications"]["schedule"]:
        permitted |= (seconds >= w["startSecond"]) & (seconds + horizon * 60 <= w["endSecond"])
    swap = metadata["specifications"].get("swapTime", 1260)
    entry_minute, last_minute = entry.hour * 60 + entry.minute, last.hour * 60 + last.minute
    permitted &= ~((entry_minute <= swap) & (last_minute >= swap))
    permitted &= entry.normalize() == last.normalize()
    return permitted & session_open(entry, metadata) & session_open(last, metadata)


def outcomes(bars, atr, horizon, stop_atr=2.0, reward_risk=2.0):
    """Future execution outcomes are targets only, never candidate-selection inputs.

    Entry is next contiguous bar open. A later data gap cannot retrospectively
    cancel an order: close conservatively at first post-gap open, at least a stop
    loss. Both SL and TP within one OHLC bar resolves to SL. Missing next entry
    gives no fill. Targets crossing gaps or dataset end are unavailable for fitting.
    """
    if horizon < 1 or stop_atr <= 0 or reward_risk <= 0:
        raise ValueError("Positive execution parameters required")
    n = len(bars)
    ix = np.arange(n)
    stamps = bars.index.asi8
    values = {k: bars[k].to_numpy(float) for k in ("open", "high", "low", "close")}
    nxt = np.minimum(ix + 1, n - 1)
    entry = values["open"][nxt]
    distance = np.asarray(atr, float) * stop_atr
    fill = (nxt > ix) & (stamps[nxt] - stamps == MINUTE) & np.isfinite(distance) & (distance > 0)
    valid_target = fill & (ix + horizon < n)
    # Full-horizon continuity only restricts labels, never backtest opportunities.
    for offset in range(1, horizon + 1):
        j = np.minimum(ix + offset, n - 1)
        valid_target &= stamps[j] - stamps == offset * MINUTE
    result = {
        "entry": entry,
        "distance": distance,
        "fill": fill,
        "label_valid": valid_target,
        "horizon": horizon,
    }
    for side, direction in (("long", 1), ("short", -1)):
        stop = entry - direction * distance
        target = entry + direction * distance * reward_risk
        exit_price = values["close"].copy()
        exit_index = ix.copy()
        ambiguous = np.zeros(n, bool)
        gap = np.zeros(n, bool)
        active = fill.copy()
        for offset in range(1, horizon + 1):
            j = np.minimum(ix + offset, n - 1)
            actual_gap = active & (stamps[j] - stamps != offset * MINUTE)
            gap_price = (
                np.minimum(values["open"][j], stop)
                if direction == 1
                else np.maximum(values["open"][j], stop)
            )
            exit_price[actual_gap], exit_index[actual_gap] = gap_price[actual_gap], j[actual_gap]
            gap[actual_gap] = True
            active[actual_gap] = False
            exhausted = active & (ix + offset >= n)
            exit_price[exhausted] = values["close"][-1]
            exit_index[exhausted] = n - 1
            active[exhausted] = False
            stop_hit = active & (
                (values["low"][j] <= stop) if direction == 1 else (values["high"][j] >= stop)
            )
            target_hit = active & (
                (values["high"][j] >= target) if direction == 1 else (values["low"][j] <= target)
            )
            ambiguous |= stop_hit & target_hit
            # An adverse opening gap gets the worse opening price, not a guaranteed stop.
            stop_price = (
                np.minimum(stop, values["open"][j])
                if direction == 1
                else np.maximum(stop, values["open"][j])
            )
            exit_price[stop_hit], exit_index[stop_hit] = stop_price[stop_hit], j[stop_hit]
            take = target_hit & ~stop_hit
            exit_price[take], exit_index[take] = target[take], j[take]
            active[stop_hit | take] = False
            if offset == horizon:
                exit_price[active], exit_index[active] = values["close"][j[active]], j[active]
        result[side] = {
            "gross_per_unit": direction * (exit_price - entry),
            "exit_price": exit_price,
            "exit_index": exit_index,
            "ambiguous": ambiguous,
            "gap": gap,
        }
    return result


def targets(path, costs):
    fee = path["entry"] * costs.fraction
    long = path["long"]["gross_per_unit"] - fee
    short = path["short"]["gross_per_unit"] - fee
    labels = np.select([(long > 0) & (long > short), (short > 0) & (short > long)], [1, 2], 0)
    return np.where(path["label_valid"], labels, -1).astype("int8")


def rank_opportunities(candidates):
    """Only decision-time inputs belong in this API; no outcomes or future prices."""
    required = {"time", "asset", "score", "probability", "confidence"}
    if not required.issubset(candidates):
        raise ValueError("Missing opportunity fields")
    return candidates.sort_values(
        ["time", "score", "confidence", "asset"],
        ascending=[True, False, False, True],
        kind="stable",
    )


def backtest(candidates, datasets, contracts, config, start, end, cost_multiplier=1.0):
    if config["max_positions"] != 1:
        raise ValueError("This research ledger supports exactly one shared concurrent position")
    equity = float(config["initial_equity"])
    ledger, marks = [], [(pd.Timestamp(start), equity)]
    busy_until = pd.Timestamp(start)
    day, day_start = None, equity
    ranked = rank_opportunities(candidates)
    for row in ranked.itertuples(index=False):
        when = row.time
        if when < busy_until or when >= pd.Timestamp(end) or when < pd.Timestamp(start):
            continue
        if when + pd.Timedelta(minutes=row.horizon) > pd.Timestamp(end):
            continue  # The evaluation end is known before an order, not inferred from outcomes.
        if day != when.date():
            day, day_start = when.date(), equity
        budget = min(
            equity * config["risk_fraction"],
            max(0, equity - day_start * (1 - config["daily_loss_fraction"])),
        )
        if budget <= 0:
            continue
        data = datasets[row.asset]
        path = data["paths"][row.horizon]
        i = row.position
        if not path["fill"][i]:
            continue
        side = "long" if row.direction == 1 else "short"
        entry, distance = path["entry"][i], path["distance"][i]
        cost = Costs(**config["costs"][row.asset]).fraction * cost_multiplier * entry
        spec = contracts[row.asset]["specifications"]
        minimum, step, maximum = (spec[k] / 100 for k in ("minVolume", "stepVolume", "maxVolume"))
        if not 0 < minimum <= maximum or step <= 0:
            raise ValueError("Invalid contract quantity metadata")
        quantity = min(
            budget / (distance + cost), equity * config["max_gross_leverage"] / entry, maximum
        )
        quantity = np.floor((quantity + 1e-12) / step) * step
        if quantity < minimum:
            continue
        exit_i = int(path[side]["exit_index"][i])
        bars = data["bars"]
        censored = bars.index[exit_i] + pd.Timedelta(minutes=1) > pd.Timestamp(end)
        if censored:
            # No post-window price enters evaluation. This is an unresolved marked
            # position, not a fabricated fill at a missing timestamp.
            exit_i = int(bars.index.searchsorted(pd.Timestamp(end)) - 1)
        exit_time = bars.index[exit_i] + pd.Timedelta(minutes=1)
        exit_price = bars.close.iloc[exit_i] if censored else path[side]["exit_price"][i]
        gross = row.direction * (exit_price - entry) * quantity
        fees = cost * quantity
        pnl = gross - fees
        entry_equity = equity
        # Mark every observed bar close during the position; terminal fill uses execution outcome.
        for j in range(i + 1, exit_i + 1):
            mtm = entry_equity + row.direction * (bars.close.iloc[j] - entry) * quantity - fees
            marks.append((bars.index[j] + pd.Timedelta(minutes=1), float(mtm)))
        equity += pnl
        marks[-1] = (exit_time, equity)
        ledger.append(
            {
                "asset": row.asset,
                "entry_time": when,
                "exit_time": exit_time,
                "direction": side,
                "quantity": float(quantity),
                "entry": float(entry),
                "exit": float(exit_price),
                "gross_pnl": float(gross),
                "cost": float(fees),
                "net_pnl": float(pnl),
                "equity_before": entry_equity,
                "equity_after": equity,
                "account_return": float(pnl / entry_equity),
                "risk_fraction": float((distance + cost) * quantity / entry_equity),
                "ambiguous": bool(path[side]["ambiguous"][i]),
                "data_gap_exit": bool(path[side]["gap"][i]),
                "regime": row.regime,
                "unresolved_terminal_mark": bool(censored),
                "probability": row.probability,
                "score": row.score,
            }
        )
        busy_until = pd.Timestamp(end) if censored else exit_time
    trades = pd.DataFrame(ledger)
    curve = pd.Series([v for _, v in marks], index=pd.DatetimeIndex([t for t, _ in marks]))
    curve = curve.groupby(level=0).last().sort_index()
    return trades, curve, metrics(trades, curve, config, start, end)


def metrics(trades, curve, config, start, end):
    days = pd.date_range(pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize(), freq="D")
    daily = curve.resample("D").last().reindex(days).ffill().fillna(config["initial_equity"])
    daily_returns = daily.pct_change().fillna(0)
    daily_std = float(daily_returns.std(ddof=1))
    pnl = trades.net_pnl if len(trades) else pd.Series([], dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    exposure_minutes = (
        sum((r.exit_time - r.entry_time).total_seconds() / 60 for r in trades.itertuples())
        if len(trades)
        else 0
    )
    result = {
        "trades": len(trades),
        "trades_per_calendar_day": len(trades) / max(1, len(days)),
        "win_rate": float((pnl > 0).mean()) if len(pnl) else None,
        "average_win_usd": float(wins.mean()) if len(wins) else None,
        "average_loss_usd": float(losses.mean()) if len(losses) else None,
        "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) else None,
        "net_return": float(curve.iloc[-1] / config["initial_equity"] - 1),
        "maximum_mark_to_market_drawdown": float((1 - curve / curve.cummax()).max()),
        "daily_sharpe": float(daily_returns.mean() / daily_std * np.sqrt(365))
        if len(days) >= 60 and daily_std > 0 and len(trades) >= 30
        else None,
        "sharpe_convention": "365 calendar-day daily returns, cash days included; descriptive",
        "exposure_calendar_fraction": exposure_minutes
        / max(1, (pd.Timestamp(end) - pd.Timestamp(start)).total_seconds() / 60),
        "total_cost_usd": float(trades.cost.sum()) if len(trades) else 0,
        "ambiguous_trades": int(trades.ambiguous.sum()) if len(trades) else 0,
        "gap_exits": int(trades.data_gap_exit.sum()) if len(trades) else 0,
        "unresolved_terminal_positions": int(trades.unresolved_terminal_mark.sum())
        if len(trades)
        else 0,
        "winning_trades_net_account_return_0_5pct_or_more": int(
            (trades.account_return >= 0.005).sum()
        )
        if len(trades)
        else 0,
        "statistically_inconclusive": len(trades) < 100
        or (bool(trades.unresolved_terminal_mark.any()) if len(trades) else False),
        "by_instrument": {},
        "by_year": {},
        "by_regime": {},
    }
    if len(trades):
        for field, groups in (
            ("by_instrument", trades.groupby("asset")),
            ("by_year", trades.groupby(trades.exit_time.dt.year)),
            ("by_regime", trades.groupby("regime")),
        ):
            result[field] = {
                str(k): {
                    "trades": len(g),
                    "net_pnl": float(g.net_pnl.sum()),
                    "win_rate": float(g.net_pnl.gt(0).mean()),
                }
                for k, g in groups
            }
    return result
