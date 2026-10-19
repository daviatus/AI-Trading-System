from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from trading.data.domain import InstrumentCode, Timeframe

_CANDLE_COLUMNS = {
    "instrument",
    "timeframe_minutes",
    "opened_at",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "source_provider",
    "source_reference",
}


class DuckDBParquetAnalytics:
    """Optional read-only analytics over existing Parquet files; no persistent DB copy."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def query_candles(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        columns: Sequence[str],
        source_provider: str | None = None,
    ) -> pd.DataFrame:
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("Analytics range start must be timezone-aware")
        if end.tzinfo is None or end.utcoffset() is None:
            raise ValueError("Analytics range end must be timezone-aware")
        if start >= end:
            raise ValueError("Analytics range start must be earlier than end")
        selected = list(dict.fromkeys(columns))
        if not selected or set(selected) - _CANDLE_COLUMNS:
            raise ValueError("Analytics columns must be selected from the candle schema")
        if not self.root.exists():
            return pd.DataFrame(columns=selected)

        try:
            import duckdb
        except ImportError as error:
            raise RuntimeError(
                'Install DuckDB support with: python -m pip install -e ".[analytics]"'
            ) from error

        parquet_glob = str(self.root / "**" / "*.parquet")
        projection = ", ".join(f'"{column}"' for column in selected)
        connection = duckdb.connect(":memory:")
        try:
            return connection.execute(
                f"""
                SELECT {projection}
                FROM read_parquet(?, hive_partitioning = true)
                WHERE instrument = ?
                  AND timeframe = ?
                  AND opened_at >= ?
                  AND opened_at < ?
                  AND (? IS NULL OR source_provider = ?)
                ORDER BY opened_at
                """,
                [
                    parquet_glob,
                    instrument.value,
                    timeframe.label,
                    start.astimezone(UTC),
                    end.astimezone(UTC),
                    source_provider,
                    source_provider,
                ],
            ).fetchdf()
        finally:
            connection.close()

    def aggregate_candles(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        bucket: str = "1 hour",
        source_provider: str | None = None,
    ) -> pd.DataFrame:
        buckets = {"1 hour", "1 day"}
        if bucket not in buckets:
            raise ValueError(f"bucket must be one of {sorted(buckets)}")
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("Analytics range start must be timezone-aware")
        if end.tzinfo is None or end.utcoffset() is None or start >= end:
            raise ValueError("Analytics range must have timezone-aware increasing bounds")
        try:
            import duckdb
        except ImportError as error:
            raise RuntimeError(
                'Install DuckDB support with: python -m pip install -e ".[analytics]"'
            ) from error

        connection = duckdb.connect(":memory:")
        try:
            parquet_glob = str(self.root / "**" / "*.parquet")
            return connection.execute(
                f"""
                SELECT time_bucket(INTERVAL '{bucket}', opened_at) AS bucket_at,
                       min(low) AS low, max(high) AS high,
                       first(open ORDER BY opened_at) AS open,
                       last(close ORDER BY opened_at) AS close,
                       sum(volume) AS volume, count(*) AS candle_count
                FROM read_parquet(?, hive_partitioning = true)
                WHERE instrument = ? AND timeframe = ?
                  AND opened_at >= ? AND opened_at < ?
                  AND (? IS NULL OR source_provider = ?)
                GROUP BY bucket_at
                ORDER BY bucket_at
                """,
                [
                    parquet_glob,
                    instrument.value,
                    timeframe.label,
                    start.astimezone(UTC),
                    end.astimezone(UTC),
                    source_provider,
                    source_provider,
                ],
            ).fetchdf()
        finally:
            connection.close()
