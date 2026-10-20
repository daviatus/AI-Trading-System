from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from trading.data.domain import Candle, InstrumentCode, Timeframe
from trading.data.quality import check_candle_quality


def validate_candle_data(
    data: list[dict[str, Any]],
    start: datetime,
    end: datetime,
) -> tuple[list[Candle], dict[str, object]]:
    """Validate normalized cTrader candles and summarize coverage without filling gaps."""
    valid_candles: list[Candle] = []
    input_errors: list[dict[str, str | None]] = []

    for index, item in enumerate(data):
        original_timestamp = _parse_timestamp(item.get("opened_at"))
        if original_timestamp is None or original_timestamp.tzinfo is None:
            input_errors.append(
                {
                    "code": "invalid_timestamp",
                    "timestamp": _timestamp_text(item.get("opened_at")),
                    "detail": (
                        f"Candle at index {index} does not contain a timezone-aware timestamp"
                    ),
                }
            )
            continue
        if original_timestamp.utcoffset() != UTC.utcoffset(original_timestamp):
            input_errors.append(
                {
                    "code": "non_utc_timestamp",
                    "timestamp": original_timestamp.isoformat(),
                    "detail": f"Candle at index {index} does not contain a UTC timestamp",
                }
            )
            continue
        try:
            candle = Candle.model_validate(item)
        except (ValidationError, TypeError, ValueError):
            input_errors.append(
                {
                    "code": "invalid_candle",
                    "timestamp": _timestamp_text(item.get("opened_at")),
                    "detail": f"Candle at index {index} failed model validation",
                }
            )
            continue

        timestamp = candle.opened_at
        if not start <= timestamp < end:
            input_errors.append(
                {
                    "code": "out_of_range",
                    "timestamp": timestamp.isoformat(),
                    "detail": f"Candle at index {index} is outside the requested interval",
                }
            )
            continue
        if any(value <= 0 for value in (candle.open, candle.high, candle.low, candle.close)):
            input_errors.append(
                {
                    "code": "non_positive_price",
                    "timestamp": timestamp.isoformat(),
                    "detail": f"Candle at index {index} contains a non-positive OHLC price",
                }
            )
            continue
        valid_candles.append(candle)

    report = check_candle_quality(
        valid_candles,
        InstrumentCode.EURUSD,
        Timeframe.M1.value,
        range_start=start,
        range_end=end,
    )
    issues = [
        {
            "code": issue.code,
            "timestamp": issue.timestamp.isoformat() if issue.timestamp else None,
            "detail": issue.detail,
            "severity": issue.severity,
        }
        for issue in report.issues
    ]
    errors = input_errors + [issue for issue in issues if issue["severity"] == "error"]
    gaps = [
        {
            "missing_from": gap.missing_from.isoformat(),
            "missing_through": gap.missing_through.isoformat(),
            "missing_count": gap.missing_count,
            "unexpected_count": gap.unexpected_count,
            "expected_market_closure_count": gap.expected_closure_count,
        }
        for gap in report.gaps
    ]
    status = "invalid" if errors else "valid_with_gaps" if gaps else "valid"
    return valid_candles, {
        "instrument": InstrumentCode.EURUSD.value,
        "timeframe_minutes": Timeframe.M1.value,
        "requested_interval": {
            "start": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "end": end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "boundary": "[start, end)",
        },
        "candle_count": len(data),
        "first_timestamp": (
            report.coverage_start.isoformat().replace("+00:00", "Z")
            if report.coverage_start
            else None
        ),
        "last_timestamp": (
            report.coverage_end.isoformat().replace("+00:00", "Z") if report.coverage_end else None
        ),
        "duplicate_count": report.duplicate_count,
        "missing_minute_count": report.missing_candle_count,
        "missing_volume_count": report.missing_volume_count,
        "gaps": gaps,
        "validation_status": status,
        "errors": errors,
        "warnings": [issue for issue in issues if issue["severity"] != "error"],
    }


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _timestamp_text(value: object) -> str | None:
    timestamp = _parse_timestamp(value)
    return timestamp.isoformat() if timestamp else None
