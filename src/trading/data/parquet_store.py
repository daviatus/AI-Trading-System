import hashlib
import os
import uuid
from collections import defaultdict
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from trading.data.domain import Candle, InstrumentCode, Timeframe

PARQUET_SCHEMA_VERSION = "1"
PARQUET_SCHEMA = pa.schema(
    [
        pa.field("instrument", pa.string(), nullable=False),
        pa.field("timeframe_minutes", pa.int16(), nullable=False),
        pa.field("opened_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("open", pa.decimal128(20, 8), nullable=False),
        pa.field("high", pa.decimal128(20, 8), nullable=False),
        pa.field("low", pa.decimal128(20, 8), nullable=False),
        pa.field("close", pa.decimal128(20, 8), nullable=False),
        pa.field("volume", pa.decimal128(24, 8), nullable=False),
        pa.field("source_provider", pa.string(), nullable=False),
        pa.field("source_reference", pa.string(), nullable=False),
    ],
    metadata={
        b"trading.schema_name": b"ohlcv_candles",
        b"trading.schema_version": PARQUET_SCHEMA_VERSION.encode(),
        b"trading.timestamp_semantics": b"candle_open_utc",
    },
)


class ParquetSchemaError(ValueError):
    """Raised when a Parquet file has an unsupported candle schema."""


def partition_path(root: Path, candle: Candle) -> Path:
    return (
        root
        / f"instrument={candle.instrument.value}"
        / f"timeframe={candle.timeframe.label}"
        / f"year={candle.opened_at.year:04d}"
        / f"month={candle.opened_at.month:02d}"
        / "part-000.parquet"
    )


def export_partition(
    candles: Iterable[tuple[Candle, str, str]],
    root: str | Path,
) -> list[tuple[Path, str]]:
    root = Path(root)
    grouped: dict[Path, dict[tuple[datetime, str], tuple[Candle, str]]] = defaultdict(dict)
    for candle, provider, reference in candles:
        path = partition_path(root, candle)
        key = (candle.opened_at, provider)
        existing = grouped[path].get(key)
        current = (candle, reference)
        if existing is not None and existing != current:
            raise ValueError(f"Conflicting export candle for {key} in {path}")
        grouped[path][key] = current

    results: list[tuple[Path, str]] = []
    for path, records in sorted(grouped.items(), key=lambda item: str(item[0])):
        if path.exists():
            for prior in read_parquet(path, include_lineage=True):
                key = (prior[0].opened_at, prior[1])
                current = (prior[0], prior[2])
                existing = records.get(key)
                if existing is not None and existing != current:
                    raise ValueError(f"Conflicting export candle for {key} in {path}")
                records[key] = current
        ordered = [(records[key][0], key[1], records[key][1]) for key in sorted(records)]
        table = _table_from_records(ordered)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            pq.write_table(table, temporary, compression="zstd")
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        results.append((path, file_sha256(path)))
    return results


def import_parquet(root: str | Path) -> Iterator[tuple[Candle, str, str]]:
    for path in sorted(Path(root).rglob("*.parquet")):
        yield from read_parquet(path, include_lineage=True)


def read_parquet(
    path: str | Path,
    *,
    include_lineage: bool = False,
    batch_size: int = 4096,
) -> Iterator[Candle] | Iterator[tuple[Candle, str, str]]:
    parquet_file = pq.ParquetFile(path)
    metadata = parquet_file.schema_arrow.metadata or {}
    if metadata.get(b"trading.schema_version") != PARQUET_SCHEMA_VERSION.encode():
        raise ParquetSchemaError(f"Unsupported Parquet schema version: {path}")
    if not parquet_file.schema_arrow.equals(PARQUET_SCHEMA, check_metadata=True):
        raise ParquetSchemaError(
            f"Parquet schema does not match version {PARQUET_SCHEMA_VERSION}: {path}"
        )
    for batch in parquet_file.iter_batches(batch_size=batch_size):
        columns = batch.to_pydict()
        for index in range(batch.num_rows):
            candle = Candle(
                instrument=InstrumentCode(columns["instrument"][index]),
                timeframe=Timeframe(columns["timeframe_minutes"][index]),
                opened_at=columns["opened_at"][index],
                open=columns["open"][index],
                high=columns["high"][index],
                low=columns["low"][index],
                close=columns["close"][index],
                volume=columns["volume"][index],
            )
            if include_lineage:
                yield (
                    candle,
                    columns["source_provider"][index],
                    columns["source_reference"][index],
                )
            else:
                yield candle


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _table_from_records(records: list[tuple[Candle, str, str]]) -> pa.Table:
    rows = [
        {
            "instrument": candle.instrument.value,
            "timeframe_minutes": candle.timeframe.value,
            "opened_at": candle.opened_at.astimezone(UTC),
            "open": _quantize(candle.open, 20),
            "high": _quantize(candle.high, 20),
            "low": _quantize(candle.low, 20),
            "close": _quantize(candle.close, 20),
            "volume": _quantize(candle.volume, 24),
            "source_provider": provider,
            "source_reference": reference,
        }
        for candle, provider, reference in records
    ]
    return pa.Table.from_pylist(rows, schema=PARQUET_SCHEMA)


def _quantize(value: Decimal, precision: int) -> Decimal:
    quantized = value.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN)
    if len(quantized.as_tuple().digits) > precision:
        raise ValueError(f"Value {value} exceeds Parquet decimal precision {precision}")
    return quantized
