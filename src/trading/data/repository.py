from collections.abc import Iterator
from datetime import datetime
from typing import Protocol

from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from trading.data.domain import Candle, Instrument, InstrumentCode, Timeframe
from trading.models import DataIngestionCheckpoint, InstrumentRecord, OhlcvCandleRecord


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
                            "source_provider": provider,
                            "source_reference": source_reference,
                        }
                        for candle in candles
                    ]
                )
                result = session.execute(
                    statement.on_conflict_do_nothing(
                        constraint="uq_ohlcv_instrument_tf_time_source"
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
            .order_by(OhlcvCandleRecord.opened_at, OhlcvCandleRecord.source_provider)
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
                    ),
                    row.source_provider,
                    row.source_reference,
                )
