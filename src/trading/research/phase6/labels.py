"""Documented offline labels and purged chronological Phase 6 partitions.

Label codes: -1 unavailable, 0 no-trade, 1 long, 2 short. Inputs are raw candle
open timestamps; returned rows use completed-bar decision timestamps. Future
prices are targets only. They must never be passed to a signal gate.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trading.research.phase6.features import (
    DEVELOPMENT_END,
    MINUTE,
    calendar_open,
    utc_index,
    validate_candles,
)


@dataclass(frozen=True)
class LabelSpec:
    """Controlled label definition, with costs in roundtrip basis points.

    forward: next contiguous open to horizon close; threshold = minimum_return.
    volatility: same return; threshold additionally includes volatility_multiple
      * causal per-minute return sigma * sqrt(horizon_minutes).
    barrier: two directional ATR-style stop/target paths, with stop distance
      entry * causal supplied volatility fraction * stop_multiple. The caller
      explicitly chooses whether that fraction is ATR/price or another estimate.
      Stops win same-bar ambiguity; adverse open gaps fill at the worse open.
    In every case costs are explicit scenarios, never historical spread claims.
    """

    kind: str = "forward"
    horizon_minutes: int = 30
    minimum_return: float = 0.0
    cost_bps: float = 2.0
    volatility_multiple: float = 1.0
    stop_multiple: float = 2.0
    reward_risk: float = 2.0
    max_rows: int = 400_000

    def __post_init__(self):
        if self.kind not in {"forward", "volatility", "barrier"}:
            raise ValueError("Unknown label kind")
        if not isinstance(self.horizon_minutes, int) or not 1 <= self.horizon_minutes <= 1440:
            raise ValueError("Label horizon must be an integer from 1 to 1440 minutes")
        values = (
            self.minimum_return,
            self.cost_bps,
            self.volatility_multiple,
            self.stop_multiple,
            self.reward_risk,
        )
        if not np.isfinite(values).all() or min(values) < 0:
            raise ValueError("Label parameters must be finite and nonnegative")
        if min(self.stop_multiple, self.reward_risk) <= 0 or self.max_rows < 1:
            raise ValueError("Positive barrier parameters and row bound required")


def _volatility_values(volatility, decision):
    if not isinstance(volatility, pd.Series) or not volatility.index.equals(decision):
        raise ValueError("Volatility must be a Series exactly aligned to decision timestamps")
    values = volatility.to_numpy(float)
    if np.isinf(values).any() or np.any(values[np.isfinite(values)] < 0):
        raise ValueError("Volatility must be nonnegative and finite or missing during warmup")
    return values


def _forward_outcome(bars, spec, opened):
    n = len(bars)
    horizon = spec.horizon_minutes
    position = np.arange(n)
    end_position = np.minimum(position + horizon, n - 1)
    valid = position + horizon < n
    for offset in range(1, horizon + 1):
        future = np.minimum(position + offset, n - 1)
        valid &= bars.index.asi8[future] - bars.index.asi8 == offset * MINUTE.value
        valid &= opened[future]
    entry = bars.open.to_numpy(float)[np.minimum(position + 1, n - 1)]
    gross = bars.close.to_numpy(float)[end_position] / entry - 1
    ends = bars.index[end_position] + MINUTE
    return gross, ends, valid


def _barrier_outcome(bars, spec, volatility, opened):
    """O(rows*horizon), O(rows) memory; no future matrix or terminal fake fill."""
    n = len(bars)
    positions = np.arange(n)
    entry_position = np.minimum(positions + 1, n - 1)
    entry = bars.open.to_numpy(float)[entry_position]
    distance = entry * volatility * spec.stop_multiple
    initial = (positions + 1 < n) & np.isfinite(distance) & (distance > 0)
    initial &= bars.index.asi8[entry_position] - bars.index.asi8 == MINUTE.value
    initial &= opened[entry_position]
    arrays = {name: bars[name].to_numpy(float) for name in ("open", "high", "low", "close")}
    returns, exits, validities = [], [], []
    for direction in (1, -1):
        stop = entry - direction * distance
        target = entry + direction * spec.reward_risk * distance
        active, valid = initial.copy(), initial.copy()
        exit_price = np.full(n, np.nan)
        exit_positions = np.zeros(n, dtype=int)
        for offset in range(1, spec.horizon_minutes + 1):
            future = np.minimum(positions + offset, n - 1)
            known = (positions + offset < n) & opened[future]
            known &= bars.index.asi8[future] - bars.index.asi8 == offset * MINUTE.value
            invalid = active & ~known
            valid[invalid], active[invalid] = False, False
            hit_stop = active & (
                (arrays["low"][future] <= stop)
                if direction == 1
                else (arrays["high"][future] >= stop)
            )
            hit_target = (
                active
                & ~hit_stop
                & (
                    (arrays["high"][future] >= target)
                    if direction == 1
                    else (arrays["low"][future] <= target)
                )
            )
            adverse_fill = (
                np.minimum(arrays["open"][future], stop)
                if direction == 1
                else (np.maximum(arrays["open"][future], stop))
            )
            exit_price[hit_stop] = adverse_fill[hit_stop]
            # Favorable gaps receive only the requested target, conservatively.
            exit_price[hit_target] = target[hit_target]
            done = hit_stop | hit_target
            if offset == spec.horizon_minutes:
                timed = active & ~done
                exit_price[timed] = arrays["close"][future][timed]
                done |= timed
            exit_positions[done] = future[done]
            active[done] = False
            if not active.any():
                break
        valid &= np.isfinite(exit_price)
        returns.append(direction * (exit_price / entry - 1))
        exits.append(exit_positions)
        validities.append(valid)
    label_position = np.maximum(exits[0], exits[1])
    return (
        returns[0],
        returns[1],
        bars.index[label_position] + MINUTE,
        validities[0] & validities[1],
    )


def make_labels(bars, spec=None, volatility=None, metadata=None):
    """Return future-only labels with the actual latest information-use time.

    Any absent/closed candle needed by an outcome makes its label unavailable.
    Forward labels require the entire horizon; barrier labels stop once both
    directional outcomes resolve. label_end is NaT for unavailable labels.
    """
    spec = LabelSpec() if spec is None else spec
    index = validate_candles(bars, max_rows=spec.max_rows)
    # Arrow/Parquet can preserve microsecond resolution. Normalize timestamps
    # without copying candle values or mutating the caller's frame.
    bars = bars.set_axis(index, axis=0, copy=False)
    decision = index + MINUTE
    opened = np.ones(len(index), dtype=bool) if metadata is None else calendar_open(index, metadata)
    sigma = None
    if spec.kind in {"volatility", "barrier"}:
        sigma = _volatility_values(volatility, decision)
    costs = spec.cost_bps / 10_000
    if spec.kind == "barrier":
        long_return, short_return, label_end, valid = _barrier_outcome(bars, spec, sigma, opened)
        forward = np.full(len(index), np.nan)
    else:
        forward, label_end, valid = _forward_outcome(bars, spec, opened)
        long_return, short_return = forward.copy(), -forward
    threshold = np.full(len(index), spec.minimum_return, dtype=float)
    if spec.kind == "volatility":
        threshold += spec.volatility_multiple * sigma * np.sqrt(spec.horizon_minutes)
        valid &= np.isfinite(sigma)
    valid &= opened & (decision < DEVELOPMENT_END) & (label_end < DEVELOPMENT_END)
    long_net, short_net = long_return - costs, short_return - costs
    labels = np.full(len(index), -1, dtype="int8")
    labels[valid] = 0
    labels[valid & (long_net > threshold) & (long_net > short_net)] = 1
    labels[valid & (short_net > threshold) & (short_net > long_net)] = 2
    output = pd.DataFrame(
        {
            "label": labels,
            "label_end": label_end,
            "forward_return": forward,
            "long_net_return": long_net,
            "short_net_return": short_net,
            "outcome_threshold": threshold,
        },
        index=decision,
    )
    output.index.name = "decision_time"
    output.loc[~valid, "label_end"] = pd.NaT
    output.loc[~valid, ["forward_return", "long_net_return", "short_net_return"]] = np.nan
    output.attrs.update(
        {
            "schema_version": "phase6-labels-v1",
            "kind": spec.kind,
            "costs_are_scenarios": True,
            "label_end_definition": "latest candle close used by either side",
        }
    )
    return output


def purged_split(index, label_end, boundaries, embargo_minutes=60):
    """Strict chronological windows, label-boundary purging and forward embargo.

    Boundaries contain train_start/train_end/calibration_end/tuning_end/test_end.
    Labels must end strictly before their window's right boundary. Calibration,
    tuning and test each discard the first embargo_minutes after their nominal
    start. Expanding walk-forward fitting uses only preceding windows; therefore
    no later training samples require a backward-facing embargo. Within-window
    labels still overlap and must not be treated as independent observations.
    """
    index = utc_index(index, name="decision index")
    if index.has_duplicates or not index.is_monotonic_increasing:
        raise ValueError("Decision timestamps must be strictly increasing")
    if isinstance(label_end, pd.Series):
        if not label_end.index.equals(index):
            raise ValueError("Label ends must align exactly to decision timestamps")
        ends = pd.DatetimeIndex(label_end)
    else:
        ends = pd.DatetimeIndex(label_end)
    if ends.tz is None or str(ends.tz) != "UTC" or len(ends) != len(index):
        raise ValueError("Label ends must be UTC and align with every decision")
    if ((~ends.isna()) & (ends < index)).any():
        raise ValueError("Label ends cannot precede decisions")
    if not isinstance(embargo_minutes, int) or embargo_minutes < 0:
        raise ValueError("Embargo must be a nonnegative integer of minutes")
    keys = ("train_start", "train_end", "calibration_end", "tuning_end", "test_end")
    stamps = [pd.Timestamp(boundaries[key]) for key in keys]
    utc_index(pd.DatetimeIndex(stamps), name="split boundaries")
    if not all(a < b for a, b in zip(stamps[:-1], stamps[1:], strict=True)):
        raise ValueError("Split boundaries must strictly increase")
    if stamps[-1] > DEVELOPMENT_END:
        raise ValueError("2026 and later are excluded from all development partitions")
    embargo = embargo_minutes * MINUTE
    masks = {}
    for number, name in enumerate(("train", "calibration", "tuning", "test")):
        left, right = stamps[number : number + 2]
        effective_start = left + (embargo if number else pd.Timedelta(0))
        masks[name] = np.asarray(
            (index >= effective_start)
            & (index < right)
            & ~ends.isna()
            & (ends < right)
            & (ends < DEVELOPMENT_END)
        )
    return masks
