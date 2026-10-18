import argparse
import asyncio
import sys
from datetime import datetime

import structlog

from trading.config import get_settings
from trading.data.domain import InstrumentCode, Timeframe, configured_instruments
from trading.data.ingestion import ingest_historical
from trading.data.providers import CsvMarketDataProvider
from trading.data.repository import PostgresMarketDataRepository
from trading.db import get_engine
from trading.logging import configure_logging


def parse_timestamp(value: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"Invalid ISO 8601 timestamp: {value}") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise argparse.ArgumentTypeError("Timestamps must include a UTC offset, for example +00:00")
    return timestamp


async def run_import(arguments: argparse.Namespace) -> None:
    settings = get_settings()
    provider = CsvMarketDataProvider(
        arguments.csv,
        InstrumentCode(arguments.instrument),
        Timeframe(arguments.timeframe),
    )
    gaps = await ingest_historical(
        provider,
        PostgresMarketDataRepository(get_engine()),
        InstrumentCode(arguments.instrument),
        Timeframe(arguments.timeframe),
        arguments.start,
        arguments.end,
        instrument_record=configured_instruments(settings.broker_symbol_mappings)[
            InstrumentCode(arguments.instrument)
        ],
    )
    if gaps:
        structlog.get_logger(__name__).warning(
            "csv_import_completed_with_gaps",
            gap_count=len(gaps),
            missing_candles=sum(gap.missing_count for gap in gaps),
        )


def main() -> None:
    from trading.data.commands import run_command

    configure_logging(get_settings().log_level)
    if run_command(sys.argv[1:]):
        return
    parser = argparse.ArgumentParser(description="Import historical OHLCV candles from CSV")
    parser.add_argument("--csv", required=True, help="CSV file with timestamp, OHLC, and volume")
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
    arguments = parser.parse_args()
    asyncio.run(run_import(arguments))


if __name__ == "__main__":
    main()
