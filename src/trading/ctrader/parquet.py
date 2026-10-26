import os
import tempfile
from datetime import datetime
from pathlib import Path

from trading.data.domain import Candle

CTRADER_PARQUET_PROVIDER = "ctrader_open_api_demo"


def persist_candles(
    candles: list[Candle],
    root: str | Path,
    start: datetime,
    end: datetime,
) -> list[tuple[Path, str]]:
    """Atomically publish a new cTrader Parquet dataset without overwriting an existing root."""
    if not candles:
        return []

    output_root = Path(root)
    if output_root.exists() or output_root.is_symlink():
        raise FileExistsError(f"Parquet output already exists: {output_root}")

    # PostgreSQL collectors import the provider constant without needing Arrow.
    from trading.data.parquet_store import export_partition

    output_root.parent.mkdir(parents=True, exist_ok=True)

    reference = f"demo:EURUSD:M1:[{start.isoformat()},{end.isoformat()})"
    with tempfile.TemporaryDirectory(
        dir=output_root.parent,
        prefix=f".{output_root.name}.",
    ) as temporary_directory:
        staging_root = Path(temporary_directory) / "dataset"
        staged_results = export_partition(
            ((candle, CTRADER_PARQUET_PROVIDER, reference) for candle in candles),
            staging_root,
        )
        os.replace(staging_root, output_root)
        return [
            (output_root / path.relative_to(staging_root), digest)
            for path, digest in staged_results
        ]
