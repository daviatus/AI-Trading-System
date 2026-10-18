from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SystemEvent(Base):
    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class InstrumentRecord(Base):
    __tablename__ = "instruments"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    broker_symbol_mappings: Mapped[dict[str, str]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )


class OhlcvCandleRecord(Base):
    __tablename__ = "ohlcv_candles"
    __table_args__ = (
        UniqueConstraint(
            "instrument_symbol",
            "timeframe_minutes",
            "opened_at",
            "source_provider",
            name="uq_ohlcv_instrument_tf_time_source",
        ),
        CheckConstraint("timeframe_minutes IN (1, 5)", name="ck_ohlcv_supported_timeframe"),
        CheckConstraint(
            "high >= low AND high >= open AND high >= close AND low <= open AND low <= close",
            name="ck_ohlcv_price_bounds",
        ),
        CheckConstraint("volume >= 0", name="ck_ohlcv_nonnegative_volume"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instrument_symbol: Mapped[str] = mapped_column(
        ForeignKey("instruments.symbol", ondelete="RESTRICT"),
        nullable=False,
    )
    timeframe_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    source_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(512), nullable=False)


class DataIngestionCheckpoint(Base):
    __tablename__ = "data_ingestion_checkpoints"
    __table_args__ = (
        CheckConstraint(
            "timeframe_minutes IN (1, 5)",
            name="ck_data_ingestion_checkpoints_supported_timeframe",
        ),
    )

    provider: Mapped[str] = mapped_column(String(100), primary_key=True)
    instrument_symbol: Mapped[str] = mapped_column(
        ForeignKey("instruments.symbol", ondelete="CASCADE"),
        primary_key=True,
    )
    timeframe_minutes: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    source_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    last_candle_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
