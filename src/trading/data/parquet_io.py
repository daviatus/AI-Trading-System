from datetime import datetime
from pathlib import Path

from trading.data.domain import Candle, Instrument, InstrumentCode, Timeframe
from trading.data.parquet_store import export_partition, import_parquet
from trading.data.repository import MarketDataRepository


def export_months(
    repository: MarketDataRepository,
    root: str | Path,
    instrument: InstrumentCode,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    *,
    source_provider: str | None = None,
    batch_size: int = 2000,
) -> list[tuple[Path, str]]:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Parquet export bounds must be timezone-aware")
    if start >= end:
        raise ValueError("Parquet export start must be earlier than end")
    output: list[tuple[Path, str]] = []
    cursor = start
    while cursor < end:
        month_end = _next_month(cursor)
        partition_end = min(month_end, end)
        records = repository.iter_candle_records(
            instrument,
            timeframe,
            cursor,
            partition_end,
            batch_size=batch_size,
        )
        selected = (
            (candle, provider, reference)
            for candle, provider, reference in records
            if source_provider is None or provider == source_provider
        )
        output.extend(export_partition(selected, root))
        cursor = partition_end
    return output


def import_parquet_directory(
    repository: MarketDataRepository,
    root: str | Path,
    *,
    batch_size: int = 1000,
) -> int:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    inserted_total = 0
    current_key: tuple[InstrumentCode, Timeframe, str, str] | None = None
    buffer: list[Candle] = []
    instrument: Instrument | None = None

    def persist() -> int:
        if current_key is None or instrument is None or not buffer:
            return 0
        return repository.persist_batch(
            instrument,
            current_key[1],
            current_key[2],
            current_key[3],
            current_key[3],
            list(buffer),
            max(candle.opened_at for candle in buffer),
        )

    for candle, source_provider, source_reference in import_parquet(root):
        key = (candle.instrument, candle.timeframe, source_provider, source_reference)
        if current_key is not None and key != current_key:
            inserted_total += persist()
            buffer.clear()
        if key != current_key:
            instrument = Instrument(
                symbol=candle.instrument,
                display_name=candle.instrument.value,
            )
            current_key = key
        buffer.append(candle)
        if len(buffer) >= batch_size:
            inserted_total += persist()
            buffer.clear()
    inserted_total += persist()
    return inserted_total


def _next_month(value: datetime) -> datetime:
    if value.month == 12:
        return value.replace(
            year=value.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
    return value.replace(month=value.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
