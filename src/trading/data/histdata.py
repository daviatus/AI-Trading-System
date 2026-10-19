import csv
import hashlib
import re
import zipfile
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from io import TextIOWrapper
from pathlib import Path
from typing import TextIO

from trading.data.domain import Candle, InstrumentCode, PriceType, Timeframe
from trading.data.providers import MarketDataProviderError, _validate_history_request

HISTDATA_SOURCE_ID = "histdata_generic_ascii"
HISTDATA_TIMESTAMP_OFFSET = timezone(timedelta(hours=-5), name="EST")
HISTDATA_CONFIG_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "historical_data_sources.json"
)


class HistDataAsciiProvider:
    """Historical reader for local HistData Generic ASCII minute files."""

    name = HISTDATA_SOURCE_ID

    def __init__(
        self,
        path: str | Path,
        instrument: InstrumentCode,
        timeframe: Timeframe = Timeframe.M1,
        *,
        config_path: str | Path = HISTDATA_CONFIG_PATH,
        member_name: str | None = None,
        expected_month: str | None = None,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self.instrument = instrument
        self.timeframe = timeframe
        if timeframe != Timeframe.M1:
            raise ValueError("HistData Generic ASCII provider imports M1 only")
        if not self.path.is_file():
            raise FileNotFoundError(f"HistData ASCII file does not exist: {self.path}")
        mapping = load_histdata_mapping(config_path, instrument)
        self.histdata_symbol = mapping["symbol"]
        self.file_code = mapping["file_code"]
        self.member_name = member_name
        self.expected_month = expected_month
        if self.path.suffix.casefold() == ".zip":
            if self.member_name is None:
                self.member_name = self._find_zip_member()
            with zipfile.ZipFile(self.path) as archive:
                if self.member_name not in archive.namelist():
                    raise ValueError(f"HistData ZIP member does not exist: {self.member_name}")
        self.price_type = PriceType.BID
        self.source_reference = (
            f"{self.path}::{self.member_name}" if self.member_name else str(self.path)
        )
        digest = hashlib.sha256()
        with self.path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        self.content_sha256 = digest.hexdigest()
        self.source_key = f"sha256:{self.content_sha256}"

    async def history(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> AsyncIterator[Candle]:
        if instrument != self.instrument or timeframe != self.timeframe:
            raise MarketDataProviderError(
                "Requested instrument/timeframe does not match the configured HistData file"
            )
        _validate_history_request(start, end, timeframe)
        start_utc, end_utc = start.astimezone(UTC), end.astimezone(UTC)
        previous: datetime | None = None
        try:
            with self._open_text() as source:
                for line_number, raw_line in enumerate(source, start=1):
                    if not raw_line.strip():
                        continue
                    delimiter = ";" if raw_line.count(";") > raw_line.count(",") else ","
                    fields = next(csv.reader([raw_line], delimiter=delimiter))
                    values = [field.strip() for field in fields]
                    if _looks_like_header(values):
                        continue
                    try:
                        timestamp, raw_timestamp, price_values = _parse_fields(values)
                        if (
                            self.expected_month
                            and timestamp.strftime("%Y%m") != self.expected_month
                        ):
                            raise ValueError(
                                f"Record month {timestamp:%Y%m} does not match "
                                f"filename month {self.expected_month}"
                            )
                        opened_at = timestamp.replace(tzinfo=HISTDATA_TIMESTAMP_OFFSET).astimezone(
                            UTC
                        )
                        candle = Candle(
                            instrument=instrument,
                            timeframe=timeframe,
                            opened_at=opened_at,
                            open=Decimal(price_values[0]),
                            high=Decimal(price_values[1]),
                            low=Decimal(price_values[2]),
                            close=Decimal(price_values[3]),
                            volume=Decimal(price_values[4]) if price_values[4] else None,
                            price_type=self.price_type,
                            quality_metadata={
                                "source_timestamp": raw_timestamp,
                                "source_timezone": "UTC-05:00",
                                "source_timezone_is_fixed": True,
                                "dst_adjustment_applied": False,
                            },
                        )
                    except (ValueError, InvalidOperation) as error:
                        raise MarketDataProviderError(
                            f"Invalid HistData record at line {line_number}: {error}"
                        ) from error
                    if previous is not None and opened_at <= previous:
                        reason = "duplicate" if opened_at == previous else "out of order"
                        raise MarketDataProviderError(
                            f"HistData timestamps must be unique and chronological; "
                            f"{reason} record at line {line_number}"
                        )
                    previous = opened_at
                    if start_utc <= opened_at < end_utc:
                        yield candle
        except UnicodeDecodeError as error:
            raise MarketDataProviderError(
                f"HistData file is not UTF-8/ASCII text: {self.path}"
            ) from error

    def _find_zip_member(self) -> str:
        with zipfile.ZipFile(self.path) as archive:
            candidates = [
                name
                for name in archive.namelist()
                if not name.endswith("/") and Path(name).suffix.casefold() == ".csv"
            ]
        matching = [
            name
            for name in candidates
            if histdata_file_metadata(name) == histdata_file_metadata(self.path.name)
        ]
        if len(matching) != 1:
            raise ValueError(
                f"Expected one matching HistData CSV member in {self.path}; found {len(matching)}"
            )
        return matching[0]

    @contextmanager
    def _open_text(self) -> Iterator[TextIO]:
        if self.member_name is None:
            with self.path.open(encoding="utf-8-sig", newline="") as source:
                yield source
            return
        with zipfile.ZipFile(self.path) as archive, archive.open(self.member_name) as member:
            with TextIOWrapper(member, encoding="utf-8-sig", newline="") as source:
                yield source

    async def stream(
        self,
        instrument: InstrumentCode,
        timeframe: Timeframe,
    ) -> AsyncIterator[Candle]:
        raise MarketDataProviderError("HistData files provide historical candles only")
        yield


def load_histdata_mapping(
    path: str | Path,
    instrument: InstrumentCode,
) -> dict[str, str]:
    import json

    content = json.loads(Path(path).read_text(encoding="utf-8"))
    if content.get("schema_version") != 1:
        raise ValueError("Unsupported historical source mapping schema version")
    try:
        mapping = content["sources"][HISTDATA_SOURCE_ID]["instruments"][instrument.value]
    except KeyError as error:
        raise ValueError(f"No HistData instrument mapping for {instrument.value}") from error
    if not mapping.get("symbol") or not mapping.get("file_code"):
        raise ValueError(f"Invalid HistData instrument mapping for {instrument.value}")
    return mapping


def _parse_fields(values: list[str]) -> tuple[datetime, str, list[str]]:
    split_timestamp = len(values) in (6, 7) and len(values[0]) == 8 and values[0].isdigit()
    if split_timestamp:
        raw_timestamp = f"{values[0]} {values[1]}"
        price_values = values[2:]
    elif len(values) in (5, 6):
        raw_timestamp = values[0]
        price_values = values[1:]
    else:
        raise ValueError("Expected 5/6 fields with a combined timestamp or 6/7 split fields")
    if len(price_values) == 4:
        price_values.append("")
    timestamp = None
    for format_string in ("%Y%m%d %H%M%S", "%Y%m%d%H%M%S", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            timestamp = datetime.strptime(raw_timestamp, format_string)
            break
        except ValueError:
            continue
    if timestamp is None:
        raise ValueError(f"Unsupported HistData timestamp {raw_timestamp!r}")
    if len(price_values) != 5:
        raise ValueError("Expected OHLC and an optional volume field")
    return timestamp, raw_timestamp, price_values


def _looks_like_header(values: list[str]) -> bool:
    if not values:
        return False
    first = values[0].strip().casefold()
    return first in {"date", "timestamp", "datetime"}


def histdata_file_metadata(name: str) -> tuple[str, str] | None:
    match = re.fullmatch(
        r"DAT_ASCII_([A-Z0-9]+)_M1_(\d{6})\.(?:csv|zip)",
        Path(name).name,
        flags=re.IGNORECASE,
    )
    return (match.group(1).upper(), match.group(2)) if match else None
