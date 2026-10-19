import asyncio
import hashlib
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from trading.data.domain import InstrumentCode, Timeframe
from trading.data.histdata import (
    HISTDATA_CONFIG_PATH,
    HISTDATA_TIMESTAMP_OFFSET,
    HistDataAsciiProvider,
    histdata_file_metadata,
    load_histdata_mapping,
)


@dataclass(frozen=True)
class HistDataBatchFile:
    path: Path
    member_name: str | None
    month: str
    start: datetime
    end: datetime
    error: str | None = None

    @property
    def source_reference(self) -> str:
        return f"{self.path}::{self.member_name}" if self.member_name else str(self.path)


def discover_histdata_files(
    input_dir: str | Path,
    instrument: InstrumentCode,
    start: datetime,
    end: datetime,
    *,
    config_path: str | Path = HISTDATA_CONFIG_PATH,
) -> list[HistDataBatchFile]:
    _validate_bounds(start, end)
    root = Path(input_dir).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"HistData input directory does not exist: {root}")
    file_code = load_histdata_mapping(config_path, instrument)["file_code"].upper()
    start_utc, end_utc = start.astimezone(UTC), end.astimezone(UTC)
    found: list[HistDataBatchFile] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.casefold() not in {".csv", ".zip"}:
            continue
        metadata = histdata_file_metadata(path.name)
        if metadata is None:
            if path.name.upper().startswith(f"DAT_ASCII_{file_code}_M1_"):
                found.append(
                    HistDataBatchFile(
                        path=path,
                        member_name=None,
                        month="unknown",
                        start=start_utc,
                        end=end_utc,
                        error="Filename does not match DAT_ASCII_<symbol>_M1_YYYYMM.csv/.zip",
                    )
                )
            continue
        candidate_code, month = metadata
        if candidate_code != file_code:
            continue
        try:
            month_start, month_end = _month_bounds(month)
        except ValueError as error:
            found.append(
                HistDataBatchFile(
                    path=path,
                    member_name=None,
                    month=month,
                    start=start_utc,
                    end=end_utc,
                    error=str(error),
                )
            )
            continue
        if max(start_utc, month_start) >= min(end_utc, month_end):
            continue

        member_name = None
        error = None
        if path.suffix.casefold() == ".zip":
            try:
                member_name = _matching_zip_member(path, candidate_code, month)
            except (ValueError, zipfile.BadZipFile) as exception:
                error = str(exception)
        found.append(
            HistDataBatchFile(
                path=path,
                member_name=member_name,
                month=month,
                start=start_utc,
                end=end_utc,
                error=error,
            )
        )

    by_month: dict[str, list[int]] = {}
    for index, candidate in enumerate(found):
        if candidate.month != "unknown":
            by_month.setdefault(candidate.month, []).append(index)
    for month, indices in by_month.items():
        if len(indices) > 1:
            for index in indices:
                candidate = found[index]
                found[index] = HistDataBatchFile(
                    path=candidate.path,
                    member_name=candidate.member_name,
                    month=candidate.month,
                    start=candidate.start,
                    end=candidate.end,
                    error=f"Multiple HistData files found for {instrument.value} {month}",
                )
    return sorted(found, key=lambda item: (item.month, str(item.path).casefold()))


def process_histdata_batch(
    files: list[HistDataBatchFile],
    instrument: InstrumentCode,
    *,
    batch_size: int,
    dry_run: bool,
    import_file: Callable[[HistDataBatchFile, HistDataAsciiProvider], int] | None = None,
    is_completed: Callable[[HistDataAsciiProvider, HistDataBatchFile], bool] | None = None,
    config_path: str | Path = HISTDATA_CONFIG_PATH,
) -> dict[str, object]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    started = time.perf_counter()
    results: list[dict[str, object]] = []

    for candidate in files:
        if candidate.error:
            results.append(_file_result(candidate, "failed", error=candidate.error))
            continue
        try:
            provider = HistDataAsciiProvider(
                candidate.path,
                instrument,
                Timeframe.M1,
                config_path=config_path,
                member_name=candidate.member_name,
                expected_month=candidate.month,
            )
            range_identity = (
                f"{provider.content_sha256}:{candidate.start.isoformat()}:"
                f"{candidate.end.isoformat()}"
            )
            provider.source_key = f"sha256:{hashlib.sha256(range_identity.encode()).hexdigest()}"
            if not dry_run and is_completed and is_completed(provider, candidate):
                results.append(_file_result(candidate, "skipped", candles=0))
                continue
            if dry_run:
                candles = asyncio.run(
                    _count_candles(provider, instrument, candidate.start, candidate.end)
                )
                results.append(_file_result(candidate, "dry_run", candles=candles))
                continue
            if import_file is None:
                raise ValueError("import_file callback is required unless --dry-run is set")
            inserted = import_file(candidate, provider)
            results.append(_file_result(candidate, "imported", candles=inserted))
        except Exception as error:
            results.append(
                _file_result(candidate, "failed", error=f"{type(error).__name__}: {error}")
            )

    imported = sum(result["status"] == "imported" for result in results)
    skipped = sum(result["status"] == "skipped" for result in results)
    failed = sum(result["status"] == "failed" for result in results)
    validated = sum(
        int(result.get("candles", 0)) for result in results if result["status"] == "dry_run"
    )
    return {
        "discovered": len(files),
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
        "candles_inserted": sum(
            int(result.get("candles", 0)) for result in results if result["status"] == "imported"
        ),
        "candles_validated": validated,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "files": results,
    }


async def _count_candles(
    provider: HistDataAsciiProvider,
    instrument: InstrumentCode,
    start: datetime,
    end: datetime,
) -> int:
    count = 0
    async for _ in provider.history(instrument, Timeframe.M1, start, end):
        count += 1
    return count


def _file_result(
    candidate: HistDataBatchFile,
    status: str,
    *,
    candles: int = 0,
    error: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "file": str(candidate.path),
        "month": candidate.month,
        "status": status,
        "candles": candles,
    }
    if candidate.member_name:
        result["zip_member"] = candidate.member_name
    if error:
        result["error"] = error
    return result


def _matching_zip_member(path: Path, file_code: str, month: str) -> str:
    expected = (file_code, month)
    with zipfile.ZipFile(path) as archive:
        csv_members = [
            name
            for name in archive.namelist()
            if not name.endswith("/") and Path(name).suffix.casefold() == ".csv"
        ]
    matching = [name for name in csv_members if histdata_file_metadata(name) == expected]
    if len(matching) != 1:
        raise ValueError(
            f"Expected one ZIP CSV member for {file_code} {month}; found {len(matching)}"
        )
    return matching[0]


def _month_bounds(month: str) -> tuple[datetime, datetime]:
    try:
        year, number = int(month[:4]), int(month[4:])
        if year < 1 or number < 1 or number > 12:
            raise ValueError
    except ValueError as error:
        raise ValueError(f"Invalid HistData month metadata: {month}") from error
    month_start = datetime(year, number, 1, tzinfo=HISTDATA_TIMESTAMP_OFFSET)
    if number == 12:
        month_end = datetime(year + 1, 1, 1, tzinfo=HISTDATA_TIMESTAMP_OFFSET)
    else:
        month_end = datetime(year, number + 1, 1, tzinfo=HISTDATA_TIMESTAMP_OFFSET)
    return month_start.astimezone(UTC), month_end.astimezone(UTC)


def _validate_bounds(start: datetime, end: datetime) -> None:
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("Batch start must be timezone-aware")
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("Batch end must be timezone-aware")
    if start >= end:
        raise ValueError("Batch start must be earlier than end")
