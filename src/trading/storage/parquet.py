import hashlib
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from trading.data.domain import Candle, Instrument, InstrumentCode, Timeframe
from trading.data.parquet_store import export_partition, read_parquet
from trading.data.repository import MarketDataRepository


def parquet_checkpoint_key(root: str | Path, source_key: str) -> str:
    prefix = hashlib.sha256(str(Path(root).resolve()).casefold().encode("utf-8")).hexdigest()[:16]
    return f"parquet:{prefix}:{source_key}"


class ParquetMarketDataRepository:
    """Monthly, atomic Parquet candle storage with PostgreSQL checkpoints."""

    def __init__(self, root: str | Path, checkpoints: MarketDataRepository) -> None:
        self.root = Path(root)
        self.checkpoints = checkpoints
        self._checkpoint_prefix = hashlib.sha256(
            str(self.root.resolve()).casefold().encode("utf-8")
        ).hexdigest()[:16]

    def get_checkpoint(
        self,
        provider: str,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        source_key: str,
    ) -> datetime | None:
        return self.checkpoints.get_checkpoint(
            provider,
            instrument,
            timeframe,
            self._checkpoint_key(source_key),
        )

    def persist_batch(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        provider: str,
        source_key: str,
        source_reference: str,
        candles: list[Candle],
        checkpoint: datetime | None,
    ) -> int:
        identities: set[tuple[datetime, str, str]] = set()
        for candle in candles:
            if candle.instrument != instrument.symbol or candle.timeframe != timeframe:
                raise ValueError("Parquet batch contains an unexpected instrument or timeframe")
            identities.add((candle.opened_at, provider, candle.price_type.value))

        prior_identities: set[tuple[datetime, str, str]] = set()
        if candles:
            for path in _partition_files(self.root, instrument.symbol, timeframe, candles):
                if path.exists():
                    prior_identities.update(
                        (record.opened_at, record_provider, record.price_type.value)
                        for record, record_provider, _ in read_parquet(
                            path,
                            include_lineage=True,
                        )
                    )
            export_partition(
                ((candle, provider, source_reference) for candle in candles),
                self.root,
            )

        inserted = len(identities - prior_identities)
        # Persist the checkpoint only after every Parquet replacement is durable.
        self.checkpoints.persist_batch(
            instrument,
            timeframe,
            provider,
            self._checkpoint_key(source_key),
            source_reference,
            [],
            checkpoint,
        )
        return inserted

    def _checkpoint_key(self, source_key: str) -> str:
        key = parquet_checkpoint_key(self.root, source_key)
        if len(key) > 512:
            raise ValueError("Parquet checkpoint source key exceeds 512 characters")
        return key

    def iter_candles(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        source_provider: str | None = None,
        batch_size: int = 2000,
    ) -> Iterator[Candle]:
        for candle, _, _ in self.iter_candle_records(
            instrument,
            timeframe,
            start,
            end,
            source_provider=source_provider,
            batch_size=batch_size,
        ):
            yield candle

    def iter_candle_records(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        source_provider: str | None = None,
        batch_size: int = 2000,
    ) -> Iterator[tuple[Candle, str, str]]:
        _validate_query(start, end, batch_size)
        start_utc = start.astimezone(UTC)
        end_utc = end.astimezone(UTC)
        for path in _partition_files(self.root, instrument, timeframe, start_utc, end_utc):
            if not path.exists():
                continue
            for candle, provider, reference in read_parquet(
                path,
                include_lineage=True,
                batch_size=batch_size,
                start=start_utc,
                end=end_utc,
            ):
                if start_utc <= candle.opened_at < end_utc and (
                    source_provider is None or provider == source_provider
                ):
                    yield candle, provider, reference


def _partition_files(
    root: Path,
    instrument: InstrumentCode,
    timeframe: Timeframe,
    values: Iterable[Candle] | datetime,
    end: datetime | None = None,
) -> Iterator[Path]:
    if isinstance(values, datetime):
        if end is None:
            raise ValueError("A range end is required when enumerating Parquet partitions")
        cursor = values
        stop = end
        while cursor < stop:
            year, month = cursor.year, cursor.month
            yield _partition_path(root, instrument, timeframe, year, month)
            cursor = _next_month(cursor)
        return

    keys = sorted({(candle.opened_at.year, candle.opened_at.month) for candle in values})
    for year, month in keys:
        yield _partition_path(root, instrument, timeframe, year, month)


def _partition_path(
    root: Path,
    instrument: InstrumentCode,
    timeframe: Timeframe,
    year: int,
    month: int,
) -> Path:
    return (
        root
        / f"instrument={instrument.value}"
        / f"timeframe={timeframe.label}"
        / f"year={year:04d}"
        / f"month={month:02d}"
        / "part-000.parquet"
    )


def _next_month(value: datetime) -> datetime:
    if value.month == 12:
        return value.replace(
            year=value.year + 1,
            month=1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    return value.replace(month=value.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)


def _validate_query(start: datetime, end: datetime, batch_size: int) -> None:
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("Parquet range start must be timezone-aware")
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("Parquet range end must be timezone-aware")
    if start >= end:
        raise ValueError("Parquet range start must be earlier than end")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
