from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest
from pydantic import ValidationError

from trading.config import MarketScheduleSettings, Settings
from trading.data.aggregation import aggregate_complete_minutes, compare_aggregated_source
from trading.data.dataset import FeatureConfig, build_ml_dataset, chronological_splits
from trading.data.domain import Candle, InstrumentCode, Timeframe
from trading.data.market_calendar import MarketCalendar
from trading.data.parquet_store import (
    PARQUET_SCHEMA_VERSION,
    export_partition,
    import_parquet,
)
from trading.data.quality import check_candle_quality


def candle(
    timestamp: datetime,
    index: int,
    *,
    timeframe: Timeframe = Timeframe.M1,
    volume: Decimal | None = None,
    close: Decimal | None = None,
) -> Candle:
    opening = Decimal(100 + index) / Decimal(10)
    close_price = close or opening + Decimal("0.01")
    return Candle(
        instrument=InstrumentCode.WTI,
        timeframe=timeframe,
        opened_at=timestamp,
        open=opening,
        high=max(opening, close_price) + Decimal("0.02"),
        low=min(opening, close_price) - Decimal("0.02"),
        close=close_price,
        volume=volume if volume is not None else Decimal(index % 13 + 1),
    )


def test_complete_minute_bars_aggregate_with_correct_ohlcv() -> None:
    start = datetime(2025, 1, 6, 12, 0, tzinfo=UTC)
    minutes = [candle(start + timedelta(minutes=index), index) for index in range(10)]

    result = aggregate_complete_minutes(minutes, start + timedelta(minutes=10))

    assert len(result.candles) == 2
    first = result.candles[0]
    assert first.timeframe == Timeframe.M5
    assert first.open == minutes[0].open
    assert first.high == max(item.high for item in minutes[:5])
    assert first.low == min(item.low for item in minutes[:5])
    assert first.close == minutes[4].close
    assert first.volume == sum((item.volume for item in minutes[:5]), Decimal(0))


def test_aggregation_omits_incomplete_and_unfinalized_bars() -> None:
    start = datetime(2025, 1, 6, 12, 0, tzinfo=UTC)
    minutes = [candle(start + timedelta(minutes=index), index) for index in range(5)]

    result = aggregate_complete_minutes(minutes[:4], start + timedelta(minutes=5))
    assert result.candles == ()
    assert result.incomplete_bucket_starts == (start,)

    unfinalized = aggregate_complete_minutes(minutes, start + timedelta(minutes=4))
    assert unfinalized.candles == ()
    assert unfinalized.excluded_unfinalized == 1


def test_aggregation_requires_ordered_unique_minute_candles() -> None:
    start = datetime(2025, 1, 6, 12, 0, tzinfo=UTC)
    first = candle(start, 0)
    second = candle(start + timedelta(minutes=1), 1)

    with pytest.raises(ValueError, match="chronological"):
        aggregate_complete_minutes([second, first], start + timedelta(minutes=5))
    with pytest.raises(ValueError, match="Duplicate"):
        aggregate_complete_minutes([first, first], start + timedelta(minutes=5))


def test_provider_five_minute_candles_are_retained_for_comparison() -> None:
    start = datetime(2025, 1, 6, 12, 0, tzinfo=UTC)
    derived = candle(start, 1, timeframe=Timeframe.M5)
    provider = candle(start, 1, timeframe=Timeframe.M5, close=Decimal("12"))

    discrepancies = compare_aggregated_source([derived], [provider])

    assert len(discrepancies) == 1
    assert discrepancies[0].absolute_difference > 0


def test_quality_report_detects_duplicates_order_zero_volume_and_price_jumps() -> None:
    start = datetime(2025, 1, 6, 12, 0, tzinfo=UTC)
    ordered = candle(start, 0)
    duplicate = candle(start, 0, volume=Decimal(0))
    reversed_time = candle(start - timedelta(minutes=1), 1, close=Decimal("50"))

    report = check_candle_quality(
        [ordered, duplicate, reversed_time],
        InstrumentCode.WTI,
        Timeframe.M1.value,
        price_jump_threshold=0.1,
    )
    codes = {issue.code for issue in report.issues}

    assert report.duplicate_count == 1
    assert report.out_of_order_count == 1
    assert report.zero_volume_count == 1
    assert {
        "duplicate_candle",
        "out_of_order_candle",
        "zero_volume",
        "suspicious_price_jump",
    } <= codes


def test_quality_report_surfaces_negative_price_and_invalid_ohlc_observations() -> None:
    @dataclass
    class RawObservation:
        instrument: InstrumentCode
        timeframe: Timeframe
        opened_at: datetime
        open: Decimal
        high: Decimal
        low: Decimal
        close: Decimal
        volume: Decimal

    observation = RawObservation(
        instrument=InstrumentCode.WTI,
        timeframe=Timeframe.M1,
        opened_at=datetime(2025, 1, 6, tzinfo=UTC),
        open=Decimal("-2"),
        high=Decimal("-3"),
        low=Decimal("-4"),
        close=Decimal("-2"),
        volume=Decimal("-1"),
    )
    report = check_candle_quality([observation], InstrumentCode.WTI, 1)

    assert {"negative_price", "negative_volume", "invalid_ohlc"} <= {
        issue.code for issue in report.issues
    }


def test_quality_report_distinguishes_weekend_closures_from_gaps() -> None:
    friday = datetime(2025, 1, 10, 23, 59, tzinfo=UTC)
    monday = datetime(2025, 1, 13, 0, 0, tzinfo=UTC)
    report = check_candle_quality(
        [candle(friday, 1), candle(monday, 2)],
        InstrumentCode.WTI,
        Timeframe.M1.value,
    )

    assert report.missing_candle_count == 2880
    assert report.unexpected_gap_count == 0
    assert report.gaps[0].expected_closure_count == 2880


def test_quality_report_checks_requested_range_edges() -> None:
    start = datetime(2025, 1, 6, 12, 0, tzinfo=UTC)
    candles = [candle(start + timedelta(minutes=1), 1)]
    report = check_candle_quality(
        candles,
        InstrumentCode.WTI,
        Timeframe.M1.value,
        range_start=start,
        range_end=start + timedelta(minutes=3),
    )

    assert report.missing_candle_count == 2
    assert report.unexpected_gap_count == 2


def test_quality_report_does_not_treat_tail_as_leading_gap() -> None:
    start = datetime(2025, 1, 6, 12, 0, tzinfo=UTC)
    candles = [candle(start + timedelta(minutes=index), index) for index in range(3)]
    report = check_candle_quality(
        candles,
        InstrumentCode.WTI,
        Timeframe.M1.value,
        range_start=start,
        range_end=start + timedelta(minutes=3),
    )

    assert report.missing_candle_count == 0
    assert report.unexpected_gap_count == 0


def test_quality_report_honors_instrument_specific_sessions() -> None:
    schedule = MarketScheduleSettings(
        timezone="UTC",
        weekdays=[0, 1, 2, 3, 4],
        sessions=[
            {"open": "09:00", "close": "12:00"},
            {"open": "13:00", "close": "17:00"},
        ],
    )
    calendar = MarketCalendar.from_config({"WTI": schedule})
    start = datetime(2025, 1, 6, 11, 59, tzinfo=UTC)
    report = check_candle_quality(
        [candle(start, 1), candle(start + timedelta(minutes=2), 2)],
        InstrumentCode.WTI,
        Timeframe.M1.value,
        calendar=calendar,
    )

    assert report.gaps[0].expected_closure_count == 1
    assert report.unexpected_gap_count == 0


def test_overnight_market_session_uses_opening_weekdays() -> None:
    schedule = MarketScheduleSettings(
        timezone="UTC",
        weekdays=[0],
        sessions=[{"open": "18:00", "close": "17:00"}],
    )
    calendar = MarketCalendar.from_config({"WTI": schedule})

    assert not calendar.is_open(InstrumentCode.WTI, datetime(2025, 1, 6, 17, 59, tzinfo=UTC))
    assert calendar.is_open(InstrumentCode.WTI, datetime(2025, 1, 6, 18, 0, tzinfo=UTC))
    assert calendar.is_open(InstrumentCode.WTI, datetime(2025, 1, 7, 16, 59, tzinfo=UTC))
    assert not calendar.is_open(InstrumentCode.WTI, datetime(2025, 1, 7, 17, 0, tzinfo=UTC))
    assert not calendar.is_open(InstrumentCode.WTI, datetime(2025, 1, 7, 18, 0, tzinfo=UTC))


def test_market_schedule_configuration_rejects_invalid_symbol_and_weekday() -> None:
    with pytest.raises(ValidationError, match="Unknown market-session symbols"):
        Settings(_env_file=None, market_sessions={"BTCUSD": {}})
    with pytest.raises(ValidationError, match="weekdays"):
        MarketScheduleSettings(weekdays=[7])


def test_phase_2b_cli_dispatches_command_name(monkeypatch) -> None:
    from trading.data import commands

    dispatched = []
    monkeypatch.setattr(commands, "_execute", dispatched.append)

    assert commands.run_command(
        [
            "backfill",
            "--instrument",
            "WTI",
            "--timeframe",
            "1m",
            "--start",
            "2025-01-06T00:00:00Z",
            "--end",
            "2025-01-06T00:01:00Z",
        ]
    )
    assert dispatched[0].command == "backfill"


def test_parquet_partition_round_trip_is_stable_and_idempotent(tmp_path) -> None:
    start = datetime(2025, 1, 6, 12, 0, tzinfo=UTC)
    rows = [
        (
            candle(start + timedelta(minutes=index), index),
            "fixture",
            "deterministic test source",
        )
        for index in range(5)
    ]

    first_export = export_partition(rows, tmp_path)
    second_export = export_partition(rows, tmp_path)
    imported = list(import_parquet(tmp_path))

    assert len(first_export) == 1
    assert first_export == second_export
    assert len(imported) == len(rows)
    assert imported[0] == rows[0]
    assert "instrument=WTI" in str(first_export[0][0])
    assert "timeframe=1m" in str(first_export[0][0])
    assert "year=2025" in str(first_export[0][0])

    import pyarrow.parquet as pq

    schema = pq.read_schema(first_export[0][0])
    assert schema.metadata[b"trading.schema_version"] == PARQUET_SCHEMA_VERSION.encode()
    assert str(schema.field("opened_at").type) == "timestamp[us, tz=UTC]"


def test_parquet_export_rejects_conflicting_same_source_rows(tmp_path) -> None:
    start = datetime(2025, 1, 6, 12, 0, tzinfo=UTC)
    first = candle(start, 1)
    different = candle(start, 2)

    with pytest.raises(ValueError, match="Conflicting export candle"):
        export_partition(
            [(first, "provider", "same"), (different, "provider", "same")],
            tmp_path,
        )


def synthetic_series(length: int = 180) -> list[Candle]:
    start = datetime(2025, 1, 6, tzinfo=UTC)
    rows = []
    previous_close = Decimal("75")
    for index in range(length):
        timestamp = start + timedelta(minutes=index)
        variation = Decimal((index * 7) % 17 - 8) / Decimal(1000)
        opening = previous_close
        closing = opening + variation
        rows.append(
            Candle(
                instrument=InstrumentCode.WTI,
                timeframe=Timeframe.M1,
                opened_at=timestamp,
                open=opening,
                high=max(opening, closing) + Decimal("0.02"),
                low=min(opening, closing) - Decimal("0.02"),
                close=closing,
                volume=Decimal(index % 19 + 1),
            )
        )
        previous_close = closing
    return rows


def build_dataset(candles: list[Candle]):
    return build_ml_dataset(
        candles,
        InstrumentCode.WTI,
        Timeframe.M1,
        source="synthetic-test",
        feature_config=FeatureConfig(
            return_lags=(1, 2),
            volatility_window=8,
            ema_spans=(5, 10),
            rsi_period=6,
            atr_period=6,
            volume_window=8,
            target_horizon_bars=3,
        ),
        train_fraction=0.6,
        validation_fraction=0.2,
    )


def test_dataset_features_are_causal_targets_aligned_and_separate() -> None:
    rows = synthetic_series()
    dataset = build_dataset(rows)
    altered = list(rows)
    last = altered[-1]
    altered[-1] = Candle(
        instrument=last.instrument,
        timeframe=last.timeframe,
        opened_at=last.opened_at,
        open=last.open,
        high=last.high + Decimal("0.1"),
        low=last.low,
        close=last.close + Decimal("0.05"),
        volume=last.volume,
    )
    changed = build_dataset(altered)
    common = dataset.features.index[:-3]

    pd.testing.assert_frame_equal(dataset.features.loc[common], changed.features.loc[common])
    assert set(dataset.features.columns).isdisjoint(dataset.targets.columns)
    prediction_time = rows[40].opened_at + timedelta(minutes=1)
    row_position = dataset.features.index.get_loc(prediction_time)
    expected_return = float(rows[43].close / rows[40].close - 1)
    assert dataset.targets.iloc[row_position]["forward_return"] == pytest.approx(expected_return)
    assert dataset.manifest.target_definition["horizon_bars"] == 3


def test_dataset_chronological_splits_purge_overlapping_targets() -> None:
    dataset = build_dataset(synthetic_series())
    train = dataset.features.iloc[dataset.splits.train]
    validation = dataset.features.iloc[dataset.splits.validation]
    test = dataset.features.iloc[dataset.splits.test]

    assert train.index.max() < validation.index.min() < test.index.min()
    assert (validation.index.min() - train.index.max()) > timedelta(minutes=3)
    assert (test.index.min() - validation.index.max()) > timedelta(minutes=3)
    assert dataset.splits.purge_gap_bars == 3


def test_chronological_splits_reject_empty_splits_and_negative_gaps() -> None:
    index = pd.date_range("2025-01-01", periods=100, freq="min", tz="UTC")

    with pytest.raises(ValueError, match="cannot be negative"):
        chronological_splits(index, purge_gap_bars=-1)


def test_dataset_manifest_and_feature_targets_write_as_versioned_parquet(tmp_path) -> None:
    dataset = build_dataset(synthetic_series())
    dataset.write(tmp_path / "dataset")
    output = tmp_path / "dataset"
    manifest = (output / "manifest.json").read_text(encoding="utf-8")

    assert (output / "features.parquet").is_file()
    assert (output / "targets.parquet").is_file()
    assert '"schema_version": "1"' in manifest
    assert dataset.manifest.dataset_fingerprint in manifest

    import pyarrow.parquet as pq

    schema = pq.read_schema(output / "features.parquet")
    assert schema.metadata[b"trading.dataset_schema_version"] == b"1"
    assert schema.metadata[b"trading.dataset_table"] == b"features"


def test_simulated_dataset_fingerprint_is_reproducible() -> None:
    first = build_dataset(synthetic_series())
    second = build_dataset(synthetic_series())

    assert first.manifest.dataset_fingerprint == second.manifest.dataset_fingerprint
    assert first.manifest.source_fingerprint == second.manifest.source_fingerprint
