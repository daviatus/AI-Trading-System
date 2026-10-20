from datetime import UTC, datetime, timedelta
from pathlib import Path

from trading.ctrader.parquet import CTRADER_PARQUET_PROVIDER
from trading.ctrader.quality import validate_candle_data
from trading.data.domain import Candle, InstrumentCode, Timeframe, configured_instruments
from trading.data.parquet_store import import_parquet
from trading.data.repository import CandleImportReport, CandleImportRepository

MAX_PARQUET_IMPORT_DURATION = timedelta(days=31)
MAX_PARQUET_IMPORT_CANDLES = 31 * 24 * 60


def import_api_candles(
    repository: CandleImportRepository,
    data: list[dict[str, object]],
    start: datetime,
    end: datetime,
) -> tuple[list[Candle], dict[str, object], CandleImportReport]:
    candles, quality = validate_candle_data(data, start, end)
    if quality["validation_status"] == "invalid":
        return candles, quality, _rejected_report(data, quality)

    reference = (
        "ctrader-demo:EURUSD:M1:"
        f"[{start.astimezone(UTC).isoformat()},{end.astimezone(UTC).isoformat()})"
    )
    database_report = repository.import_candles(
        configured_instruments()[InstrumentCode.EURUSD],
        Timeframe.M1,
        CTRADER_PARQUET_PROVIDER,
        [(candle, reference) for candle in candles],
    )
    return candles, quality, database_report


def import_parquet_dataset(
    repository: CandleImportRepository,
    root: str | Path,
) -> tuple[dict[str, object], CandleImportReport]:
    input_root = Path(root)
    if not input_root.is_dir():
        raise FileNotFoundError(f"Parquet dataset directory does not exist: {input_root}")
    records = []
    for record in import_parquet(input_root):
        records.append(record)
        if len(records) > MAX_PARQUET_IMPORT_CANDLES:
            raise ValueError(
                f"Parquet dataset exceeds the {MAX_PARQUET_IMPORT_CANDLES}-candle import limit"
            )
    if not records:
        return (
            {
                "instrument": InstrumentCode.EURUSD.value,
                "timeframe_minutes": Timeframe.M1.value,
                "requested_interval": None,
                "candle_count": 0,
                "first_timestamp": None,
                "last_timestamp": None,
                "duplicate_count": 0,
                "missing_minute_count": 0,
                "gaps": [],
                "validation_status": "valid",
                "errors": [],
                "warnings": [],
            },
            CandleImportReport(),
        )

    source_errors = [
        f"Parquet record {index} has a non-cTrader source provider"
        for index, (_, provider, _) in enumerate(records)
        if provider != CTRADER_PARQUET_PROVIDER
    ]
    candles = [candle for candle, _, _ in records]
    start = min(candle.opened_at for candle in candles)
    end = max(candle.opened_at for candle in candles) + timedelta(minutes=1)
    if end - start > MAX_PARQUET_IMPORT_DURATION:
        source_errors.append("Parquet dataset exceeds the 31-day import duration limit")

    candle_data = [candle.model_dump(mode="json") for candle in candles]
    candles_to_import, quality = validate_candle_data(candle_data, start, end)
    if source_errors:
        quality["validation_status"] = "invalid"
        quality["errors"] = [
            *quality["errors"],
            *(
                {
                    "code": "source_mismatch",
                    "timestamp": None,
                    "detail": error,
                }
                for error in source_errors
            ),
        ]
    if quality["validation_status"] == "invalid":
        return quality, _rejected_report(records, quality)

    reference_by_key = {
        (candle.opened_at, candle.price_type): reference for candle, _, reference in records
    }
    database_report = repository.import_candles(
        configured_instruments()[InstrumentCode.EURUSD],
        Timeframe.M1,
        CTRADER_PARQUET_PROVIDER,
        [
            (candle, reference_by_key[(candle.opened_at, candle.price_type)])
            for candle in candles_to_import
        ],
    )
    return quality, database_report


def _rejected_report(records: list[object], quality: dict[str, object]) -> CandleImportReport:
    errors = quality["errors"]
    issues = tuple(f"{error['code']}: {error['detail']}" for error in errors)
    return CandleImportReport(seen=len(records), rejected=len(records), issues=issues)
