"""Causal features, observable targets, session integrity and chronological cuts."""

import numpy as np
import pandas as pd
import pytest

from trading.ctrader.calendar import BrokerCalendar
from trading.data.domain import InstrumentCode
from trading.research.phase6.features import (
    FeatureSpec,
    audit_candles,
    calendar_open,
    make_features,
    validate_candles,
)
from trading.research.phase6.labels import LabelSpec, make_labels, purged_split


def bars(n=240, start="2024-01-08T10:00:00Z"):
    close = 100 + np.arange(n) * 0.002 + np.sin(np.arange(n) / 11) * 0.2
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": np.arange(n) % 20 + 1.0,
        },
        index=pd.date_range(start, periods=n, freq="min", name="opened_at"),
    )


def metadata():
    return {
        "specifications": {
            "scheduleTimeZone": "America/New_York",
            "schedule": [{"startSecond": 86400, "endSecond": 5 * 86400}],
            "holiday": [],
        }
    }


def test_features_future_prices_do_not_change_past_and_prefix_is_identical():
    source = bars()
    original = make_features(source)
    changed = source.copy()
    changed.iloc[160:, :4] *= 4
    pd.testing.assert_frame_equal(original.iloc[:160], make_features(changed).iloc[:160])
    pd.testing.assert_frame_equal(original.iloc[:160], make_features(source.iloc[:160]))
    assert original.index.equals(source.index + pd.Timedelta(minutes=1))
    assert original.notna().all(axis=1).any()
    assert all(dtype == np.dtype("float32") for dtype in original.dtypes)


def test_parquet_microsecond_timestamps_have_identical_features_and_labels():
    source = bars()
    microseconds = source.copy()
    microseconds.index = microseconds.index.as_unit("us")
    pd.testing.assert_frame_equal(make_features(source), make_features(microseconds))
    spec = LabelSpec(kind="volatility", minimum_return=0, horizon_minutes=2)
    sigma = pd.Series(0.001, index=source.index + pd.Timedelta(minutes=1))
    pd.testing.assert_frame_equal(
        make_labels(source, spec, sigma), make_labels(microseconds, spec, sigma)
    )


def test_features_gap_resets_state_and_never_imputes():
    source = bars().drop(bars().index[110])
    result = make_features(source)
    reset = make_features(source.iloc[110:])
    assert len(result) == len(source)
    assert result.iloc[110:173].isna().all().all()
    pd.testing.assert_frame_equal(result.iloc[110:], reset)


def test_severely_fragmented_batches_have_no_ready_rows():
    source = bars().iloc[::2]
    assert make_features(source).isna().all().all()


@pytest.mark.parametrize(
    "problem",
    [
        "naive",
        "local",
        "duplicate",
        "backwards",
        "unaligned",
        "nonfinite",
        "nonpositive",
        "inconsistent",
        "2026",
        "bound",
    ],
)
def test_invalid_candle_inputs_fail_closed(problem):
    source = bars(5)
    kwargs = {}
    if problem == "naive":
        source.index = source.index.tz_localize(None)
    elif problem == "local":
        source.index = source.index.tz_convert("Europe/Budapest")
    elif problem == "duplicate":
        source = pd.concat([source.iloc[:1], source])
    elif problem == "backwards":
        source = source.iloc[::-1]
    elif problem == "unaligned":
        source.index += pd.Timedelta(seconds=1)
    elif problem == "nonfinite":
        source.iloc[0, 0] = np.inf
    elif problem == "nonpositive":
        source.iloc[0, 0] = -1
    elif problem == "inconsistent":
        source.iloc[0, 1] = 1
    elif problem == "2026":
        source = bars(5, "2026-01-01T00:00:00Z")
    elif problem == "bound":
        kwargs["max_rows"] = 4
    with pytest.raises(ValueError):
        validate_candles(source, **kwargs)


def test_volume_excluded_unless_reliable_and_explicitly_validated():
    source = bars()
    assert not any("volume" in column for column in make_features(source))
    assert "relative_volume" in make_features(source, FeatureSpec(reliable_volume=True))
    source.loc[source.index[100], "volume"] = np.nan
    with pytest.raises(ValueError, match="Reliable-volume"):
        make_features(source, FeatureSpec(reliable_volume=True))


def test_calendar_matches_broker_dst_and_holiday_rules():
    meta = metadata()
    meta["specifications"]["holiday"] = [
        {
            "holidayDate": (pd.Timestamp("2024-03-11") - pd.Timestamp("1970-01-01")).days,
            "scheduleTimeZone": "America/New_York",
            "isRecurring": False,
            "startSecond": 12 * 3600,
            "endSecond": 14 * 3600,
        }
    ]
    index = pd.date_range("2024-03-08", "2024-03-12", freq="17min", tz="UTC")
    calendar = BrokerCalendar({"XAUUSD": meta})
    expected = [calendar.is_open(InstrumentCode.XAUUSD, time.to_pydatetime()) for time in index]
    np.testing.assert_array_equal(calendar_open(index, meta), expected)


def test_audit_distinguishes_closed_absences_from_missing_open_minutes():
    source = bars(1, "2024-01-08T05:00:00Z")  # Monday midnight New York.
    source = pd.concat([source, bars(1, "2024-01-08T05:02:00Z")])
    report = audit_candles(
        source,
        metadata(),
        start="2024-01-08T04:58:00Z",
        end="2024-01-08T05:03:00Z",
        grid_batch_minutes=2,
        now="2024-01-08T05:10:00Z",
    )
    assert report["missing_expected_open_minutes"] == 1
    assert report["known_closed_absent_minutes"] == 2
    assert report["interpolated_prices"] == 0
    assert report["stale_at_requested_clock"]
    assert report["historical_spreads"] == "unavailable"


def test_no_calendar_does_not_invent_missing_open_minutes():
    source = bars(4).iloc[[0, 3]]
    report = audit_candles(source)
    assert report["missing_expected_open_minutes"] is None
    assert report["unclassified_absent_minutes"] == 2


def test_session_exclusions_reset_features_and_invalidate_labels():
    source = bars(150, "2024-01-08T03:00:00Z")
    result = make_features(source, metadata=metadata())
    assert result.iloc[:120].isna().all().all()
    labels = make_labels(source, LabelSpec(horizon_minutes=1), metadata=metadata())
    assert (labels.iloc[:120].label == -1).all()


def test_forward_labels_next_open_cost_threshold_and_actual_close_time():
    source = bars(8)
    source.loc[:, ["open", "close"]] = 100.0
    source.loc[:, "high"] = 101.0
    source.loc[:, "low"] = 99.0
    source.loc[source.index[2], "close"] = 100.1
    free = make_labels(source, LabelSpec(horizon_minutes=2, cost_bps=0))
    costly = make_labels(source, LabelSpec(horizon_minutes=2, cost_bps=20))
    assert free.label.iloc[0] == 1
    assert costly.label.iloc[0] == 0
    assert free.label_end.iloc[0] == source.index[2] + pd.Timedelta(minutes=1)
    assert free.long_net_return.iloc[0] == pytest.approx(0.001)
    assert free.label.iloc[-1] == -1
    assert pd.isna(free.label_end.iloc[-1])


def test_label_prices_are_future_targets_without_mutating_features():
    source = bars()
    before = make_features(source)
    make_labels(source, LabelSpec(horizon_minutes=10))
    pd.testing.assert_frame_equal(before, make_features(source))
    changed = source.copy()
    changed.iloc[100:, :4] *= 2
    original_label = make_labels(source, LabelSpec(horizon_minutes=10))
    changed_label = make_labels(changed, LabelSpec(horizon_minutes=10))
    pd.testing.assert_frame_equal(original_label.iloc[:90], changed_label.iloc[:90])


def test_forward_label_crossing_gap_or_end_is_unavailable():
    source = bars(8).drop(bars(8).index[2])
    result = make_labels(source, LabelSpec(horizon_minutes=3))
    assert result.label.iloc[0] == -1
    assert result.label_end.iloc[0] is pd.NaT
    assert result.label.iloc[-3:].eq(-1).all()


def test_volatility_labels_use_causal_decision_aligned_scale():
    source = bars(8)
    decision = source.index + pd.Timedelta(minutes=1)
    sigma = pd.Series(0.1, index=decision)
    result = make_labels(source, LabelSpec(kind="volatility", horizon_minutes=2), sigma)
    assert result.label.iloc[0] == 0
    assert result.outcome_threshold.iloc[0] == pytest.approx(0.1 * np.sqrt(2))
    with pytest.raises(ValueError, match="exactly aligned"):
        make_labels(source, LabelSpec(kind="volatility"), pd.Series(0.1, index=source.index))


def test_barrier_ambiguity_is_adverse_for_both_directions_and_ends_early():
    source = bars(5)
    source.loc[:, ["open", "close"]] = 100.0
    source.loc[:, "high"] = 100.1
    source.loc[:, "low"] = 99.9
    source.loc[source.index[1], ["high", "low"]] = [105.0, 95.0]
    sigma = pd.Series(0.01, index=source.index + pd.Timedelta(minutes=1))
    result = make_labels(source, LabelSpec(kind="barrier", horizon_minutes=3, cost_bps=0), sigma)
    assert result.label.iloc[0] == 0
    assert result.long_net_return.iloc[0] == pytest.approx(-0.02)
    assert result.short_net_return.iloc[0] == pytest.approx(-0.02)
    assert result.label_end.iloc[0] == source.index[1] + pd.Timedelta(minutes=1)


def test_barrier_actual_end_ignores_gap_after_both_paths_finished():
    source = bars(8)
    source.loc[:, ["open", "close"]] = 100.0
    source.loc[:, "high"] = 100.1
    source.loc[:, "low"] = 99.9
    source.loc[source.index[1], "high"] = 105.0
    source = source.drop(source.index[2])
    sigma = pd.Series(0.01, index=source.index + pd.Timedelta(minutes=1))
    result = make_labels(source, LabelSpec(kind="barrier", horizon_minutes=4, cost_bps=0), sigma)
    assert result.label.iloc[0] == 1
    assert result.long_net_return.iloc[0] == pytest.approx(0.04)
    assert result.short_net_return.iloc[0] == pytest.approx(-0.02)
    assert result.label_end.iloc[0] == source.index[1] + pd.Timedelta(minutes=1)


def test_barrier_adverse_open_gap_and_terminal_exhaustion():
    source = bars(6)
    source.loc[:, ["open", "close"]] = 100.0
    source.loc[:, "high"] = 100.1
    source.loc[:, "low"] = 99.9
    source.loc[source.index[2], ["open", "close", "high", "low"]] = [95, 95, 95.1, 94.9]
    sigma = pd.Series(0.01, index=source.index + pd.Timedelta(minutes=1))
    result = make_labels(source, LabelSpec(kind="barrier", horizon_minutes=4, cost_bps=0), sigma)
    assert result.long_net_return.iloc[0] == pytest.approx(-0.05)
    assert result.label.iloc[-1] == -1
    assert pd.isna(result.label_end.iloc[-1])


def test_purged_windows_use_actual_label_end_and_forward_embargo():
    source = bars(120)
    labels = make_labels(source, LabelSpec(horizon_minutes=5))
    boundaries = dict(
        zip(
            ("train_start", "train_end", "calibration_end", "tuning_end", "test_end"),
            [source.index[0] + pd.Timedelta(minutes=i) for i in (0, 30, 60, 90, 120)],
            strict=True,
        )
    )
    masks = purged_split(labels.index, labels.label_end, boundaries, embargo_minutes=3)
    assert not np.any(sum(mask.astype(int) for mask in masks.values()) > 1)
    for number, mask in enumerate(masks.values()):
        assert mask.any()
        left, right = list(boundaries.values())[number : number + 2]
        assert (labels.index[mask] >= left + pd.Timedelta(minutes=3 if number else 0)).all()
        assert (labels.label_end[mask] < right).all()
    assert not masks["train"][24]  # End exactly at the right boundary is purged.


def test_split_rejects_holdout_and_misaligned_label_metadata():
    source = bars(120)
    labels = make_labels(source, LabelSpec(horizon_minutes=2))
    bounds = {
        "train_start": "2024-01-01T00:00Z",
        "train_end": "2024-02-01T00:00Z",
        "calibration_end": "2024-03-01T00:00Z",
        "tuning_end": "2024-04-01T00:00Z",
        "test_end": "2026-01-02T00:00Z",
    }
    with pytest.raises(ValueError, match="2026"):
        purged_split(labels.index, labels.label_end, bounds)
    bounds["test_end"] = "2025-01-01T00:00Z"
    with pytest.raises(ValueError, match="exactly"):
        purged_split(labels.index, labels.label_end.iloc[::-1], bounds)


def test_labels_and_features_are_deterministic():
    source = bars()
    pd.testing.assert_frame_equal(make_features(source), make_features(source))
    for kind in ("forward", "volatility", "barrier"):
        spec = LabelSpec(kind=kind, horizon_minutes=5)
        sigma = pd.Series(0.002, index=source.index + pd.Timedelta(minutes=1))
        pd.testing.assert_frame_equal(
            make_labels(source, spec, sigma), make_labels(source, spec, sigma)
        )
