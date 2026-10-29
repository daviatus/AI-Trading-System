"""Causal bounded features, deterministic targets and versioned small model states."""

import math


class Features:
    def __init__(self, state=None):
        self.state = (
            state
            if state is not None
            else {"history": [], "bucket": None, "count": 0, "completed": []}
        )

    def clear(self):
        self.state.update(history=[], bucket=None, count=0, completed=[])

    def push(self, minute, bar):
        s = self.state
        h = s["history"]
        if h and minute <= h[-1][0]:
            raise ValueError("Duplicate or nonchronological causal observation")
        if h and minute != h[-1][0] + 1:
            self.clear()
            h = s["history"]
        if (
            len(bar) != 4
            or not all(math.isfinite(v) and v > 0 for v in bar)
            or bar[1] < max(bar)
            or bar[2] > min(bar)
        ):
            raise ValueError("Invalid causal OHLC")
        h.append([minute, *bar])
        del h[:-61]
        bucket = minute // 5
        if bucket != s["bucket"]:
            s["bucket"], s["count"] = bucket, 0
        s["count"] += 1
        if minute % 5 == 4 and s["count"] == 5:
            s["completed"].append(bar[3])
            del s["completed"][:-2]
        if len(h) < 31 or len(s["completed"]) < 2:
            return None
        closes = [r[4] for r in h]
        returns = [closes[j] / closes[j - 1] - 1 for j in range(len(closes) - 20, len(closes))]
        average = math.fsum(returns) / 20
        sigma = math.sqrt(math.fsum((r - average) ** 2 for r in returns) / 20)
        floor = max(sigma, 1e-6)
        close = closes[-1]
        momentum = [close / closes[-1 - k] - 1 for k in (1, 5, 15, 30)]
        high, low = max(r[2] for r in h[-20:]), min(r[3] for r in h[-20:])
        position = 0.0 if high == low else 2 * (close - low) / (high - low) - 1
        mean = math.fsum(closes[-20:]) / 20
        angle = 2 * math.pi * ((minute + 1) % 1440) / 1440
        x = [
            *(r / 0.001 for r in momentum),
            momentum[1] / (floor * math.sqrt(5)),
            sigma / 0.0005,
            (bar[1] - bar[2]) / close / 0.001,
            position,
            -(close / mean - 1) / (floor * math.sqrt(20)),
            (s["completed"][-1] / s["completed"][-2] - 1) / 0.001,
            math.sin(angle),
            math.cos(angle),
        ]
        return [max(-5.0, min(5.0, v)) for v in x], sigma


def target(rows, definition):
    horizon = definition["horizon"]
    if len(rows) <= horizon or any(
        b["minute"] != rows[0]["minute"] + i for i, b in enumerate(rows[: horizon + 1])
    ):
        raise ValueError("Target crosses an unobserved path")
    entry, exit_price = rows[1]["bar"][0], rows[horizon]["bar"][3]
    gross = exit_price / entry - 1
    neutral = max(
        definition["neutral_cost_bps"] / 10000,
        definition["volatility_multiple"] * rows[0]["sigma"] * math.sqrt(horizon),
    )
    label = 1 if gross > neutral else 2 if gross < -neutral else 0
    return label, gross * 10000, neutral * 10000


def net_bps(gross_bps, side, cost_bps=2.1):
    if (
        side not in (1, 2)
        or not math.isfinite(gross_bps)
        or cost_bps < 0
        or not math.isfinite(cost_bps)
    ):
        raise ValueError("Invalid directional cost scenario")
    return (gross_bps if side == 1 else -gross_bps) - cost_bps


def phase_of(decision, common_end, protocol):
    from datetime import datetime

    for name, (left, right) in protocol["splits"].items():
        a = int(datetime.fromisoformat(left.replace("Z", "+00:00")).timestamp()) // 60
        b = int(datetime.fromisoformat(right.replace("Z", "+00:00")).timestamp()) // 60
        if name != "train":
            a += protocol["embargo_minutes"]
        if a <= decision < b:
            return name if common_end < b else "purged"
    return "outside"


def safe_observation(stamp, calendar):
    local = stamp.tz_convert(calendar.zone)
    minute = local.hour * 60 + local.minute
    if (
        local.dayofweek == 5
        or (local.dayofweek == 4 and minute >= 960)
        or (local.dayofweek == 6 and minute < 1080)
    ):
        return False
    if 990 <= minute < 1050:
        return False
    return calendar.classify(stamp) == "open"


class Candidate:
    def __init__(self, family, target_id, protocol, base, saved=None):
        from trading.research.strat2d.model import OnlineModel

        self.family, self.target_id = family, target_id
        item = next(m for m in protocol["models"] if m["id"] == family)
        self.spec = dict(base)
        if family != "reference8":
            names = protocol["feature_policy"]["new_features"]
            if family == "interaction16":
                names = [*names, *protocol["feature_policy"]["interactions"]]
            self.spec.update(features=names, scales=[1.0] * len(names))
        self.spec.update(learning_rate=item["learning_rate"], l2=item["l2"], seed=protocol["seed"])
        if saved and (
            saved["schema"] != "strat2g-model-state-v1"
            or saved["family"] != family
            or saved["target"] != target_id
            or saved["spec"] != self.spec
        ):
            raise ValueError("Incompatible experimental model checkpoint")
        self.model = OnlineModel(self.spec, saved["state"] if saved else None)

    def vector(self, row):
        if self.family == "reference8":
            return row["reference"]
        x = row["new"]
        if self.family == "linear12":
            return x
        extra = [x[4] * x[7], x[1] * x[6], x[8] ** 2, x[5] * abs(x[1])]
        return [*x, *(max(-5.0, min(5.0, v)) for v in extra)]

    def snapshot(self):
        return {
            "schema": "strat2g-model-state-v1",
            "family": self.family,
            "target": self.target_id,
            "spec": self.spec,
            "state": self.model.export(),
        }


def select(metrics, days, protocol):
    rule = protocol["selection"]
    if len(days) < rule["minimum_observed_days"]:
        return None
    best = None
    best_loss = None
    for family in rule["complexity_order"]:
        m = metrics.get(family + "_cal")
        if not m or m["n"] < rule["minimum_rows"]:
            continue
        loss = m["loss"] / m["n"]
        if best is None or loss < best_loss - rule["minimum_absolute_logloss_improvement"]:
            best, best_loss = family, loss
    return best
