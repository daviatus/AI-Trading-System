"""Use broker-exposed weekly sessions and dated holiday closures per symbol."""

import hashlib
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from trading.data.domain import InstrumentCode
from trading.data.market_calendar import MarketCalendar


class BrokerCalendar(MarketCalendar):
    def __init__(self, symbols: dict[str, dict]):
        super().__init__()
        self.symbols = symbols
        self.zones = {}
        self.holidays = {}
        for instrument, symbol in symbols.items():
            specs = symbol["specifications"]
            self.zones[instrument] = ZoneInfo(specs["scheduleTimeZone"])
            if not specs.get("schedule"):
                raise ValueError(f"Broker schedule missing for {instrument}")
            for interval in specs["schedule"]:
                if not 0 <= interval["startSecond"] < interval["endSecond"] <= 604800:
                    raise ValueError("Invalid broker weekly interval")
            self.holidays[instrument] = [
                (date(1970, 1, 1) + timedelta(days=int(h["holidayDate"])),
                 ZoneInfo(h["scheduleTimeZone"]), bool(h["isRecurring"]),
                 int(h.get("startSecond", 0)), int(h.get("endSecond", 0)))
                for h in specs.get("holiday", [])
            ]

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.symbols, sort_keys=True).encode()).hexdigest()

    def is_open(self, instrument: InstrumentCode, timestamp: datetime):
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Broker calendar requires aware timestamps")
        if instrument.value not in self.symbols:
            raise ValueError(f"Broker schedule not configured for {instrument.value}")
        local = timestamp.astimezone(self.zones[instrument.value])
        seconds = (((local.weekday() + 1) % 7) * 86400 + local.hour * 3600
                   + local.minute * 60 + local.second)
        specs = self.symbols[instrument.value]["specifications"]
        if not any(w["startSecond"] <= seconds < w["endSecond"] for w in specs["schedule"]):
            return False
        for day, zone, recurring, start, end in self.holidays[instrument.value]:
            holiday_local = timestamp.astimezone(zone)
            matching = ((holiday_local.month, holiday_local.day) == (day.month, day.day)
                        if recurring else holiday_local.date() == day)
            second = holiday_local.hour * 3600 + holiday_local.minute * 60 + holiday_local.second
            if matching and (start == end == 0 or start <= second < end):
                return False
        return True
