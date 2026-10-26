import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from trading.ctrader.calendar import BrokerCalendar
from trading.ctrader.client import _decode_m1_trendbars
from trading.ctrader.history_backfill import (
    LOCK_KEY,
    calendar_for,
    history_lock_key,
    instrument_for,
)
from trading.ctrader.multiasset import choose_symbol
from trading.ctrader.operations import CTraderOperationsRepository
from trading.ctrader.quality import validate_candle_data
from trading.ctrader.sync import CTraderSyncService
from trading.ctrader.worker import WorkerConfig, synchronize_worker_cycle
from trading.data.dataset import FeatureConfig, build_ml_dataset
from trading.data.domain import Candle, InstrumentCode, Timeframe
from trading.data.phase4a import analyze, export_analysis

START = datetime(2026, 10, 19, 12, tzinfo=UTC)


def symbol(instrument="XAUUSD"):
    return {"internal_instrument": instrument, "broker_symbol_name": instrument,
            "broker_symbol_id": 41, "specifications": {
                "scheduleTimeZone": "America/New_York",
                "schedule": [{"startSecond": 64860, "endSecond": 147540},
                             {"startSecond": 151260, "endSecond": 233940}],
                "holiday": [{"holidayDate": 20447, "isRecurring": True,
                             "scheduleTimeZone": "America/New_York",
                             "startSecond": 0, "endSecond": 0}],
            }}


def candles(instrument=InstrumentCode.XAUUSD, count=200):
    return [Candle(instrument=instrument, timeframe=Timeframe.M1,
                   opened_at=START + timedelta(minutes=i),
                   open=Decimal(2000) + Decimal(i) / 10, high=Decimal(2001) + Decimal(i) / 10,
                   low=Decimal(1999) + Decimal(i) / 10, close=Decimal(2000) + Decimal(i) / 10,
                   volume=Decimal(i % 9 + 1)) for i in range(count)]


def test_oil_resolution_uses_exact_enabled_alias_and_never_brent_perpetual():
    entry = {"candidates": ["SpotCrude", "USOIL", "XTIUSD"]}
    alternatives = [{"symbolName": "XTIUSD", "enabled": False},
                    {"symbolName": "WTOIL-PERP", "enabled": True},
                    {"symbolName": "SpotBrent", "enabled": True}]
    assert choose_symbol(entry, alternatives) is None
    spot = {"symbolName": "SpotCrude", "enabled": True, "symbolId": 247}
    assert choose_symbol(entry, alternatives + [spot]) == spot
    with pytest.raises(ValueError, match="Ambiguous"):
        choose_symbol(entry, [spot, spot])


def test_broker_calendar_maintenance_dst_holidays_and_instrument_isolation():
    calendar = BrokerCalendar({"XAUUSD": symbol()})
    assert calendar.is_open(InstrumentCode.XAUUSD, START)
    assert not calendar.is_open(InstrumentCode.XAUUSD, datetime(2026, 10, 19, 21, tzinfo=UTC))
    assert calendar.is_open(InstrumentCode.XAUUSD, datetime(2026, 10, 19, 22, 1, tzinfo=UTC))
    assert not calendar.is_open(InstrumentCode.XAUUSD, datetime(2026, 11, 2, 22, tzinfo=UTC))
    assert calendar.is_open(InstrumentCode.XAUUSD, datetime(2026, 11, 2, 23, 1, tzinfo=UTC))
    # Recurring closure observed on a weekday in a later year.
    assert not calendar.is_open(InstrumentCode.XAUUSD, datetime(2028, 12, 25, 12, tzinfo=UTC))
    with pytest.raises(ValueError, match="not configured"):
        calendar.is_open(InstrumentCode.WTI, START)
    assert len(calendar.fingerprint) == 64


def test_decoder_preserves_symbol_identity_precision_and_provenance():
    response = {"ctidTraderAccountId": 123, "period": 1, "trendbar": [{
        "utcTimestampInMinutes": int(START.timestamp()) // 60, "low": 7025000,
        "deltaOpen": 1000, "deltaHigh": 2000, "deltaClose": 1500, "volume": 32,
    }]}
    bars = _decode_m1_trendbars(response, START, START + timedelta(minutes=1), 3,
                               instrument=InstrumentCode.WTI,
                               broker_symbol="SpotCrude", broker_symbol_id=247)
    candle = Candle.model_validate(bars[0])
    assert candle.instrument == InstrumentCode.WTI
    assert candle.close == Decimal("70.265")
    assert candle.quality_metadata["broker_symbol_id"] == 247
    _, quality = validate_candle_data(bars, START, START + timedelta(minutes=1))
    assert quality["validation_status"] == "invalid"
    _, quality = validate_candle_data(bars, START, START + timedelta(minutes=1),
                                      instrument=InstrumentCode.WTI)
    assert quality["validation_status"] == "valid"


def test_worker_and_sync_persist_the_selected_instrument_and_isolated_retry_state():
    items = candles(count=2)
    stored, imports = [], []

    class Repository:
        def iter_candle_records(self, instrument, timeframe, start, end, **kwargs):
            assert instrument == InstrumentCode.XAUUSD
            return iter((c, "ctrader_open_api_demo", "fixture") for c in stored
                        if start <= c.opened_at < end)

        def import_candles(self, instrument, timeframe, provider, records):
            assert instrument.symbol == InstrumentCode.XAUUSD
            imports.extend(records)
            stored.extend(c for c, _ in records)
            return SimpleNamespace(inserted=len(records), unchanged=0, conflicting=0, rejected=0,
                                   as_dict=lambda: {})

    class Client:
        async def get_eurusd_m1_candles_multi_day(self, start, end, *args, **kwargs):
            assert kwargs["instrument"] == InstrumentCode.XAUUSD
            assert kwargs["broker_symbol"] == "XAUUSD"
            kwargs["request_observer"]()
            return [c.model_dump(mode="json") for c in items if start <= c.opened_at < end]

    reports = []
    operations = SimpleNamespace(load_retry_state=lambda: {},
                                 record_run=lambda report, state: reports.append(report))
    now = START + timedelta(minutes=2)
    service = CTraderSyncService(Repository(), Client(), instrument=InstrumentCode.XAUUSD,
                                 broker_symbol="XAUUSD", now=lambda: now,
                                 calendar=BrokerCalendar({"XAUUSD": symbol()}))
    report = asyncio.run(synchronize_worker_cycle(
        service, operations, WorkerConfig(lookback_hours=1), now=lambda: now, save_postgres=True,
    ))
    assert report["instrument"] == "XAUUSD"
    assert report["quality"]["stored_candle_count"] == 2
    assert all("XAUUSD" in reference for _, reference in imports)
    assert reports[0]["instrument"] == "XAUUSD"
    assert CTraderOperationsRepository(None).event_type == "ctrader_worker_run"
    assert CTraderOperationsRepository(
        None, InstrumentCode.WTI,
    ).event_type == "ctrader_worker_run:WTI"


def test_history_defaults_preserve_eurusd_jobs_and_other_assets_have_unique_locks():
    assert instrument_for({}) == InstrumentCode.EURUSD
    assert history_lock_key(InstrumentCode.EURUSD) == LOCK_KEY
    assert len({history_lock_key(i) for i in InstrumentCode}) == len(InstrumentCode)
    assert isinstance(calendar_for({"instrument": "XAUUSD", "symbol_metadata": symbol()}),
                      BrokerCalendar)


def test_audit_and_features_are_instrument_isolated_causal_and_gap_safe(tmp_path):
    bars = candles()
    dataset = build_ml_dataset(bars, InstrumentCode.XAUUSD, Timeframe.M1, source="fixture",
                               reset_on_gaps=True,
                               feature_config=FeatureConfig(include_trend_indicators=True))
    assert dataset.manifest.instrument == "XAUUSD"
    with pytest.raises(ValueError, match="unexpected instrument"):
        build_ml_dataset(bars + candles(InstrumentCode.WTI, 1), InstrumentCode.XAUUSD,
                         Timeframe.M1, source="fixture")
    report, _ = analyze([c.model_dump() for c in bars], as_of=START + timedelta(minutes=200),
                         instrument=InstrumentCode.WTI)
    assert report["dataset_blocked_by_invalid_data"]
    export = export_analysis([c.model_dump() for c in bars],
                             {"as_of": START + timedelta(minutes=200)}, tmp_path / "gold",
                             instrument=InstrumentCode.XAUUSD,
                             calendar=BrokerCalendar({"XAUUSD": symbol()}))
    assert export["instrument"] == "XAUUSD"
    manifest = (tmp_path / "gold/dataset/manifest.json").read_text()
    assert '"instrument": "XAUUSD"' in manifest
    other = build_ml_dataset(bars[:150], InstrumentCode.XAUUSD, Timeframe.M1, source="fixture",
                             reset_on_gaps=True,
                             feature_config=FeatureConfig(include_trend_indicators=True))
    pd.testing.assert_frame_equal(dataset.features.loc[other.features.index], other.features)
