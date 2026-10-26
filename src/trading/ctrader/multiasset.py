"""Deployment and handover for a disjoint multi-asset DEMO collector group."""

import argparse
import asyncio
import json
import shutil
import signal
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

from sqlalchemy import text

from trading.ctrader import history_backfill as history
from trading.ctrader.calendar import BrokerCalendar
from trading.ctrader.client import CTraderReadOnlyClient
from trading.ctrader.operations import CTraderOperationsRepository
from trading.ctrader.parquet import CTRADER_PARQUET_PROVIDER
from trading.ctrader.sync import CTraderSyncService
from trading.ctrader.worker import WorkerConfig, run_foreground_worker, synchronize_worker_cycle
from trading.data.domain import InstrumentCode

MODULE = "trading.ctrader.multiasset"
ROOT = Path(__file__).resolve().parents[3]
STATE = ROOT / "data" / "phase4c"
CONFIG = ROOT / "config" / "ctrader_instruments.json"
COLLECTOR_LOCK = history.LOCK_KEY + 100


def choose_symbol(entry, symbols):
    """Exact, enabled aliases only; never silently substitute Brent or perpetuals."""
    for candidate in entry["candidates"]:
        matches = [s for s in symbols if s.get("enabled") is True
                   and s.get("symbolName") == candidate]
        if len(matches) > 1:
            raise ValueError("Ambiguous broker symbol")
        if matches:
            return matches[0]
    return None


async def discover():
    access = history.load_access()
    client = CTraderReadOnlyClient(
        access["client_id"], access["client_secret"], access["access_token"],
    )
    eur = await client.discover_eurusd()
    if eur["account"]["is_live"] or "pepperstone" not in str(eur["account"]["broker_name"]).lower():
        raise history.SafetyStop("Pepperstone DEMO required")
    account_id = eur["account"]["account_id"]
    symbols = await client.list_symbols(account_id)
    resolved, unavailable = {}, []
    for entry in json.loads(CONFIG.read_text())["instruments"]:
        symbol = choose_symbol(entry, symbols)
        if symbol is None:
            unavailable.append(entry["instrument"])
            continue
        instrument = InstrumentCode(entry["instrument"])
        metadata = await client.discover_instrument(instrument, symbol["symbolName"], account_id)
        metadata = metadata["symbol"]
        metadata["collector_owner"] = entry["collector_owner"]
        metadata["discovered_at"] = datetime.now(UTC).isoformat()
        if metadata["specifications"].get("tradingMode") not in (None, 0):
            unavailable.append(instrument.value)
            continue
        BrokerCalendar({instrument.value: metadata})  # validate exposed timezone/sessions
        if instrument != InstrumentCode.EURUSD:
            probes = []
            for stamp in ("2021-10-22T12:00:00Z", "2024-10-01T12:00:00Z"):
                start = history.utc(stamp)
                await asyncio.sleep(1)
                bars = await client.get_m1_candles(
                    instrument, start, start + timedelta(minutes=5), account_id,
                    broker_symbol=metadata["broker_symbol_name"],
                )
                probes.append({"start": stamp, "count": len(bars),
                               "first": bars[0]["opened_at"] if bars else None})
                if bars:
                    break
            metadata["history_probes"] = probes
        resolved[instrument.value] = metadata
    result = {"symbols": resolved, "unavailable": unavailable,
              "oil_alternatives": [s for s in symbols if s.get("enabled") is True and any(
                  word in (s.get("symbolName", "") + s.get("description", "")).upper()
                  for word in ("CRUDE", "WTI", "BRENT")
              )]}
    STATE.mkdir(parents=True, exist_ok=True)
    history.write_json(STATE / "symbols.json", result)
    return result


def initialize_collector(root):
    root.mkdir(parents=True, exist_ok=True)
    path = root / "job.json"
    if path.exists():
        raise history.SafetyStop("Collector already initialized; use start")
    if shutil.disk_usage(root).free < history.FREE_FLOOR:
        raise history.SafetyStop("Free disk space below 10 GiB")
    symbols = json.loads((STATE / "symbols.json").read_text())["symbols"]
    assets = {k: v for k, v in symbols.items() if v["collector_owner"] == "multiasset"}
    if not assets or "EURUSD" in assets:
        raise history.SafetyStop("New collector must own only the resolved additional instruments")
    access = history.load_access()
    engine = history.engine_for(access)
    try:
        with engine.begin() as connection:
            history.verify_identity(connection, access["identity"])
            for instrument, symbol in assets.items():
                updated = connection.execute(text(
                    "UPDATE instruments SET broker_symbol_mappings = broker_symbol_mappings "
                    "|| CAST(:mapping AS jsonb) WHERE symbol=:instrument"
                ), {"mapping": json.dumps({CTRADER_PARQUET_PROVIDER: symbol["broker_symbol_name"]}),
                    "instrument": instrument})
                if updated.rowcount != 1:
                    raise history.SafetyStop("Canonical instrument missing; no migration performed")
            connection.execute(text(
                "INSERT INTO system_events(event_type,payload) VALUES "
                "('ctrader_symbol_discovery',CAST(:payload AS jsonb))"
            ), {"payload": json.dumps(symbols)})
        config = {"symbols": assets, "identity": access["identity"],
                  "disk_paths": [str(root.anchor)], "interval_seconds": 300,
                  "continuous_since": datetime.now(UTC).replace(
                      second=0, microsecond=0).isoformat()}
        history.write_json(path, config)
    finally:
        engine.dispose()


class GuardedOperations(CTraderOperationsRepository):
    def __init__(self, engine, instrument, guard):
        super().__init__(engine, instrument)
        self.guard = guard

    def record_run(self, report, retry_state):
        self.guard.check()
        super().record_run(report, retry_state)


def save_watermark(engine, instrument, latest, guard):
    if latest is None:
        return
    guard.check()
    with engine.begin() as connection:
        history.verify_identity(connection, guard.config["identity"])
        connection.execute(text(
            "INSERT INTO data_ingestion_checkpoints "
            "(provider,instrument_symbol,timeframe_minutes,source_key,last_candle_at) "
            "VALUES(:provider,:instrument,1,:source,:latest) "
            "ON CONFLICT(provider,instrument_symbol,timeframe_minutes,source_key) DO UPDATE "
            "SET last_candle_at=greatest(data_ingestion_checkpoints.last_candle_at,"
            "excluded.last_candle_at),updated_at=now()"
        ), {"provider": CTRADER_PARQUET_PROVIDER, "instrument": instrument.value,
            "source": f"continuous:{instrument.value}:M1", "latest": history.utc(latest)})


def run(root):
    config = json.loads((root / "job.json").read_text())
    if "EURUSD" in config["symbols"]:
        raise history.SafetyStop("EURUSD remains owned by the existing Docker collector")
    access = history.load_access()
    engine = history.engine_for(access)
    stop = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
            history.verify_identity(lock, config["identity"])
            if not lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": COLLECTOR_LOCK}):
                raise history.SafetyStop("Additional-instrument collector already active")
            guard = history.Guard(root, config, lock)
            repository = history.GuardedRepository(engine, guard)
            calendar = BrokerCalendar(config["symbols"])

            def cycle():
                guard.check()
                reports = {}
                for name, symbol in config["symbols"].items():
                    guard.check()
                    instrument = InstrumentCode(name)
                    try:
                        credentials = history.load_access()
                        client = history.PacedClient(credentials, guard)
                        metadata = asyncio.run(client.discover_instrument(
                            instrument, symbol["broker_symbol_name"], symbol["account_id"],
                        ))
                        if metadata["symbol"]["broker_symbol_id"] != symbol["broker_symbol_id"]:
                            raise history.SafetyStop("Broker mapping changed; review required")
                        service = CTraderSyncService(
                            repository, client, calendar=calendar, instrument=instrument,
                            broker_symbol=symbol["broker_symbol_name"],
                            account_id=symbol["account_id"],
                        )
                        report = asyncio.run(synchronize_worker_cycle(
                            service, GuardedOperations(engine, instrument, guard), WorkerConfig(),
                            save_postgres=True,
                        ))
                        reports[name] = report
                        save_watermark(engine, instrument,
                                       report["quality"]["latest_stored_candle"], guard)
                    except (history.StopRequested, history.SafetyStop):
                        raise
                    except Exception as error:
                        # Isolate a transient asset failure; do not leak transport or SQL details.
                        reports[name] = {"execution_status": "failed",
                                         "error_type": type(error).__name__}
                history.progress(root, status="running", instruments=reports)

            def wait(seconds):
                guard.check()
                stop.wait(seconds)

            run_foreground_worker(cycle, 300, stop_event=stop, wait=wait)
    finally:
        engine.dispose()


def status():
    access = history.load_access(require_token=False)
    engine = history.engine_for(access)
    try:
        with engine.connect() as connection:
            history.verify_identity(connection, access["identity"])
            rows = connection.execute(text(
                "SELECT instrument_symbol,source_provider,price_type,count(*) AS candles,"
                "count(*) AS distinct_timestamps,min(opened_at) AS earliest,"
                "max(opened_at) AS latest "
                "FROM ohlcv_candles WHERE timeframe_minutes=1 "
                "GROUP BY 1,2,3 ORDER BY 1,2,3"
            )).mappings().all()
            runs = {name: CTraderOperationsRepository(engine, InstrumentCode(name)).latest_run()
                    for name in json.loads((STATE / "symbols.json").read_text())["symbols"]}
            checkpoints = [dict(row) for row in connection.execute(text(
                "SELECT instrument_symbol,source_key,last_candle_at "
                "FROM data_ingestion_checkpoints "
                "WHERE provider=:provider ORDER BY instrument_symbol"
            ), {"provider": CTRADER_PARQUET_PROVIDER}).mappings()]
            size = connection.scalar(text("SELECT pg_database_size(current_database())"))
            bounds_constraint = connection.scalar(text(
                "SELECT convalidated FROM pg_constraint WHERE conname='ck_ohlcv_price_bounds' "
                "AND conrelid='ohlcv_candles'::regclass"
            ))
        result = {"as_of": datetime.now(UTC), "inventory": [dict(row) for row in rows],
                  "collection": {k: v["report"] if v else None for k, v in runs.items()},
                  "checkpoints": checkpoints, "database_size_bytes": size}
        result["ohlc_bounds_constraint_validated"] = bounds_constraint
        result["distinctness_proof"] = (
            "Counts grouped by instrument/source/price; the existing unique "
            "uq_ohlcv_instrument_tf_time_source_price constraint forbids timestamp duplicates"
        )
        supervisor_root = STATE / "collector"
        try:
            with history.FileLock(supervisor_root / "supervisor.lock"):
                result["additional_collector_supervised"] = False
        except history.SafetyStop:
            result["additional_collector_supervised"] = True
        result["windows_free_bytes"] = shutil.disk_usage(STATE).free
        history.write_json(STATE / "status.json", result)
        compact = {**result, "collection": {k: {field: v.get(field) for field in (
            "finished_at", "execution_status", "status", "quality",
        )} if v else None for k, v in result["collection"].items()}}
        print(json.dumps(compact, indent=2, default=str))
    finally:
        engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["discover", "init", "start", "stop", "status",
                                            "run", "supervise", "history-init"])
    parser.add_argument("--state-dir", type=Path, default=STATE / "collector")
    parser.add_argument("--instrument", choices=[i.value for i in InstrumentCode])
    args = parser.parse_args(argv)
    root = args.state_dir.resolve()
    try:
        if args.command == "discover":
            result = asyncio.run(discover())
            print(json.dumps({k: {"name": v["broker_symbol_name"], "id": v["broker_symbol_id"],
                                  "probes": v.get("history_probes")}
                              for k, v in result["symbols"].items()}, indent=2))
        elif args.command == "init":
            initialize_collector(root)
        elif args.command == "start":
            history.start_background(root, module=MODULE)
        elif args.command == "stop":
            (root / "stop.requested").touch()
        elif args.command == "status":
            status()
        elif args.command == "supervise":
            return history.supervise(root, module=MODULE)
        elif args.command == "run":
            run(root)
        else:
            if not args.instrument or args.instrument == "EURUSD":
                raise history.SafetyStop("EURUSD history already scanned; no duplicate job")
            symbols = json.loads((STATE / "symbols.json").read_text())["symbols"]
            history.initialize(STATE / "history" / args.instrument,
                               instrument=InstrumentCode(args.instrument),
                               symbol_metadata=symbols[args.instrument])
        return 0
    except history.StopRequested:
        history.progress(root, status="stopped")
        return 0
    except history.SafetyStop as error:
        if args.command == "run":
            history.progress(root, status="safety_stopped", reason=str(error))
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:
        if args.command == "run":
            history.progress(root, status="transient_failure", error_type=type(error).__name__)
        code = getattr(getattr(error, "orig", None), "sqlstate", None)
        print(f"Multi-asset failed ({type(error).__name__}, code={code}); secrets suppressed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
