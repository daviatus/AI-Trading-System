from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from trading.data.dataset import FeatureConfig, build_ml_dataset
from trading.data.domain import Candle, InstrumentCode, Timeframe
from trading.data.phase4a import (
    AuditCalendar,
    analyze,
    export_analysis,
    read_snapshot,
    validate_test_database_url,
)
from trading.storage.features import ParquetFeatureStore

START = datetime(2026, 10, 19, 8, tzinfo=UTC)


def candles(count=300, start=START):
    result = []
    for i in range(count):
        price = Decimal("1.1") + Decimal(i) / Decimal(100000)
        result.append(Candle(
            instrument=InstrumentCode.EURUSD, timeframe=Timeframe.M1,
            opened_at=start + timedelta(minutes=i), open=price,
            high=price + Decimal("0.0002"), low=price - Decimal("0.0002"),
            close=price, volume=Decimal(42),
        ))
    return result


def prepare(items):
    return build_ml_dataset(items, InstrumentCode.EURUSD, Timeframe.M1,
                            source="fixture", reset_on_gaps=True,
                            feature_config=FeatureConfig(include_trend_indicators=True))


def raw(items):
    return [item.model_dump() for item in items]


@pytest.mark.parametrize("url", [
    "postgresql://test:secret@127.0.0.1:5432/test",
    "postgresql://test:secret@127.0.0.1/test",
    "postgresql://test:secret@remote:15432/test",
    "postgresql://test:secret@127.0.0.1:15432/test?host=other&port=5432",
    "sqlite:///test.db",
])
def test_rejects_unsafe_database_before_creating_engine(url, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("An unsafe database must never be contacted")

    monkeypatch.setattr("trading.data.phase4a.create_engine", forbidden)
    with pytest.raises(ValueError):
        read_snapshot(url)


def test_explicit_test_endpoint_accepted():
    url = validate_test_database_url("postgresql://test:secret@127.0.0.1:15432/test")
    assert url.port == 15432
    assert url.drivername == "postgresql+psycopg"


def test_numeric_indicators_and_constant_volume():
    items = candles()
    dataset = prepare(items)
    features = dataset.features
    prices = pd.Series([float(c.close) for c in items],
                       index=pd.DatetimeIndex([c.opened_at + timedelta(minutes=1) for c in items],
                                              name="prediction_at"))
    at = features.index[50]
    assert features.loc[at, "return_5"] == pytest.approx(
        prices.loc[at] / prices.shift(5).loc[at] - 1
    )
    ema = prices.ewm(span=12, adjust=False, min_periods=12).mean()
    assert features.loc[at, "ema_12"] == pytest.approx(ema.loc[at])
    assert features.loc[at, "rsi_14"] == pytest.approx(100)
    assert features.loc[at, "atr_14"] == pytest.approx(0.0004)
    assert features.loc[at, "realized_volatility_20"] == pytest.approx(
        prices.pct_change().rolling(20).std(ddof=0).loc[at]
    )
    assert (features["volume_zscore_20"] == 0).all()
    assert np.isfinite(features.to_numpy()).all()
    assert {"ema_slope_12", "ema_slope_26", "ema_trend_spread"} <= set(features)


def test_future_price_changes_do_not_change_past_features():
    items = candles()
    baseline = prepare(items)
    changed = [c if i < 200 else c.model_copy(update={
        "open": c.open * 2, "high": c.high * 2,
        "low": c.low * 2, "close": c.close * 2,
    }) for i, c in enumerate(items)]
    other = prepare(changed)
    cutoff = items[199].opened_at + timedelta(minutes=1)
    pd.testing.assert_frame_equal(baseline.features.loc[:cutoff], other.features.loc[:cutoff])


def test_gap_resets_warmup_and_excludes_crossing_targets():
    items = candles()
    items = items[:150] + items[160:]
    dataset = prepare(items)
    before_gap = START + timedelta(minutes=150)
    after_gap = START + timedelta(minutes=161)
    assert not ((dataset.features.index > before_gap - timedelta(minutes=5)) &
                (dataset.features.index <= before_gap)).any()
    assert not ((dataset.features.index >= after_gap) &
                (dataset.features.index < after_gap + timedelta(minutes=25))).any()
    # Rebuilt segment has the same recursive state as an independent input.
    independent = prepare(candles(140, START + timedelta(minutes=160)))
    assert dataset.features.loc[independent.features.index[0], "rsi_14"] == 100
    for earlier, later in ((dataset.splits.train, dataset.splits.validation),
                           (dataset.splits.validation, dataset.splits.test)):
        last = dataset.features.iloc[earlier].index[-1]
        first = dataset.features.iloc[later].index[0]
        assert last + timedelta(minutes=5) < first


def test_weekend_dst_and_confirmed_closure_are_not_missing_data():
    calendar = AuditCalendar([{"start": "2026-10-19T08:01:00Z",
                               "end": "2026-10-19T08:03:00Z", "reason": "fixture maintenance"}])
    report, _ = analyze(raw([candles(1)[0], candles(4)[3]]),
                        as_of=START + timedelta(minutes=4), calendar=calendar)
    assert report["unexpected_gap_count"] == 0
    assert report["expected_closure_minutes"] == 2
    # Before/after US DST transition, Sunday opening moves from 21 to 22 UTC.
    assert calendar.is_open(InstrumentCode.EURUSD, datetime(2026, 10, 25, 21, tzinfo=UTC))
    assert not calendar.is_open(InstrumentCode.EURUSD, datetime(2026, 11, 1, 21, tzinfo=UTC))
    assert calendar.is_open(InstrumentCode.EURUSD, datetime(2026, 11, 1, 22, tzinfo=UTC))
    friday = candles(1, datetime(2026, 10, 23, 20, 59, tzinfo=UTC))[0]
    sunday = candles(1, datetime(2026, 10, 25, 21, tzinfo=UTC))[0]
    weekend, _ = analyze(raw([friday, sunday]), as_of=sunday.opened_at + timedelta(minutes=1))
    assert weekend["unexpected_gap_count"] == 0
    assert weekend["expected_closure_minutes"] == 2880


def test_duplicates_corrupt_ohlc_and_freshness_are_reported():
    rows = raw(candles(3))
    rows[0]["high"] = Decimal("0.1")
    rows.insert(2, rows[1].copy())
    report, _ = analyze(rows, as_of=START + timedelta(minutes=20))
    assert report["duplicate_count"] == 1
    assert report["ohlc_inconsistency_count"] == 1
    assert report["invalid_row_count"] == 1
    assert report["dataset_blocked_by_invalid_data"]
    assert report["freshness"]["missing_completed_trading_minutes_after_latest"] == 17
    assert report["freshness"]["stale_over_10_trading_minutes"]


def test_unfinished_candles_excluded_and_empty_data_handled():
    report, complete = analyze(raw(candles(3)), as_of=START + timedelta(minutes=2, seconds=20))
    assert len(complete) == 2
    assert report["unfinalized_or_future_count"] == 1
    empty, complete = analyze([], as_of=START)
    assert complete == []
    assert empty["coverage_start"] is None


def test_export_roundtrip_and_small_data_report(tmp_path):
    rows = raw(candles())
    snapshot = {"as_of": START + timedelta(minutes=300), "read_only": True}
    report = export_analysis(rows, snapshot, tmp_path / "full")
    assert report["dataset"]["row_count"] > 200
    store = ParquetFeatureStore()
    features = store.read_features(tmp_path / "full/dataset")
    targets = store.read_targets(tmp_path / "full/dataset")
    assert "forward_return" not in features
    assert "forward_return" in targets
    assert (tmp_path / "full/quality.md").exists()
    with pytest.raises(FileExistsError):
        export_analysis(rows, snapshot, tmp_path / "full")
    small = export_analysis(rows[:5], snapshot, tmp_path / "small")
    assert small["dataset"] is None
    assert "dataset_unavailable_reason" in small
