"""Unified interfaces and adapters for project storage backends."""

from trading.storage.access import (
    AnalyticsStore,
    FeatureStore,
    OperationalDatabase,
    StorageAccess,
    TradeHistoryStore,
)
from trading.storage.duckdb_analytics import DuckDBParquetAnalytics
from trading.storage.features import ParquetFeatureStore
from trading.storage.parquet import ParquetMarketDataRepository

__all__ = [
    "DuckDBParquetAnalytics",
    "AnalyticsStore",
    "FeatureStore",
    "OperationalDatabase",
    "ParquetFeatureStore",
    "ParquetMarketDataRepository",
    "StorageAccess",
    "TradeHistoryStore",
]
