import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from trading.data.domain import Candle, InstrumentCode, Timeframe

DATASET_SCHEMA_VERSION = "1"
FEATURE_SCHEMA_VERSION = "2"


@dataclass(frozen=True)
class FeatureConfig:
    return_lags: tuple[int, ...] = (1, 2, 5)
    volatility_window: int = 20
    ema_spans: tuple[int, ...] = (12, 26)
    rsi_period: int = 14
    atr_period: int = 14
    volume_window: int = 20
    target_horizon_bars: int = 5
    direction_threshold: float = 0.0

    def __post_init__(self) -> None:
        if not self.return_lags or any(lag <= 0 for lag in self.return_lags):
            raise ValueError("return_lags must contain positive integers")
        if any(
            window <= 1
            for window in (
                self.volatility_window,
                *self.ema_spans,
                self.rsi_period,
                self.atr_period,
                self.volume_window,
            )
        ):
            raise ValueError("Indicator periods must be greater than one")
        if self.target_horizon_bars <= 0:
            raise ValueError("target_horizon_bars must be positive")


@dataclass(frozen=True)
class DatasetSplits:
    train: slice
    validation: slice
    test: slice
    boundaries: dict[str, str | None]
    purge_gap_bars: int
    embargo_bars: int


@dataclass(frozen=True)
class DatasetManifest:
    schema_version: str
    feature_schema_version: str
    source: str
    source_fingerprint: str
    instrument: str
    timeframe_minutes: int
    date_range_start: str
    date_range_end: str
    feature_definitions: dict[str, str]
    target_definition: dict[str, Any]
    split_boundaries: dict[str, str | None]
    purge_gap_bars: int
    embargo_bars: int
    configuration: dict[str, Any]
    dataset_fingerprint: str
    row_count: int
    feature_columns: tuple[str, ...]

    def write(self, path: str | Path) -> None:
        _atomic_write_text(
            Path(path),
            json.dumps(asdict(self), sort_keys=True, indent=2, default=_json_default) + "\n",
        )


@dataclass(frozen=True)
class PreparedDataset:
    features: pd.DataFrame
    targets: pd.DataFrame
    splits: DatasetSplits
    manifest: DatasetManifest

    def write(self, directory: str | Path) -> None:
        import pyarrow.parquet as pq

        output = Path(directory)
        output.mkdir(parents=True, exist_ok=True)
        temporary_features = output / f".features.{uuid.uuid4().hex}.tmp"
        temporary_targets = output / f".targets.{uuid.uuid4().hex}.tmp"
        try:
            pq.write_table(
                pd_to_arrow_table(
                    self.features,
                    "features",
                    instrument=self.manifest.instrument,
                    timeframe_minutes=self.manifest.timeframe_minutes,
                    configuration=self.manifest.configuration,
                    dataset_fingerprint=self.manifest.dataset_fingerprint,
                ),
                temporary_features,
                compression="zstd",
            )
            pq.write_table(
                pd_to_arrow_table(
                    self.targets,
                    "targets",
                    instrument=self.manifest.instrument,
                    timeframe_minutes=self.manifest.timeframe_minutes,
                    configuration=self.manifest.configuration,
                    dataset_fingerprint=self.manifest.dataset_fingerprint,
                ),
                temporary_targets,
                compression="zstd",
            )
            os.replace(temporary_features, output / "features.parquet")
            os.replace(temporary_targets, output / "targets.parquet")
            self.manifest.write(output / "manifest.json")
        finally:
            temporary_features.unlink(missing_ok=True)
            temporary_targets.unlink(missing_ok=True)


def build_ml_dataset(
    candles: list[Candle],
    instrument: InstrumentCode,
    timeframe: Timeframe,
    *,
    source: str,
    source_fingerprint: str | None = None,
    feature_config: FeatureConfig | None = None,
    train_fraction: float = 0.70,
    validation_fraction: float = 0.15,
    purge_gap_bars: int | None = None,
    embargo_bars: int = 0,
    max_rows: int = 2_000_000,
) -> PreparedDataset:
    config = feature_config or FeatureConfig()
    if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1:
        raise ValueError("Train and validation fractions must be between 0 and 1")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("Train and validation fractions must leave a non-empty test split")
    if embargo_bars < 0:
        raise ValueError("embargo_bars cannot be negative")
    if max_rows <= 0:
        raise ValueError("max_rows must be positive")
    if len(candles) > max_rows:
        raise ValueError(
            f"Dataset has {len(candles)} candles, above max_rows={max_rows}; "
            "select a narrower date range"
        )
    purge = config.target_horizon_bars if purge_gap_bars is None else purge_gap_bars
    if purge < config.target_horizon_bars:
        raise ValueError("purge_gap_bars must be at least the forward target horizon")

    raw = _candles_to_frame(candles, instrument, timeframe)
    features, targets, definitions = _create_features_and_targets(raw, config)
    valid = features.notna().all(axis=1) & targets.notna().all(axis=1)
    features = features.loc[valid].astype("float64")
    targets = targets.loc[valid].astype("float64")
    if len(features) < (2 * purge + 3):
        raise ValueError("Not enough complete feature rows for purged chronological splits")

    splits = chronological_splits(
        features.index,
        train_fraction=train_fraction,
        validation_fraction=validation_fraction,
        purge_gap_bars=purge,
        embargo_bars=embargo_bars,
    )
    input_fingerprint = source_fingerprint or _frame_fingerprint(raw)
    dataset_fingerprint = _dataset_fingerprint(features, targets, input_fingerprint, config)
    manifest = DatasetManifest(
        schema_version=DATASET_SCHEMA_VERSION,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        source=source,
        source_fingerprint=input_fingerprint,
        instrument=instrument.value,
        timeframe_minutes=timeframe.value,
        date_range_start=features.index[0].isoformat(),
        date_range_end=features.index[-1].isoformat(),
        feature_definitions=definitions,
        target_definition={
            "forward_return": (f"close[t+{config.target_horizon_bars}] / close[t] - 1"),
            "binary_direction": (f"forward_return > {config.direction_threshold}"),
            "horizon_bars": config.target_horizon_bars,
            "features_exclude_targets": True,
        },
        split_boundaries=splits.boundaries,
        purge_gap_bars=purge,
        embargo_bars=embargo_bars,
        configuration={
            "features": asdict(config),
            "train_fraction": train_fraction,
            "validation_fraction": validation_fraction,
        },
        dataset_fingerprint=dataset_fingerprint,
        row_count=len(features),
        feature_columns=tuple(features.columns),
    )
    return PreparedDataset(features, targets, splits, manifest)


def chronological_splits(
    index: pd.Index,
    *,
    train_fraction: float = 0.70,
    validation_fraction: float = 0.15,
    purge_gap_bars: int = 5,
    embargo_bars: int = 0,
) -> DatasetSplits:
    row_count = len(index)
    if row_count < 3:
        raise ValueError("At least three rows are required for chronological splits")
    if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1:
        raise ValueError("Split fractions must be between 0 and 1")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("Split fractions must leave a non-empty test split")
    if purge_gap_bars < 0 or embargo_bars < 0:
        raise ValueError("Purge and embargo gaps cannot be negative")

    train_boundary = int(row_count * train_fraction)
    validation_boundary = int(row_count * (train_fraction + validation_fraction))
    train_end = train_boundary - purge_gap_bars
    validation_start = train_boundary + embargo_bars
    validation_end = validation_boundary - purge_gap_bars
    test_start = validation_boundary + embargo_bars
    if train_end <= 0 or validation_end <= validation_start or test_start >= row_count:
        raise ValueError("Purge/embargo gaps leave an empty train, validation, or test split")

    def label(position: int) -> str | None:
        if position < 0 or position >= row_count:
            return None
        value = index[position]
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    boundaries = {
        "train_start": label(0),
        "train_end_exclusive": label(train_end),
        "validation_start": label(validation_start),
        "validation_end_exclusive": label(validation_end),
        "test_start": label(test_start),
        "test_end_exclusive": None,
    }
    return DatasetSplits(
        train=slice(0, train_end),
        validation=slice(validation_start, validation_end),
        test=slice(test_start, row_count),
        boundaries=boundaries,
        purge_gap_bars=purge_gap_bars,
        embargo_bars=embargo_bars,
    )


def _candles_to_frame(
    candles: list[Candle],
    instrument: InstrumentCode,
    timeframe: Timeframe,
) -> pd.DataFrame:
    if not candles:
        raise ValueError("No candles supplied for dataset preparation")
    ordered = candles
    seen: set[datetime] = set()
    previous_timestamp: datetime | None = None
    records = []
    for candle in ordered:
        if candle.instrument != instrument or candle.timeframe != timeframe:
            raise ValueError("Dataset input contains an unexpected instrument or timeframe")
        if candle.opened_at in seen:
            raise ValueError(f"Duplicate candle in dataset input at {candle.opened_at}")
        if previous_timestamp is not None and candle.opened_at < previous_timestamp:
            raise ValueError("Dataset candle timestamps must be chronological")
        seen.add(candle.opened_at)
        previous_timestamp = candle.opened_at
        records.append(
            {
                "opened_at": candle.opened_at,
                "open": float(candle.open),
                "high": float(candle.high),
                "low": float(candle.low),
                "close": float(candle.close),
                "volume": float(candle.volume) if candle.volume is not None else float("nan"),
            }
        )
    frame = pd.DataFrame.from_records(records).set_index("opened_at").sort_index()
    frame.index = pd.DatetimeIndex(
        frame.index + timedelta(minutes=timeframe.value),
        tz="UTC",
        name="prediction_at",
    )
    if not frame.index.is_monotonic_increasing:
        raise ValueError("Dataset candle timestamps must be chronological")
    if frame["close"].le(0).any():
        raise ValueError("Dataset closes must be positive")
    return frame


def _create_features_and_targets(
    bars: pd.DataFrame,
    config: FeatureConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str]]:
    close = bars["close"]
    volume = bars["volume"]
    returns = close.pct_change(fill_method=None)
    features: dict[str, pd.Series] = {}
    definitions: dict[str, str] = {
        "prediction_at": (
            "Candle open timestamp plus timeframe; all features available at candle close (UTC)"
        )
    }
    for lag in config.return_lags:
        name = f"return_{lag}"
        features[name] = close.pct_change(lag, fill_method=None)
        definitions[name] = f"close[t] / close[t-{lag}] - 1"
    vol_name = f"realized_volatility_{config.volatility_window}"
    features[vol_name] = returns.rolling(
        config.volatility_window,
        min_periods=config.volatility_window,
    ).std(ddof=0)
    definitions[vol_name] = f"std(close returns[t-{config.volatility_window - 1}:t])"

    for span in config.ema_spans:
        name = f"ema_distance_{span}"
        ema = close.ewm(span=span, adjust=False, min_periods=span).mean()
        features[name] = close / ema - 1
        definitions[name] = f"close[t] / causal EMA(span={span}, adjust=False) - 1"

    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    average_gain = gains.ewm(
        alpha=1 / config.rsi_period,
        adjust=False,
        min_periods=config.rsi_period,
    ).mean()
    average_loss = losses.ewm(
        alpha=1 / config.rsi_period,
        adjust=False,
        min_periods=config.rsi_period,
    ).mean()
    relative_strength = average_gain / average_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + relative_strength))
    rsi = rsi.mask((average_loss == 0) & (average_gain > 0), 100)
    rsi = rsi.mask((average_loss == 0) & (average_gain == 0), 50)
    features[f"rsi_{config.rsi_period}"] = rsi
    definitions[f"rsi_{config.rsi_period}"] = (
        f"Wilder-style causal RSI with period {config.rsi_period}, through t"
    )

    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - previous_close).abs(),
            (bars["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.ewm(
        alpha=1 / config.atr_period,
        adjust=False,
        min_periods=config.atr_period,
    ).mean()
    features[f"atr_fraction_{config.atr_period}"] = atr / close
    definitions[f"atr_fraction_{config.atr_period}"] = (
        f"Causal Wilder-style ATR(period={config.atr_period}) / close[t]"
    )

    volume_mean = volume.rolling(
        config.volume_window,
        min_periods=config.volume_window,
    ).mean()
    volume_std = volume.rolling(
        config.volume_window,
        min_periods=config.volume_window,
    ).std(ddof=0)
    features[f"volume_mean_{config.volume_window}"] = volume_mean
    definitions[f"volume_mean_{config.volume_window}"] = (
        f"Mean volume over current and prior {config.volume_window - 1} bars"
    )
    features[f"volume_zscore_{config.volume_window}"] = (volume - volume_mean) / volume_std.replace(
        0, np.nan
    )
    definitions[f"volume_zscore_{config.volume_window}"] = (
        f"Current volume causal z-score over {config.volume_window} bars"
    )

    hour = bars.index.hour + bars.index.minute / 60
    weekday = bars.index.dayofweek
    features["time_of_day_sin"] = pd.Series(np.sin(2 * np.pi * hour / 24), index=bars.index)
    features["time_of_day_cos"] = pd.Series(np.cos(2 * np.pi * hour / 24), index=bars.index)
    features["weekday_sin"] = pd.Series(np.sin(2 * np.pi * weekday / 7), index=bars.index)
    features["weekday_cos"] = pd.Series(np.cos(2 * np.pi * weekday / 7), index=bars.index)
    for name in ("time_of_day_sin", "time_of_day_cos", "weekday_sin", "weekday_cos"):
        definitions[name] = "Deterministic UTC candle-close time/calendar encoding"

    feature_frame = pd.DataFrame(features, index=bars.index)
    horizon = config.target_horizon_bars
    forward_return = close.shift(-horizon) / close - 1
    target_frame = pd.DataFrame(
        {
            "forward_return": forward_return,
            "direction": (forward_return > config.direction_threshold).astype("float64"),
        },
        index=bars.index,
    )
    target_frame.loc[forward_return.isna(), "direction"] = np.nan
    return feature_frame, target_frame, definitions


def _frame_fingerprint(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    digest.update(frame.to_csv(float_format="%.17g", date_format="%Y-%m-%dT%H:%M:%S%z").encode())
    return digest.hexdigest()


def _dataset_fingerprint(
    features: pd.DataFrame,
    targets: pd.DataFrame,
    source_fingerprint: str,
    config: FeatureConfig,
) -> str:
    digest = hashlib.sha256(source_fingerprint.encode())
    digest.update(json.dumps(asdict(config), sort_keys=True).encode())
    digest.update(_frame_fingerprint(features).encode())
    digest.update(_frame_fingerprint(targets).encode())
    return digest.hexdigest()


def _atomic_write_text(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(contents, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"Not JSON serializable: {type(value).__name__}")


def pd_to_arrow_table(
    frame: pd.DataFrame,
    table_name: str,
    *,
    instrument: str | None = None,
    timeframe_minutes: int | None = None,
    configuration: dict[str, Any] | None = None,
    dataset_fingerprint: str | None = None,
):
    import pyarrow as pa

    table = pa.Table.from_pandas(frame, preserve_index=True)
    metadata = dict(table.schema.metadata or {})
    metadata[b"trading.dataset_schema_version"] = DATASET_SCHEMA_VERSION.encode()
    metadata[b"trading.dataset_table"] = table_name.encode()
    if table_name == "features":
        metadata[b"trading.feature_schema_version"] = FEATURE_SCHEMA_VERSION.encode()
    if instrument is not None:
        metadata[b"trading.instrument"] = instrument.encode()
    if timeframe_minutes is not None:
        metadata[b"trading.timeframe_minutes"] = str(timeframe_minutes).encode()
    if configuration is not None:
        metadata[b"trading.feature_configuration"] = json.dumps(
            configuration,
            sort_keys=True,
            default=_json_default,
        ).encode()
    if dataset_fingerprint is not None:
        metadata[b"trading.dataset_fingerprint"] = dataset_fingerprint.encode()
    return table.replace_schema_metadata(metadata)
