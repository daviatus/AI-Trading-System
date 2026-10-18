import asyncio
import csv
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from trading.config import MarketScheduleSettings
from trading.data.domain import Candle, Instrument, InstrumentCode, Timeframe
from trading.data.ingestion import ingest_historical
from trading.data.market_calendar import MarketCalendar
from trading.data.providers import (
    CsvMarketDataProvider,
    CTraderMarketDataProvider,
    MarketDataProviderError,
    ProviderCapabilityError,
    SimulatedMarketDataProvider,
)
from trading.data.validation import detect_gaps


class MemoryRepository:
    def __init__(self) -> None:
        self.candles: dict[tuple[InstrumentCode, Timeframe, datetime], Candle] = {}
        self.checkpoints: dict[tuple[str, InstrumentCode, Timeframe, str], datetime | None] = {}
        self.persist_calls = 0

    def get_checkpoint(self, provider, instrument, timeframe, source_key):
        return self.checkpoints.get((provider, instrument, timeframe, source_key))

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
        key = (provider, instrument.symbol, timeframe, source_key)
        inserted = 0
        for candle in candles:
            candle_key = (candle.instrument, candle.timeframe, candle.opened_at)
            if candle_key not in self.candles:
                self.candles[candle_key] = candle
                inserted += 1
        prior = self.checkpoints.get(key)
        if prior is None or (checkpoint is not None and checkpoint > prior):
            self.checkpoints[key] = checkpoint
        elif key not in self.checkpoints:
            self.checkpoints[key] = checkpoint
        self.persist_calls += 1
        return inserted


class StubProvider:
    name = "stub"
    source_key = "stub-fixture-v1"
    source_reference = "synthetic test fixture"

    def __init__(self, candles: list[Candle], fail_after: int | None = None) -> None:
        self.candles = candles
        self.fail_after = fail_after

    async def history(self, instrument, timeframe, start, end):
        count = 0
        for candle in self.candles:
            if start <= candle.opened_at < end:
                if self.fail_after == count:
                    raise RuntimeError("synthetic provider interruption")
                yield candle
                count += 1

    async def stream(self, instrument, timeframe):
        for candle in self.candles:
            yield candle


def make_candle(opened_at: datetime, value: str = "10") -> Candle:
    price = Decimal(value)
    return Candle(
        instrument=InstrumentCode.WTI,
        timeframe=Timeframe.M1,
        opened_at=opened_at,
        open=price,
        high=price + Decimal("1"),
        low=price - Decimal("1"),
        close=price,
        volume=Decimal("100"),
    )


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    columns = ["timestamp", "open", "high", "low", "close", "volume"]
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def test_candle_normalizes_timezone_to_utc_and_validates_price_bounds() -> None:
    candle = make_candle(datetime.fromisoformat("2025-01-01T01:00:00+01:00"))

    assert candle.opened_at == datetime(2025, 1, 1, tzinfo=UTC)
    with pytest.raises(ValidationError, match="do not bound"):
        Candle(
            instrument=InstrumentCode.WTI,
            timeframe=Timeframe.M1,
            opened_at=datetime(2025, 1, 1, tzinfo=UTC),
            open=Decimal("10"),
            high=Decimal("9"),
            low=Decimal("8"),
            close=Decimal("10"),
            volume=Decimal("1"),
        )


def test_candle_rejects_naive_timestamp_and_unsupported_timeframe() -> None:
    with pytest.raises(ValidationError, match="include a timezone"):
        make_candle(datetime(2025, 1, 1))
    with pytest.raises(ValidationError):
        Candle(
            instrument=InstrumentCode.WTI,
            timeframe=3,
            opened_at=datetime(2025, 1, 1, tzinfo=UTC),
            open=Decimal("10"),
            high=Decimal("11"),
            low=Decimal("9"),
            close=Decimal("10"),
            volume=Decimal("1"),
        )


def test_catalog_supports_all_requested_instruments_and_mappings() -> None:
    from trading.data.domain import configured_instruments

    instruments = configured_instruments(
        {"ctrader": {"WTI": "mapped-wti", "SPX500": "mapped-index"}}
    )

    assert set(instruments) == set(InstrumentCode)
    assert instruments[InstrumentCode.WTI].broker_symbol_mappings == {"ctrader": "mapped-wti"}
    assert instruments[InstrumentCode.SPX500].broker_symbol_mappings == {"ctrader": "mapped-index"}


def test_csv_provider_sorts_deduplicates_and_filters_half_open_range(tmp_path: Path) -> None:
    path = tmp_path / "candles.csv"
    row_1 = {
        "timestamp": "2025-01-01T00:01:00Z",
        "open": "10",
        "high": "11",
        "low": "9",
        "close": "10",
        "volume": "100",
    }
    row_0 = {**row_1, "timestamp": "2025-01-01T00:00:00Z"}
    write_csv(path, [row_0, row_0, row_1])
    provider = CsvMarketDataProvider(path, InstrumentCode.WTI, Timeframe.M1)

    async def read():
        return [
            candle
            async for candle in provider.history(
                InstrumentCode.WTI,
                Timeframe.M1,
                datetime(2025, 1, 1, tzinfo=UTC),
                datetime(2025, 1, 1, 0, 1, tzinfo=UTC),
            )
        ]

    candles = asyncio.run(read())
    assert [candle.opened_at.minute for candle in candles] == [0]


def test_csv_provider_rejects_conflicting_duplicate_candles(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.csv"
    row = {
        "timestamp": "2025-01-01T00:00:00Z",
        "open": "10",
        "high": "11",
        "low": "9",
        "close": "10",
        "volume": "100",
    }
    write_csv(path, [row, {**row, "close": "10.5", "high": "11"}])
    provider = CsvMarketDataProvider(path, InstrumentCode.WTI, Timeframe.M1)

    async def read():
        return [
            candle
            async for candle in provider.history(
                InstrumentCode.WTI,
                Timeframe.M1,
                datetime(2025, 1, 1, tzinfo=UTC),
                datetime(2025, 1, 1, 0, 1, tzinfo=UTC),
            )
        ]

    with pytest.raises(MarketDataProviderError, match="Conflicting duplicate"):
        asyncio.run(read())


def test_simulated_provider_is_deterministic_across_ranges() -> None:
    provider = SimulatedMarketDataProvider(seed=17)
    start = datetime(2025, 1, 1, tzinfo=UTC)

    async def read(provider_start, provider_end):
        return [
            candle
            async for candle in provider.history(
                InstrumentCode.WTI, Timeframe.M1, provider_start, provider_end
            )
        ]

    first = asyncio.run(read(start, start + timedelta(minutes=3)))
    overlapping = asyncio.run(read(start + timedelta(minutes=1), start + timedelta(minutes=3)))

    assert first[1:] == overlapping


def test_provider_streaming_interface_returns_async_updates() -> None:
    calendar = MarketCalendar.from_config(
        {
            "WTI": MarketScheduleSettings(
                timezone="UTC",
                weekdays=[0, 1, 2, 3, 4, 5, 6],
                sessions=[{"open": "00:00", "close": "24:00"}],
            )
        }
    )
    provider = SimulatedMarketDataProvider(
        seed=5,
        stream_interval_seconds=0.001,
        calendar=calendar,
    )

    async def read_one():
        return await asyncio.wait_for(anext(provider.stream(InstrumentCode.WTI, Timeframe.M1)), 1)

    update = asyncio.run(read_one())
    assert update.instrument == InstrumentCode.WTI
    assert update.timeframe == Timeframe.M1


def test_gap_detection_reports_missing_minute_buckets() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)

    gaps = detect_gaps([make_candle(start), make_candle(start + timedelta(minutes=3))])

    assert len(gaps) == 1
    assert gaps[0].missing_from == start + timedelta(minutes=1)
    assert gaps[0].missing_through == start + timedelta(minutes=2)
    assert gaps[0].missing_count == 2


def test_historical_ingestion_is_idempotent_and_resumes_gaps() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    repository = MemoryRepository()
    provider = StubProvider([make_candle(start), make_candle(start + timedelta(minutes=2))])
    end = start + timedelta(minutes=3)

    first_gaps = asyncio.run(
        ingest_historical(provider, repository, InstrumentCode.WTI, Timeframe.M1, start, end)
    )
    assert first_gaps[0].missing_count == 1
    checkpoint_key = ("stub", InstrumentCode.WTI, Timeframe.M1, provider.source_key)
    assert repository.checkpoints[checkpoint_key] == start

    repaired_provider = StubProvider(
        [
            make_candle(start + timedelta(minutes=1), "11"),
            make_candle(start + timedelta(minutes=2)),
        ]
    )
    second_gaps = asyncio.run(
        ingest_historical(
            repaired_provider, repository, InstrumentCode.WTI, Timeframe.M1, start, end
        )
    )

    assert second_gaps == []
    assert len(repository.candles) == 3
    assert repository.checkpoints[checkpoint_key] == start + timedelta(minutes=2)
    persist_calls = repository.persist_calls
    assert (
        asyncio.run(
            ingest_historical(
                repaired_provider, repository, InstrumentCode.WTI, Timeframe.M1, start, end
            )
        )
        == []
    )
    assert repository.persist_calls == persist_calls


def test_ingestion_restart_recovers_after_provider_interruption() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = start + timedelta(minutes=3)
    repository = MemoryRepository()
    provider = StubProvider(
        [
            make_candle(start),
            make_candle(start + timedelta(minutes=1)),
            make_candle(start + timedelta(minutes=2)),
        ],
        fail_after=1,
    )

    with pytest.raises(RuntimeError, match="interruption"):
        asyncio.run(
            ingest_historical(
                provider,
                repository,
                InstrumentCode.WTI,
                Timeframe.M1,
                start,
                end,
                batch_size=1,
            )
        )

    checkpoint_key = ("stub", InstrumentCode.WTI, Timeframe.M1, provider.source_key)
    assert repository.checkpoints[checkpoint_key] == start
    asyncio.run(
        ingest_historical(
            StubProvider(provider.candles),
            repository,
            InstrumentCode.WTI,
            Timeframe.M1,
            start,
            end,
        )
    )
    assert len(repository.candles) == 3
    assert repository.checkpoints[checkpoint_key] == end - timedelta(minutes=1)


def test_ctrader_provider_is_a_nonconnecting_placeholder() -> None:
    provider = CTraderMarketDataProvider()
    start = datetime(2025, 1, 1, tzinfo=UTC)

    async def read():
        return [
            candle
            async for candle in provider.history(
                InstrumentCode.WTI, Timeframe.M1, start, start + timedelta(minutes=1)
            )
        ]

    with pytest.raises(ProviderCapabilityError, match="application approval"):
        asyncio.run(read())
