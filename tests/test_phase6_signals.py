"""Signal diagnostics cannot change decisions or depend on future label coverage."""

import numpy as np
import pandas as pd
import pytest

from trading.research.phase6.signals import generate_signals


def fixture():
    features = pd.DataFrame(
        {
            "atr_fraction_14": [0.001] * 5,
            "ema_trend_spread": [0.002] * 5,
            "rsi_14": [60.0] * 5,
            "reference_close": [100.0] * 5,
            "volatility_ratio": [1.0] * 5,
        },
        index=pd.date_range("2024-01-08", periods=5, freq="min", tz="UTC"),
    )
    probability = np.array(
        [[0.1, 0.7, 0.2], [0.8, 0.1, 0.1], [0.2, 0.4, 0.4], [np.nan] * 3, [0.1, 0.7, 0.2]]
    )
    features.iloc[4, 0] = np.nan
    return features, probability


def generate(features, probability, diagnostic=None):
    return generate_signals(
        "EURUSD",
        features,
        probability,
        np.ones(5, bool),
        threshold=0.55,
        horizon=15,
        stop_multiple=2,
        profile=np.full((3, 2), 0.001),
        model_id="v1",
        window="tuning",
        diagnostic=diagnostic,
    )


def test_gate_attribution_and_diagnostic_independence():
    f, p = fixture()
    events = []
    baseline = generate(f, p)
    recorded = generate(f, p, events.append)
    pd.testing.assert_frame_equal(baseline, recorded)
    assert [event["reason"] for event in events] == [
        "accepted",
        "neutral_class_preferred",
        "below_probability_threshold",
        "unavailable_probability",
        "incomplete_features",
    ]
    assert len(baseline) == 1
    assert baseline.time.iloc[0] == f.index[0]
    assert events[0]["probabilities"] == [0.1, 0.7, 0.2]


def test_throwing_or_mutating_sink_changes_nothing():
    f, p = fixture()

    def failure(record):
        record.clear()
        raise OSError("sink full")

    baseline = generate(f, p)
    pd.testing.assert_frame_equal(baseline, generate(f, p, failure))
    assert p[0, 1] == 0.7


def test_bad_probability_alignment_rejected():
    f, p = fixture()
    with pytest.raises(ValueError, match="alignment"):
        generate(f, p[:-1])
