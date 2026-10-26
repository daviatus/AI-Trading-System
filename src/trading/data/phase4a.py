"""Read-only, instrument-isolated M1 audit and gap-aware feature export.

No application settings, broker credentials, ingestion code or worker lifecycle
are loaded. An explicit loopback PostgreSQL URL on port 15432 is mandatory.
"""

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from trading.ctrader.parquet import CTRADER_PARQUET_PROVIDER
from trading.data.dataset import FeatureConfig, build_ml_dataset
from trading.data.domain import Candle, InstrumentCode, PriceType, Timeframe
from trading.data.market_calendar import MarketCalendar
from trading.data.quality import check_candle_quality
from trading.models import OhlcvCandleRecord
from trading.storage.features import ParquetFeatureStore

MINUTE = timedelta(minutes=1)


def validate_test_database_url(value: str):
    url = make_url(value)
    if (
        url.drivername not in {"postgresql", "postgresql+psycopg"}
        or url.host not in {"127.0.0.1", "localhost", "::1"}
        or url.port != 15432
        or not url.database
        or not url.username
        or url.query
    ):
        raise ValueError("Phase 4A requires an explicit loopback PostgreSQL URL on port 15432; "
                         "URL query overrides are forbidden")
    return url.set(drivername="postgresql+psycopg")


class AuditCalendar(MarketCalendar):
    """DST-aware FX week plus explicitly confirmed broker closure intervals."""

    def __init__(self, closures: list[dict] | None = None):
        super().__init__()
        self.closures = []
        for closure in closures or []:
            start, end = (_utc(closure[key]) for key in ("start", "end"))
            if start >= end or not closure.get("reason"):
                raise ValueError("Closures require start < end and a reason")
            self.closures.append((start, end, closure["reason"]))

    def is_open(self, instrument, timestamp):
        return super().is_open(instrument, timestamp) and not any(
            start <= timestamp < end for start, end, _ in self.closures
        )


def _utc(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Timestamps must be timezone-aware")
    timestamp = timestamp.astimezone(UTC)
    if timestamp.second or timestamp.microsecond:
        raise ValueError("Closure timestamps must align to whole minutes")
    return timestamp


def read_snapshot(database_url: str, *, max_rows: int = 2_000_000,
                  instrument: InstrumentCode = InstrumentCode.EURUSD,
                  start: datetime | None = None, end: datetime | None = None):
    """One bounded, repeatable-read snapshot; never connect to the default DB."""
    if max_rows <= 0:
        raise ValueError("max_rows must be positive")
    url = validate_test_database_url(database_url)
    engine = create_engine(
        url,
        connect_args={"connect_timeout": 10,
                      "options": "-c default_transaction_read_only=on -c statement_timeout=30000"},
        isolation_level="REPEATABLE READ",
    )
    table = OhlcvCandleRecord.__table__
    scope = [table.c.instrument_symbol == instrument.value, table.c.timeframe_minutes == 1]
    if start is not None:
        scope.append(table.c.opened_at >= start)
    if end is not None:
        scope.append(table.c.opened_at < end)
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            now = connection.scalar(text("SELECT CURRENT_TIMESTAMP"))
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            inventory = [dict(row) for row in connection.execute(
                select(table.c.source_provider, table.c.price_type,
                       func.count().label("candle_count"),
                       func.min(table.c.opened_at).label("first_timestamp"),
                       func.max(table.c.opened_at).label("last_timestamp"))
                .where(*scope).group_by(table.c.source_provider, table.c.price_type)
            ).mappings()]
            rows = [dict(row) for row in connection.execute(
                select(table).where(*scope, table.c.source_provider == CTRADER_PARQUET_PROVIDER,
                                    table.c.price_type == PriceType.UNKNOWN.value)
                .order_by(table.c.opened_at, table.c.id).limit(max_rows + 1)
            ).mappings()]
            if len(rows) > max_rows:
                raise ValueError("Snapshot exceeds max_rows; export a reviewed narrower range")
        return rows, {"as_of": now, "schema_revision": revision, "inventory": inventory,
                      "endpoint": f"{url.host}:15432", "read_only": True}
    finally:
        engine.dispose()


def analyze(rows: list[dict], *, as_of: datetime, calendar: MarketCalendar | None = None,
            instrument: InstrumentCode = InstrumentCode.EURUSD):
    """Audit raw rows before model validation so corrupt OHLC is reportable."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    calendar = calendar or AuditCalendar()
    observations = [SimpleNamespace(**{
        **row, "instrument": InstrumentCode(row.get("instrument_symbol", row.get(
            "instrument", instrument.value))), "timeframe": Timeframe.M1,
    }) for row in rows]
    quality = check_candle_quality(observations, instrument, 1, calendar=calendar)
    report = quality.as_dict()
    report["expected_closure_minutes"] = sum(g.expected_closure_count for g in quality.gaps)
    report["calendar"] = {
        "definition": ("Broker symbol schedule and dated holidays" if hasattr(calendar, "symbols")
                       else f"Configured {instrument.value} schedule (DST-aware)"),
        "fingerprint": calendar.fingerprint,
        "confirmed_closures": [
            {"start": start, "end": end, "reason": reason}
            for start, end, reason in getattr(calendar, "closures", [])
        ],
        "limitation": "Unconfirmed broker holidays/maintenance remain unresolved missing minutes",
    }
    invalid, non_positive, unfinished = [], 0, 0
    candles = []
    for row in rows:
        try:
            candle = Candle.model_validate({**row, "instrument": row.get(
                "instrument_symbol", row.get("instrument", instrument.value)), "timeframe": 1})
            if candle.instrument != instrument:
                raise ValueError("Mixed instrument input")
            if any(value <= 0 for value in (candle.open, candle.high, candle.low, candle.close)):
                non_positive += 1
                continue
            if candle.opened_at + MINUTE > as_of:
                unfinished += 1
                continue
            candles.append(candle)
        except ValueError:
            invalid.append(str(row["opened_at"]))
    report["invalid_row_count"] = len(invalid)
    report["invalid_row_timestamps"] = invalid
    report["non_positive_price_count"] = non_positive
    report["unfinalized_or_future_count"] = unfinished
    report["ohlc_inconsistency_count"] = sum(i.code == "invalid_ohlc" for i in quality.issues)
    report["freshness"] = None
    if quality.coverage_end:
        next_open = quality.coverage_end + MINUTE
        cutoff = as_of.astimezone(UTC).replace(second=0, microsecond=0)
        missing = max(0, int((cutoff - next_open) // MINUTE))
        open_minutes, closed_minutes = calendar.classify_missing_timestamps(
            instrument, next_open, missing, 1
        )
        report["freshness"] = {
            "latest_close_age_minutes": (as_of - next_open).total_seconds() / 60,
            "missing_completed_trading_minutes_after_latest": open_minutes,
            "closed_minutes_after_latest": closed_minutes,
            "stale_over_10_trading_minutes": open_minutes > 10,
        }
    if quality.coverage_start and quality.coverage_end:
        total = int((quality.coverage_end - quality.coverage_start) // MINUTE) + 1
        expected, _ = calendar.classify_missing_timestamps(
            instrument, quality.coverage_start, total, 1
        )
        report["expected_trading_minutes_in_coverage"] = expected
        observed_open = len({r["opened_at"] for r in rows
                             if calendar.is_open(instrument, r["opened_at"])})
        report["trading_minute_coverage_fraction"] = observed_open / expected if expected else None
    report["dataset_blocked_by_invalid_data"] = bool(
        invalid or non_positive or quality.duplicate_count or quality.out_of_order_count
        or any(issue.severity == "error" for issue in quality.issues)
    )
    return report, candles


def export_analysis(rows, snapshot, output: Path, *, calendar=None,
                    instrument: InstrumentCode = InstrumentCode.EURUSD):
    report, candles = analyze(rows, as_of=snapshot["as_of"], calendar=calendar,
                              instrument=instrument)
    report["snapshot"] = snapshot
    report["dataset"] = None
    output.mkdir(parents=True, exist_ok=False)
    if not report["dataset_blocked_by_invalid_data"]:
        try:
            dataset = build_ml_dataset(
                candles, instrument, Timeframe.M1,
                source=f"{CTRADER_PARQUET_PROVIDER}:{instrument.value}:unknown:isolated-test-15432",
                feature_config=FeatureConfig(include_trend_indicators=True),
                reset_on_gaps=True,
            )
        except ValueError as error:
            report["dataset_unavailable_reason"] = str(error)
        else:
            ParquetFeatureStore().write(dataset, output / "dataset")
            report["dataset"] = {
                "row_count": len(dataset.features),
                "feature_count": len(dataset.features.columns),
                "features": list(dataset.features.columns),
                "split_rows": {name: len(dataset.features.iloc[getattr(dataset.splits, name)])
                               for name in ("train", "validation", "test")},
                "fingerprint": dataset.manifest.dataset_fingerprint,
                "gap_policy": (
                    "Reset indicators and discard crossing targets at every absent minute"
                ),
            }
    report["sufficiency"] = (
        "Suitable for pipeline and small chronological ML smoke experiments only; "
        "insufficient evidence for robust model selection or out-of-period performance. "
        "Collect multiple months across market conditions before meaningful evaluation."
        if report["dataset"] else "Insufficient clean continuous data for initial ML experiments"
    )
    (output / "quality.json").write_text(json.dumps(report, indent=2, default=str) + "\n",
                                        encoding="utf-8")
    (output / "quality.md").write_text(render_report(report), encoding="utf-8")
    return report


def render_report(report):
    freshness = report["freshness"] or {}
    return (
        f"# Market data quality — {report['instrument']} M1\n\n"
        f"Snapshot (UTC): {report['snapshot']['as_of']}. Read-only test port 15432.\n\n"
        f"Candles: {report['candle_count']}; {report['coverage_start']} through "
        f"{report['coverage_end']}. Duplicates: {report['duplicate_count']}; "
        f"OHLC inconsistencies: {report['ohlc_inconsistency_count']}; "
        f"invalid rows: {report['invalid_row_count']}.\n\n"
        f"Internal absent minutes: {report['missing_candle_count']}; "
        f"scheduled closures: {report['expected_closure_minutes']}; "
        f"unresolved trading minutes: {report['unexpected_gap_count']}. "
        f"Trading-minute coverage: {report.get('trading_minute_coverage_fraction')}.\n\n"
        f"Freshness: {json.dumps(freshness)}\n\n"
        f"Dataset: {json.dumps(report['dataset'])}\n\n"
        "Features are available at candle close; no imputation or scaling fitted on future data. "
        "Targets are stored separately; five-bar purge gaps separate chronological splits. "
        "Indicator state and five-minute labels never cross absent minutes, including closures. "
        "RSI/ATR use the existing recursive Wilder-style EWM seed, not an SMA seed. "
        "cTrader volume is a raw protocol integer, not exchange volume.\n\n"
        f"{report['sufficiency']}\n\n"
        "Full gap details, closure assumptions and feature definitions are in quality.json "
        "and dataset/manifest.json. No model trained or history backfilled.\n"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="Use a new directory for each immutable snapshot")
    parser.add_argument("--closures", type=Path,
                        help="JSON array of confirmed UTC start/end/reason closure intervals")
    parser.add_argument("--max-rows", type=int, default=2_000_000)
    parser.add_argument("--instrument", choices=[i.value for i in InstrumentCode], default="EURUSD")
    parser.add_argument("--symbols", type=Path, help="Verified multi-asset symbols.json")
    parser.add_argument("--start", type=_utc, help="Optional inclusive UTC minute")
    parser.add_argument("--end", type=_utc, help="Optional exclusive UTC minute")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Output already exists; choose a new snapshot directory")
    value = os.environ.get("PHASE4A_DATABASE_URL")
    if not value:
        parser.error("Set PHASE4A_DATABASE_URL explicitly; application DATABASE_URL is never used")
    instrument = InstrumentCode(args.instrument)
    if instrument != InstrumentCode.EURUSD and not args.symbols:
        parser.error("Additional instruments require verified --symbols broker schedules")
    if args.symbols:
        from trading.ctrader.calendar import BrokerCalendar

        symbols = json.loads(args.symbols.read_text())["symbols"]
        calendar = BrokerCalendar({instrument.value: symbols[instrument.value]})
        if args.closures:
            parser.error("Use broker holidays from --symbols or --closures, not both")
    else:
        calendar = AuditCalendar(json.loads(args.closures.read_text()) if args.closures else None)
    rows, snapshot = read_snapshot(value, max_rows=args.max_rows, instrument=instrument,
                                   start=args.start, end=args.end)
    report = export_analysis(rows, snapshot, args.output, calendar=calendar, instrument=instrument)
    print(json.dumps({key: report[key] for key in (
        "candle_count", "coverage_start", "coverage_end", "unexpected_gap_count", "dataset"
    )}, indent=2, default=str))
    return 0 if report["dataset"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
