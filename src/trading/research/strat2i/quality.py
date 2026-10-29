"""Constant row state plus bounded day counters; no filling, sorting or repairs."""

import math
from datetime import UTC, datetime

from trading.research.strat2d.journal import logical_hash


def initial():
    return {
        "rows": 0,
        "previous": None,
        "first": None,
        "chain": None,
        "days": {},
        "internal_gaps": 0,
        "internal_minutes": 0,
        "edge_minutes": 0,
        "internal_missing": {},
        "edge_missing": {},
        "gaps_touching_open": 0,
        "outside": 0,
        "outside_times": [],
        "segment": 0,
        "max_segment": 0,
        "gap_label_blocks": {str(h): 0 for h in (5, 15, 30)},
        "precision_warnings": 0,
        "zero_volume": 0,
        "invalid_volume": 0,
        "jumps_contiguous": 0,
        "jumps_across_gap": 0,
        "max_jump_bps": 0,
        "maximum_spacing_minutes": 0,
        "dst_offsets": [],
        "duplicates": 0,
        "conflicts": 0,
        "out_of_order": 0,
        "invalid_ohlc": 0,
    }


def stamp(minute):
    import pandas as pd

    return pd.Timestamp(minute * 60, unit="s", tz="UTC")


def day(s, minute):
    key = datetime.fromtimestamp(minute * 60, UTC).date().isoformat()
    if key not in s["days"]:
        if len(s["days"]) >= 64:
            raise ValueError("Day counter budget exceeded")
        s["days"][key] = {}
    return s["days"][key]


def bump(mapping, name, n=1):
    mapping[name] = mapping.get(name, 0) + n


def missing(s, left, right, calendar, events, internal, guard=lambda: None):
    if left >= right:
        return
    counts = {}
    for minute in range(left, right):
        if (minute - left) % 256 == 0:
            guard()
        category = calendar.classify(stamp(minute))
        bump(counts, category)
        bump(day(s, minute), "missing_" + category)
    target = s["internal_missing"] if internal else s["edge_missing"]
    for k, value in counts.items():
        bump(target, k, value)
    if internal:
        s["internal_gaps"] += 1
        s["internal_minutes"] += right - left
        s["gaps_touching_open"] += int(counts.get("open", 0) > 0)
    else:
        s["edge_minutes"] += right - left
    events.append(
        {
            "event": "missing_interval",
            "start_minute": left,
            "end_minute": right,
            "minutes": right - left,
            "internal": internal,
            "classification": counts,
            "verified": calendar.rules["verified"],
        }
    )


def row_digest(previous, minute, prices, volume):
    return logical_hash([previous, minute, list(prices), volume])


def observe(
    s, minute, prices, volume, calendar, events, left, right, jump_bps=20, guard=lambda: None
):
    if type(minute) is not int or not left <= minute < right:
        raise ValueError("Timestamp outside authorized minute scope")
    previous = s["previous"]
    row = [minute, *prices, volume]
    if previous and minute <= previous[0]:
        if minute == previous[0]:
            key = "duplicates" if row == previous else "conflicts"
            s[key] += 1
            raise ValueError("Duplicate candle: " + key)
        s["out_of_order"] += 1
        raise ValueError("Out-of-order timestamp; bounded audit stops without sorting")
    if (
        len(prices) != 4
        or not all(math.isfinite(p) and p > 0 for p in prices)
        or prices[1] < max(prices)
        or prices[2] > min(prices)
    ):
        s["invalid_ohlc"] += 1
        raise ValueError("Invalid nonfinite/nonpositive/inconsistent OHLC")
    spacing = minute - previous[0] if previous else 1
    if previous:
        s["maximum_spacing_minutes"] = max(s["maximum_spacing_minutes"], spacing)
        if spacing > 1:
            for h in s["gap_label_blocks"]:
                s["gap_label_blocks"][h] += min(int(h), s["segment"])
            missing(s, previous[0] + 1, minute, calendar, events, True, guard)
            s["segment"] = 0
        jump = abs(prices[0] / previous[4] - 1) * 10000
        s["max_jump_bps"] = max(s["max_jump_bps"], jump)
        if jump >= jump_bps:
            s["jumps_contiguous" if spacing == 1 else "jumps_across_gap"] += 1
            events.append(
                {
                    "event": "jump_warning",
                    "minute": minute,
                    "bps": jump,
                    "spacing": spacing,
                    "interpretation": "warning only, not proof of bad price",
                }
            )
    else:
        missing(s, left, minute, calendar, events, False, guard)
        s["first"] = minute
    s["segment"] += 1
    s["max_segment"] = max(s["max_segment"], s["segment"])
    s["precision_warnings"] += sum(abs(p * 100000 - round(p * 100000)) > 1e-6 for p in prices)
    s["zero_volume"] += int(volume == 0)
    s["invalid_volume"] += int(volume is None or volume < 0)
    category = calendar.classify(stamp(minute))
    bump(day(s, minute), "observed_open" if category == "open" else "observed_outside")
    if category != "open":
        if len(s["outside_times"]) >= 4096:
            raise ValueError("Boundary evidence budget exceeded")
        s["outside"] += 1
        s["outside_times"].append(minute)
        events.append({"event": "outside_observation", "minute": minute, "hypothesis": category})
    offset = stamp(minute).tz_convert(calendar.zone).utcoffset().total_seconds()
    if offset not in s["dst_offsets"]:
        s["dst_offsets"].append(offset)
    s["rows"] += 1
    s["previous"] = row
    s["chain"] = row_digest(s["chain"], minute, prices, volume)
