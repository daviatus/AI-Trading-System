import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from trading.config import MarketScheduleSettings
from trading.data.domain import InstrumentCode


@dataclass(frozen=True)
class MarketSchedule:
    timezone: ZoneInfo
    weekdays: frozenset[int]
    sessions: tuple[tuple[time, time], ...]

    @classmethod
    def from_settings(cls, settings: MarketScheduleSettings) -> "MarketSchedule":
        windows = tuple(
            (
                _parse_session_time(window.open),
                _parse_session_time(window.close, allow_midnight_end=True),
            )
            for window in settings.sessions
        )
        return cls(
            timezone=ZoneInfo(settings.timezone),
            weekdays=frozenset(settings.weekdays),
            sessions=windows,
        )

    @classmethod
    def default_weekdays(cls) -> "MarketSchedule":
        return cls(
            timezone=ZoneInfo("UTC"),
            weekdays=frozenset({0, 1, 2, 3, 4}),
            sessions=((time.min, time.max),),
        )

    def is_open(self, timestamp: datetime) -> bool:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Market-calendar timestamps must be timezone-aware")
        local = timestamp.astimezone(self.timezone)
        local_time = local.timetz().replace(tzinfo=None)
        previous_date = local.date() - timedelta(days=1)
        for start, end in self.sessions:
            if end < start:
                if local.weekday() in self.weekdays and local_time >= start:
                    return True
                if previous_date.weekday() in self.weekdays and local_time < end:
                    return True
            elif local.weekday() in self.weekdays and start <= local_time < end:
                return True
        return False


class MarketCalendar:
    def __init__(self, schedules: dict[InstrumentCode, MarketSchedule] | None = None) -> None:
        defaults = {instrument: MarketSchedule.default_weekdays() for instrument in InstrumentCode}
        if schedules:
            defaults.update(schedules)
        self.schedules = defaults

    @property
    def fingerprint(self) -> str:
        payload = {
            instrument.value: {
                "timezone": schedule.timezone.key,
                "weekdays": sorted(schedule.weekdays),
                "sessions": [
                    [start.isoformat(), end.isoformat()] for start, end in schedule.sessions
                ],
            }
            for instrument, schedule in sorted(
                self.schedules.items(),
                key=lambda item: item[0].value,
            )
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    @classmethod
    def from_config(
        cls,
        schedules: dict[str, MarketScheduleSettings],
    ) -> "MarketCalendar":
        return cls(
            {
                InstrumentCode(symbol): MarketSchedule.from_settings(schedule)
                for symbol, schedule in schedules.items()
            }
        )

    def is_open(self, instrument: InstrumentCode, timestamp: datetime) -> bool:
        return self.schedules[instrument].is_open(timestamp)

    def classify_missing_timestamps(
        self,
        instrument: InstrumentCode,
        start: datetime,
        count: int,
        interval_minutes: int,
    ) -> tuple[int, int]:
        expected = 0
        closed = 0
        for index in range(count):
            timestamp = start + timedelta(minutes=index * interval_minutes)
            if self.is_open(instrument, timestamp):
                expected += 1
            else:
                closed += 1
        return expected, closed


def _parse_session_time(value: str, allow_midnight_end: bool = False) -> time:
    if allow_midnight_end and value == "24:00":
        return time.max
    return time.fromisoformat(value)
