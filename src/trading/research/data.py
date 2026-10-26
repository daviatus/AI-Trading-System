"""Bounded isolated-database snapshots, local quality audits and causal features."""

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from trading.data.dataset import FeatureConfig, _create_features_and_targets
from trading.research.safety import resources

ROOT = Path(__file__).resolve().parents[3]
DATABASE = "ctrader_demo_fe1a7194a87c"
USER = "ctrader_demo_test"
COLLECTOR = "ai-trading-ctrader-worker-ctrader-worker-1"
MINUTE = 60_000_000_000


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, default=str, allow_nan=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def isolated_access():
    # No OAuth file or token is read, and no secret is put in argv or output.
    code = (
        "import json,os;from pathlib import Path;"
        "assert os.environ['TRADING_MODE']=='DEMO';"
        "assert os.environ['LIVE_EXECUTION_ENABLED']=='false';"
        "print(json.dumps({'url':Path(os.environ['DATABASE_URL_FILE']).read_text().strip(),"
        "'identity':[os.environ['CTRADER_EXPECTED_DATABASE'],"
        "os.environ['CTRADER_EXPECTED_USER'],os.environ['CTRADER_EXPECTED_CLUSTER_ID']]}))"
    )
    try:
        p = subprocess.run(
            ["docker", "exec", COLLECTOR, "python", "-c", code],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        access = json.loads(p.stdout)
        url = make_url(access["url"])
        if (
            url.drivername != "postgresql+psycopg"
            or url.host != "host.docker.internal"
            or url.port != 15432
            or url.database != DATABASE
            or url.username != USER
            or url.query
            or access["identity"][:2] != [DATABASE, USER]
        ):
            raise ValueError("Unexpected database target")
        return url.set(host="127.0.0.1"), access["identity"]
    except Exception:
        raise RuntimeError(
            "Isolated read-only database access unavailable; secrets suppressed"
        ) from None


def verify(connection, identity):
    if connection.scalar(text("SHOW transaction_read_only")) != "on":
        raise RuntimeError("Read-only database transaction required")
    actual = connection.execute(
        text(
            "SELECT current_database(),current_user,system_identifier::text "
            "FROM pg_control_system()"
        )
    ).one()
    if list(actual) != identity:
        raise RuntimeError("Isolated database identity mismatch")
    if connection.scalar(text("SELECT pg_has_role(current_user,'pg_read_all_data','MEMBER')")):
        connection.execute(text("SET LOCAL ROLE pg_read_all_data"))


def snapshot(config):
    output = ROOT / config["output"]
    resources(config)
    url, identity = isolated_access()
    engine = create_engine(
        url,
        connect_args={
            "connect_timeout": 10,
            "options": "-c default_transaction_read_only=on "
            "-c statement_timeout=20000 -c lock_timeout=2000",
        },
    )
    metadata = json.loads((ROOT / "data/phase4c/symbols.json").read_text())["symbols"]
    public = {
        a: {k: metadata[a][k] for k in ("broker_symbol_name", "digits", "specifications")}
        for a in config["assets"]
    }
    write_json(output / "contracts.json", public)
    boundaries = pd.date_range(
        pd.Timestamp(config["start"]).normalize().replace(day=1),
        pd.Timestamp(config["end"]) + pd.offsets.MonthBegin(),
        freq="MS",
    )
    manifest_path = output / "snapshot.json"
    manifest = (
        json.loads(manifest_path.read_text())
        if manifest_path.exists()
        else {
            "start": config["start"],
            "end": config["end"],
            "source": "ctrader_open_api_demo",
            "price_type": "unknown",
            "read_only": True,
            "parts": {},
            "latest_available": {},
        }
    )
    if (manifest["start"], manifest["end"]) != (config["start"], config["end"]):
        raise ValueError("Cache boundaries differ; use a new output directory")
    try:
        for asset in config["assets"]:
            with engine.connect() as c:
                verify(c, identity)
                latest = c.scalar(
                    text(
                        "SELECT opened_at FROM ohlcv_candles WHERE instrument_symbol=:asset "
                        "AND timeframe_minutes=1 AND source_provider='ctrader_open_api_demo' "
                        "AND price_type='unknown' ORDER BY opened_at DESC LIMIT 1"
                    ),
                    {"asset": asset},
                )
                manifest["latest_available"][asset] = str(latest)
                c.rollback()
            for left, right in zip(boundaries[:-1], boundaries[1:], strict=True):
                start, end = (
                    max(left, pd.Timestamp(config["start"])),
                    min(right, pd.Timestamp(config["end"])),
                )
                if start >= end:
                    continue
                resources(config)
                relative = f"cache/{asset}/{left:%Y-%m}.parquet"
                path = output / relative
                if relative in manifest["parts"]:
                    if (
                        hashlib.sha256(path.read_bytes()).hexdigest()
                        != manifest["parts"][relative]["sha256"]
                    ):
                        raise RuntimeError("Cached data checksum mismatch")
                    continue
                params = {
                    "asset": asset,
                    "start": start.to_pydatetime(),
                    "end": end.to_pydatetime(),
                }
                sql = (
                    "SELECT opened_at,open::double precision,high::double precision,"
                    "low::double precision,close::double precision,volume::double precision "
                    "FROM ohlcv_candles WHERE instrument_symbol=:asset AND timeframe_minutes=1 "
                    "AND source_provider='ctrader_open_api_demo' AND price_type='unknown' "
                    "AND opened_at>=:start AND opened_at<:end ORDER BY opened_at LIMIT 50001"
                )
                with engine.connect() as c:
                    verify(c, identity)
                    plan = c.scalar(text("EXPLAIN (FORMAT JSON) " + sql), params)
                    if "Seq Scan" in json.dumps(plan):
                        raise RuntimeError("Refusing unindexed candle snapshot query")
                    frame = pd.DataFrame(
                        c.execute(text(sql), params).mappings().all(),
                        columns=["opened_at", "open", "high", "low", "close", "volume"],
                    )
                    c.rollback()
                if len(frame) > 50000:
                    raise RuntimeError("Monthly snapshot row limit exceeded")
                frame["opened_at"] = pd.to_datetime(frame["opened_at"], utc=True)
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tmp")
                frame.to_parquet(temporary, index=False, compression="zstd")
                os.replace(temporary, path)
                manifest["parts"][relative] = {
                    "rows": len(frame),
                    "start": str(start),
                    "end": str(end),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                write_json(manifest_path, manifest)
                print(f"SNAPSHOT {asset} {left:%Y-%m} rows={len(frame)}", flush=True)
                time.sleep(0.05)
    finally:
        engine.dispose()
    manifest["complete"] = True
    write_json(manifest_path, manifest)
    return manifest


def session_open(index, metadata):
    """Vectorized equivalent of Phase 4C BrokerCalendar's exposed schedule."""
    specs = metadata["specifications"]
    local = index.tz_convert(specs["scheduleTimeZone"])
    second = ((local.dayofweek + 1) % 7) * 86400 + local.hour * 3600 + local.minute * 60
    opened = np.zeros(len(index), dtype=bool)
    for window in specs["schedule"]:
        opened |= (second >= window["startSecond"]) & (second < window["endSecond"])
    for holiday in specs.get("holiday", []):
        day = pd.Timestamp("1970-01-01") + pd.Timedelta(days=holiday["holidayDate"])
        stamps = index.tz_convert(holiday["scheduleTimeZone"])
        matching = (
            (stamps.month == day.month) & (stamps.day == day.day)
            if holiday["isRecurring"]
            else (stamps.date == day.date())
        )
        start, end = holiday.get("startSecond", 0), holiday.get("endSecond", 0)
        seconds = stamps.hour * 3600 + stamps.minute * 60
        closed = (
            np.ones(len(index), bool)
            if start == end == 0
            else ((seconds >= start) & (seconds < end))
        )
        opened &= ~(matching & closed)
    return np.asarray(opened)


def load_bars(output, asset):
    files = sorted((Path(output) / "cache" / asset).glob("*.parquet"))
    if not files:
        raise ValueError("Snapshot cache is empty")
    frame = pd.concat([pd.read_parquet(path) for path in files], ignore_index=True)
    frame = frame.set_index("opened_at").sort_index(kind="stable")
    if frame.index.has_duplicates:
        raise ValueError("Duplicate timestamps must be resolved before research")
    return frame


def valid_ohlc(bars):
    p = bars[["open", "high", "low", "close"]]
    return (
        np.isfinite(p).all(axis=1)
        & p.gt(0).all(axis=1)
        & bars.high.ge(p.max(axis=1))
        & bars.low.le(p.min(axis=1))
    )


def audit(bars, metadata, config):
    grid = pd.date_range(config["start"], config["end"], freq="min", inclusive="left")
    opened = session_open(grid, metadata)
    present = grid.isin(bars.index)
    absent_open = opened & ~present
    missing_indices = np.flatnonzero(absent_open)
    ranges = []
    if len(missing_indices):
        groups = np.split(missing_indices, np.flatnonzero(np.diff(missing_indices) > 1) + 1)
        ranges = [
            {
                "start": str(grid[g[0]]),
                "end_exclusive": str(grid[g[-1]] + pd.Timedelta(minutes=1)),
                "minutes": len(g),
            }
            for g in groups
        ]
    years = {}
    for year in sorted(set(grid.year)):
        mask = grid.year == year
        observed = bars.index.year == year
        years[str(year)] = {
            "candles": int(observed.sum()),
            "unresolved_minutes": int(absent_open[mask].sum()),
            "expected_open_minutes": int(opened[mask].sum()),
        }
    return {
        "candles": len(bars),
        "earliest": str(bars.index.min()),
        "latest_snapshot": str(bars.index.max()),
        "duplicate_timestamps": int(bars.index.duplicated().sum()),
        "invalid_ohlc": int((~valid_ohlc(bars)).sum()),
        "missing_volume": int(bars.volume.isna().sum()),
        "zero_volume": int(bars.volume.eq(0).sum()),
        "suspicious_one_minute_jumps_over_5pct": int(
            (
                bars.close.pct_change(fill_method=None).abs().gt(0.05)
                & bars.index.to_series().diff().eq(pd.Timedelta(minutes=1))
            ).sum()
        ),
        "outside_exposed_sessions": int((~session_open(bars.index, metadata)).sum()),
        "known_closed_absent_minutes": int((~opened & ~present).sum()),
        "unresolved_missing_minutes": int(absent_open.sum()),
        "missing_intervals": ranges,
        "by_year": years,
        "calendar_limitation": "Exposed current weekly sessions and dated holidays; historical "
        "schedule changes and unexposed holidays remain unresolved, never imputed",
    }


def features(bars, warmup=64):
    """Reuse Phase 4A indicators; reset all state after every absent/invalid bar."""
    bars = bars.loc[valid_ohlc(bars)]
    breaks = np.r_[True, np.diff(bars.index.asi8) != MINUTE]
    bounds = np.r_[np.flatnonzero(breaks), len(bars)]
    parts = []
    cfg = FeatureConfig(include_trend_indicators=True)
    for start, end in zip(bounds[:-1], bounds[1:], strict=True):
        if end - start < warmup:
            continue
        part = bars.iloc[start:end]
        f, _, _ = _create_features_and_targets(part, cfg)
        f = f.drop(columns=["ema_12", "ema_26", "atr_14", "volume_mean_20"])
        width = (part.high - part.low).replace(0, np.nan)
        f["body_fraction"] = (part.close - part.open) / part.close
        f["range_fraction"] = width / part.close
        f["close_location"] = ((part.close - part.low) / width).fillna(0.5)
        f["high_low_position_20"] = (part.close - part.low.rolling(20).min()) / (
            part.high.rolling(20).max() - part.low.rolling(20).min()
        ).replace(0, np.nan)
        f["momentum_10"] = part.close.pct_change(10, fill_method=None)
        f["return_previous_1"] = part.close.pct_change(fill_method=None).shift(1)
        f["volatility_ratio"] = (
            f["atr_fraction_14"]
            / f["atr_fraction_14"].ewm(span=120, min_periods=30, adjust=False).mean()
        )
        f.iloc[: warmup - 1] = np.nan
        parts.append(f)
    if not parts:
        raise ValueError("No complete causal feature segment")
    return pd.concat(parts).reindex(bars.index).replace([np.inf, -np.inf], np.nan).astype("float32")


def regimes(f):
    ratio = f.volatility_ratio
    trend = f.ema_trend_spread.abs() / f.atr_fraction_14
    return np.select(
        [ratio > 2.5, ratio > 1.5, ratio < 0.7, trend > 0.5],
        ["abnormal", "high_volatility", "low_volatility", "trending"],
        default="ranging",
    )
