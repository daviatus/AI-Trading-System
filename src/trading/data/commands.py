import argparse
import asyncio
import itertools
import json
import sys
from datetime import datetime

import structlog

from trading.config import get_settings
from trading.data.aggregation import aggregate_complete_minutes
from trading.data.backfill import backfill_historical
from trading.data.dataset import FeatureConfig, build_ml_dataset
from trading.data.domain import InstrumentCode, Timeframe, configured_instruments
from trading.data.market_calendar import MarketCalendar
from trading.data.parquet_io import export_months, import_parquet_directory
from trading.data.providers import CsvMarketDataProvider, SimulatedMarketDataProvider
from trading.data.quality import check_candle_quality
from trading.data.repository import MarketDataRepository, PostgresMarketDataRepository
from trading.db import get_engine
from trading.storage.parquet import ParquetMarketDataRepository

COMMANDS = {"backfill", "quality", "aggregate", "export-parquet", "import-parquet", "dataset"}


def parse_timestamp(value: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"Invalid ISO 8601 timestamp: {value}") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise argparse.ArgumentTypeError("Timestamps must include a UTC offset, for example +00:00")
    return timestamp


def run_command(argv: list[str]) -> bool:
    if argv in (["--help"], ["-h"]):
        parser = argparse.ArgumentParser(
            description="Historical market-data and dataset preparation commands"
        )
        parser.add_argument("command", choices=sorted(COMMANDS))
        parser.print_help()
        return True
    if not argv or argv[0] not in COMMANDS:
        return False
    parser = _build_parser(argv[0])
    arguments = parser.parse_args(argv[1:])
    _execute(arguments)
    return True


def _base_parser(command: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"python -m trading.data.cli {command}")
    parser.set_defaults(command=command)
    if command != "import-parquet":
        parser.add_argument(
            "--instrument",
            required=True,
            choices=[code.value for code in InstrumentCode],
        )
        parser.add_argument("--timeframe", required=True, choices=["1m", "5m"])
        parser.add_argument(
            "--start",
            required=True,
            type=parse_timestamp,
            help="Inclusive ISO 8601 bound",
        )
        parser.add_argument(
            "--end",
            required=True,
            type=parse_timestamp,
            help="Exclusive ISO 8601 bound",
        )
    return parser


def _build_parser(command: str) -> argparse.ArgumentParser:
    parser = _base_parser(command)
    if command == "backfill":
        parser.add_argument("--provider", choices=["csv", "simulated"], default="simulated")
        parser.add_argument("--csv")
        parser.add_argument("--seed", type=int, default=0)
        parser.add_argument(
            "--batch-size",
            type=int,
            help="Default: 500 for PostgreSQL, 50000 for monthly Parquet writes",
        )
        parser.add_argument("--retries", type=int, default=3)
    elif command == "quality":
        parser.add_argument("--source-provider")
        parser.add_argument("--batch-size", type=int, default=2000)
    elif command == "aggregate":
        parser.add_argument("--finalized-through", required=True, type=parse_timestamp)
        parser.add_argument("--source-provider")
        parser.add_argument("--batch-size", type=int, default=2000)
    elif command == "export-parquet":
        parser.add_argument("--output", required=True)
        parser.add_argument("--source-provider")
        parser.add_argument("--batch-size", type=int, default=2000)
    elif command == "import-parquet":
        parser.add_argument("--input", required=True)
        parser.add_argument("--batch-size", type=int, default=1000)
    elif command == "dataset":
        parser.add_argument("--output", required=True)
        parser.add_argument("--source-provider")
        parser.add_argument("--batch-size", type=int, default=2000)
        parser.add_argument("--max-bars", type=int, default=500_000)
        parser.add_argument("--return-lags", type=int, nargs="+", default=[1, 2, 5])
        parser.add_argument("--volatility-window", type=int, default=20)
        parser.add_argument("--ema-spans", type=int, nargs="+", default=[12, 26])
        parser.add_argument("--rsi-period", type=int, default=14)
        parser.add_argument("--atr-period", type=int, default=14)
        parser.add_argument("--volume-window", type=int, default=20)
        parser.add_argument("--target-horizon", type=int, default=5)
        parser.add_argument("--direction-threshold", type=float, default=0.0)
        parser.add_argument("--train-fraction", type=float, default=0.70)
        parser.add_argument("--validation-fraction", type=float, default=0.15)
        parser.add_argument("--purge-gap", type=int)
        parser.add_argument("--embargo", type=int, default=0)
    if command in {"backfill", "quality", "aggregate", "dataset"}:
        parser.add_argument(
            "--storage",
            choices=["postgres", "parquet"],
            default="postgres",
            help="Candle backend; PostgreSQL remains the default",
        )
        parser.add_argument("--data-root", default="data/market/ohlcv")
    return parser


def _execute(arguments: argparse.Namespace) -> None:
    postgres_repository = PostgresMarketDataRepository(get_engine())
    settings = get_settings()
    if arguments.command == "import-parquet":
        inserted = import_parquet_directory(
            postgres_repository,
            arguments.input,
            batch_size=arguments.batch_size,
        )
        print(json.dumps({"inserted": inserted}, indent=2))
        return
    repository: MarketDataRepository = postgres_repository
    if getattr(arguments, "storage", "postgres") == "parquet":
        repository = ParquetMarketDataRepository(arguments.data_root, postgres_repository)

    instrument = InstrumentCode(arguments.instrument)
    timeframe = Timeframe(arguments.timeframe)
    if arguments.command == "backfill":
        batch_size = arguments.batch_size
        if batch_size is None:
            batch_size = 50_000 if isinstance(repository, ParquetMarketDataRepository) else 500
        if arguments.provider == "csv":
            if not arguments.csv:
                raise SystemExit("--csv is required when --provider=csv")
            provider = CsvMarketDataProvider(arguments.csv, instrument, timeframe)
        else:
            provider = SimulatedMarketDataProvider(
                seed=arguments.seed,
                calendar=MarketCalendar.from_config(settings.market_sessions),
            )
        gaps = asyncio.run(
            backfill_historical(
                provider,
                repository,
                instrument,
                timeframe,
                arguments.start,
                arguments.end,
                batch_size=batch_size,
                retries=arguments.retries,
            )
        )
        structlog.get_logger(__name__).info(
            "historical_backfill_report",
            instrument=instrument.value,
            timeframe=timeframe.label,
            gap_count=len(gaps),
            missing_candles=sum(gap.missing_count for gap in gaps),
        )
    elif arguments.command == "quality":
        candles = repository.iter_candles(
            instrument,
            timeframe,
            arguments.start,
            arguments.end,
            source_provider=arguments.source_provider,
            batch_size=arguments.batch_size,
        )
        report = check_candle_quality(
            candles,
            instrument,
            timeframe.value,
            calendar=MarketCalendar.from_config(settings.market_sessions),
            price_jump_threshold=settings.price_jump_thresholds.get(instrument.value, 0.10),
            range_start=arguments.start,
            range_end=arguments.end,
        )
        print(json.dumps(report.as_dict(), default=str, indent=2))
    elif arguments.command == "aggregate":
        if timeframe != Timeframe.M5:
            raise SystemExit("Aggregation creates 5-minute candles; pass --timeframe 5m")
        candles = repository.iter_candles(
            instrument,
            Timeframe.M1,
            arguments.start,
            arguments.end,
            source_provider=arguments.source_provider,
            batch_size=arguments.batch_size,
        )
        result = aggregate_complete_minutes(
            candles,
            arguments.finalized_through,
            calendar=MarketCalendar.from_config(settings.market_sessions),
        )
        reference = f"derived-from-1m;watermark={arguments.finalized_through.isoformat()}"
        inserted = (
            repository.persist_batch(
                configured_instruments(settings.broker_symbol_mappings)[instrument],
                Timeframe.M5,
                "aggregate_1m",
                reference,
                reference,
                list(result.candles),
                result.candles[-1].opened_at,
            )
            if result.candles
            else 0
        )
        print(
            json.dumps(
                {
                    "aggregated": len(result.candles),
                    "inserted": inserted,
                    "incomplete_buckets": [
                        item.isoformat() for item in result.incomplete_bucket_starts
                    ],
                    "excluded_unfinalized_minutes": result.excluded_unfinalized,
                },
                indent=2,
            )
        )
    elif arguments.command == "export-parquet":
        paths = export_months(
            repository,
            arguments.output,
            instrument,
            timeframe,
            arguments.start,
            arguments.end,
            source_provider=arguments.source_provider,
            batch_size=arguments.batch_size,
        )
        print(
            json.dumps(
                [{"path": str(path), "sha256": checksum} for path, checksum in paths],
                indent=2,
            )
        )
    elif arguments.command == "dataset":
        if arguments.max_bars <= 0:
            raise SystemExit("--max-bars must be positive")
        candles = list(
            itertools.islice(
                repository.iter_candles(
                    instrument,
                    timeframe,
                    arguments.start,
                    arguments.end,
                    source_provider=arguments.source_provider,
                    batch_size=arguments.batch_size,
                ),
                arguments.max_bars + 1,
            )
        )
        config = FeatureConfig(
            return_lags=tuple(arguments.return_lags),
            volatility_window=arguments.volatility_window,
            ema_spans=tuple(arguments.ema_spans),
            rsi_period=arguments.rsi_period,
            atr_period=arguments.atr_period,
            volume_window=arguments.volume_window,
            target_horizon_bars=arguments.target_horizon,
            direction_threshold=arguments.direction_threshold,
        )
        dataset = build_ml_dataset(
            candles,
            instrument,
            timeframe,
            source=arguments.source_provider or "postgresql:ohlcv_candles",
            feature_config=config,
            train_fraction=arguments.train_fraction,
            validation_fraction=arguments.validation_fraction,
            purge_gap_bars=arguments.purge_gap,
            embargo_bars=arguments.embargo,
            max_rows=arguments.max_bars,
        )
        dataset.write(arguments.output)
        print(
            json.dumps(
                {
                    "row_count": dataset.manifest.row_count,
                    "feature_columns": dataset.manifest.feature_columns,
                    "dataset_fingerprint": dataset.manifest.dataset_fingerprint,
                    "manifest": arguments.output,
                },
                default=str,
                indent=2,
            )
        )


def main() -> None:
    from trading.logging import configure_logging

    configure_logging(get_settings().log_level)
    if not run_command(sys.argv[1:]):
        raise SystemExit("No Phase 2B command specified; run with --help for CSV import usage")
