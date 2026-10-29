"""Lightweight verification of sealed STRAT-2C evidence, without feature/DB imports.

The v1 envelope, receipt chain and every file hash are checked before use. This
module reads immutable preparation artifacts; it never repairs or writes them.
"""

import json

from trading.research.strat2d.journal import file_hash, logical_hash


def verify(source, guard):
    try:
        manifest = json.loads((source / "stream-manifest.json").read_text("utf-8"))
        envelope = json.loads((source / "stream-checkpoint.json").read_text("utf-8"))
        checkpoint = envelope["payload"]
        if (
            checkpoint["version"] != 1
            or type(checkpoint["generation"]) is not int
            or not 0 <= checkpoint["generation"] <= 2048
            or envelope["sha256"] != logical_hash(checkpoint)
            or checkpoint["identity"] != logical_hash(manifest)
        ):
            raise ValueError
        files = {
            name: file_hash(source / name)
            for name in ("stream-manifest.json", "stream-checkpoint.json")
        }
        previous = None
        for generation in range(checkpoint["generation"] + 1):
            guard()
            directory = source / "chunks" / f"{generation:08d}"
            if directory.is_symlink() or directory.is_junction():
                raise ValueError
            receipt = json.loads((directory / "receipt.json").read_text("utf-8"))
            if (
                receipt["generation"] != generation
                or receipt["previous"] != previous
                or receipt["identity"] != checkpoint["identity"]
                or set(receipt["files"])
                != {"bars.parquet", "features.parquet", "labels.parquet", "state.json"}
            ):
                raise ValueError
            for name, expected in receipt["files"].items():
                path = directory / name
                if path.is_symlink():
                    raise ValueError
                actual = file_hash(path)
                if actual != expected:
                    raise ValueError
                files[f"chunks/{generation:08d}/{name}"] = actual
            previous = file_hash(directory / "receipt.json")
            files[f"chunks/{generation:08d}/receipt.json"] = previous
        if previous != checkpoint["receipt"]:
            raise ValueError
        state = json.loads(
            (source / "chunks" / f"{checkpoint['generation']:08d}" / "state.json").read_text(
                "utf-8"
            )
        )
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("Prepared checkpoint/receipt corrupt; source preserved") from None
    if not state["complete"]:
        raise ValueError("Prepared source is incomplete")
    return {
        "files": files,
        "generation": checkpoint["generation"],
        "rows": state["rows_written"],
        "identity": checkpoint["identity"],
        "start": manifest["start"],
        "end": manifest["end"],
    }


def utc_index(index):
    import pandas as pd

    if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
        raise ValueError("Prepared timestamps require an explicit UTC DatetimeIndex")
    if str(index.tz) not in {"UTC", "UTC+00:00", "Etc/UTC", "GMT"} or index.hasnans:
        raise ValueError("Prepared timestamps must use UTC without missing values")
    return index.tz_convert("UTC").as_unit("ns")


def validate_candles(bars, max_rows):
    """Same OHLC/minute/order checks as Phase 6, without its feature import graph."""
    import numpy as np
    import pandas as pd

    index = utc_index(bars.index)
    if not 0 < len(index) <= max_rows:
        raise ValueError("Candle batch outside row budget")
    if index.has_duplicates or not index.is_monotonic_increasing:
        raise ValueError("Candle timestamps must strictly increase")
    if np.any(index.asi8 % 60_000_000_000) or index[-1] >= pd.Timestamp("2026-01-01T00:00:00Z"):
        raise ValueError("Nonminute timestamps or forbidden holdout")
    prices = bars[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if not (
        np.isfinite(prices).all()
        and (prices > 0).all()
        and (prices[:, 1] >= prices.max(axis=1)).all()
        and (prices[:, 2] <= prices.min(axis=1)).all()
    ):
        raise ValueError("Invalid OHLC")
