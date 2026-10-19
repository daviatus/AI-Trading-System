import json
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from trading.data.dataset import (
    DATASET_SCHEMA_VERSION,
    FEATURE_SCHEMA_VERSION,
    PreparedDataset,
)


class ParquetFeatureStore:
    """Read/write the existing split feature and target Parquet dataset format."""

    def write(self, dataset: PreparedDataset, directory: str | Path) -> None:
        dataset.write(directory)

    def read_features(
        self,
        directory: str | Path,
        *,
        columns: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        return self._read(directory, "features", columns)

    def read_targets(
        self,
        directory: str | Path,
        *,
        columns: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        return self._read(directory, "targets", columns)

    def _read(
        self,
        directory: str | Path,
        table_name: str,
        columns: Sequence[str] | None,
    ) -> pd.DataFrame:
        root = Path(directory)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("schema_version") != DATASET_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported feature dataset schema version: {manifest.get('schema_version')}"
            )
        manifest_feature_version = manifest.get("feature_schema_version", "1")
        if manifest_feature_version not in {"1", FEATURE_SCHEMA_VERSION}:
            raise ValueError(f"Unsupported feature schema version: {manifest_feature_version}")
        path = root / f"{table_name}.parquet"
        parquet = pq.ParquetFile(path)
        metadata = parquet.schema_arrow.metadata or {}
        stored_feature_version = metadata.get(b"trading.feature_schema_version", b"1").decode()
        if (
            metadata.get(b"trading.dataset_schema_version") != DATASET_SCHEMA_VERSION.encode()
            or metadata.get(b"trading.dataset_table") != table_name.encode()
            or (table_name == "features" and stored_feature_version != manifest_feature_version)
            or (
                manifest_feature_version == FEATURE_SCHEMA_VERSION
                and (
                    metadata.get(b"trading.instrument", b"").decode() != manifest.get("instrument")
                    or metadata.get(b"trading.timeframe_minutes", b"").decode()
                    != str(manifest.get("timeframe_minutes"))
                    or metadata.get(b"trading.dataset_fingerprint", b"").decode()
                    != manifest.get("dataset_fingerprint")
                    or metadata.get(b"trading.feature_configuration", b"").decode()
                    != json.dumps(manifest.get("configuration"), sort_keys=True)
                )
            )
        ):
            raise ValueError(f"Parquet feature schema metadata is invalid: {path}")
        stored_columns = set(parquet.schema_arrow.names)
        requested = (
            list(columns)
            if columns is not None
            else [name for name in parquet.schema_arrow.names if name != "prediction_at"]
        )
        if not requested or len(set(requested)) != len(requested):
            raise ValueError("Feature columns must be a non-empty unique selection")
        if "prediction_at" in requested:
            raise ValueError("prediction_at is returned as the feature DataFrame index")
        if "prediction_at" in stored_columns and "prediction_at" not in requested:
            load_columns = [*requested, "prediction_at"]
        else:
            load_columns = requested
        missing = set(load_columns) - stored_columns
        if missing:
            raise ValueError(f"Unknown {table_name} columns: {', '.join(sorted(missing))}")
        frame = pq.read_table(path, columns=load_columns).to_pandas()
        if "prediction_at" in frame.columns:
            frame = frame.set_index("prediction_at")
        return frame.loc[:, requested]
