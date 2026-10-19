import hashlib
import zipfile
from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects import postgresql

import trading.data.repository as repository_module
from trading.data.commands import _build_parser
from trading.data.domain import InstrumentCode
from trading.data.histdata import HISTDATA_SOURCE_ID
from trading.data.histdata_batch import (
    discover_histdata_files,
    process_histdata_batch,
)
from trading.data.repository import HistoricalImportRepository
from trading.storage.parquet import parquet_checkpoint_key


class _CompletedImportSession:
    def __init__(self, completed_job: dict[str, object], checkpoint_keys: set[str]) -> None:
        self.completed_job = completed_job
        self.checkpoint_keys = checkpoint_keys

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def scalar(self, statement):
        compiled = statement.compile(dialect=postgresql.dialect())
        params = compiled.params
        if "market_data_import_jobs" in str(statement):
            fields = (
                ("source_id_1", "source_id"),
                ("instrument_symbol_1", "instrument"),
                ("timeframe_minutes_1", "timeframe"),
                ("price_type_1", "price_type"),
                ("source_checksum_1", "checksum"),
                ("source_reference_1", "source_reference"),
                ("requested_start_1", "start"),
                ("requested_end_1", "end"),
                ("status_1", "status"),
            )
            if all(
                params.get(parameter) == self.completed_job[field] for parameter, field in fields
            ):
                return self.completed_job["job_id"]
            return None
        if "data_ingestion_checkpoints" in str(statement):
            bound_keys = params["source_key_1"]
            return next(iter(self.checkpoint_keys.intersection(bound_keys)), None)
        raise AssertionError(f"Unexpected query: {statement}")


def _csv(month: str, rows: int = 1) -> str:
    return "".join(f"{month}01 000{index}00,1.0,1.2,0.9,1.1,10\n" for index in range(rows))


def _bounds(start: str, end: str) -> tuple[datetime, datetime]:
    return datetime.fromisoformat(start), datetime.fromisoformat(end)


def test_discovery_is_recursive_and_orders_month_files_chronologically(tmp_path) -> None:
    newest = tmp_path / "nested" / "DAT_ASCII_EURUSD_M1_202602.csv"
    oldest = tmp_path / "DAT_ASCII_EURUSD_M1_202601.csv"
    newest.parent.mkdir()
    newest.write_text(_csv("202602"), encoding="ascii")
    oldest.write_text(_csv("202601"), encoding="ascii")
    start, end = _bounds("2026-01-01T05:00:00+00:00", "2026-03-01T05:00:00+00:00")

    discovered = discover_histdata_files(tmp_path, InstrumentCode.EURUSD, start, end)

    assert [item.month for item in discovered] == ["202601", "202602"]
    assert [item.path for item in discovered] == [oldest.resolve(), newest.resolve()]


def test_zip_discovery_and_provider_read_member_without_extraction(tmp_path) -> None:
    name = "DAT_ASCII_EURUSD_M1_202601.csv"
    archive_path = tmp_path / "DAT_ASCII_EURUSD_M1_202601.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"nested/{name}", "20260101 000000,1.0,1.2,0.9,1.1,10\n")
    start, end = _bounds("2026-01-01T05:00:00+00:00", "2026-02-01T05:00:00+00:00")

    discovered = discover_histdata_files(tmp_path, InstrumentCode.EURUSD, start, end)
    summary = process_histdata_batch(
        discovered,
        InstrumentCode.EURUSD,
        batch_size=10,
        dry_run=True,
    )

    assert discovered[0].member_name == f"nested/{name}"
    assert summary["candles_validated"] == 1
    assert not list(tmp_path.rglob(name))
    assert summary["files"][0]["status"] == "dry_run"


def test_file_rows_must_match_the_month_encoded_in_the_filename(tmp_path) -> None:
    path = tmp_path / "DAT_ASCII_EURUSD_M1_202601.csv"
    path.write_text("20260201 000000,1.0,1.2,0.9,1.1,10\n", encoding="ascii")
    start, end = _bounds("2026-01-01T05:00:00+00:00", "2026-02-01T05:00:00+00:00")
    files = discover_histdata_files(tmp_path, InstrumentCode.EURUSD, start, end)

    summary = process_histdata_batch(
        files,
        InstrumentCode.EURUSD,
        batch_size=10,
        dry_run=True,
    )

    assert summary["failed"] == 1
    assert "does not match filename month" in summary["files"][0]["error"]


def test_duplicate_month_files_are_reported_as_failed_candidates(tmp_path) -> None:
    first = tmp_path / "DAT_ASCII_EURUSD_M1_202601.csv"
    second = tmp_path / "copy" / first.name
    second.parent.mkdir()
    first.write_text(_csv("202601"), encoding="ascii")
    second.write_text(_csv("202601"), encoding="ascii")
    start, end = _bounds("2026-01-01T05:00:00+00:00", "2026-02-01T05:00:00+00:00")

    discovered = discover_histdata_files(tmp_path, InstrumentCode.EURUSD, start, end)
    summary = process_histdata_batch(
        discovered,
        InstrumentCode.EURUSD,
        batch_size=100,
        dry_run=True,
    )

    assert summary["discovered"] == 2
    assert summary["failed"] == 2
    assert all("Multiple HistData files" in item["error"] for item in summary["files"])


def test_interrupted_file_can_resume_and_successful_file_is_skipped(tmp_path) -> None:
    path = tmp_path / "DAT_ASCII_EURUSD_M1_202601.csv"
    path.write_text(_csv("202601", rows=2), encoding="ascii")
    start, end = _bounds("2026-01-01T05:00:00+00:00", "2026-02-01T05:00:00+00:00")
    files = discover_histdata_files(tmp_path, InstrumentCode.EURUSD, start, end)
    completed = False

    def fail_once(candidate, provider):
        raise OSError("simulated interruption")

    first_run = process_histdata_batch(
        files,
        InstrumentCode.EURUSD,
        batch_size=10,
        dry_run=False,
        import_file=fail_once,
    )
    assert first_run["failed"] == 1

    def import_successfully(candidate, provider):
        nonlocal completed
        completed = True
        return 2

    resumed = process_histdata_batch(
        files,
        InstrumentCode.EURUSD,
        batch_size=10,
        dry_run=False,
        import_file=import_successfully,
    )
    assert resumed["imported"] == 1
    assert resumed["candles_inserted"] == 2

    skipped = process_histdata_batch(
        files,
        InstrumentCode.EURUSD,
        batch_size=10,
        dry_run=False,
        import_file=import_successfully,
        is_completed=lambda provider, candidate: True,
    )
    assert skipped["skipped"] == 1
    assert skipped["candles_inserted"] == 0
    assert completed


@pytest.mark.parametrize(
    "changed",
    [
        "none",
        "checksum",
        "range",
        "source_reference",
        "source_id",
        "instrument",
        "timeframe",
        "price_type",
        "storage",
    ],
)
def test_batch_recognizes_equivalent_completed_single_file_job_and_checks_identity(
    tmp_path,
    monkeypatch,
    changed,
) -> None:
    path = tmp_path / "DAT_ASCII_EURUSD_M1_202601.csv"
    source = _csv("202601")
    path.write_text(source, encoding="ascii")
    data_root = tmp_path / "parquet"
    source_checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    requested_start = datetime(2026, 1, 1, tzinfo=UTC)
    requested_end = datetime(2026, 2, 1, tzinfo=UTC)
    if changed == "checksum":
        path.write_text(source + "20260102 000000,1.1,1.2,1.0,1.15,11\n", encoding="ascii")
    if changed == "range":
        requested_start = datetime(2026, 1, 1, 1, tzinfo=UTC)
    files = discover_histdata_files(
        tmp_path,
        InstrumentCode.EURUSD,
        requested_start,
        requested_end,
    )
    completed_job = {
        "job_id": "completed-single-file-job",
        "source_id": HISTDATA_SOURCE_ID,
        "instrument": InstrumentCode.EURUSD.value,
        "timeframe": 1,
        "price_type": "bid",
        "checksum": source_checksum,
        "source_reference": str(path.resolve()),
        "start": datetime(2026, 1, 1, tzinfo=UTC),
        "end": datetime(2026, 2, 1, tzinfo=UTC),
        "status": "completed",
    }
    if changed == "source_reference":
        completed_job["source_reference"] = str(path.parent / "old-location" / path.name)
    elif changed == "source_id":
        completed_job["source_id"] = "another-source"
    elif changed == "instrument":
        completed_job["instrument"] = "XAUUSD"
    elif changed == "timeframe":
        completed_job["timeframe"] = 5
    elif changed == "price_type":
        completed_job["price_type"] = "ask"
    session = _CompletedImportSession(
        completed_job,
        {
            parquet_checkpoint_key(data_root, f"sha256:{source_checksum}"),
        },
    )
    monkeypatch.setattr(repository_module, "Session", lambda engine: session)
    import_repository = HistoricalImportRepository(engine=object())
    imported: list[str] = []

    def is_completed(provider, candidate):
        range_source_key = provider.source_key
        lookup_root = tmp_path / "different-parquet-root" if changed == "storage" else data_root
        return import_repository.has_completed_job(
            source_id=HISTDATA_SOURCE_ID,
            instrument=InstrumentCode.EURUSD,
            timeframe=provider.timeframe,
            price_type=provider.price_type,
            source_checksum=provider.content_sha256,
            source_reference=provider.source_reference,
            requested_start=candidate.start,
            requested_end=candidate.end,
            storage_checkpoint_keys=(
                parquet_checkpoint_key(lookup_root, range_source_key),
                parquet_checkpoint_key(
                    lookup_root,
                    f"sha256:{provider.content_sha256}",
                ),
            ),
        )

    result = process_histdata_batch(
        files,
        InstrumentCode.EURUSD,
        batch_size=10,
        dry_run=False,
        import_file=lambda candidate, provider: imported.append(candidate.month) or 1,
        is_completed=is_completed,
    )

    if changed != "none":
        assert result["imported"] == 1
        assert imported == ["202601"]
    else:
        assert result["skipped"] == 1
        assert imported == []


def test_failed_month_does_not_prevent_later_month_import(tmp_path) -> None:
    january = tmp_path / "DAT_ASCII_EURUSD_M1_202601.csv"
    february = tmp_path / "DAT_ASCII_EURUSD_M1_202602.csv"
    january.write_text(_csv("202601"), encoding="ascii")
    february.write_text(_csv("202602"), encoding="ascii")
    start, end = _bounds("2026-01-01T05:00:00+00:00", "2026-03-01T05:00:00+00:00")
    files = discover_histdata_files(tmp_path, InstrumentCode.EURUSD, start, end)

    def import_one(candidate, provider):
        if candidate.month == "202601":
            raise OSError("unavailable source")
        return 1

    result = process_histdata_batch(
        files,
        InstrumentCode.EURUSD,
        batch_size=10,
        dry_run=False,
        import_file=import_one,
    )

    assert result["failed"] == 1
    assert result["imported"] == 1
    assert result["candles_inserted"] == 1
    assert [entry["status"] for entry in result["files"]] == ["failed", "imported"]


def test_dry_run_does_not_call_database_or_import_callbacks(tmp_path) -> None:
    path = tmp_path / "DAT_ASCII_EURUSD_M1_202601.csv"
    path.write_text(_csv("202601"), encoding="ascii")
    initial = path.read_bytes()
    start, end = _bounds("2026-01-01T05:00:00+00:00", "2026-02-01T05:00:00+00:00")
    files = discover_histdata_files(tmp_path, InstrumentCode.EURUSD, start, end)

    def unexpected(*args):
        pytest.fail("dry-run must not call persistent import/job callbacks")

    summary = process_histdata_batch(
        files,
        InstrumentCode.EURUSD,
        batch_size=10,
        dry_run=True,
        import_file=unexpected,
        is_completed=unexpected,
    )

    assert summary["candles_inserted"] == 0
    assert summary["candles_validated"] == 1
    assert path.read_bytes() == initial
    assert list(tmp_path.iterdir()) == [path]


def test_batch_cli_requires_parquet_and_accepts_dry_run() -> None:
    arguments = _build_parser("import-histdata-batch").parse_args(
        [
            "--input-dir",
            "data\\histdata",
            "--instrument",
            "EURUSD",
            "--start",
            "2026-01-01T00:00:00Z",
            "--end",
            "2026-03-01T00:00:00Z",
            "--storage",
            "parquet",
            "--batch-size",
            "1000",
            "--dry-run",
        ]
    )

    assert arguments.dry_run is True
    assert arguments.storage == "parquet"
    assert arguments.batch_size == 1000


def test_discovery_rejects_invalid_date_range(tmp_path) -> None:
    with pytest.raises(ValueError, match="earlier"):
        discover_histdata_files(
            tmp_path,
            InstrumentCode.EURUSD,
            datetime(2026, 2, 1, tzinfo=UTC),
            datetime(2026, 1, 1, tzinfo=UTC),
        )
