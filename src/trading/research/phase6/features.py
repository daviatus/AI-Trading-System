"""Bounded, causal Phase 6 features and explicit candle integrity diagnostics.

Input timestamps identify one-minute candle opens. Features are indexed by the
completed candle's decision time (open + one minute). No price is interpolated;
all indicator state resets after every absent minute or excluded session bar.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trading.ctrader.calendar import BrokerCalendar
from trading.research.data import session_open

MINUTE = pd.Timedelta(minutes=1)
DEVELOPMENT_END = pd.Timestamp("2026-01-01", tz="UTC")


@dataclass(frozen=True)
class FeatureSpec:
    """Small price-only family; volume requires an explicit reliability assertion."""

    return_lags: tuple[int, ...] = (1, 5, 15)
    volatility_window: int = 20
    atr_window: int = 14
    rsi_window: int = 14
    fast_span: int = 12
    slow_span: int = 26
    regime_window: int = 60
    warmup: int = 64
    reliable_volume: bool = False
    max_rows: int = 400_000

    def __post_init__(self):
        periods = (
            self.volatility_window,
            self.atr_window,
            self.rsi_window,
            self.fast_span,
            self.slow_span,
            self.regime_window,
            self.warmup,
        )
        if any(not isinstance(value, int) or value < 2 for value in periods):
            raise ValueError("Feature periods must be integers of at least two")
        if (
            not self.return_lags
            or any(not isinstance(value, int) or value < 1 for value in self.return_lags)
            or len(set(self.return_lags)) != len(self.return_lags)
        ):
            raise ValueError("Return lags must be unique positive integers")
        if self.fast_span >= self.slow_span or self.max_rows < 1:
            raise ValueError("Require fast_span < slow_span and a positive row bound")


def utc_index(index, *, name="timestamps"):
    """Require UTC rather than silently interpreting or converting local stamps."""
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
        raise ValueError(f"{name} must be a timezone-aware UTC DatetimeIndex")
    if str(index.tz) not in {"UTC", "UTC+00:00", "Etc/UTC", "GMT"}:
        raise ValueError(f"{name} must use UTC")
    if index.hasnans:
        raise ValueError(f"{name} contains missing timestamps")
    return index.tz_convert("UTC").as_unit("ns")


def validate_candles(bars, *, max_rows=400_000, development_only=True):
    """Fail on ambiguity; repairs/deduplication must be explicit upstream steps."""
    index = utc_index(bars.index, name="candle index")
    if not len(index) or len(index) > max_rows:
        raise ValueError("Candle batch must be nonempty and within the row bound")
    if index.has_duplicates or not index.is_monotonic_increasing:
        raise ValueError("Candle timestamps must be strictly increasing and unique")
    if np.any(index.asi8 % MINUTE.value):
        raise ValueError("Candles must be aligned to whole UTC minutes")
    if development_only and index[-1] >= DEVELOPMENT_END:
        raise ValueError("2026 and later candles are excluded from model development")
    if not {"open", "high", "low", "close"}.issubset(bars.columns):
        raise ValueError("OHLC columns are required")
    prices = bars[["open", "high", "low", "close"]].to_numpy(dtype=float)
    valid = np.isfinite(prices).all(axis=1) & (prices > 0).all(axis=1)
    valid &= (prices[:, 1] >= prices.max(axis=1)) & (prices[:, 2] <= prices.min(axis=1))
    if not valid.all():
        raise ValueError(f"Invalid OHLC candles: {int((~valid).sum())}")
    return index


def calendar_open(index, metadata):
    """Reuse the exposed broker calendar, without assuming historical verification."""
    utc_index(index)
    # Constructor checks timezone names and weekly interval validity.
    BrokerCalendar({"EURUSD": metadata})
    return session_open(index, metadata)


def _segment_features(part, spec):
    close = part.close
    returns = close.pct_change(fill_method=None)
    result = {f"return_{lag}": close.pct_change(lag, fill_method=None) for lag in spec.return_lags}
    sigma_name = f"volatility_{spec.volatility_window}"
    atr_name = f"atr_fraction_{spec.atr_window}"
    result[sigma_name] = returns.rolling(spec.volatility_window).std(ddof=0)
    prior = close.shift(1)
    true_range = pd.concat(
        [part.high - part.low, (part.high - prior).abs(), (part.low - prior).abs()], axis=1
    ).max(axis=1)
    result[atr_name] = true_range.rolling(spec.atr_window).mean() / close
    fast = close.ewm(span=spec.fast_span, adjust=False, min_periods=spec.fast_span).mean()
    slow = close.ewm(span=spec.slow_span, adjust=False, min_periods=spec.slow_span).mean()
    result["ema_trend_spread"] = (fast - slow) / close
    result["ema_slope"] = slow.pct_change(5, fill_method=None)
    change = close.diff()
    gains = change.clip(lower=0).rolling(spec.rsi_window).mean()
    losses = (-change.clip(upper=0)).rolling(spec.rsi_window).mean()
    total = gains + losses
    result[f"rsi_{spec.rsi_window}"] = (100 * gains / total).where(total.ne(0), 50)
    width = part.high - part.low
    result["body_fraction"] = (close - part.open) / close
    result["range_fraction"] = width / close
    result["close_location"] = ((close - part.low) / width.replace(0, np.nan)).fillna(0.5)
    result["volatility_ratio"] = result[sigma_name] / result[sigma_name].rolling(
        spec.regime_window, min_periods=max(2, spec.regime_window // 2)
    ).mean().replace(0, np.nan)
    result["trend_strength"] = result["ema_trend_spread"].abs() / result[atr_name].replace(
        0, np.nan
    )
    decision = part.index + MINUTE
    minute = decision.hour * 60 + decision.minute
    result["time_sin"] = pd.Series(np.sin(2 * np.pi * minute / 1440), index=part.index)
    result["time_cos"] = pd.Series(np.cos(2 * np.pi * minute / 1440), index=part.index)
    result["weekday_sin"] = pd.Series(np.sin(2 * np.pi * decision.dayofweek / 7), index=part.index)
    result["weekday_cos"] = pd.Series(np.cos(2 * np.pi * decision.dayofweek / 7), index=part.index)
    if spec.reliable_volume:
        result["relative_volume"] = part.volume / part.volume.rolling(
            spec.volatility_window
        ).mean().replace(0, np.nan)
    frame = pd.DataFrame(result, index=part.index)
    frame.iloc[: spec.warmup - 1] = np.nan
    return frame


def make_features(bars, spec=None, metadata=None):
    """Return causal float32 features at completed-bar decision timestamps.

    Current OHLC is available only at candle close. Original publication/revision
    timestamps are absent in Phase 5 snapshots; this convention is an assumption.
    Supplying metadata excludes outside-session candles and resets after them.
    Without metadata, no assertion of trading-session eligibility is made.
    """
    spec = FeatureSpec() if spec is None else spec
    index = validate_candles(bars, max_rows=spec.max_rows)
    if spec.reliable_volume:
        if "volume" not in bars or not (
            np.isfinite(bars.volume.to_numpy(float)).all() and bars.volume.ge(0).all()
        ):
            raise ValueError("Reliable-volume features require finite nonnegative volume")
    opened = np.ones(len(index), dtype=bool) if metadata is None else calendar_open(index, metadata)
    eligible = bars.loc[opened]
    if not len(eligible):
        raise ValueError("No candles lie inside the supplied schedule")
    eligible.index = index[opened]
    breaks = np.r_[True, np.diff(eligible.index.asi8) != MINUTE.value]
    bounds = np.r_[np.flatnonzero(breaks), len(eligible)]
    parts = [
        _segment_features(eligible.iloc[left:right], spec)
        for left, right in zip(bounds[:-1], bounds[1:], strict=True)
        if right - left >= spec.warmup
    ]
    # Short broken segments cannot produce a ready row. Avoid allocating one
    # indicator DataFrame per short segment in severely fragmented histories.
    result = pd.concat(parts) if parts else _segment_features(eligible.iloc[:1], spec)
    result = result.reindex(index).replace([np.inf, -np.inf], np.nan).astype("float32")
    result.index = index + MINUTE
    result.index.name = "decision_time"
    result.attrs.update(
        {
            "schema_version": "phase6-features-v1",
            "causal": True,
            "timestamp_convention": "completed candle open + 1 minute",
            "calendar_verified_historically": False,
            "volume_reliability_asserted": spec.reliable_volume,
        }
    )
    return result


def audit_candles(
    bars,
    metadata=None,
    *,
    start=None,
    end=None,
    now=None,
    stale_after_minutes=5,
    max_rows=400_000,
    grid_batch_minutes=10_080,
):
    """Bounded session-aware integrity summary; missing minutes are never filled.

    Malformed candles raise with a precise reason. Expected-open and known-closed
    absences are separated only when an exposed broker calendar is supplied.
    Its historical applicability remains unverified. Provenance/spread cannot be
    recovered from OHLC and are explicitly marked unavailable.
    """
    index = validate_candles(bars, max_rows=max_rows)
    if grid_batch_minutes < 1 or stale_after_minutes < 0:
        raise ValueError("Positive grid bound and nonnegative staleness threshold required")
    left = index[0] if start is None else pd.Timestamp(start)
    right = index[-1] + MINUTE if end is None else pd.Timestamp(end)
    utc_index(pd.DatetimeIndex([left, right]))
    if left >= right or right > DEVELOPMENT_END:
        raise ValueError("Audit interval must be ordered and exclude 2026")
    if left.value % MINUTE.value or right.value % MINUTE.value:
        raise ValueError("Audit boundaries must be whole UTC minutes")
    absent = expected = closed = 0
    cursor = left
    while cursor < right:
        stop = min(right, cursor + grid_batch_minutes * MINUTE)
        grid = pd.date_range(cursor, stop, freq="min", inclusive="left")
        missing = ~grid.isin(index)
        absent += int(missing.sum())
        if metadata is not None:
            opened = calendar_open(grid, metadata)
            expected += int((missing & opened).sum())
            closed += int((missing & ~opened).sum())
        cursor = stop
    gaps = np.diff(index.asi8) // MINUTE.value - 1
    stale = None
    if now is not None:
        clock = pd.Timestamp(now)
        utc_index(pd.DatetimeIndex([clock]))
        if clock < index[-1] + MINUTE:
            raise ValueError("Staleness clock cannot precede the last completed candle")
        stale = bool(clock - (index[-1] + MINUTE) > stale_after_minutes * MINUTE)
    return {
        "schema_version": "phase6-data-audit-v1",
        "rows": len(index),
        "start": left.isoformat(),
        "end_exclusive": right.isoformat(),
        "duplicate_timestamps": 0,
        "invalid_ohlc": 0,
        "nonminute_timestamps": 0,
        "gap_count": int((gaps > 0).sum()),
        "absent_calendar_minutes": absent,
        "missing_expected_open_minutes": expected if metadata is not None else None,
        "known_closed_absent_minutes": closed if metadata is not None else None,
        "unclassified_absent_minutes": absent if metadata is None else 0,
        "outside_exposed_sessions": int((~calendar_open(index, metadata)).sum())
        if metadata is not None
        else None,
        "calendar_fingerprint": BrokerCalendar({"EURUSD": metadata}).fingerprint
        if metadata is not None
        else None,
        "stale_at_requested_clock": stale,
        "price_type": "unverified",
        "historical_spreads": "unavailable",
        "publication_and_revision_timestamps": "unavailable",
        "calendar_limitation": "Exposed schedule only; historical changes remain unverified",
        "interpolated_prices": 0,
    }
