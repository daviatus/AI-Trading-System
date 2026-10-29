"""Offline M1 preparation with bounded causal state and transactional chunk publication.

Only checkpoint-referenced chunks are committed. Targets remain separate from
features. This adapter does not fit models, replay portfolios or contact services.
"""

import hashlib
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from trading.research.phase6.controller import canonical, code_manifest, controller_lock, digest
from trading.research.phase6.features import (
    DEVELOPMENT_END,
    MINUTE,
    FeatureSpec,
    _segment_features,
    calendar_open,
    utc_index,
    validate_candles,
)
from trading.research.phase6.labels import LabelSpec, make_labels

VERSION = 1
MAX_BATCH_ROWS = 8192
MAX_STATE_ROWS = 8192
COLUMNS = ["open", "high", "low", "close", "volume"]


def _hash(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _sync_directory(path):
    # Windows exposes no portable directory fsync. Every file is flushed before
    # same-volume rename; NTFS/os.replace supplies atomic namespace replacement.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _write(path, value):
    with path.open("xb") as handle:
        handle.write((canonical(value) + "\n").encode())
        handle.flush()
        os.fsync(handle.fileno())


def _checkpoint(path, payload):
    temporary = path.with_name(f".checkpoint-{uuid.uuid4().hex}.partial")
    _write(temporary, {"payload": payload, "sha256": _hash(payload)})
    os.replace(temporary, path)
    _sync_directory(path.parent)


def _read_checkpoint(output):
    try:
        envelope = json.loads((output / "stream-checkpoint.json").read_text("utf-8"))
        value = envelope["payload"]
        if envelope["sha256"] != _hash(value) or value["version"] != VERSION:
            raise ValueError
        if type(value["generation"]) is not int or value["generation"] < -1:
            raise ValueError
        manifest = json.loads((output / "stream-manifest.json").read_text("utf-8"))
        if _hash(manifest) != value["identity"]:
            raise ValueError
        return value
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError(
            "Corrupt or unsupported streaming checkpoint; evidence preserved"
        ) from None


def _pack(frame):
    if frame is None:
        return None
    values = frame.to_numpy(float)
    return {
        "index": frame.index.asi8.tolist(),
        "index_name": frame.index.name,
        "columns": list(frame.columns),
        "dtypes": [str(dtype) for dtype in frame.dtypes],
        "data": [[float(v) if np.isfinite(v) else None for v in row] for row in values],
    }


def _unpack(value):
    if value is None:
        return None
    frame = pd.DataFrame(
        value["data"],
        columns=value["columns"],
        index=pd.to_datetime(value["index"], utc=True),
        dtype=float,
    )
    frame.index.name = value["index_name"]
    return frame.astype(dict(zip(value["columns"], value["dtypes"], strict=True)))


class CausalFeatures:
    """Finite rolling context plus exact recursive EMA seeds; resets on real gaps."""

    def __init__(self, spec, metadata, state=None):
        self.spec, self.metadata = spec, metadata
        self.keep = max(
            max(spec.return_lags),
            spec.volatility_window + spec.regime_window,
            spec.atr_window + 1,
            spec.rsi_window + 1,
            6,
        )
        if max(self.keep, spec.warmup, spec.slow_span) > MAX_STATE_ROWS:
            raise ValueError("Streaming feature periods exceed the state budget")
        self.history = _unpack(state["history"]) if state else None
        self.age = state["age"] if state else 0

    def state(self):
        return {"history": _pack(self.history), "age": self.age}

    def transform(self, bars):
        index = validate_candles(bars, max_rows=MAX_BATCH_ROWS)
        spec = self.spec
        if spec.reliable_volume and not (
            np.isfinite(bars.volume.to_numpy(float)).all() and bars.volume.ge(0).all()
        ):
            raise ValueError("Reliable-volume features require finite nonnegative volume")
        opened = (
            np.ones(len(bars), bool)
            if self.metadata is None
            else calendar_open(index, self.metadata)
        )
        template = _segment_features(bars.iloc[:1], spec)
        result = pd.DataFrame(np.nan, index=index, columns=template.columns)
        # Split on session exclusions and missing timestamps, including chunk edges.
        breaks = np.r_[True, np.diff(index.asi8) != MINUTE.value]
        breaks[1:] |= opened[1:] != opened[:-1]
        bounds = np.r_[np.flatnonzero(breaks), len(bars)]
        for left, right in zip(bounds[:-1], bounds[1:], strict=True):
            if not opened[left]:
                self.history, self.age = None, 0
                continue
            part = bars.iloc[left:right]
            history = self.history
            if history is not None and history.index[-1] + MINUTE != part.index[0]:
                history, self.age = None, 0
            prefix = 0 if history is None else len(history)
            combined = part.copy() if history is None else pd.concat([history[COLUMNS], part])
            averages = []
            for name, span in (("_fast", spec.fast_span), ("_slow", spec.slow_span)):
                prices = part.close.reset_index(drop=True)
                if history is not None:
                    prices = pd.concat(
                        [pd.Series([history[name].iloc[-1]]), prices], ignore_index=True
                    )
                raw = prices.ewm(span=span, adjust=False).mean().iloc[-len(part) :].to_numpy()
                prior = np.array([]) if history is None else history[name].to_numpy()
                combined[name] = np.r_[prior, raw]
                age = np.arange(self.age - prefix + 1, self.age + len(part) + 1)
                averages.append(combined[name].where(age >= span))
            frame = _segment_features(
                combined,
                spec,
                averages=averages,
                warmup_rows=max(0, spec.warmup - 1 - (self.age - prefix)),
            )
            result.loc[part.index] = frame.iloc[prefix:].to_numpy()
            self.age += len(part)
            self.history = combined.iloc[-self.keep :].copy()
        result = result.replace([np.inf, -np.inf], np.nan).astype("float32")
        result.index = index + MINUTE
        result.index.name = "decision_time"
        return result


def _selection(source, asset, start, end):
    if asset not in {"EURUSD", "XAUUSD", "WTI"}:
        raise ValueError("Unsupported research instrument")
    utc_index(pd.DatetimeIndex([start, end]))
    if start >= end or end > DEVELOPMENT_END:
        raise ValueError(
            "Ordered development interval required; 2026 is excluded before source access"
        )
    if start.value % MINUTE.value or end.value % MINUTE.value:
        raise ValueError("Streaming boundaries must be whole UTC minutes")
    snapshot = json.loads((source / "snapshot.json").read_text("utf-8"))
    selected = []
    for relative, entry in sorted(snapshot["parts"].items()):
        if not relative.startswith(f"cache/{asset}/") or not relative.endswith(".parquet"):
            continue
        if not re.fullmatch(rf"cache/{asset}/\d{{4}}-\d{{2}}\.parquet", relative):
            raise ValueError("Invalid monthly partition path")
        month = pd.Timestamp(Path(relative).stem + "-01", tz="UTC")
        if month >= end or month + pd.offsets.MonthBegin() <= start:
            continue  # Never stat, hash or parse holdout/unselected partitions.
        path = (source / relative).resolve()
        if not path.is_relative_to(source):
            raise ValueError("Snapshot path traversal")
        if digest(path) != entry["sha256"]:
            raise ValueError("Development partition differs from frozen provenance")
        selected.append({"path": relative, "sha256": entry["sha256"]})
    if not selected:
        raise ValueError("No registered development partitions")
    return selected


def _batches(source, selected, start, end, batch_rows, cursor, guard):
    for part_number in range(cursor["part"], len(selected)):
        path = source / selected[part_number]["path"]
        month = pd.Timestamp(path.stem + "-01", tz="UTC")
        with pq.ParquetFile(path) as parquet:
            # Projection and single-threaded batches prevent whole-month materialization.
            names = parquet.schema_arrow.names
            if not {"opened_at", "open", "high", "low", "close"}.issubset(names):
                raise ValueError("Monthly partition requires explicit timestamps and OHLC columns")
            columns = ["opened_at", *[c for c in COLUMNS if c in names]]
            skip = cursor["batch"] if part_number == cursor["part"] else 0
            for number, batch in enumerate(
                parquet.iter_batches(
                    batch_size=batch_rows,
                    columns=columns,
                    use_threads=False,
                )
            ):
                if number < skip:
                    continue
                guard()
                frame = batch.to_pandas().set_index("opened_at")
                stamps = utc_index(frame.index, name="explicit source timezone")
                if ((stamps < month) | (stamps >= month + pd.offsets.MonthBegin())).any():
                    raise ValueError("Candle outside its declared monthly partition")
                frame.index = stamps
                validate_candles(frame, max_rows=batch_rows)
                if "volume" not in frame:
                    frame["volume"] = np.nan
                frame = frame.loc[(stamps >= start) & (stamps < end), COLUMNS].astype("float64")
                if np.isinf(frame.volume.to_numpy()).any():
                    raise ValueError("Infinite volume is invalid; missing volume must be null")
                yield frame, {"part": part_number, "batch": number + 1}


def _verify_chunk(output, generation, previous, identity):
    directory = output / "chunks" / f"{generation:08d}"
    try:
        receipt = json.loads((directory / "receipt.json").read_text("utf-8"))
        if (
            receipt["generation"] != generation
            or receipt["previous"] != previous
            or receipt["identity"] != identity
            or set(receipt["files"])
            != {"bars.parquet", "features.parquet", "labels.parquet", "state.json"}
        ):
            raise ValueError
        for name, checksum in receipt["files"].items():
            if digest(directory / name) != checksum:
                raise ValueError
        return digest(directory / "receipt.json")
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError(
            "Committed streaming chunk is missing or corrupt; evidence preserved"
        ) from None


def _verify_chain(output, checkpoint, guard):
    previous = None
    for generation in range(checkpoint["generation"] + 1):
        guard()
        previous = _verify_chunk(output, generation, previous, checkpoint["identity"])
    if previous != checkpoint["receipt"]:
        raise ValueError("Streaming checkpoint receipt chain mismatch")


def _publish(output, checkpoint, state, bars, features, labels):
    generation = checkpoint["generation"] + 1
    chunks = output / "chunks"
    chunks.mkdir(exist_ok=True)
    staging = chunks / f".pending-{uuid.uuid4().hex}"
    staging.mkdir()
    for name, frame in (("bars", bars), ("features", features), ("labels", labels)):
        path = staging / f"{name}.parquet"
        frame.to_parquet(path, index=True, compression="zstd")
        with path.open("r+b") as handle:
            os.fsync(handle.fileno())
    _write(staging / "state.json", state)
    receipt = {
        "generation": generation,
        "identity": checkpoint["identity"],
        "previous": checkpoint["receipt"],
        "rows": len(bars),
        "files": {p.name: digest(p) for p in sorted(staging.iterdir())},
    }
    _write(staging / "receipt.json", receipt)
    _sync_directory(staging)
    target = chunks / f"{generation:08d}"
    if target.exists():
        # Crash after directory rename but before checkpoint replacement: replay
        # from the old state and accept only byte-identical deterministic evidence.
        actual = _verify_chunk(output, generation, checkpoint["receipt"], checkpoint["identity"])
        if actual != digest(staging / "receipt.json"):
            raise ValueError("Uncommitted streaming chunk differs on replay; evidence preserved")
    else:
        os.rename(staging, target)
        _sync_directory(chunks)
    updated = {**checkpoint, "generation": generation, "receipt": digest(target / "receipt.json")}
    _checkpoint(output / "stream-checkpoint.json", updated)
    return updated


@dataclass(frozen=True)
class PreparedChunk:
    """Separate causal predictors and future targets; never join targets into signals."""

    bars: pd.DataFrame
    features: pd.DataFrame
    labels: pd.DataFrame


def iter_prepared(output):
    """Yield only committed, checksum-verified chunks, one at a time in time order."""
    output = Path(output).resolve()
    checkpoint = _read_checkpoint(output)
    previous = None
    for generation in range(checkpoint["generation"] + 1):
        previous = _verify_chunk(output, generation, previous, checkpoint["identity"])
        if generation == checkpoint["generation"] and previous != checkpoint["receipt"]:
            raise ValueError("Streaming checkpoint receipt chain mismatch")
        directory = output / "chunks" / f"{generation:08d}"
        yield PreparedChunk(
            *[
                pd.read_parquet(directory / f"{name}.parquet")
                for name in ("bars", "features", "labels")
            ]
        )


def prepare_stream(
    source,
    output,
    asset,
    start,
    end,
    *,
    feature_spec=None,
    label_spec=None,
    metadata=None,
    batch_rows=MAX_BATCH_ROWS,
    guard=None,
    max_chunks=None,
):
    """Prepare/resume one asset over a half-open UTC development interval.

    max_chunks is a bounded invocation budget (including the terminal flush).
    Reuse the identical arguments to resume. Missing unregistered months are gaps;
    registered missing/changed files fail closed. No source or evidence is deleted.
    """
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output or source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError("Research source/output overlap forbidden")
    if type(batch_rows) is not int or not 1 <= batch_rows <= MAX_BATCH_ROWS:
        raise ValueError("Streaming batch row budget must be between 1 and 8192")
    if max_chunks is not None and (type(max_chunks) is not int or max_chunks < 1):
        raise ValueError("Invocation chunk budget must be positive")
    guard = guard or (lambda: None)
    guard()
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    spec = FeatureSpec() if feature_spec is None else feature_spec
    label = LabelSpec() if label_spec is None else label_spec
    engine = CausalFeatures(spec, metadata)
    selected = _selection(source, asset, start, end)
    manifest = {
        "version": VERSION,
        "source": str(source),
        "asset": asset,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "parts": selected,
        "features": asdict(spec),
        "labels": asdict(label),
        "metadata": metadata,
        "batch_rows": batch_rows,
        "code": code_manifest(),
        "libraries": {
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "arrow": pa.__version__,
        },
    }
    identity = _hash(manifest)
    with controller_lock(output, lock_name=".stream.lock"):
        path = output / "stream-checkpoint.json"
        manifest_path = output / "stream-manifest.json"
        if path.exists():
            checkpoint = _read_checkpoint(output)
            if checkpoint["identity"] != identity:
                raise ValueError("Streaming inputs/config/code changed; use a new output version")
        else:
            # A missing checkpoint beside existing artifacts is not a fresh run.
            if (output / "chunks").exists():
                raise ValueError("Streaming checkpoint missing beside evidence; refusing reset")
            checkpoint = {
                "version": VERSION,
                "identity": identity,
                "generation": -1,
                "receipt": None,
            }
            if manifest_path.exists():
                if manifest_path.read_text("utf-8") != canonical(manifest) + "\n":
                    raise ValueError("Streaming manifest differs; evidence preserved")
            else:
                temporary = output / f".manifest-{uuid.uuid4().hex}.partial"
                _write(temporary, manifest)
                os.rename(temporary, manifest_path)
                _sync_directory(output)
            _checkpoint(path, checkpoint)
        _verify_chain(output, checkpoint, guard)
        state = {
            "cursor": {"part": 0, "batch": 0},
            "features": engine.state(),
            "pending_bars": None,
            "pending_features": None,
            "last_open": None,
            "rows_read": 0,
            "rows_written": 0,
            "gap_minutes": 0,
            "complete": False,
        }
        if checkpoint["generation"] >= 0:
            state = json.loads(
                (output / "chunks" / f"{checkpoint['generation']:08d}" / "state.json").read_text(
                    "utf-8"
                )
            )
        if state["complete"]:
            return state
        engine = CausalFeatures(spec, metadata, state["features"])
        pending, pending_features = (
            _unpack(state["pending_bars"]),
            _unpack(state["pending_features"]),
        )
        count = 0

        def commit(bars, features, cursor, final=False):
            nonlocal checkpoint, pending, pending_features, state, count
            if pending is not None:
                bars = pd.concat([pending, bars]) if len(bars) else pending
                features = (
                    pd.concat([pending_features, features]) if len(features) else pending_features
                )
            if not len(bars):
                return
            horizon = label.horizon_minutes
            sigma_name = (
                f"atr_fraction_{spec.atr_window}"
                if label.kind == "barrier"
                else f"volatility_{spec.volatility_window}"
            )
            labels = make_labels(
                bars,
                replace(label, max_rows=batch_rows + horizon),
                volatility=features[sigma_name],
                metadata=metadata,
            )
            ready = len(bars) if final else max(0, len(bars) - horizon)
            pending = None if final else bars.iloc[ready:].copy()
            pending_features = None if final else features.iloc[ready:].copy()
            state.update(
                cursor=cursor,
                features=engine.state(),
                pending_bars=_pack(pending),
                pending_features=_pack(pending_features),
                complete=final,
                rows_written=state["rows_written"] + ready,
            )
            checkpoint = _publish(
                output,
                checkpoint,
                state,
                bars.iloc[:ready],
                features.iloc[:ready],
                labels.iloc[:ready],
            )
            count += 1

        for bars, cursor in _batches(
            source, selected, start, end, batch_rows, state["cursor"], guard
        ):
            if not len(bars):
                continue
            index = validate_candles(bars, max_rows=batch_rows)
            prior = state["last_open"]
            if prior is not None and index[0].value <= prior:
                raise ValueError("Candle timestamps must increase across chunks and months")
            gaps = np.diff(index.asi8) // MINUTE.value - 1
            state["gap_minutes"] += int(gaps.sum())
            if prior is not None:
                state["gap_minutes"] += int((index[0].value - prior) // MINUTE.value - 1)
            state["last_open"] = int(index[-1].value)
            state["rows_read"] += len(bars)
            features = engine.transform(bars)
            commit(bars, features, cursor)
            if max_chunks is not None and count >= max_chunks:
                return state
        if state["rows_read"] == 0:
            raise ValueError("No development candles available in requested interval")
        guard()
        empty = pd.DataFrame()
        commit(empty, empty, {"part": len(selected), "batch": 0}, final=True)
        return state
