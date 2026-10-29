"""Single-position reference-price CFD simulation, with no execution API imports."""

import math
from decimal import ROUND_FLOOR, Decimal

import pandas as pd

from trading.research.strat2d.journal import logical_hash

MINUTE = pd.Timedelta(minutes=1)


def initial_portfolio(config):
    return {
        "cash": config["initial_equity"],
        "equity": config["initial_equity"],
        "peak_equity": config["initial_equity"],
        "maximum_drawdown": 0.0,
        "unrealized": 0.0,
        "realized_net": 0.0,
        "transaction_costs": 0.0,
        "reserved_margin": 0.0,
        "position": None,
        "pending": None,
        "last_open": None,
        "last_close": None,
        "fills": 0,
        "trades": 0,
        "last_fill_id": None,
        "last_trade_id": None,
        "trade_history_hash": None,
        "censored": False,
        "blocked_reason": None,
        "rejections": {},
    }


class Portfolio:
    def __init__(self, spec, state=None):
        self.spec, self.state = spec, state or initial_portfolio(spec)
        values = [
            spec[k]
            for k in (
                "initial_equity",
                "risk_fraction",
                "leverage",
                "quantity_step",
                "minimum_quantity",
                "maximum_quantity",
                "stop_multiple",
                "reward_risk",
            )
        ]
        if not all(math.isfinite(x) and x > 0 for x in values):
            raise ValueError("Invalid portfolio limits")
        if spec["risk_fraction"] > 0.005 or spec["leverage"] > 30 or spec["max_positions"] != 1:
            raise ValueError("Portfolio risk/exposure limit exceeded")
        if spec["minimum_quantity"] > spec["maximum_quantity"]:
            raise ValueError("Invalid quantity range")
        if (
            type(spec["horizon_minutes"]) is not int
            or not 1 <= spec["horizon_minutes"] <= 1440
            or not 0 < spec["threshold"] < 1
        ):
            raise ValueError("Invalid horizon or signal threshold")
        if any(
            not math.isfinite(spec[k]) or spec[k] < 0
            for k in ("spread_bps", "slippage_bps", "commission_bps_roundtrip")
        ):
            raise ValueError("Invalid cost scenario")

    def reject(self, reason, events, stamp):
        count = self.state["rejections"]
        count[reason] = count.get(reason, 0) + 1
        events.append({"event": "execution_gate", "time": stamp.isoformat(), "reason": reason})

    def mark(self, price):
        state, position = self.state, self.state["position"]
        state["unrealized"] = (
            position["side"] * position["quantity"] * (price - position["entry"])
            if position
            else 0.0
        )
        state["equity"] = state["cash"] + state["unrealized"]
        state["peak_equity"] = max(state["peak_equity"], state["equity"])
        state["maximum_drawdown"] = max(
            state["maximum_drawdown"],
            (state["peak_equity"] - state["equity"]) / state["peak_equity"],
        )

    def fill_price(self, reference, side):
        return reference * (
            1 + side * (self.spec["spread_bps"] / 2 + self.spec["slippage_bps"]) / 10000
        )

    def exit(self, reference, reason, stamp, events):
        state, p = self.state, self.state["position"]
        exit_price = self.fill_price(reference, -p["side"])
        fee = p["quantity"] * exit_price * self.spec["commission_bps_roundtrip"] / 20000
        gross = p["side"] * p["quantity"] * (exit_price - p["entry"])
        net = gross - p["entry_fee"] - fee
        costs = p["entry_costs"] + p["quantity"] * abs(exit_price - reference) + fee
        state["cash"] += gross - fee
        state["realized_net"] += net
        state["transaction_costs"] += p["quantity"] * abs(exit_price - reference) + fee
        state["fills"] += 1
        state["trades"] += 1
        state["last_fill_id"] = state["fills"]
        state["last_trade_id"] = state["trades"]
        trade = {
            "event": "trade",
            "trade_id": state["trades"],
            "exit_fill_id": state["fills"],
            **p,
            "exit_reference": reference,
            "exit": exit_price,
            "exit_time": stamp.isoformat(),
            "reason": reason,
            "gross_pnl": gross,
            "net_pnl": net,
            "costs": costs,
        }
        state["trade_history_hash"] = logical_hash(
            {"previous": state["trade_history_hash"], "trade": trade}
        )
        events.append(
            {
                "event": "fill",
                "fill_id": state["fills"],
                "kind": "exit",
                "time": stamp.isoformat(),
                "price": exit_price,
                "quantity": p["quantity"],
                "fee": fee,
            }
        )
        events.append(trade)
        state["position"], state["reserved_margin"] = None, 0.0
        self.mark(reference)

    def on_bar(self, stamp, bar, calendar_open, events):
        stamp = pd.Timestamp(stamp)
        prices = [float(bar[k]) for k in ("open", "high", "low", "close")]
        if (
            stamp.tzinfo is None
            or stamp.utcoffset().total_seconds() != 0
            or stamp.value % MINUTE.value
            or not all(math.isfinite(p) and p > 0 for p in prices)
            or prices[1] < max(prices)
            or prices[2] > min(prices)
        ):
            raise ValueError("Invalid portfolio candle integrity/timestamp")
        state = self.state
        if state["last_open"] is not None and stamp <= pd.Timestamp(state["last_open"]):
            raise ValueError("Duplicate or nonchronological portfolio candle")
        gap = state["last_open"] is not None and stamp != pd.Timestamp(state["last_open"]) + MINUTE
        if (gap or not calendar_open) and state["position"] and not state["censored"]:
            # No invented fill on an unobserved path. Retain the exposure, mark it,
            # block new execution and disclose that this episode is not evaluable.
            state["censored"], state["blocked_reason"] = True, "unobservable_position_path"
            events.append(
                {
                    "event": "position_censored",
                    "time": stamp.isoformat(),
                    "reason": state["blocked_reason"],
                    "position": dict(state["position"]),
                }
            )
        pending = state["pending"]
        state["pending"] = None
        if pending:
            if (
                pending["expected_open"] != stamp.isoformat()
                or not calendar_open
                or state["censored"]
            ):
                self.reject("unavailable_next_bar", events, stamp)
            elif state["position"] is not None:
                self.reject("position_busy", events, stamp)
            else:
                side, distance = pending["side"], pending["stop_distance"]
                entry = self.fill_price(float(bar["open"]), side)
                equity = state["cash"]
                estimated_cost = (
                    entry
                    * (
                        self.spec["spread_bps"]
                        + 2 * self.spec["slippage_bps"]
                        + self.spec["commission_bps_roundtrip"]
                    )
                    / 10000
                )
                intended = min(
                    equity * self.spec["risk_fraction"] / (distance + estimated_cost),
                    equity
                    / (
                        entry / self.spec["leverage"]
                        + entry * self.spec["commission_bps_roundtrip"] / 20000
                    ),
                    self.spec["maximum_quantity"],
                )
                step = Decimal(str(self.spec["quantity_step"]))
                quantity = float(
                    (Decimal(str(max(0.0, intended))) / step).to_integral_value(
                        rounding=ROUND_FLOOR
                    )
                    * step
                )
                if quantity < self.spec["minimum_quantity"]:
                    self.reject("quantity_below_minimum", events, stamp)
                else:
                    fee = quantity * entry * self.spec["commission_bps_roundtrip"] / 20000
                    entry_costs = quantity * abs(entry - float(bar["open"])) + fee
                    state["cash"] -= fee
                    state["transaction_costs"] += entry_costs
                    state["fills"] += 1
                    state["last_fill_id"] = state["fills"]
                    state["position"] = {
                        "side": side,
                        "quantity": quantity,
                        "entry": entry,
                        "entry_reference": float(bar["open"]),
                        "entry_time": stamp.isoformat(),
                        "entry_fee": fee,
                        "entry_costs": entry_costs,
                        "entry_fill_id": state["fills"],
                        "stop": entry - side * distance,
                        "target": entry + side * self.spec["reward_risk"] * distance,
                        "bars_held": 0,
                        "model_version": pending["model_version"],
                    }
                    state["reserved_margin"] = quantity * entry / self.spec["leverage"]
                    events.append(
                        {
                            "event": "fill",
                            "kind": "entry",
                            "fill_id": state["fills"],
                            "time": stamp.isoformat(),
                            "price": entry,
                            "quantity": quantity,
                            "fee": fee,
                        }
                    )
        p = state["position"]
        if p and not state["censored"]:
            p["bars_held"] += 1
            stop_hit = (
                float(bar["low"]) <= p["stop"]
                if p["side"] == 1
                else float(bar["high"]) >= p["stop"]
            )
            target_hit = (
                float(bar["high"]) >= p["target"]
                if p["side"] == 1
                else float(bar["low"]) <= p["target"]
            )
            if stop_hit:
                price = (
                    min(float(bar["open"]), p["stop"])
                    if p["side"] == 1
                    else max(float(bar["open"]), p["stop"])
                )
                self.mark(price)
                self.exit(price, "stop_loss", stamp + MINUTE, events)
            else:
                self.mark(float(bar["low"] if p["side"] == 1 else bar["high"]))
                if target_hit:
                    self.exit(p["target"], "take_profit", stamp + MINUTE, events)
                elif p["bars_held"] >= self.spec["horizon_minutes"]:
                    self.exit(float(bar["close"]), "horizon", stamp + MINUTE, events)
        self.mark(float(bar["close"]))
        state["last_open"], state["last_close"] = stamp.isoformat(), float(bar["close"])
        events.append(
            {
                "event": "equity",
                "time": (stamp + MINUTE).isoformat(),
                **{
                    k: state[k]
                    for k in ("cash", "equity", "unrealized", "reserved_margin", "maximum_drawdown")
                },
                "censored": state["censored"],
            }
        )

    def signal(self, decision, probability, stop_distance, model_version, allowed, events):
        if (
            len(probability) != 3
            or not all(math.isfinite(p) and 0 <= p <= 1 for p in probability)
            or not math.isclose(sum(probability), 1, abs_tol=1e-8)
        ):
            raise ValueError("Invalid model probabilities")
        if self.state["censored"] or self.state["position"] or self.state["pending"]:
            return
        side = 1 if probability[1] >= probability[2] else -1
        chosen = 1 if side == 1 else 2
        if (
            allowed
            and math.isfinite(stop_distance)
            and stop_distance > 0
            and probability[chosen] >= max(probability[0], self.spec["threshold"])
        ):
            self.state["pending"] = {
                "expected_open": decision.isoformat(),
                "side": side,
                "stop_distance": stop_distance,
                "model_version": model_version,
            }
            events.append(
                {"event": "pending_order", "time": decision.isoformat(), **self.state["pending"]}
            )
