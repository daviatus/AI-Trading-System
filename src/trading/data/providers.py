import asyncio
import csv
import hashlib
import random
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import ValidationError

from trading.data.domain import Candle, InstrumentCode, Timeframe
from trading.data.market_calendar import MarketCalendar


class MarketDataProviderError(Exception):
    """Raised when a market data provider cannot fulfill a request."""


class ProviderCapabilityError(MarketDataProviderError):
    """Raised when a provider does not implement the requested data operation."""


@runtime_checkable
class MarketDataProvider(Protocol):
    name: str
    source_key: str
    source_reference: str

    def history(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> AsyncIterator[Candle]: ...

    def stream(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
    ) -> AsyncIterator[Candle]: ...


class CsvMarketDataProvider:
    name = "csv"

    def __init__(
        self,
        path: str | Path,
        instrument: InstrumentCode,
        timeframe: Timeframe,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self.instrument = instrument
        self.timeframe = timeframe
        if not self.path.is_file():
            raise FileNotFoundError(f"CSV market data file does not exist: {self.path}")
        digest = hashlib.sha256()
        with self.path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        self.source_reference = str(self.path)
        path_hash = hashlib.sha256(self.source_reference.encode("utf-8")).hexdigest()
        self.source_key = f"csv:{path_hash}:{digest.hexdigest()}"

    def _read(self, start: datetime, end: datetime):
        expected_columns = {"timestamp", "open", "high", "low", "close", "volume"}
        try:
            with self.path.open(newline="", encoding="utf-8-sig") as source:
                reader = csv.DictReader(source)
                columns = {column.strip().lower() for column in (reader.fieldnames or [])}
                missing_columns = expected_columns - columns
                if missing_columns:
                    raise MarketDataProviderError(
                        f"CSV is missing required columns: {sorted(missing_columns)}"
                    )
                previous: Candle | None = None
                for row_number, row in enumerate(reader, start=2):
                    normalized_row = {
                        key.strip().lower(): value.strip() if value is not None else ""
                        for key, value in row.items()
                        if key is not None
                    }
                    candle = Candle(
                        instrument=self.instrument,
                        timeframe=self.timeframe,
                        opened_at=datetime.fromisoformat(
                            normalized_row["timestamp"].replace("Z", "+00:00")
                        ),
                        open=Decimal(normalized_row["open"]),
                        high=Decimal(normalized_row["high"]),
                        low=Decimal(normalized_row["low"]),
                        close=Decimal(normalized_row["close"]),
                        volume=Decimal(normalized_row["volume"]),
                    )
                    if previous is not None and candle.opened_at < previous.opened_at:
                        raise MarketDataProviderError(
                            f"CSV candles must be chronological; out-of-order row {row_number}"
                        )
                    if previous is not None and candle.opened_at == previous.opened_at:
                        if previous != candle:
                            raise MarketDataProviderError(
                                f"Conflicting duplicate candle at CSV row {row_number}"
                            )
                        continue
                    previous = candle
                    if start <= candle.opened_at < end:
                        yield candle
        except MarketDataProviderError:
            raise
        except (KeyError, ValueError, InvalidOperation, ValidationError) as error:
            raise MarketDataProviderError(f"Invalid CSV market data: {error}") from error

    async def history(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> AsyncIterator[Candle]:
        self._validate_request(instrument, timeframe, start, end)
        start = start.astimezone(UTC)
        end = end.astimezone(UTC)
        for candle in self._read(start, end):
            yield candle

    async def stream(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
    ) -> AsyncIterator[Candle]:
        raise ProviderCapabilityError("CSV files provide historical candles only")
        yield  # Make this an async iterator while raising on first iteration.

    def _validate_request(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> None:
        if instrument != self.instrument or timeframe != self.timeframe:
            raise MarketDataProviderError(
                "Requested instrument/timeframe does not match the configured CSV"
            )
        if start.tzinfo is None or end.tzinfo is None:
            raise MarketDataProviderError("History bounds must be timezone-aware")
        start = start.astimezone(UTC)
        end = end.astimezone(UTC)
        if start >= end:
            raise MarketDataProviderError("History start must be earlier than end")
        _validate_history_request(start, end, timeframe)


class SimulatedMarketDataProvider:
    """Deterministic synthetic data for tests and local pipeline exercises only."""

    name = "simulated"

    def __init__(
        self,
        seed: int = 0,
        stream_interval_seconds: float = 60,
        calendar: MarketCalendar | None = None,
    ) -> None:
        if stream_interval_seconds <= 0:
            raise ValueError("stream_interval_seconds must be positive")
        self.seed = seed
        self.stream_interval_seconds = stream_interval_seconds
        self.calendar = calendar or MarketCalendar()
        self.source_key = f"seed:{seed}:calendar:{self.calendar.fingerprint}"
        self.source_reference = "synthetic data; not broker or market data"

    async def history(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> AsyncIterator[Candle]:
        _validate_history_request(start, end, timeframe)
        start = start.astimezone(UTC)
        end = end.astimezone(UTC)
        base_prices = {
            InstrumentCode.WTI: Decimal("75"),
            InstrumentCode.XAUUSD: Decimal("2300"),
            InstrumentCode.EURUSD: Decimal("1.08"),
            InstrumentCode.SPX500: Decimal("5200"),
        }
        cursor = start
        interval = timedelta(minutes=timeframe.value)
        while cursor < end:
            if self.calendar.is_open(instrument, cursor):
                seed_text = f"{self.seed}:{instrument.value}:{timeframe.value}:{cursor.isoformat()}"
                rng = random.Random(seed_text)
                open_price = max(
                    Decimal("0.00000001"),
                    base_prices[instrument] + Decimal(str(rng.uniform(-1, 1))),
                )
                close_price = max(
                    Decimal("0.00000001"),
                    open_price + Decimal(str(rng.uniform(-1, 1))),
                )
                spread = Decimal(str(rng.uniform(0, 0.5)))
                yield Candle(
                    instrument=instrument,
                    timeframe=timeframe,
                    opened_at=cursor,
                    open=open_price,
                    high=max(open_price, close_price) + spread,
                    low=max(Decimal("0.00000001"), min(open_price, close_price) - spread),
                    close=close_price,
                    volume=Decimal(rng.randrange(1, 10000)),
                )
            cursor += interval

    async def stream(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
    ) -> AsyncIterator[Candle]:
        interval = timedelta(minutes=timeframe.value)
        cursor = datetime.now(UTC).replace(second=0, microsecond=0)
        remainder = cursor.minute % timeframe.value
        cursor -= timedelta(minutes=remainder)
        while True:
            next_cursor = cursor + interval
            await asyncio.sleep(self.stream_interval_seconds)
            async for candle in self.history(instrument, timeframe, cursor, next_cursor):
                yield candle
            cursor = next_cursor


class CTraderMarketDataProvider:
    """Unavailable adapter placeholder until the Spotware application is approved."""

    name = "ctrader"
    source_key = "ctrader:unconfigured"
    source_reference = "cTrader Open API integration pending application approval"

    async def history(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> AsyncIterator[Candle]:
        raise ProviderCapabilityError(
            "cTrader historical data is unavailable until application approval and integration"
        )
        yield

    async def stream(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
    ) -> AsyncIterator[Candle]:
        raise ProviderCapabilityError(
            "cTrader streaming is unavailable until application approval and integration"
        )
        yield


def _validate_history_request(start: datetime, end: datetime, timeframe: Timeframe) -> None:
    if start.tzinfo is None or end.tzinfo is None:
        raise MarketDataProviderError("History bounds must be timezone-aware")
    start = start.astimezone(UTC)
    end = end.astimezone(UTC)
    if start >= end:
        raise MarketDataProviderError("History start must be earlier than end")
    if start.second or start.microsecond or start.minute % timeframe.value:
        raise MarketDataProviderError(
            f"History start must align to {timeframe.label} candle boundaries"
        )
