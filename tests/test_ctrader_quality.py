import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading.ctrader import cli, parquet
from trading.ctrader.parquet import CTRADER_PARQUET_PROVIDER, persist_candles
from trading.ctrader.quality import validate_candle_data
from trading.data.domain import Candle, InstrumentCode, Timeframe
from trading.data.parquet_store import import_parquet

START = datetime(2026, 1, 5, tzinfo=UTC)
END = START + timedelta(minutes=3)


def _candle(timestamp: datetime, **updates) -> dict:
    values = {
        "instrument": "EURUSD",
        "timeframe": 1,
        "opened_at": timestamp.isoformat().replace("+00:00", "Z"),
        "open": "1.10000",
        "high": "1.10100",
        "low": "1.09900",
        "close": "1.10050",
        "volume": "10",
        "price_type": "unknown",
        "quality_metadata": {"volume_units": "raw_protocol_integer"},
    }
    values.update(updates)
    return values


def _cli(monkeypatch, candles):
    class FakeManager:
        def __init__(self, _store):
            pass

        def credentials(self):
            return "client-id", "client-secret"

        def access_token(self):
            return "access-token"

    class FakeClient:
        def __init__(self, *_args):
            pass

        async def get_eurusd_m1_candles(self, *_args, **_kwargs):
            return candles

        async def get_eurusd_m1_candles_multi_day(self, *_args, **_kwargs):
            return candles

    monkeypatch.setattr(cli, "OAuthManager", FakeManager)
    monkeypatch.setattr(cli, "CTraderReadOnlyClient", FakeClient)


def _args(*extra):
    return [
        "candles",
        "--instrument",
        "EURUSD",
        "--environment",
        "demo",
        "--start",
        START.isoformat().replace("+00:00", "Z"),
        "--end",
        END.isoformat().replace("+00:00", "Z"),
        *extra,
    ]


def test_quality_report_valid_candles_and_gaps_are_not_errors():
    data = [
        _candle(START),
        _candle(START + timedelta(minutes=2)),
    ]

    candles, report = validate_candle_data(data, START, END)

    assert len(candles) == 2
    assert report["validation_status"] == "valid_with_gaps"
    assert report["missing_minute_count"] == 1
    assert report["duplicate_count"] == 0
    assert report["errors"] == []
    assert report["gaps"][0]["missing_from"] == "2026-01-05T00:01:00+00:00"


def test_quality_report_marks_complete_data_valid():
    data = [_candle(START + timedelta(minutes=index)) for index in range(3)]

    _, report = validate_candle_data(data, START, END)

    assert report["validation_status"] == "valid"
    assert report["missing_minute_count"] == 0
    assert report["errors"] == []


def test_quality_report_detects_duplicates_and_unsorted_candles():
    data = [
        _candle(START + timedelta(minutes=1)),
        _candle(START),
        _candle(START + timedelta(minutes=1)),
    ]

    _, report = validate_candle_data(data, START, END)

    assert report["validation_status"] == "invalid"
    assert report["duplicate_count"] == 1
    assert {error["code"] for error in report["errors"]} >= {
        "duplicate_candle",
        "out_of_order_candle",
    }


@pytest.mark.parametrize(
    ("updates", "error_code"),
    [
        ({"high": "1.0", "low": "1.2"}, "invalid_candle"),
        ({"open": "0", "high": "0", "low": "0", "close": "0"}, "non_positive_price"),
        ({"volume": "-1"}, "invalid_candle"),
        ({"open": "NaN"}, "invalid_candle"),
    ],
)
def test_quality_report_rejects_invalid_ohlc_prices_and_volume(updates, error_code):
    _, report = validate_candle_data([_candle(START, **updates)], START, END)

    assert report["validation_status"] == "invalid"
    assert report["errors"][0]["code"] == error_code


@pytest.mark.parametrize(
    ("timestamp", "error_code"),
    [
        ("2026-01-05T00:00:01Z", "invalid_candle"),
        ("2026-01-05T00:00:00", "invalid_timestamp"),
        ("2026-01-05T01:00:00+01:00", "non_utc_timestamp"),
        ("2026-01-05T00:03:00Z", "out_of_range"),
    ],
)
def test_quality_report_rejects_incorrect_timestamps(timestamp, error_code):
    _, report = validate_candle_data([_candle(START, opened_at=timestamp)], START, END)

    assert report["validation_status"] == "invalid"
    assert report["errors"][0]["code"] == error_code


def test_quality_report_handles_empty_data_without_synthetic_candles():
    candles, report = validate_candle_data([], START, END)

    assert candles == []
    assert report["candle_count"] == 0
    assert report["missing_minute_count"] == 3
    assert report["validation_status"] == "valid_with_gaps"


def test_ctrader_parquet_roundtrip_uses_repository_schema_and_lineage(tmp_path):
    candle = Candle(
        instrument=InstrumentCode.EURUSD,
        timeframe=Timeframe.M1,
        opened_at=START,
        open=Decimal("1.10000"),
        high=Decimal("1.10100"),
        low=Decimal("1.09900"),
        close=Decimal("1.10050"),
        volume=Decimal("10"),
        quality_metadata={"volume_units": "raw_protocol_integer"},
    )
    output = tmp_path / "ctrader"

    results = persist_candles([candle], output, START, END)

    assert len(results) == 1
    assert results[0][0].is_file()
    assert list(import_parquet(output)) == [
        (
            candle,
            CTRADER_PARQUET_PROVIDER,
            "demo:EURUSD:M1:[2026-01-05T00:00:00+00:00,2026-01-05T00:03:00+00:00)",
        )
    ]


def test_ctrader_parquet_refuses_existing_output_root(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    candle = Candle(
        instrument=InstrumentCode.EURUSD,
        timeframe=Timeframe.M1,
        opened_at=START,
        open=Decimal("1.1"),
        high=Decimal("1.2"),
        low=Decimal("1.0"),
        close=Decimal("1.15"),
        volume=Decimal("1"),
    )

    with pytest.raises(FileExistsError):
        persist_candles([candle], output, START, END)

    assert list(output.iterdir()) == []


def test_ctrader_parquet_empty_data_does_not_create_output(tmp_path):
    output = tmp_path / "empty"

    assert persist_candles([], output, START, END) == []
    assert not output.exists()


def test_ctrader_parquet_failure_does_not_publish_partial_dataset(tmp_path, monkeypatch):
    output = tmp_path / "dataset"

    def fail_after_staging(_records, staging_root):
        staging_root.mkdir(parents=True)
        (staging_root / "partial.parquet").write_text("partial", encoding="ascii")
        raise OSError("simulated storage failure")

    monkeypatch.setattr(parquet, "export_partition", fail_after_staging)
    candle = Candle(
        instrument=InstrumentCode.EURUSD,
        timeframe=Timeframe.M1,
        opened_at=START,
        open=Decimal("1.1"),
        high=Decimal("1.2"),
        low=Decimal("1.0"),
        close=Decimal("1.15"),
        volume=Decimal("1"),
    )

    with pytest.raises(OSError, match="simulated storage failure"):
        persist_candles([candle], output, START, END)

    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


def test_cli_keeps_legacy_json_array_output(monkeypatch, capsys):
    candles = [_candle(START)]
    _cli(monkeypatch, candles)

    assert cli.main(_args()) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == candles
    assert captured.err == ""


def test_cli_quality_report_and_opt_in_parquet_save(monkeypatch, capsys, tmp_path):
    candles = [_candle(START)]
    output = tmp_path / "saved"
    _cli(monkeypatch, candles)

    exit_code = cli.main(_args("--quality-report", "--save-parquet", "--parquet-root", str(output)))

    captured = capsys.readouterr()
    body = json.loads(captured.out)
    assert exit_code == 0
    assert body["quality_report"]["validation_status"] == "valid_with_gaps"
    assert body["candles"] == candles
    assert len(list(import_parquet(output))) == 1
    assert "Saved 1 validated candles" in captured.err


def test_cli_does_not_save_invalid_candles(monkeypatch, capsys, tmp_path):
    candles = [_candle(START, open="0")]
    output = tmp_path / "invalid"
    _cli(monkeypatch, candles)

    exit_code = cli.main(_args("--save-parquet", "--parquet-root", str(output)))

    captured = capsys.readouterr()
    assert exit_code == 1
    assert not output.exists()
    assert "quality validation failed" in captured.err
