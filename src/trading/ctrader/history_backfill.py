"""Independent, supervised Pepperstone DEMO history job; never loads application settings.

Only the existing isolated test deployment is supported. Secrets cross a captured
private subprocess pipe, never argv, environment variables, files or logs. The
collector remains the sole OAuth refresher. Its encrypted file is read atomically.
"""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from trading.ctrader.calendar import BrokerCalendar
from trading.ctrader.client import CTraderError, CTraderReadOnlyClient
from trading.ctrader.parquet import CTRADER_PARQUET_PROVIDER
from trading.ctrader.sync import CTraderSyncService
from trading.data.domain import InstrumentCode
from trading.data.market_calendar import MarketCalendar
from trading.data.repository import PostgresMarketDataRepository
from trading.models import SystemEvent

COLLECTOR = "ai-trading-ctrader-worker-ctrader-worker-1"
MODULE = "trading.ctrader.history_backfill"
DATABASE = "ctrader_demo_fe1a7194a87c"
USER = "ctrader_demo_test"
LOCK_KEY = 44004221012026
CHECKPOINT = "ctrader_history_backfill_checkpoint"
ERROR = "ctrader_history_backfill_error"
DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "data" / "phase4b"
FREE_FLOOR = 10 * 1024**3

# Deliberately bypass the backend's read lock: encrypted-file writes use atomic
# replace; a read-only snapshot must not create lock files or write credentials.
ACCESS_SCRIPT = """
import json,os
from pathlib import Path
from trading.ctrader.encrypted_store import EncryptedFileBackend
from trading.ctrader.oauth import CTraderSecretStore,OAuthTokens
assert os.environ.get('TRADING_MODE') == 'DEMO'
assert os.environ.get('LIVE_EXECUTION_ENABLED') == 'false'
b=EncryptedFileBackend(os.environ['CTRADER_CREDENTIAL_FILE'],
                       os.environ['CTRADER_CREDENTIAL_KEY_FILE'])
d=b._read()
prefix=CTraderSecretStore.SERVICE+'/'
t=OAuthTokens.from_json(d[prefix+CTraderSecretStore.TOKEN_KEY],strict=True)
print(json.dumps(dict(client_id=d[prefix+CTraderSecretStore.CLIENT_ID_KEY],
 client_secret=d[prefix+CTraderSecretStore.CLIENT_SECRET_KEY],access_token=t.access_token,
 expires_at=t.expires_at,
 database_url=Path(os.environ['DATABASE_URL_FILE']).read_text().strip(),
 identity=[os.environ['CTRADER_EXPECTED_DATABASE'],os.environ['CTRADER_EXPECTED_USER'],
           os.environ['CTRADER_EXPECTED_CLUSTER_ID']])))
"""


class SafetyStop(Exception):
    """A sanitized fail-closed stop requiring operator review."""


class StopRequested(Exception):
    pass


class CredentialUnavailable(Exception):
    """Wait for the independent collector rather than refreshing its token."""


def utc(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise ValueError("UTC timestamps required")
    if result.second or result.microsecond:
        raise ValueError("Whole-minute timestamps required")
    return result


def batches(start, end):
    while start < end:
        stop = min(start + timedelta(days=7), end)
        yield start, stop
        start = stop


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, default=str)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def load_access(*, require_token=True):
    try:
        result = subprocess.run(
            ["docker", "exec", COLLECTOR, "python", "-c", ACCESS_SCRIPT],
            capture_output=True, text=True, timeout=30, check=True,
        )
        access = json.loads(result.stdout)
        url = make_url(access.pop("database_url"))
        if (url.drivername != "postgresql+psycopg" or url.port != 15432
                or url.host != "host.docker.internal" or url.query
                or url.database != DATABASE or url.username != USER
                or access["identity"][:2] != [DATABASE, USER]):
            raise SafetyStop("Isolated database target mismatch")
        access["url"] = url.set(host="127.0.0.1")
        if require_token and access.get("expires_at", 0) <= time.time() + 60:
            raise CredentialUnavailable("Waiting for the collector to refresh its access token")
        return access
    except SafetyStop:
        raise
    except CredentialUnavailable:
        raise
    except Exception:
        raise CredentialUnavailable("Read-only credential snapshot unavailable; refresh disabled") \
            from None


def engine_for(access):
    return create_engine(access["url"], pool_pre_ping=True, connect_args={
        "connect_timeout": 10, "options": "-c statement_timeout=60000 -c lock_timeout=5000",
    })


def verify_identity(connection, expected):
    actual = connection.execute(text(
        "SELECT current_database(),current_user,system_identifier::text FROM pg_control_system()"
    )).one()
    if list(actual) != list(expected) or list(actual[:2]) != [DATABASE, USER]:
        raise SafetyStop("Disposable database identity mismatch; writes forbidden")


class FileLock:
    """OS-owned lock released on process death; persistent file is never deleted."""

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.handle = self.path.open("a+b")
        self.handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                self.handle.close()
                raise SafetyStop("A backfill supervisor already owns this job") from None
        else:
            import fcntl
            try:
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.handle.close()
                raise SafetyStop("A backfill supervisor already owns this job") from None
        return self

    def __exit__(self, *args):
        self.handle.close()


class Guard:
    def __init__(self, root, config, connection, disk_usage=shutil.disk_usage):
        self.root, self.config, self.connection = root, config, connection
        self.disk_usage = disk_usage
        self.pid = connection.scalar(text("SELECT pg_backend_pid()"))
        self.last_disk = {}
        self.vm_checked_at = 0.0
        self.vm_free = None
        self.heartbeat_at = 0.0

    def check(self):
        if (self.root / "stop.requested").exists():
            raise StopRequested()
        paths = self.config["disk_paths"]
        self.last_disk = {path: self.disk_usage(path).free for path in paths}
        if min(self.last_disk.values()) < FREE_FLOOR:
            raise SafetyStop("Free disk space below 10 GiB; safely stopped without deleting data")
        if time.monotonic() - self.vm_checked_at > 10:
            for attempt in range(3):
                if (self.root / "stop.requested").exists():
                    raise StopRequested()
                try:
                    result = subprocess.run(
                        ["docker", "exec", "ctrader-worker-test", "df", "-PB1",
                         "/var/lib/postgresql/data"],
                        capture_output=True, text=True, check=True, timeout=20,
                    )
                    free = int(result.stdout.splitlines()[-1].split()[3])
                    if free < 0:
                        raise ValueError("Invalid available byte count")
                except (OSError, subprocess.SubprocessError, ValueError, IndexError) as error:
                    if attempt == 2:
                        raise SafetyStop(
                            "PostgreSQL storage free-space check unavailable after 3 attempts "
                            f"({type(error).__name__}); writes forbidden"
                        ) from None
                    time.sleep(2**attempt)
                else:
                    self.vm_free = free
                    self.vm_checked_at = time.monotonic()
                    break
        self.last_disk["postgres_filesystem"] = self.vm_free
        if min(self.last_disk.values()) < FREE_FLOOR:
            raise SafetyStop("Free disk space below 10 GiB; safely stopped without deleting data")
        if self.connection.scalar(text("SELECT pg_backend_pid()")) != self.pid:
            raise SafetyStop("Database advisory-lock session changed; writes forbidden")
        verify_identity(self.connection, self.config["identity"])
        if time.monotonic() - self.heartbeat_at > 10:
            write_json(self.root / "heartbeat.json", {
                "at": datetime.now(UTC).isoformat(), "free_bytes": self.last_disk,
            })
            self.heartbeat_at = time.monotonic()


class GuardedRepository(PostgresMarketDataRepository):
    def __init__(self, engine, guard):
        super().__init__(engine)
        self.guard = guard

    def import_candles(self, *args, **kwargs):
        self.guard.check()
        return super().import_candles(*args, **kwargs)


class PacedClient(CTraderReadOnlyClient):
    def __init__(self, access, guard):
        super().__init__(access["client_id"], access["client_secret"], access["access_token"])
        self.guard = guard

    async def get_eurusd_m1_candles(self, *args, **kwargs):
        return await super().get_eurusd_m1_candles(*args, **kwargs)

    async def get_m1_candles(self, *args, **kwargs):
        self.guard.check()
        # <=1 historical request/s even across local chunk retries. Existing
        # daily chunk retry/backoff and response validation remain in use.
        await asyncio.sleep(1)
        return await super().get_m1_candles(*args, **kwargs)


def instrument_for(config):
    return InstrumentCode(config.get("instrument", "EURUSD"))


def calendar_for(config):
    return (BrokerCalendar({instrument_for(config).value: config["symbol_metadata"]})
            if config.get("symbol_metadata") else MarketCalendar())


def history_lock_key(instrument):
    # Preserve the Phase 4B EURUSD lock key for old running binaries.
    if instrument == InstrumentCode.EURUSD:
        return LOCK_KEY
    return LOCK_KEY + list(InstrumentCode).index(instrument) + 1


def events(engine, config, event_type=CHECKPOINT):
    with engine.connect() as connection:
        return list(connection.scalars(
            select(SystemEvent.payload).where(
                SystemEvent.event_type == event_type,
                SystemEvent.payload["job_id"].astext == config["job_id"],
            ).order_by(SystemEvent.id)
        ))


def record(engine, config, payload, event_type, guard):
    guard.check()
    with engine.begin() as connection:
        # Separate append-only event namespace; existing worker reports stay intact.
        verify_identity(connection, config["identity"])
        connection.execute(SystemEvent.__table__.insert().values(
            event_type=event_type,
            payload=json.loads(json.dumps({"job_id": config["job_id"], **payload}, default=str)),
        ))


def next_batch(config, checkpoints):
    visited = {(p["start"], p["end"]) for p in checkpoints}
    for start, end in batches(utc(config["start"]), utc(config["backfill_end"])):
        if (start.isoformat(), end.isoformat()) not in visited:
            return start, end
    return None


def scan_summary(engine, start, end, *, global_totals=False,
                 instrument=InstrumentCode.EURUSD, calendar=None):
    """Verify stored data, not logs; bounded SQL window query for genuine gaps."""
    params = {"start": start, "end": end, "provider": CTRADER_PARQUET_PROVIDER,
              "instrument": instrument.value}
    where = ("instrument_symbol=:instrument AND timeframe_minutes=1 "
             "AND source_provider=:provider AND price_type='unknown'")
    with engine.connect() as connection:
        # The unique source/price key proves timestamp distinctness. Keep whole-
        # archive status reads on the covering index instead of rereading every
        # historical price. Each small batch still checks its actual OHLC values.
        count_distinct = "count(*)" if global_totals else "count(DISTINCT opened_at)"
        ohlc = ("0" if global_totals else
                "count(*) FILTER(WHERE high<greatest(open,close,low) "
                "OR low>least(open,close,high))")
        totals = dict(connection.execute(text(
            f"SELECT count(*) AS candle_count,{count_distinct} AS distinct_timestamps,"
            "min(opened_at) AS earliest,max(opened_at) AS latest,"
            f"{ohlc} AS ohlc_inconsistency_count "
            "FROM ohlcv_candles WHERE "
            + where + ("" if global_totals else " AND opened_at>=:start AND opened_at<:end")
        ), params).mappings().one())
        size = connection.scalar(text("SELECT pg_database_size(current_database())"))
        if global_totals:
            validated = connection.scalar(text(
                "SELECT convalidated FROM pg_constraint WHERE conname='ck_ohlcv_price_bounds' "
                "AND conrelid='ohlcv_candles'::regclass"
            ))
            totals["ohlc_bounds_constraint_validated"] = validated
            if validated is not True:
                totals["ohlc_inconsistency_count"] = None
        intervals = list(connection.execute(text(
            "WITH stamps AS (SELECT opened_at FROM ohlcv_candles WHERE " + where
            + " AND opened_at>=:start AND opened_at<:end UNION "
            "SELECT CAST(:start AS timestamptz)-interval '1 minute' UNION "
            "SELECT CAST(:end AS timestamptz)), gaps AS (SELECT opened_at,"
            "lag(opened_at) OVER(ORDER BY opened_at) AS previous FROM stamps) "
            "SELECT previous,opened_at FROM gaps "
            "WHERE opened_at>previous+interval '1 minute' ORDER BY opened_at"
        ), params))
    calendar = calendar or MarketCalendar()
    gaps, missing, closed = [], 0, 0
    for previous, stamp in intervals:
        count = max(0, int((stamp - previous).total_seconds() // 60) - 1)
        if count:
            first = previous + timedelta(minutes=1)
            expected, closure = calendar.classify_missing_timestamps(
                instrument,
                first, count, 1,
            )
            missing += expected
            closed += closure
            gaps.append({"start": first.isoformat(), "end": stamp.isoformat(),
                         "missing_trading_minutes": expected, "scheduled_closed_minutes": closure})
    return {**totals, "instrument": instrument.value, "source_provider": CTRADER_PARQUET_PROVIDER,
            "price_type": "unknown", "duplicate_timestamps": totals["candle_count"]
            - totals["distinct_timestamps"],
            "database_size_bytes": size, "verified_start": start.isoformat(),
            "verified_end": end.isoformat(), "missing_trading_minutes": missing,
            "scheduled_closed_minutes": closed, "gaps": gaps,
            "calendar_limitation": (
                "Current broker sessions and exposed holidays used; historical schedule changes "
                "and unexposed closures cannot be confirmed"
                if isinstance(calendar, BrokerCalendar)
                else "Unconfirmed holidays/maintenance remain unresolved"
            )}


def initialize(root, *, instrument=InstrumentCode.EURUSD, symbol_metadata=None):
    root.mkdir(parents=True, exist_ok=True)
    if (root / "job.json").exists():
        raise SafetyStop("Job already initialized; use start to resume")
    access = load_access()
    engine = engine_for(access)
    try:
        with engine.connect() as connection:
            verify_identity(connection, access["identity"])
            latest = connection.scalar(text(
                "SELECT min(opened_at) FROM ohlcv_candles WHERE instrument_symbol=:instrument "
                "AND timeframe_minutes=1 AND source_provider=:provider AND price_type='unknown'"
            ), {"provider": CTRADER_PARQUET_PROVIDER, "instrument": instrument.value})
        start, end = utc("2021-10-22T00:00:00Z"), utc("2026-10-22T00:00:00Z")
        # Disjoint ownership: reserve the entire existing recent stream for the
        # collector, including its current rolling 24h synchronization window.
        protected = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(hours=48)
        cutoff = min(end, latest) if latest else protected
        if cutoff < protected:
            raise SafetyStop("Existing history ownership requires review before initialization")
        config = {"job_id": hashlib.sha256(f"{start}:{end}".encode()).hexdigest()[:16],
                  "start": start.isoformat(), "end": end.isoformat(),
                  "backfill_end": cutoff.isoformat(), "identity": access["identity"],
                  "disk_paths": [str(root.resolve().anchor)], "created_at": datetime.now(UTC),
                  "initial_free_bytes": shutil.disk_usage(root).free}
        if instrument != InstrumentCode.EURUSD:
            if not symbol_metadata or not latest:
                raise SafetyStop("Discover and seed the continuous instrument before backfill init")
            config.update({"instrument": instrument.value, "symbol_metadata": symbol_metadata,
                           "job_id": hashlib.sha256(
                               f"{instrument.value}:{start}:{end}".encode()
                           ).hexdigest()[:16]})
        write_json(root / "job.json", config)
        write_json(root / "initial-database.json", scan_summary(
            engine, latest or cutoff, end, global_totals=True, instrument=instrument,
            calendar=calendar_for(config),
        ))
        return config
    finally:
        engine.dispose()


def progress(root, **kwargs):
    write_json(root / "progress.json", {"updated_at": datetime.now(UTC), **kwargs})


def verified_status(root):
    """Fresh read-only DB evidence through the latest contiguous durable checkpoint."""
    config = json.loads((root / "job.json").read_text())
    access = load_access(require_token=False)
    engine = engine_for(access)
    try:
        with engine.connect() as connection:
            verify_identity(connection, config["identity"])
        checkpoints = events(engine, config)
        errors = events(engine, config, ERROR)
        upcoming = next_batch(config, checkpoints)
        through = upcoming[0] if upcoming else utc(config["backfill_end"])
        verified = scan_summary(engine, utc(config["start"]), through, global_totals=True,
                                instrument=instrument_for(config), calendar=calendar_for(config))
        total = len(list(batches(utc(config["start"]), utc(config["backfill_end"]))))
        result = {
            "as_of": datetime.now(UTC), "requested_start": config["start"],
            "requested_end": config["end"], "verified": verified,
            "completed_batches": len(checkpoints), "remaining_batches": total - len(checkpoints),
            "api_error_count": sum(e["error_category"] == "api_or_validation" for e in errors),
            "database_error_count": sum(e["error_category"] == "database" for e in errors),
            "api_attempts_in_checkpointed_batches": sum(
                p["result"]["api_requests_attempted"] for p in checkpoints
            ),
            "windows_free_bytes": shutil.disk_usage(root).free,
            "five_year_backfill_finished": upcoming is None,
            "instrument": instrument_for(config).value,
        }
        write_json(root / "verified-status.json", result)
        return result
    finally:
        engine.dispose()


def run(root):
    config = json.loads((root / "job.json").read_text())
    instrument = instrument_for(config)
    calendar = calendar_for(config)
    access = load_access()
    if access["identity"] != config["identity"]:
        raise SafetyStop("Collector identity changed")
    engine = engine_for(access)
    total = len(list(batches(utc(config["start"]), utc(config["backfill_end"]))))
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
            verify_identity(lock, config["identity"])
            if not lock.scalar(text("SELECT pg_try_advisory_lock(:key)"),
                               {"key": history_lock_key(instrument)}):
                raise SafetyStop("Another historical backfill owns the test database")
            guard = Guard(root, config, lock)
            repository = GuardedRepository(engine, guard)
            checkpoints = events(engine, config)
            while interval := next_batch(config, checkpoints):
                start, end = interval
                guard.check()
                progress(root, status="running", completed_batches=len(checkpoints),
                         total_batches=total, current_start=start, current_end=end,
                         free_bytes=guard.last_disk)
                for attempt in range(3):
                    guard.check()
                    # Always reread the collector's token; never race OAuth refresh.
                    access = load_access()
                    client = PacedClient(access, guard)
                    try:
                        metadata = asyncio.run(
                            client.discover_eurusd() if instrument == InstrumentCode.EURUSD else
                            client.discover_instrument(
                                instrument, config["symbol_metadata"]["broker_symbol_name"],
                                config["symbol_metadata"]["account_id"],
                            )
                        )
                        if (config.get("symbol_metadata") and metadata["symbol"]["broker_symbol_id"]
                                != config["symbol_metadata"]["broker_symbol_id"]):
                            raise SafetyStop("Broker symbol mapping changed; review required")
                        if (metadata["account"]["is_live"] or "pepperstone" not in
                                str(metadata["account"]["broker_name"]).lower()):
                            raise SafetyStop("Expected Pepperstone DEMO account not found")
                        service = CTraderSyncService(
                            repository, client, calendar=calendar,
                            account_id=metadata["account"]["account_id"],
                            instrument=instrument,
                            broker_symbol=metadata["symbol"]["broker_symbol_name"],
                        )
                        result = asyncio.run(service.synchronize(
                            start, end, max_days=7, save_postgres=True,
                        ))
                        if (result["api_errors"] or result["rejected_records"]
                                or result["conflicts"]):
                            raise CTraderError("Batch incomplete due to API or validation errors")
                        # Empty open-session batches get a second confirmation.
                        if (not result["candles_retrieved"] and not result["existing_candle_count"]
                                and result["expected_market_minutes"] and attempt == 0):
                            raise CTraderError("Empty history needs a second bounded confirmation")
                        verified = scan_summary(engine, start, end, instrument=instrument,
                                                calendar=calendar)
                        checkpoint = {"start": start.isoformat(), "end": end.isoformat(),
                                      "finished_at": datetime.now(UTC).isoformat(),
                                      "result": result, "verified": verified}
                        record(engine, config, checkpoint, CHECKPOINT, guard)
                        checkpoints.append(checkpoint)
                        progress(root, status="running", completed_batches=len(checkpoints),
                                 total_batches=total, last_checkpoint=checkpoint,
                                 free_bytes=guard.last_disk)
                        break
                    except (CTraderError, SQLAlchemyError) as error:
                        record(engine, config, {
                            "start": start.isoformat(), "end": end.isoformat(),
                            "attempt": attempt + 1,
                            "error_category": "database" if isinstance(error, SQLAlchemyError)
                            else "api_or_validation",
                        }, ERROR, guard)
                        if attempt == 2:
                            raise SafetyStop(
                                "Batch failed after three attempts; checkpoint preserved"
                            ) from None
                        for _ in range(15 * 2**attempt):
                            guard.check()
                            time.sleep(1)
            guard.check()
            verified = scan_summary(engine, utc(config["start"]), utc(config["end"]),
                                    global_totals=True, instrument=instrument, calendar=calendar)
            write_json(root / "final-database.json", verified)
            progress(root, status="completed_with_gaps" if verified["missing_trading_minutes"]
                     else "completed", completed_batches=len(checkpoints), total_batches=total,
                     verified=verified, free_bytes=guard.last_disk)
    finally:
        engine.dispose()


def supervise(root, *, module=MODULE):
    with FileLock(root / "supervisor.lock"):
        (root / "stop.requested").unlink(missing_ok=True)
        write_json(root / "supervisor.json", {"pid": os.getpid(), "started_at": datetime.now(UTC)})
        try:
            for restart in range(6):
                child = subprocess.Popen(
                    [sys.executable, "-m", module, "run", "--state-dir", str(root)],
                    stdin=subprocess.DEVNULL,
                )
                while child.poll() is None:
                    # Independent guard remains active while a batch is in-flight.
                    if shutil.disk_usage(root).free < FREE_FLOOR:
                        (root / "stop.requested").touch()
                    time.sleep(1)
                if child.returncode in (0, 2) or (root / "stop.requested").exists():
                    return child.returncode
                progress(root, status="retrying_process", restart=restart + 1)
                for _ in range(min(300, 30 * 2**restart)):
                    if (root / "stop.requested").exists():
                        return 0
                    time.sleep(1)
            progress(root, status="failed", reason="Supervisor restart limit reached")
            return 2
        finally:
            (root / "supervisor.json").unlink(missing_ok=True)


def start_background(root, *, module=MODULE):
    if not (root / "job.json").exists():
        raise SafetyStop("Initialize the reviewed job first")
    with FileLock(root / "supervisor.lock"):
        pass
    log = (root / "supervisor.log").open("a", encoding="utf-8")
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
             if os.name == "nt" else 0)
    process = subprocess.Popen(
        [sys.executable, "-m", module, "supervise", "--state-dir", str(root)],
        stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags,
        start_new_session=os.name != "nt", close_fds=True,
    )
    log.close()
    print(json.dumps({"supervisor_pid": process.pid, "state_directory": str(root)}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "start", "stop", "status", "run", "supervise"])
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--verify", action="store_true", help="Read current PostgreSQL evidence")
    args = parser.parse_args(argv)
    root = args.state_dir.resolve()
    try:
        if args.command == "init":
            print(json.dumps(initialize(root), default=str, indent=2))
        elif args.command == "start":
            start_background(root)
        elif args.command == "stop":
            (root / "stop.requested").touch()
            print("Stop requested; finish/cancel at the next safe request/write boundary")
        elif args.command == "status":
            current = {}
            for name in ("supervisor.json", "progress.json", "heartbeat.json"):
                if (root / name).exists():
                    value = json.loads((root / name).read_text())
                    value.pop("last_checkpoint", None)
                    value.pop("verified", None)
                    current[name] = value
            current.update({"stop_requested": (root / "stop.requested").exists(),
                            "windows_free_bytes": shutil.disk_usage(root).free})
            print(json.dumps(current, indent=2))
            if args.verify:
                verified = verified_status(root)
                verified["verified"]["gap_interval_count"] = len(
                    verified["verified"].pop("gaps")
                )
                print(json.dumps(verified, default=str, indent=2))
        elif args.command == "supervise":
            return supervise(root)
        else:
            run(root)
        return 0
    except StopRequested:
        progress(root, status="stopped", reason="Stop requested or host disk guard")
        return 0
    except SafetyStop as error:
        if args.command == "run":
            progress(root, status="safety_stopped", reason=str(error))
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:
        # Never log raw exception text, SQL parameters, subprocess stdout or URLs.
        if args.command == "run":
            progress(root, status="transient_failure", error_type=type(error).__name__)
        print(f"Backfill failed ({type(error).__name__}); secrets suppressed", file=sys.stderr)
        print(" > ".join(f"{Path(frame.filename).name}:{frame.name}:{frame.lineno}"
                         for frame in traceback.extract_tb(error.__traceback__)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
