from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from trading.data.dataset import PreparedDataset
from trading.data.domain import Candle, Instrument, InstrumentCode, Timeframe
from trading.data.repository import MarketDataRepository


class FeatureStore(Protocol):
    def write(self, dataset: PreparedDataset, directory: str | Path) -> None: ...

    def read_features(
        self,
        directory: str | Path,
        *,
        columns: Sequence[str] | None = None,
    ) -> pd.DataFrame: ...

    def read_targets(
        self,
        directory: str | Path,
        *,
        columns: Sequence[str] | None = None,
    ) -> pd.DataFrame: ...


class TradeHistoryStore(Protocol):
    def get_trade(self, trade_id: str) -> dict[str, Any] | None: ...

    def trade_ids(self) -> set[str]: ...

    def iter_trades(self) -> Iterator[dict[str, Any]]: ...

    def upsert_batch(
        self,
        interval: object,
        trades: list[dict[str, Any]],
    ) -> tuple[int, int]: ...


class AnalyticsStore(Protocol):
    def query_candles(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        columns: Sequence[str],
        source_provider: str | None = None,
    ) -> pd.DataFrame: ...


@dataclass(frozen=True)
class OperationalDatabase:
    engine: Engine

    def check_connection(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))


@dataclass(frozen=True)
class StorageAccess:
    candles: MarketDataRepository
    features: FeatureStore
    trades: TradeHistoryStore
    operational: OperationalDatabase
    analytics: AnalyticsStore | None = None

    def read_candles(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        batch_size: int = 2000,
    ) -> Iterator[Candle]:
        return self.candles.iter_candles(
            instrument,
            timeframe,
            start,
            end,
            batch_size=batch_size,
        )

    def read_features(
        self,
        directory: str | Path,
        *,
        columns: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        return self.features.read_features(directory, columns=columns)

    def read_targets(
        self,
        directory: str | Path,
        *,
        columns: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        return self.features.read_targets(directory, columns=columns)

    def write_features(self, dataset: PreparedDataset, directory: str | Path) -> None:
        self.features.write(dataset, directory)

    def write_candles(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        provider: str,
        source_key: str,
        source_reference: str,
        candles: list[Candle],
        checkpoint: datetime | None,
    ) -> int:
        return self.candles.persist_batch(
            instrument,
            timeframe,
            provider,
            source_key,
            source_reference,
            candles,
            checkpoint,
        )

    def get_trade(self, trade_id: str) -> dict[str, Any] | None:
        return self.trades.get_trade(trade_id)

    def read_trades(self) -> Iterator[dict[str, Any]]:
        return self.trades.iter_trades()

    def write_trades(
        self,
        interval: object,
        trades: list[dict[str, Any]],
    ) -> tuple[int, int]:
        return self.trades.upsert_batch(interval, trades)

    def query_analytics(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        columns: Sequence[str],
        source_provider: str | None = None,
    ) -> pd.DataFrame:
        if self.analytics is None:
            raise RuntimeError("DuckDB analytics is not configured")
        return self.analytics.query_candles(
            instrument,
            timeframe,
            start,
            end,
            columns=columns,
            source_provider=source_provider,
        )
