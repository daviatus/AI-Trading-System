import argparse
import asyncio
import itertools
import json
import sys
import uuid
from datetime import datetime
from types import SimpleNamespace

import structlog

from trading.config import get_settings
from trading.data.aggregation import aggregate_complete_minutes
from trading.data.backfill import backfill_historical
from trading.data.dataset import FeatureConfig, build_ml_dataset
from trading.data.domain import InstrumentCode, Timeframe, configured_instruments
from trading.data.histdata import (
    HISTDATA_CONFIG_PATH,
    HISTDATA_SOURCE_ID,
    HistDataAsciiProvider,
    load_histdata_mapping,
)
from trading.data.histdata_batch import discover_histdata_files, process_histdata_batch
from trading.data.historical_validation import summarize_source_coverage
from trading.data.market_calendar import MarketCalendar
from trading.data.parquet_io import export_months, import_parquet_directory
from trading.data.providers import CsvMarketDataProvider, SimulatedMarketDataProvider
from trading.data.quality import check_candle_quality
from trading.data.repository import (
    HistoricalImportRepository,
    MarketDataRepository,
    PostgresMarketDataRepository,
)
from trading.db import get_engine
from trading.storage.parquet import ParquetMarketDataRepository, parquet_checkpoint_key

COMMANDS = {
    "backfill",
    "quality",
    "aggregate",
    "export-parquet",
    "import-parquet",
    "import-histdata",
    "import-histdata-batch",
    "dataset",
}


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
    if command not in {"import-parquet", "import-histdata", "import-histdata-batch"}:
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
        parser.add_argument(
            "--source-provider",
            required=True,
            help="Source provider for M1 bars; required to preserve derived-bar lineage",
        )
        parser.add_argument("--batch-size", type=int, default=2000)
    elif command == "export-parquet":
        parser.add_argument("--output", required=True)
        parser.add_argument("--source-provider")
        parser.add_argument("--batch-size", type=int, default=2000)
    elif command == "import-parquet":
        parser.add_argument("--input", required=True)
        parser.add_argument("--batch-size", type=int, default=1000)
    elif command == "import-histdata":
        parser.add_argument("--source-file", required=True)
        parser.add_argument(
            "--instrument",
            required=True,
            choices=[code.value for code in InstrumentCode],
        )
        parser.add_argument("--start", required=True, type=parse_timestamp)
        parser.add_argument("--end", required=True, type=parse_timestamp)
        parser.add_argument("--storage", choices=["postgres", "parquet"], default="parquet")
        parser.add_argument("--data-root", default="data/market/ohlcv")
        parser.add_argument("--batch-size", type=int, default=50_000)
        parser.add_argument("--retries", type=int, default=3)
    elif command == "import-histdata-batch":
        parser.add_argument("--input-dir", required=True)
        parser.add_argument(
            "--instrument",
            required=True,
            choices=[code.value for code in InstrumentCode],
        )
        parser.add_argument("--start", required=True, type=parse_timestamp)
        parser.add_argument("--end", required=True, type=parse_timestamp)
        parser.add_argument("--storage", choices=["parquet"], default="parquet")
        parser.add_argument("--data-root", default="data/market/ohlcv")
        parser.add_argument("--batch-size", type=int, default=50_000)
        parser.add_argument("--dry-run", action="store_true")
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
    settings = get_settings()
    if arguments.command == "import-histdata-batch" and arguments.dry_run:
        _execute_histdata_batch(arguments, None, settings)
        return
    postgres_repository = PostgresMarketDataRepository(get_engine())
    if arguments.command == "import-histdata":
        _import_histdata(arguments, postgres_repository, settings)
        return
    if arguments.command == "import-histdata-batch":
        _execute_histdata_batch(arguments, postgres_repository, settings)
        return
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
            source_provider=arguments.source_provider,
        )
        reference = f"derived-from-1m:{arguments.source_provider}"
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


def _import_histdata(
    arguments: argparse.Namespace,
    postgres_repository,
    settings,
    *,
    provider: HistDataAsciiProvider | None = None,
    emit: bool = True,
    track_inserted: bool = False,
) -> dict[str, int | str]:
    instrument = InstrumentCode(arguments.instrument)
    provider = provider or HistDataAsciiProvider(
        arguments.source_file, instrument, Timeframe.M1, config_path=HISTDATA_CONFIG_PATH
    )
    start = getattr(arguments, "start", arguments.start)
    end = getattr(arguments, "end", arguments.end)
    source_config = json.loads(HISTDATA_CONFIG_PATH.read_text(encoding="utf-8"))["sources"][
        HISTDATA_SOURCE_ID
    ]
    source_mapping = load_histdata_mapping(HISTDATA_CONFIG_PATH, instrument)
    import_repository = HistoricalImportRepository(postgres_repository.engine)
    import_repository.register_source(
        HISTDATA_SOURCE_ID,
        source_config["provider"],
        source_config["format"],
        source_config["timestamp_timezone"],
        fixed_offset=True,
        metadata={
            "fixed_offset_minutes": source_config["fixed_offset_minutes"],
            "price_type": source_config["price_type"],
            "instrument_mappings": source_config["instruments"],
        },
    )
    timeframe = Timeframe.M1
    job_id = str(uuid.uuid4())
    import_repository.start_job(
        job_id=job_id,
        source_id=HISTDATA_SOURCE_ID,
        instrument=instrument,
        timeframe=timeframe,
        price_type=provider.price_type,
        source_reference=provider.source_reference,
        source_checksum=provider.content_sha256,
        requested_start=start,
        requested_end=end,
    )
    repository: MarketDataRepository = postgres_repository
    if arguments.storage == "parquet":
        repository = ParquetMarketDataRepository(arguments.data_root, postgres_repository)
    try:
        existing_source_rows = (
            sum(
                record[2] == provider.source_reference
                for record in repository.iter_candle_records(
                    instrument,
                    timeframe,
                    start,
                    end,
                    source_provider=HISTDATA_SOURCE_ID,
                    batch_size=arguments.batch_size,
                )
            )
            if track_inserted
            else 0
        )
        gaps = asyncio.run(
            backfill_historical(
                provider,
                repository,
                instrument,
                timeframe,
                start,
                end,
                batch_size=arguments.batch_size,
                retries=arguments.retries,
            )
        )
        from trading.data.quality import check_candle_quality

        quality = check_candle_quality(
            (
                record[0]
                for record in repository.iter_candle_records(
                    instrument,
                    timeframe,
                    start,
                    end,
                    source_provider=HISTDATA_SOURCE_ID,
                    batch_size=arguments.batch_size,
                )
            ),
            instrument,
            timeframe.value,
            calendar=MarketCalendar.from_config(settings.market_sessions),
            price_jump_threshold=settings.price_jump_thresholds.get(instrument.value, 0.10),
            range_start=start,
            range_end=end,
        )
        coverage = summarize_source_coverage(quality, start, end)
        inserted = 0
        if track_inserted:
            candles_with_source = sum(
                record[2] == provider.source_reference
                for record in repository.iter_candle_records(
                    instrument,
                    timeframe,
                    start,
                    end,
                    source_provider=HISTDATA_SOURCE_ID,
                    batch_size=arguments.batch_size,
                )
            )
            inserted = max(0, candles_with_source - existing_source_rows)
        import_repository.finish_job(
            job_id,
            status="completed",
            rows_seen=coverage.candle_count,
            rows_stored=coverage.candle_count,
            gap_count=quality.missing_candle_count,
            coverage_start=coverage.observed_start,
            coverage_end=coverage.observed_end,
            quality_summary=quality.as_dict(),
        )
        output = {
            "job_id": job_id,
            "source_id": HISTDATA_SOURCE_ID,
            "instrument": instrument.value,
            "histdata_symbol": source_mapping["symbol"],
            "price_type": provider.price_type.value,
            "storage": arguments.storage,
            "source_checksum": provider.content_sha256,
            "coverage": coverage.__dict__,
            "quality": quality.as_dict(),
            "ingestion_gap_count": len(gaps),
        }
        if track_inserted:
            output["candles_inserted"] = inserted
        if emit:
            print(json.dumps(output, default=str, indent=2))
        return {
            "job_id": job_id,
            "candles_seen": coverage.candle_count,
            "candles_inserted": inserted,
        }
    except Exception as error:
        import_repository.finish_job(
            job_id,
            status="failed",
            error=f"{type(error).__name__}: {error}",
        )
        raise


def _execute_histdata_batch(arguments: argparse.Namespace, postgres_repository, settings) -> None:
    instrument = InstrumentCode(arguments.instrument)
    files = discover_histdata_files(
        arguments.input_dir,
        instrument,
        arguments.start,
        arguments.end,
    )
    import_repository = (
        HistoricalImportRepository(postgres_repository.engine)
        if postgres_repository is not None
        else None
    )

    def is_completed(provider: HistDataAsciiProvider, candidate) -> bool:
        if import_repository is None:
            return False
        return import_repository.has_completed_job(
            source_id=HISTDATA_SOURCE_ID,
            instrument=instrument,
            timeframe=Timeframe.M1,
            price_type=provider.price_type,
            source_checksum=provider.content_sha256,
            source_reference=provider.source_reference,
            requested_start=candidate.start,
            requested_end=candidate.end,
            storage_checkpoint_keys=(
                parquet_checkpoint_key(arguments.data_root, provider.source_key),
                parquet_checkpoint_key(
                    arguments.data_root,
                    f"sha256:{provider.content_sha256}",
                ),
            ),
        )

    def import_file(candidate, provider: HistDataAsciiProvider) -> int:
        arguments_for_file = SimpleNamespace(
            source_file=str(candidate.path),
            instrument=instrument.value,
            start=candidate.start,
            end=candidate.end,
            storage=arguments.storage,
            data_root=arguments.data_root,
            batch_size=arguments.batch_size,
            retries=3,
        )
        result = _import_histdata(
            arguments_for_file,
            postgres_repository,
            settings,
            provider=provider,
            emit=False,
            track_inserted=True,
        )
        return int(result["candles_inserted"])

    summary = process_histdata_batch(
        files,
        instrument,
        batch_size=arguments.batch_size,
        dry_run=arguments.dry_run,
        import_file=import_file if not arguments.dry_run else None,
        is_completed=is_completed if not arguments.dry_run else None,
    )
    for item in summary["files"]:
        line = f"{item['month']} {item['status']}: {item.get('candles', 0)} candles"
        if item.get("error"):
            line += f" ({item['error']})"
        print(line)
    summary.pop("files")
    print(json.dumps(summary, indent=2))


def main() -> None:
    from trading.logging import configure_logging

    configure_logging(get_settings().log_level)
    if not run_command(sys.argv[1:]):
        raise SystemExit("No Phase 2B command specified; run with --help for CSV import usage")
