import asyncio
import json
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from trading.data.aggregation import aggregate_complete_minutes
from trading.data.domain import Candle, InstrumentCode, PriceType, Timeframe
from trading.data.histdata import HISTDATA_SOURCE_ID, HistDataAsciiProvider, load_histdata_mapping
from trading.data.historical_validation import (
    DefaultHistoricalDataValidator,
    summarize_source_coverage,
)
from trading.data.parquet_store import (
    _LEGACY_PARQUET_SCHEMA,
    PARQUET_SCHEMA_VERSION,
    export_partition,
    import_parquet,
    read_parquet,
)
from trading.data.providers import MarketDataProviderError
from trading.data.quality import check_candle_quality
from trading.data.repository import HistoricalImportRepository


def _read(provider: HistDataAsciiProvider, start: datetime, end: datetime) -> list[Candle]:
    async def collect() -> list[Candle]:
        return [
            candle
            async for candle in provider.history(
                provider.instrument,
                provider.timeframe,
                start,
                end,
            )
        ]

    return asyncio.run(collect())


def test_histdata_mapping_covers_the_requested_symbols() -> None:
    expected = {
        InstrumentCode.EURUSD: "EUR/USD",
        InstrumentCode.XAUUSD: "XAU/USD",
        InstrumentCode.WTI: "WTI/USD",
        InstrumentCode.SPX500: "SPX/USD",
    }

    assert {
        instrument: load_histdata_mapping(
            "config/historical_data_sources.json",
            instrument,
        )["symbol"]
        for instrument in InstrumentCode
    } == expected


def test_histdata_generic_ascii_parses_fixed_est_and_optional_volume(tmp_path) -> None:
    source = tmp_path / "histdata.txt"
    source.write_text(
        "20260308 023000,1.08000,1.08100,1.07900,1.08050,12\n"
        "20260308,023100,1.08050,1.08150,1.08000,1.08100\n",
        encoding="ascii",
    )
    provider = HistDataAsciiProvider(source, InstrumentCode.EURUSD)
    start = datetime(2026, 3, 8, 7, 30, tzinfo=UTC)
    candles = _read(provider, start, start + timedelta(minutes=2))

    assert provider.name == HISTDATA_SOURCE_ID
    assert [candle.opened_at for candle in candles] == [
        datetime(2026, 3, 8, 7, 30, tzinfo=UTC),
        datetime(2026, 3, 8, 7, 31, tzinfo=UTC),
    ]
    assert candles[0].volume == Decimal("12")
    assert candles[1].volume is None
    assert candles[0].price_type == PriceType.BID
    assert candles[0].quality_metadata == {
        "source_timestamp": "20260308 023000",
        "source_timezone": "UTC-05:00",
        "source_timezone_is_fixed": True,
        "dst_adjustment_applied": False,
    }


def test_histdata_import_cli_arguments_do_not_require_timeframe() -> None:
    from trading.data.commands import _build_parser

    arguments = _build_parser("import-histdata").parse_args(
        [
            "--source-file",
            "sample.csv",
            "--instrument",
            "WTI",
            "--start",
            "2026-01-01T00:00:00Z",
            "--end",
            "2026-01-02T00:00:00Z",
        ]
    )

    assert arguments.command == "import-histdata"
    assert arguments.instrument == "WTI"
    assert arguments.storage == "parquet"


def test_historical_import_job_completion_serializes_quality_dates(monkeypatch) -> None:
    import trading.data.repository as repository_module

    job = SimpleNamespace(status="running")
    session = MagicMock()
    session.get.return_value = job
    session_context = MagicMock()
    session_context.__enter__.return_value = session
    session_context.begin.return_value = nullcontext()
    monkeypatch.setattr(repository_module, "Session", lambda engine: session_context)

    missing_from = datetime(2026, 1, 3, 12, 30, tzinfo=UTC)
    issue_date = date(2026, 1, 3)
    summary = {
        "gaps": [{"missing_from": missing_from}],
        "issues": [{"timestamp": missing_from}],
        "quality_date": issue_date,
        "candle_count": 42,
    }

    HistoricalImportRepository(engine=MagicMock()).finish_job(
        "job-id",
        status="completed",
        rows_seen=42,
        rows_stored=42,
        quality_summary=summary,
    )

    assert job.status == "completed"
    assert job.rows_seen == 42
    assert job.rows_stored == 42
    assert job.quality_summary == {
        "gaps": [{"missing_from": "2026-01-03T12:30:00+00:00"}],
        "issues": [{"timestamp": "2026-01-03T12:30:00+00:00"}],
        "quality_date": "2026-01-03",
        "candle_count": 42,
    }
    assert json.loads(json.dumps(job.quality_summary)) == job.quality_summary


def test_histdata_provider_requires_m1_and_rejects_duplicate_or_invalid_rows(tmp_path) -> None:
    with pytest.raises(ValueError, match="M1 only"):
        HistDataAsciiProvider(tmp_path / "not-read.txt", InstrumentCode.WTI, Timeframe.M5)

    source = tmp_path / "duplicate.txt"
    record = "20161001 000000,75.0,75.2,74.9,75.1,10\n"
    source.write_text(record + record, encoding="ascii")
    provider = HistDataAsciiProvider(source, InstrumentCode.WTI)
    with pytest.raises(MarketDataProviderError, match="duplicate record"):
        _read(
            provider,
            datetime(2016, 10, 1, 5, tzinfo=UTC),
            datetime(2016, 10, 1, 5, 2, tzinfo=UTC),
        )

    malformed = tmp_path / "bad-ohlc.txt"
    malformed.write_text("20161001 000000,75.0,74.0,74.9,75.1,10\n", encoding="ascii")
    with pytest.raises(MarketDataProviderError, match="Invalid HistData record"):
        _read(
            HistDataAsciiProvider(malformed, InstrumentCode.WTI),
            datetime(2016, 10, 1, 5, tzinfo=UTC),
            datetime(2016, 10, 1, 5, 1, tzinfo=UTC),
        )


def test_histdata_range_is_half_open_and_missing_bars_have_source_coverage() -> None:
    from pathlib import Path
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as temporary:
        path = Path(temporary) / "bars.csv"
        path.write_text(
            "20161001 000000,1.0,1.2,0.9,1.1,2\n20161001 000200,1.1,1.3,1.0,1.2,3\n",
            encoding="ascii",
        )
        provider = HistDataAsciiProvider(path, InstrumentCode.EURUSD)
        start = datetime(2016, 10, 1, 5, tzinfo=UTC)
        candles = _read(provider, start, start + timedelta(minutes=3))
        report = DefaultHistoricalDataValidator().validate(
            candles,
            InstrumentCode.EURUSD,
            1,
            range_start=start,
            range_end=start + timedelta(minutes=3),
        )
        coverage = summarize_source_coverage(report, start, start + timedelta(minutes=3))

        assert report.missing_candle_count == 1
        assert coverage.candle_count == 2
        assert coverage.observed_start == start
        assert coverage.observed_end == start + timedelta(minutes=2)


def test_parquet_v2_roundtrip_preserves_nullable_volume_price_type_and_quality(tmp_path) -> None:
    candle = Candle(
        instrument=InstrumentCode.XAUUSD,
        timeframe=Timeframe.M1,
        opened_at=datetime(2025, 1, 2, tzinfo=UTC),
        open=Decimal("2650.125"),
        high=Decimal("2651.125"),
        low=Decimal("2649.875"),
        close=Decimal("2650.875"),
        volume=None,
        price_type=PriceType.BID,
        quality_metadata={"source_timezone": "UTC-05:00", "dst_adjustment_applied": False},
    )
    paths = export_partition([(candle, HISTDATA_SOURCE_ID, "sha256:test")], tmp_path)
    restored = list(import_parquet(tmp_path))
    schema = pq.read_schema(paths[0][0])

    assert PARQUET_SCHEMA_VERSION == "2"
    assert restored == [(candle, HISTDATA_SOURCE_ID, "sha256:test")]
    assert schema.field("volume").nullable
    assert schema.field("price_type").type == "string"
    assert schema.metadata[b"trading.schema_version"] == b"2"
    assert pq.ParquetFile(paths[0][0]).metadata.row_group(0).column(0).compression == "ZSTD"


def test_parquet_v1_reader_defaults_new_candle_metadata(tmp_path) -> None:
    path = tmp_path / "legacy.parquet"
    table = pa.Table.from_pylist(
        [
            {
                "instrument": "EURUSD",
                "timeframe_minutes": 1,
                "opened_at": datetime(2025, 1, 2, tzinfo=UTC),
                "open": Decimal("1.1"),
                "high": Decimal("1.2"),
                "low": Decimal("1.0"),
                "close": Decimal("1.15"),
                "volume": Decimal("10"),
                "source_provider": "legacy",
                "source_reference": "legacy-file.csv",
            }
        ],
        schema=_LEGACY_PARQUET_SCHEMA,
    )
    pq.write_table(table, path, compression="zstd")

    restored = list(read_parquet(path))

    assert len(restored) == 1
    assert restored[0].price_type == PriceType.UNKNOWN
    assert restored[0].quality_metadata == {}


def test_parquet_keeps_bid_and_ask_series_separate(tmp_path) -> None:
    timestamp = datetime(2025, 1, 2, tzinfo=UTC)
    bars = [
        Candle(
            instrument=InstrumentCode.EURUSD,
            timeframe=Timeframe.M1,
            opened_at=timestamp,
            open=Decimal("1.1"),
            high=Decimal("1.2"),
            low=Decimal("1.0"),
            close=close,
            volume=Decimal("1"),
            price_type=price_type,
        )
        for price_type, close in (
            (PriceType.BID, Decimal("1.15")),
            (PriceType.ASK, Decimal("1.16")),
        )
    ]

    export_partition([(bar, "provider", "fixture") for bar in bars], tmp_path)

    assert len(list(import_parquet(tmp_path))) == 2


def test_five_minute_aggregation_preserves_price_type_and_unknown_volume() -> None:
    start = datetime(2025, 1, 6, 12, tzinfo=UTC)
    minutes = [
        Candle(
            instrument=InstrumentCode.WTI,
            timeframe=Timeframe.M1,
            opened_at=start + timedelta(minutes=index),
            open=Decimal("75") + index,
            high=Decimal("76") + index,
            low=Decimal("74") + index,
            close=Decimal("75.5") + index,
            volume=None if index == 2 else Decimal("1"),
            price_type=PriceType.BID,
        )
        for index in range(5)
    ]

    result = aggregate_complete_minutes(minutes, start + timedelta(minutes=5))

    assert len(result.candles) == 1
    assert result.candles[0].price_type == PriceType.BID
    assert result.candles[0].volume is None
    assert result.candles[0].quality_metadata["source_provider"] is None


def test_aggregation_preserves_source_lineage_and_excludes_unaligned_partial_window() -> None:
    start = datetime(2025, 1, 6, 12, 0, tzinfo=UTC)
    minutes = [
        Candle(
            instrument=InstrumentCode.EURUSD,
            timeframe=Timeframe.M1,
            opened_at=start + timedelta(minutes=index),
            open=Decimal("1.10") + Decimal(index) / Decimal("1000"),
            high=Decimal("1.11") + Decimal(index) / Decimal("1000"),
            low=Decimal("1.09") + Decimal(index) / Decimal("1000"),
            close=Decimal("1.105") + Decimal(index) / Decimal("1000"),
            volume=None,
            price_type=PriceType.BID,
        )
        for index in range(5)
    ]

    result = aggregate_complete_minutes(
        minutes,
        start + timedelta(minutes=5),
        source_provider=HISTDATA_SOURCE_ID,
    )
    unaligned_partial = aggregate_complete_minutes(
        minutes[1:],
        start + timedelta(minutes=6),
        source_provider=HISTDATA_SOURCE_ID,
    )

    assert len(result.candles) == 1
    assert result.candles[0].opened_at == start
    assert result.candles[0].high == max(candle.high for candle in minutes)
    assert result.candles[0].low == min(candle.low for candle in minutes)
    assert result.candles[0].quality_metadata["source_provider"] == HISTDATA_SOURCE_ID
    assert unaligned_partial.candles == ()
    assert unaligned_partial.incomplete_bucket_starts == (start,)


def test_quality_report_does_not_treat_missing_volume_as_zero_volume() -> None:
    candles = [
        Candle(
            instrument=InstrumentCode.WTI,
            timeframe=Timeframe.M1,
            opened_at=datetime(2025, 1, 6, tzinfo=UTC) + timedelta(minutes=index),
            open=Decimal("75"),
            high=Decimal("76"),
            low=Decimal("74"),
            close=Decimal("75"),
            volume=None,
            price_type=PriceType.BID,
        )
        for index in range(3)
    ]

    report = check_candle_quality(candles, InstrumentCode.WTI, 1)

    assert report.zero_volume_count == 0
    assert report.missing_volume_count == 3
    assert report.as_dict()["missing_volume_count"] == 3
    assert not any(issue.code in {"zero_volume", "negative_volume"} for issue in report.issues)
