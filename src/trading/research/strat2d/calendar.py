"""Versioned calendar hypotheses and explicit bounded data-integrity exclusions."""

from collections import Counter
from datetime import timedelta
from zoneinfo import ZoneInfo

import pandas as pd

MINUTE = pd.Timedelta(minutes=1)


class Calendar:
    """Weekly seconds use Sunday=0, matching the frozen broker metadata.

    Unverified rules remain hypotheses, including weekend/holiday classifications.
    Local-time conversion via ZoneInfo handles repeated and nonexistent DST hours.
    """

    def __init__(self, rules):
        self.rules = rules
        if not rules.get("version") or type(rules.get("verified")) is not bool:
            raise ValueError("Calendar requires version and explicit verification status")
        self.zone = ZoneInfo(rules["timezone"])
        windows = rules["weekly_open_seconds"]
        if not windows or any(not 0 <= a < b <= 604800 for a, b in windows):
            raise ValueError("Invalid weekly calendar windows")
        if any(b > c for (_, b), (c, _) in zip(windows, windows[1:], strict=False)):
            raise ValueError("Overlapping or unordered weekly windows")
        for holiday in rules.get("holidays", []):
            pd.Timestamp(holiday["date"])
            ZoneInfo(holiday["timezone"])
            if not 0 <= holiday["start_minute"] < holiday["end_minute"] <= 1440:
                raise ValueError("Invalid holiday closure")

    def classify(self, stamp):
        stamp = pd.Timestamp(stamp)
        if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
            raise ValueError("Calendar timestamps must explicitly use UTC")
        local = stamp.tz_convert(self.zone)
        for holiday in self.rules.get("holidays", []):
            value = stamp.tz_convert(holiday["timezone"])
            day = pd.Timestamp(holiday["date"])
            matching = (
                (value.month, value.day) == (day.month, day.day)
                if holiday.get("recurring", False)
                else value.date() == day.date()
            )
            if (
                matching
                and holiday["start_minute"]
                <= value.hour * 60 + value.minute
                < holiday["end_minute"]
            ):
                return "holiday"
        seconds = ((local.dayofweek + 1) % 7) * 86400 + local.hour * 3600 + local.minute * 60
        if any(a <= seconds < b for a, b in self.rules["weekly_open_seconds"]):
            return "open"
        return "weekend" if local.dayofweek in (4, 5, 6) else "session_break"

    def entry_window_open(self, decision, horizon):
        return all(self.classify(decision + i * MINUTE) == "open" for i in range(horizon))


def initial_quality():
    return {
        "rows": 0,
        "last_open": None,
        "first_open": None,
        "gap_count": 0,
        "internal_missing_minutes": 0,
        "missing_by_reason": {},
        "out_of_session": 0,
        "dst_transitions": 0,
        "blocks": [],
        "last_offset": None,
    }


def audit_gap(state, left, right, calendar, events, *, internal, maximum):
    if left >= right:
        return
    duration = int((right - left) / MINUTE)
    counts = Counter()
    block_start, category = left, None
    cursor = left
    previous_offset = state["last_offset"]
    if previous_offset is None:
        previous_offset = (left - MINUTE).tz_convert(calendar.zone).utcoffset().total_seconds()
    transitions = 0
    while cursor < right:
        stop = min(right, cursor + 4096 * MINUTE)
        for stamp in pd.date_range(cursor, stop, freq="min", inclusive="left"):
            kind = calendar.classify(stamp)
            counts[kind] += 1
            offset = stamp.tz_convert(calendar.zone).utcoffset().total_seconds()
            transitions += offset != previous_offset
            previous_offset = offset
            if kind != category:
                if category is not None:
                    add_block(state, block_start, stamp, "missing_" + category, maximum)
                block_start, category = stamp, kind
        cursor = stop
    add_block(state, block_start, right, "missing_" + category, maximum)
    if internal:
        state["gap_count"] += 1
        state["internal_missing_minutes"] += duration
    state["dst_transitions"] += transitions
    state["last_offset"] = previous_offset
    aggregate = Counter(state["missing_by_reason"])
    aggregate.update(counts)
    state["missing_by_reason"] = dict(aggregate)
    events.append(
        {
            "event": "data_gap",
            "start": left.isoformat(),
            "end": right.isoformat(),
            "minutes": duration,
            "internal": internal,
            "classification": dict(counts),
            "historically_verified": calendar.rules["verified"],
            "dst_transitions": transitions,
        }
    )


def add_block(state, left, right, reason, maximum):
    if len(state["blocks"]) >= maximum:
        raise ValueError("Quality exclusion metadata budget exceeded")
    state["blocks"].append({"start": left.isoformat(), "end": right.isoformat(), "reason": reason})


def audit_batch(bars, state, calendar, start, events, maximum):
    for stamp in bars.index:
        if state["last_open"] is not None:
            previous = pd.Timestamp(state["last_open"])
            if stamp <= previous or stamp.value % MINUTE.value:
                raise ValueError("Nonchronological or unaligned raw observation")
            audit_gap(
                state, previous + MINUTE, stamp, calendar, events, internal=True, maximum=maximum
            )
        else:
            audit_gap(state, start, stamp, calendar, events, internal=False, maximum=maximum)
            state["first_open"] = stamp.isoformat()
        classification = calendar.classify(stamp)
        offset = stamp.tz_convert(calendar.zone).utcoffset().total_seconds()
        if state["last_offset"] is not None and offset != state["last_offset"]:
            state["dst_transitions"] += 1
        state["last_offset"] = offset
        if classification != "open":
            state["out_of_session"] += 1
            add_block(state, stamp, stamp + MINUTE, "observed_" + classification, maximum)
            events.append(
                {
                    "event": "out_of_session",
                    "time": stamp.isoformat(),
                    "classification": classification,
                    "historically_verified": calendar.rules["verified"],
                }
            )
        state["rows"] += 1
        state["last_open"] = stamp.isoformat()


def label_integrity(decision, label_end, quality):
    """Offline target admission only; never use future exclusions to gate signals."""
    left = decision - MINUTE
    return not any(
        left < pd.Timestamp(b["end"]) and label_end > pd.Timestamp(b["start"])
        for b in quality["blocks"]
    )
