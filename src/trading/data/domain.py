from datetime import UTC, datetime
from decimal import Decimal
from enum import IntEnum, StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class InstrumentCode(StrEnum):
    WTI = "WTI"
    XAUUSD = "XAUUSD"
    EURUSD = "EURUSD"
    SPX500 = "SPX500"


class Timeframe(IntEnum):
    M1 = 1
    M5 = 5

    @classmethod
    def _missing_(cls, value: object) -> Self | None:
        if isinstance(value, str) and value.endswith("m"):
            try:
                return cls(int(value[:-1]))
            except ValueError:
                return None
        return None

    @property
    def label(self) -> str:
        return f"{self.value}m"


class Instrument(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: InstrumentCode
    display_name: str
    broker_symbol_mappings: dict[str, str] = Field(default_factory=dict)


INSTRUMENT_CATALOG: dict[InstrumentCode, Instrument] = {
    InstrumentCode.WTI: Instrument(symbol=InstrumentCode.WTI, display_name="WTI crude oil"),
    InstrumentCode.XAUUSD: Instrument(symbol=InstrumentCode.XAUUSD, display_name="Gold"),
    InstrumentCode.EURUSD: Instrument(symbol=InstrumentCode.EURUSD, display_name="EUR/USD"),
    InstrumentCode.SPX500: Instrument(symbol=InstrumentCode.SPX500, display_name="S&P 500"),
}


def configured_instruments(
    provider_mappings: dict[str, dict[str, str]] | None = None,
) -> dict[InstrumentCode, Instrument]:
    provider_mappings = provider_mappings or {}
    instruments = {}
    for code, instrument in INSTRUMENT_CATALOG.items():
        mappings = {
            provider: symbols[code.value]
            for provider, symbols in provider_mappings.items()
            if code.value in symbols
        }
        instruments[code] = instrument.model_copy(update={"broker_symbol_mappings": mappings})
    return instruments


class Candle(BaseModel):
    model_config = ConfigDict(frozen=True)

    instrument: InstrumentCode
    timeframe: Timeframe
    opened_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    @field_validator("opened_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Candle timestamps must include a timezone")
        return value.astimezone(UTC)

    @field_validator("open", "high", "low", "close", "volume")
    @classmethod
    def validate_finite_values(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("Candle values must be finite")
        return value

    @model_validator(mode="after")
    def validate_candle(self) -> Self:
        if self.opened_at.second != 0 or self.opened_at.microsecond != 0:
            raise ValueError("Candle timestamps must be aligned to whole minutes")
        if self.opened_at.minute % self.timeframe.value:
            raise ValueError(f"Candle timestamp must align to {self.timeframe.label}")
        if self.volume < 0:
            raise ValueError("Candle volume cannot be negative")
        if any(value < 0 for value in (self.open, self.high, self.low, self.close)):
            raise ValueError("Candle prices cannot be negative")
        if (
            self.high < self.low
            or self.high < self.open
            or self.high < self.close
            or self.low > self.open
            or self.low > self.close
        ):
            raise ValueError("Candle high/low values do not bound open and close")
        return self
