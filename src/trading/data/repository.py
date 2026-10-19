import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
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
