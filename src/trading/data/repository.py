import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from trading.data.domain import Candle, Instrument, InstrumentCode, PriceType, Timeframe
from trading.models import (
    DataIngestionCheckpoint,
    InstrumentRecord,
    MarketDataImportJobRecord,
    MarketDataSourceRecord,
    OhlcvCandleRecord,
)


class MarketDataRepository(Protocol):
    def get_checkpoint(
        self,
        provider: str,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        source_key: str,
    ) -> datetime | None: ...

    def persist_batch(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        provider: str,
        source_key: str,
        source_reference: str,
        candles: list[Candle],
        checkpoint: datetime | None,
    ) -> int: ...

    def iter_candles(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        source_provider: str | None = None,
        batch_size: int = 2000,
    ) -> Iterator[Candle]: ...

    def iter_candle_records(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        source_provider: str | None = None,
        batch_size: int = 2000,
    ) -> Iterator[tuple[Candle, str, str]]: ...


class CandleImportRepository(Protocol):
    def import_candles(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        provider: str,
        records: list[tuple[Candle, str]],
    ) -> "CandleImportReport": ...


@dataclass(frozen=True)
class CandleImportReport:
    seen: int = 0
    inserted: int = 0
    unchanged: int = 0
    conflicting: int = 0
    rejected: int = 0
    issues: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class PostgresMarketDataRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get_checkpoint(
        self,
        provider: str,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        source_key: str,
    ) -> datetime | None:
        with Session(self.engine) as session:
            return session.scalar(
                select(DataIngestionCheckpoint.last_candle_at).where(
                    DataIngestionCheckpoint.provider == provider,
                    DataIngestionCheckpoint.instrument_symbol == instrument.value,
                    DataIngestionCheckpoint.timeframe_minutes == timeframe.value,
                    DataIngestionCheckpoint.source_key == source_key,
                )
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
        checkpoint_table = DataIngestionCheckpoint.__table__
        instrument_table = InstrumentRecord.__table__
        candle_table = OhlcvCandleRecord.__table__
        inserted_count = 0

        with Session(self.engine) as session, session.begin():
            session.execute(
                insert(instrument_table)
                .values(
                    symbol=instrument.symbol.value,
                    display_name=instrument.display_name,
                    broker_symbol_mappings=instrument.broker_symbol_mappings,
                )
                .on_conflict_do_update(
                    index_elements=[instrument_table.c.symbol],
                    set_={
                        "display_name": instrument.display_name,
                        "broker_symbol_mappings": instrument.broker_symbol_mappings,
                    },
                )
            )
            if candles:
                statement = insert(candle_table).values(
                    [
                        {
                            "instrument_symbol": candle.instrument.value,
                            "timeframe_minutes": candle.timeframe.value,
                            "opened_at": candle.opened_at,
                            "open": candle.open,
                            "high": candle.high,
                            "low": candle.low,
                            "close": candle.close,
                            "volume": candle.volume,
                            "price_type": candle.price_type.value,
                            "quality_metadata": candle.quality_metadata,
                            "source_provider": provider,
                            "source_reference": source_reference,
                        }
                        for candle in candles
                    ]
                )
                result = session.execute(
                    statement.on_conflict_do_nothing(
                        constraint="uq_ohlcv_instrument_tf_time_source_price"
                    ).returning(candle_table.c.id)
                )
                inserted_count = len(result.scalars().all())

            excluded = insert(checkpoint_table).excluded
            current = checkpoint_table.c.last_candle_at
            session.execute(
                insert(checkpoint_table)
                .values(
                    provider=provider,
                    instrument_symbol=instrument.symbol.value,
                    timeframe_minutes=timeframe.value,
                    source_key=source_key,
                    last_candle_at=checkpoint,
                )
                .on_conflict_do_update(
                    index_elements=[
                        checkpoint_table.c.provider,
                        checkpoint_table.c.instrument_symbol,
                        checkpoint_table.c.timeframe_minutes,
                        checkpoint_table.c.source_key,
                    ],
                    set_={
                        "last_candle_at": case(
                            (
                                or_(current.is_(None), current < excluded.last_candle_at),
                                excluded.last_candle_at,
                            ),
                            else_=current,
                        ),
                        "updated_at": func.now(),
                    },
                )
            )
        return inserted_count

    def import_candles(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        provider: str,
        records: list[tuple[Candle, str]],
    ) -> CandleImportReport:
        if not provider.strip() or len(provider) > 100:
            raise ValueError("Candle source provider must contain 1 to 100 characters")

        valid, rejected, conflicting, issues = _prepare_import_candles(
            records,
            instrument.symbol,
            timeframe,
        )
        if not valid:
            return CandleImportReport(
                seen=len(records),
                conflicting=conflicting,
                rejected=rejected,
                issues=tuple(issues),
            )

        table = OhlcvCandleRecord.__table__
        key_to_record = {
            (candle.opened_at, candle.price_type.value): (candle, source_reference)
            for candle, source_reference in valid
        }
        inserted = unchanged = existing_conflicts = database_rejected = 0

        with Session(self.engine) as session, session.begin():
            if session.get(InstrumentRecord, instrument.symbol.value) is None:
                raise ValueError(
                    f"Canonical instrument is not present in PostgreSQL: {instrument.symbol.value}"
                )

            earliest = min(candle.opened_at for candle, _ in valid)
            latest = max(candle.opened_at for candle, _ in valid)
            existing_rows = session.scalars(
                select(OhlcvCandleRecord).where(
                    OhlcvCandleRecord.instrument_symbol == instrument.symbol.value,
                    OhlcvCandleRecord.timeframe_minutes == timeframe.value,
                    OhlcvCandleRecord.source_provider == provider,
                    OhlcvCandleRecord.opened_at >= earliest,
                    OhlcvCandleRecord.opened_at <= latest,
                    OhlcvCandleRecord.price_type.in_(
                        {price_type for _, price_type in key_to_record}
                    ),
                )
            ).all()
            existing = {
                (row.opened_at, row.price_type): row
                for row in existing_rows
                if (row.opened_at, row.price_type) in key_to_record
            }
            pending = [record for key, record in key_to_record.items() if key not in existing]

            if pending:
                inserted_keys = set()
                for offset in range(0, len(pending), 1000):
                    chunk = pending[offset : offset + 1000]
                    statement = insert(table).values(
                        [
                            _candle_record_values(candle, provider, source_reference)
                            for candle, source_reference in chunk
                        ]
                    )
                    result = session.execute(
                        statement.on_conflict_do_nothing(
                            constraint="uq_ohlcv_instrument_tf_time_source_price"
                        ).returning(table.c.opened_at, table.c.price_type)
                    )
                    inserted_keys.update(result.all())
                inserted = len(inserted_keys)

                raced_keys = set(key_to_record).difference(existing, inserted_keys)
                if raced_keys:
                    raced_rows = session.scalars(
                        select(OhlcvCandleRecord).where(
                            OhlcvCandleRecord.instrument_symbol == instrument.symbol.value,
                            OhlcvCandleRecord.timeframe_minutes == timeframe.value,
                            OhlcvCandleRecord.source_provider == provider,
                            OhlcvCandleRecord.opened_at.in_(
                                {opened_at for opened_at, _ in raced_keys}
                            ),
                            OhlcvCandleRecord.price_type.in_(
                                {price_type for _, price_type in raced_keys}
                            ),
                        )
                    ).all()
                    existing.update(
                        {
                            (row.opened_at, row.price_type): row
                            for row in raced_rows
                            if (row.opened_at, row.price_type) in raced_keys
                        }
                    )
                    unresolved_keys = raced_keys.difference(existing)
                    if unresolved_keys:
                        database_rejected += len(unresolved_keys)
                        issues.extend(
                            f"Could not classify concurrent insert at {opened_at.isoformat()}"
                            for opened_at, _ in sorted(unresolved_keys)
                        )

            for key, row in existing.items():
                if _same_ohlcv(key_to_record[key][0], row):
                    unchanged += 1
                else:
                    existing_conflicts += 1
                    issues.append(f"Conflicting OHLCV values already exist at {key[0].isoformat()}")

        return CandleImportReport(
            seen=len(records),
            inserted=inserted,
            unchanged=unchanged,
            conflicting=conflicting + existing_conflicts,
            rejected=rejected + database_rejected,
            issues=tuple(issues),
        )

    def iter_candles(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        source_provider: str | None = None,
        batch_size: int = 2000,
    ):
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
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        statement = (
            select(OhlcvCandleRecord)
            .where(
                OhlcvCandleRecord.instrument_symbol == instrument.value,
                OhlcvCandleRecord.timeframe_minutes == timeframe.value,
                OhlcvCandleRecord.opened_at >= start,
                OhlcvCandleRecord.opened_at < end,
            )
            .order_by(
                OhlcvCandleRecord.opened_at,
                OhlcvCandleRecord.source_provider,
                OhlcvCandleRecord.price_type,
            )
        )
        if source_provider is not None:
            statement = statement.where(OhlcvCandleRecord.source_provider == source_provider)

        with Session(self.engine) as session:
            result = session.scalars(
                statement.execution_options(stream_results=True, yield_per=batch_size)
            )
            for row in result:
                yield (
                    Candle(
                        instrument=InstrumentCode(row.instrument_symbol),
                        timeframe=Timeframe(row.timeframe_minutes),
                        opened_at=row.opened_at,
                        open=row.open,
                        high=row.high,
                        low=row.low,
                        close=row.close,
                        volume=row.volume,
                        price_type=PriceType(row.price_type),
                        quality_metadata=row.quality_metadata,
                    ),
                    row.source_provider,
                    row.source_reference,
                )


def _prepare_import_candles(
    records: list[tuple[Candle, str]],
    instrument: InstrumentCode,
    timeframe: Timeframe,
) -> tuple[list[tuple[Candle, str]], int, int, list[str]]:
    grouped: dict[tuple[datetime, str], list[tuple[Candle, str]]] = {}
    rejected = conflicting = 0
    issues: list[str] = []
    previous_timestamp: datetime | None = None
    for index, (candle, source_reference) in enumerate(records):
        reason = _candle_rejection_reason(candle, instrument, timeframe)
        if not source_reference.strip() or len(source_reference) > 512:
            reason = "source reference must contain 1 to 512 characters"
        if previous_timestamp is not None and candle.opened_at < previous_timestamp:
            reason = "candles are not ordered by ascending timestamp"
        previous_timestamp = candle.opened_at
        if reason is not None:
            rejected += 1
            issues.append(f"Rejected candle at index {index}: {reason}")
            continue
        key = (candle.opened_at, candle.price_type.value)
        grouped.setdefault(key, []).append((candle, source_reference))

    valid: list[tuple[Candle, str]] = []
    for key, duplicates in grouped.items():
        if len(duplicates) == 1:
            valid.append(duplicates[0])
        elif all(_same_ohlcv(duplicates[0][0], item[0]) for item in duplicates[1:]):
            valid.append(duplicates[0])
            rejected += len(duplicates) - 1
            issues.append(f"Rejected duplicate input timestamps at {key[0].isoformat()}")
        else:
            conflicting += len(duplicates)
            issues.append(f"Conflicting duplicate input timestamps at {key[0].isoformat()}")
    return valid, rejected, conflicting, issues


def _candle_rejection_reason(
    candle: Candle,
    instrument: InstrumentCode,
    timeframe: Timeframe,
) -> str | None:
    if candle.instrument != instrument:
        return "instrument does not match the import"
    if candle.timeframe != timeframe:
        return "timeframe does not match the import"
    if candle.opened_at.utcoffset() != UTC.utcoffset(candle.opened_at):
        return "timestamp is not normalized to UTC"
    if (
        candle.opened_at.second
        or candle.opened_at.microsecond
        or candle.opened_at.minute % timeframe.value
    ):
        return "timestamp is not aligned to the timeframe"
    prices = (candle.open, candle.high, candle.low, candle.close)
    if any(not value.is_finite() or value <= 0 for value in prices):
        return "OHLC prices must be finite and positive"
    if (
        candle.high < candle.low
        or candle.high < candle.open
        or candle.high < candle.close
        or candle.low > candle.open
        or candle.low > candle.close
    ):
        return "OHLC bounds are inconsistent"
    if candle.volume is not None and (not candle.volume.is_finite() or candle.volume < 0):
        return "volume must be finite and nonnegative"
    for value in prices:
        if _exceeds_numeric_precision(value, 20, 8):
            return "OHLC price exceeds PostgreSQL NUMERIC(20, 8) precision"
    if candle.volume is not None and _exceeds_numeric_precision(candle.volume, 24, 8):
        return "volume exceeds PostgreSQL NUMERIC(24, 8) precision"
    return None


def _exceeds_numeric_precision(value: Decimal, precision: int, scale: int) -> bool:
    digits = value.as_tuple().digits
    exponent = value.as_tuple().exponent
    while digits and digits[-1] == 0:
        digits = digits[:-1]
        exponent += 1
    decimal_scale = max(-exponent, 0)
    integer_digits = max(value.adjusted() + 1, 0) if value else 0
    return decimal_scale > scale or integer_digits > precision - scale


def _same_ohlcv(candle: Candle, record: object) -> bool:
    return all(
        getattr(record, field) == getattr(candle, field)
        for field in ("open", "high", "low", "close", "volume")
    )


def _candle_record_values(
    candle: Candle,
    provider: str,
    source_reference: str,
) -> dict[str, object]:
    return {
        "instrument_symbol": candle.instrument.value,
        "timeframe_minutes": candle.timeframe.value,
        "opened_at": candle.opened_at,
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
        "price_type": candle.price_type.value,
        "quality_metadata": candle.quality_metadata,
        "source_provider": provider,
        "source_reference": source_reference,
    }


class HistoricalImportRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def register_source(
        self,
        source_id: str,
        provider: str,
        format: str,
        timestamp_timezone: str,
        *,
        fixed_offset: bool,
        metadata: dict[str, object],
    ) -> None:
        with Session(self.engine) as session, session.begin():
            session.merge(
                MarketDataSourceRecord(
                    source_id=source_id,
                    provider=provider,
                    format=format,
                    timestamp_timezone=timestamp_timezone,
                    fixed_offset=fixed_offset,
                    metadata_json=metadata,
                )
            )

    def start_job(
        self,
        *,
        job_id: str,
        source_id: str,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        price_type: PriceType,
        source_reference: str,
        source_checksum: str,
        requested_start: datetime,
        requested_end: datetime,
    ) -> None:
        with Session(self.engine) as session, session.begin():
            session.add(
                MarketDataImportJobRecord(
                    job_id=job_id,
                    source_id=source_id,
                    instrument_symbol=instrument.value,
                    timeframe_minutes=timeframe.value,
                    price_type=price_type.value,
                    source_reference=source_reference,
                    source_checksum=source_checksum,
                    requested_start=requested_start,
                    requested_end=requested_end,
                    status="running",
                )
            )

    def has_completed_job(
        self,
        *,
        source_id: str,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        price_type: PriceType,
        source_checksum: str,
        source_reference: str,
        requested_start: datetime,
        requested_end: datetime,
        storage_checkpoint_keys: tuple[str, ...],
    ) -> bool:
        if not storage_checkpoint_keys:
            return False
        with Session(self.engine) as session:
            completed_job = session.scalar(
                select(MarketDataImportJobRecord.job_id)
                .where(
                    MarketDataImportJobRecord.source_id == source_id,
                    MarketDataImportJobRecord.instrument_symbol == instrument.value,
                    MarketDataImportJobRecord.timeframe_minutes == timeframe.value,
                    MarketDataImportJobRecord.price_type == price_type.value,
                    MarketDataImportJobRecord.source_checksum == source_checksum,
                    MarketDataImportJobRecord.source_reference == source_reference,
                    MarketDataImportJobRecord.requested_start == requested_start,
                    MarketDataImportJobRecord.requested_end == requested_end,
                    MarketDataImportJobRecord.status == "completed",
                )
                .limit(1)
            )
            if completed_job is None:
                return False
            return (
                session.scalar(
                    select(DataIngestionCheckpoint.source_key)
                    .where(
                        DataIngestionCheckpoint.provider == source_id,
                        DataIngestionCheckpoint.instrument_symbol == instrument.value,
                        DataIngestionCheckpoint.timeframe_minutes == timeframe.value,
                        DataIngestionCheckpoint.source_key.in_(storage_checkpoint_keys),
                    )
                    .limit(1)
                )
                is not None
            )

    def finish_job(
        self,
        job_id: str,
        *,
        status: str,
        rows_seen: int = 0,
        rows_stored: int = 0,
        gap_count: int = 0,
        coverage_start: datetime | None = None,
        coverage_end: datetime | None = None,
        quality_summary: dict[str, object] | None = None,
        error: str | None = None,
    ) -> None:
        with Session(self.engine) as session, session.begin():
            job = session.get(MarketDataImportJobRecord, job_id)
            if job is None:
                raise LookupError(f"Unknown market data import job: {job_id}")
            job.status = status
            job.rows_seen = rows_seen
            job.rows_stored = rows_stored
            job.gap_count = gap_count
            job.coverage_start = coverage_start
            job.coverage_end = coverage_end
            job.quality_summary = json.loads(
                json.dumps(quality_summary or {}, default=_json_default)
            )
            job.error = error
            job.finished_at = datetime.now(UTC)


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Not JSON serializable: {type(value).__name__}")
