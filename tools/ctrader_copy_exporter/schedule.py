from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ExportInterval:
    interval_id: str
    granularity: str
    start: datetime
    end: datetime
    ui_start: str
    ui_end: str
    parent_id: str | None = None

    def contains(self, timestamp: datetime) -> bool:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Trade timestamps must have a timezone")
        normalized = timestamp.astimezone(UTC)
        return self.start.astimezone(UTC) <= normalized < self.end.astimezone(UTC)


def weekly_intervals(
    requested_start: datetime,
    requested_end: datetime,
    timezone: ZoneInfo,
) -> list[ExportInterval]:
    start, end = _normalize_range(requested_start, requested_end, timezone)
    monday = start.date() - timedelta(days=start.weekday())
    result = []
    while True:
        week_start = datetime.combine(monday, time.min, tzinfo=timezone)
        week_end = datetime.combine(monday + timedelta(days=7), time.min, tzinfo=timezone)
        clipped_start = max(start, week_start)
        clipped_end = min(end, week_end)
        if clipped_start >= clipped_end:
            break
        interval_id = f"week:{week_start.date().isoformat()}"
        result.append(
            ExportInterval(
                interval_id=interval_id,
                granularity="week",
                start=clipped_start,
                end=clipped_end,
                ui_start=clipped_start.date().isoformat(),
                # Inclusive date filters intentionally overlap adjacent intervals.
                ui_end=clipped_end.date().isoformat(),
            )
        )
        if week_end >= end:
            break
        monday += timedelta(days=7)
    return result


def daily_intervals(week: ExportInterval, timezone: ZoneInfo) -> list[ExportInterval]:
    if week.granularity != "week":
        raise ValueError("Daily fallback requires a weekly parent interval")
    start_utc = week.start.astimezone(UTC)
    end_utc = week.end.astimezone(UTC)
    local_start = start_utc.astimezone(timezone)
    local_end = end_utc.astimezone(timezone)
    cursor = local_start.date()
    result = []
    while cursor < local_end.date() or (cursor == local_end.date() and local_end.time() > time.min):
        day_start = datetime.combine(cursor, time.min, tzinfo=timezone)
        day_end = datetime.combine(cursor + timedelta(days=1), time.min, tzinfo=timezone)
        clipped_start = max(start_utc, day_start.astimezone(UTC))
        clipped_end = min(end_utc, day_end.astimezone(UTC))
        if clipped_start < clipped_end:
            day = cursor.isoformat()
            result.append(
                ExportInterval(
                    interval_id=f"{week.interval_id}/day:{day}",
                    granularity="day",
                    start=clipped_start,
                    end=clipped_end,
                    ui_start=clipped_start.astimezone(timezone).date().isoformat(),
                    ui_end=clipped_end.astimezone(timezone).date().isoformat(),
                    parent_id=week.interval_id,
                )
            )
        cursor += timedelta(days=1)
    return result


def require_single_week(
    requested_start: datetime,
    requested_end: datetime,
    timezone: ZoneInfo,
) -> ExportInterval:
    intervals = weekly_intervals(requested_start, requested_end, timezone)
    if len(intervals) != 1:
        raise ValueError(
            "Dry run must be clipped to exactly one local calendar week; "
            f"received {len(intervals)} weekly intervals"
        )
    return intervals[0]


def _normalize_range(
    start: datetime,
    end: datetime,
    timezone: ZoneInfo,
) -> tuple[datetime, datetime]:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Requested range timestamps must include a UTC offset")
    normalized_start = start.astimezone(timezone)
    normalized_end = end.astimezone(timezone)
    if normalized_start >= normalized_end:
        raise ValueError("Requested start must be earlier than requested end")
    return normalized_start, normalized_end
