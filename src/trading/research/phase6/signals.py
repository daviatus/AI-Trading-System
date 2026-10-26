"""Causal signal gates; diagnostics observe already computed immutable decisions."""

import numpy as np
import pandas as pd

from trading.research.phase6.instrumentation import emit


def payoff_profile(labels, train_mask):
    """Class-conditional net returns fitted on training outcomes only."""
    result = np.zeros((3, 2))
    for label in range(3):
        mask = train_mask & (labels.label.to_numpy() == label)
        for side, name in enumerate(("long_net_return", "short_net_return")):
            values = labels.loc[mask, name].dropna()
            result[label, side] = float(values.mean()) if len(values) else 0
    return result


def generate_signals(
    asset,
    features,
    probability,
    mask,
    *,
    threshold,
    horizon,
    stop_multiple,
    profile,
    model_id,
    window,
    regime_filter="all",
    economic_filter=True,
    diagnostic=None,
    metadata=None,
    model_version="phase6a-v1",
    feature_columns=None,
):
    """First-failure attribution for every prediction; never consults future bars.

    Entry time equals decision time (completed prior bar). Schedules are checked
    from exposed contract metadata; missing next prices are an execution gate.
    """
    if not 0 < threshold < 1:
        raise ValueError("Probability threshold must be between zero and one")
    if probability.shape != (len(features), 3):
        raise ValueError("Prediction alignment mismatch")
    required = set(feature_columns or features.columns) | {
        "atr_fraction_14",
        "reference_close",
        "volatility_ratio",
        "ema_trend_spread",
    }
    complete_rows = np.isfinite(features.loc[:, sorted(required)].to_numpy()).all(axis=1)
    sigmas = features.atr_fraction_14.to_numpy()
    prices = features.reference_close.to_numpy()
    ratios = features.volatility_ratio.to_numpy()
    trends = features.ema_trend_spread.to_numpy()
    allowed = np.ones(len(features), bool)
    if metadata is not None:
        from trading.research.data import session_open
        from trading.research.execution import permitted_entries

        allowed = permitted_entries(features.index - pd.Timedelta(minutes=1), horizon, metadata)
        # Known holidays may begin within a holding horizon; check every minute.
        for offset in range(horizon):
            allowed &= session_open(features.index + pd.Timedelta(minutes=offset), metadata)
    records = []
    for i in np.flatnonzero(mask):
        when = features.index[i]
        p = probability[i]
        complete = bool(complete_rows[i])
        finite = bool(np.isfinite(p).all())
        direction = 1 if finite and p[1] >= p[2] else -1
        chosen = 1 if direction == 1 else 2
        score = float(p @ profile[:, 0 if direction == 1 else 1]) if finite else None
        sigma = float(sigmas[i])
        price = float(prices[i])
        # reference_close is causal sizing context, excluded from model features.
        distance = sigma * price * stop_multiple
        ratio = float(ratios[i])
        trend = abs(float(trends[i]))
        regime = (
            "abnormal"
            if ratio > 2.5
            else "high_volatility"
            if ratio > 1.5
            else "low_volatility"
            if ratio < 0.7
            else "trending"
            if trend > sigma * 0.5
            else "ranging"
        )
        reason = "accepted"
        if not complete:
            reason = "incomplete_features"
        elif not finite:
            reason = "unavailable_probability"
        elif p[chosen] < p[0]:
            reason = "neutral_class_preferred"
        elif p[chosen] < threshold:
            reason = "below_probability_threshold"
        elif economic_filter and score <= 0:
            reason = "nonpositive_training_expected_net_return"
        elif regime_filter == "exclude_abnormal" and regime == "abnormal":
            reason = "abnormal_regime"
        elif not np.isfinite(distance) or distance <= 0:
            reason = "invalid_causal_stop_distance"
        if reason == "accepted" and not allowed[i]:
            reason = "session_or_swap_boundary"
        candidate = finite and p[chosen] >= threshold and p[chosen] >= p[0]
        entropy = float(-(np.clip(p, 1e-10, 1) * np.log(np.clip(p, 1e-10, 1))).sum())
        confidence = 1 - entropy / np.log(3) if finite else 0
        event = {
            "event": "prediction",
            "asset": asset,
            "timestamp": str(when),
            "model_id": model_id,
            "model_version": model_version,
            "window": window,
            "probabilities": p.tolist() if finite else None,
            "threshold": threshold,
            "direction": direction,
            "candidate_signal": bool(candidate),
            "accepted": reason == "accepted",
            "reason": reason,
            "expected_net_return": score,
            "regime": regime,
        }
        # Emit only after all gates; sink cannot change any input used above.
        emit(diagnostic, event)
        if reason == "accepted":
            records.append(
                {
                    "time": when,
                    "asset": asset,
                    "direction": direction,
                    "probability": float(p[chosen]),
                    "confidence": confidence,
                    "score": score,
                    "stop_distance": distance,
                    "horizon": horizon,
                    "regime": regime,
                    "model_id": model_id,
                    "threshold": threshold,
                    "window": window,
                    "model_version": model_version,
                }
            )
    return pd.DataFrame(
        records,
        columns=[
            "time",
            "asset",
            "direction",
            "probability",
            "confidence",
            "score",
            "stop_distance",
            "horizon",
            "regime",
            "model_id",
            "threshold",
            "window",
            "model_version",
        ],
    )
