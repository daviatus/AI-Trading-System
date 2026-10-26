"""Artifact-only Phase 5B diagnostics. No fitting, inference, backtesting or DB access.

Only small JSON objects and 8,192-row Arrow batches are retained. Re-running the
same inputs produces identical bytes. Changed inputs require a new output version.
"""

import argparse
import hashlib
import json
import math
import os
from collections import Counter
from datetime import UTC, datetime, timedelta
from itertools import zip_longest

from trading.research.safety import ROOT, ResearchSafetyStop, resources

BATCH_ROWS = 8192
MINUTE_NS = 60_000_000_000
SOURCE_DIRS = (
    "bars",
    "cache",
    "choices",
    "evaluation",
    "features",
    "models",
    "prepared",
    "quality",
    "results",
)
SOURCE_FILES = ("config.json", "contracts.json", "snapshot.json", "environment.json", "results.md")
CODE_FILES = (
    "research/diagnostics.py",
    "research/runner.py",
    "research/data.py",
    "research/execution.py",
    "research/models.py",
    "research/safety.py",
    "data/dataset.py",
    "ctrader/calendar.py",
)
MISSING_GATES = (
    "incomplete_features",
    "directional_probability_below_threshold",
    "nonpositive_expected_payoff",
    "abnormal_regime",
    "session_swap_or_utc_day_boundary",
    "shared_position_busy",
    "outside_evaluation_or_horizon_end",
    "daily_loss_budget",
    "missing_contiguous_entry_or_atr",
    "quantity_below_contract_minimum",
)


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def encoded(value):
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"


def fact(value, status, sources, reason=None):
    result = {"value": value, "status": status, "sources": sources}
    if reason:
        result["reason"] = reason
    return result


class Evidence:
    def __init__(self, source):
        self.source = source
        self.missing = set()

    def path(self, relative):
        path = (self.source / relative).resolve()
        if not path.is_relative_to(self.source):
            raise ValueError("Source artifact escapes the frozen research directory")
        if not path.is_file():
            self.missing.add(relative)
            return None
        return path

    def read(self, relative):
        path = self.path(relative)
        return json.loads(path.read_text(encoding="utf-8")) if path else None


def inventory(source):
    paths = [source / name for name in SOURCE_FILES]
    for directory in SOURCE_DIRS:
        paths.extend(sorted((source / directory).rglob("*")))
    result = {}
    for path in paths:
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(source):
            raise ValueError("Source symlink escapes research directory")
        result[path.relative_to(source).as_posix()] = {
            "bytes": path.stat().st_size,
            "sha256": digest(path),
        }
    return result


def batches(path, config, columns=None):
    # Import only after the unmodified resource gate, without the experiment modules.
    import pyarrow.parquet as pq

    file = pq.ParquetFile(path)
    for batch in file.iter_batches(batch_size=BATCH_ROWS, columns=columns, use_threads=False):
        resources(config)
        yield batch


class TradeTotals:
    def __init__(self, initial):
        self.initial = initial
        self.n = self.wins = self.losses = self.unresolved = self.gaps = self.ambiguous = 0
        self.pnl = self.positive = self.negative = self.cost = 0.0
        self.peak = float(initial)
        self.close_dd = 0.0
        self.days = set()
        self.coverage = Counter()

    def add(self, row):
        pnl = float(row["net_pnl"])
        if not math.isfinite(pnl):
            raise ValueError("Nonfinite recorded trade PnL")
        self.n += 1
        self.wins += pnl > 0
        self.losses += pnl < 0
        self.pnl += pnl
        self.positive += max(0, pnl)
        self.negative += min(0, pnl)
        for name in ("cost", "unresolved_terminal_mark", "data_gap_exit", "ambiguous"):
            self.coverage[name] += row.get(name) is not None
        self.cost += row.get("cost") or 0
        self.unresolved += bool(row.get("unresolved_terminal_mark"))
        self.gaps += bool(row.get("data_gap_exit"))
        self.ambiguous += bool(row.get("ambiguous"))
        self.days.add(utc(row["entry_time"]).date())
        capital = self.initial + self.pnl
        self.peak = max(self.peak, capital)
        self.close_dd = max(self.close_dd, 1 - capital / self.peak)

    def result(self):
        return {
            "trades": self.n,
            "net_pnl_usd": self.pnl,
            "positive_pnl_usd": self.positive,
            "negative_pnl_usd": self.negative,
            "net_return_contribution": self.pnl / self.initial,
            "profit_factor": self.positive / -self.negative if self.negative < 0 else None,
            "profit_factor_unavailable_reason": None if self.negative < 0 else "no losing trades",
            "realized_attribution_drawdown": self.close_dd,
            "maximum_mark_to_market_drawdown": None,
            "drawdown_unavailable_reason": "no per-asset marked equity attribution recorded",
            "winning_trades": self.wins,
            "losing_trades": self.losses,
            "flat_trades": self.n - self.wins - self.losses,
            "active_entry_days": len(self.days),
            "total_cost_usd": self.cost if self.coverage["cost"] == self.n else None,
            "unresolved_terminal_positions": self.unresolved
            if self.coverage["unresolved_terminal_mark"] == self.n
            else None,
            "gap_exits": self.gaps if self.coverage["data_gap_exit"] == self.n else None,
            "ambiguous_trades": self.ambiguous if self.coverage["ambiguous"] == self.n else None,
            "missing_trade_fields": [
                k
                for k in ("cost", "unresolved_terminal_mark", "data_gap_exit", "ambiguous")
                if self.coverage[k] < self.n
            ],
            "trade_count_uncertainty": "small sample"
            if self.n < 100
            else "dependent trades; count alone does not establish significance",
        }


def chronological_trade_errors(row, previous_exit, fold):
    entry, exit_ = utc(row["entry_time"]), utc(row["exit_time"])
    return {
        "outside_window": int(
            not (
                utc(fold["tuning_end"]) <= entry < utc(fold["test_end"])
                and entry <= exit_ <= utc(fold["test_end"])
            )
        ),
        "overlapping_shared_positions": int(previous_exit is not None and entry < previous_exit),
        "not_minute_aligned": int(any(t.second or t.microsecond for t in (entry, exit_))),
    }


def summarize_trades(path, assets, initial, fold, config):
    totals = {asset: TradeTotals(initial) for asset in assets}
    checks = Counter(
        {"outside_window": 0, "overlapping_shared_positions": 0, "not_minute_aligned": 0}
    )
    previous_exit = None
    for batch in batches(path, config):
        for row in batch.to_pylist():
            checks.update(chronological_trade_errors(row, previous_exit, fold))
            previous_exit = utc(row["exit_time"])
            if row["asset"] not in totals:
                raise ValueError("Unexpected recorded trade asset")
            totals[row["asset"]].add(row)
    return {a: t.result() for a, t in totals.items()}, dict(checks)


def gate_attribution(choice):
    if choice is None:
        return fact(None, "unavailable", [], "choice artifact missing")
    candidates = choice.get("candidates", [])
    counts = Counter(
        {
            "below_30_trades": 0,
            "unresolved_exit": 0,
            "nonpositive_score": 0,
            "positive_eligible": 0,
            "missing_provenance": 0,
        }
    )
    marginal = Counter({"below_30_trades": 0, "unresolved_exit": 0})
    score_mismatches = 0
    for candidate in candidates:
        m = candidate.get("performance", {})
        if any(
            k not in m
            for k in (
                "trades",
                "unresolved_terminal_positions",
                "net_return",
                "maximum_mark_to_market_drawdown",
            )
        ):
            counts["missing_provenance"] += 1
            continue
        low, unresolved = m["trades"] < 30, m["unresolved_terminal_positions"] > 0
        marginal["below_30_trades"] += low
        marginal["unresolved_exit"] += unresolved
        score = m["net_return"] - 0.5 * m["maximum_mark_to_market_drawdown"]
        expected_score = -1e6 if low or unresolved else score
        score_mismatches += not math.isclose(
            candidate.get("selection_score", math.nan), expected_score, abs_tol=1e-12
        )
        reason = (
            "below_30_trades"
            if low
            else "unresolved_exit"
            if unresolved
            else "nonpositive_score"
            if score <= 0
            else "positive_eligible"
        )
        counts[reason] += 1
    selected = choice.get("selected", {})
    active = selected.get("active")
    return {
        "status": "measured",
        "candidate_count": len(candidates),
        "exclusive_reasons_in_order": dict(counts),
        "marginal_reasons": dict(marginal),
        "recorded_selected_active": active,
        "recorded_score": selected.get("selection_score"),
        "score_reconstruction_mismatches": score_mismatches,
        "activation_consistent_with_candidates": (active == bool(counts["positive_eligible"]))
        if candidates and not counts["missing_provenance"]
        else None,
        "evaluation_behavior": fact(
            "skip selected signal generation"
            if active is False
            else "unknown"
            if active is None
            else "generate selected signals",
            "inferred",
            ["code:research/runner.py"],
            "current evaluator control flow; historical code hash absent",
        ),
        "historical_signal_and_execution_rejection_counts": {
            gate: fact(None, "unavailable", [], "rejected-decision trace was not persisted")
            for gate in MISSING_GATES
        },
    }


def audit_training(training, fold, horizon):
    required = ("train_last", "calibration_first", "calibration_last")
    if any(k not in training for k in required):
        return fact(None, "unavailable", [], "training boundary metadata missing")
    last, first_cal, last_cal = (utc(training[k]) for k in required)
    return fact(
        {
            "train_label_end_before_train_boundary": last + timedelta(minutes=horizon)
            < utc(fold["train_end"]),
            "disjoint_train_calibration": last < first_cal,
            "calibration_starts_at_or_after_train_boundary": first_cal >= utc(fold["train_end"]),
            "calibration_label_end_before_calibration_boundary": last_cal
            + timedelta(minutes=horizon)
            < utc(fold["calibration_end"]),
            "calibration_ordered": first_cal <= last_cal,
        },
        "measured",
        [],
        "recorded extrema; individual sampled row identities were not stored",
    )


def scan_alignment(evidence, asset, config):
    bpath = evidence.path(f"bars/{asset}.parquet")
    fpath = evidence.path(f"features/{asset}.parquet")
    if bpath is None or fpath is None:
        return fact(None, "unavailable", [], "bars or features artifact missing")
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    bschema, fschema = pq.ParquetFile(bpath).schema_arrow, pq.ParquetFile(fpath).schema_arrow
    if "opened_at" not in bschema.names or "opened_at" not in fschema.names:
        return fact(None, "unavailable", [], "opened_at column absent")
    counts = Counter()
    windows = {f["name"]: {"bar_rows": 0, "complete_feature_rows": 0} for f in config["folds"]}
    previous, run_length = None, 0
    for bars, features in zip_longest(
        batches(bpath, config, ["opened_at"]), batches(fpath, config)
    ):
        if bars is None or features is None or bars.num_rows != features.num_rows:
            return fact(None, "unavailable", [], "bars/features row counts differ")
        stamps = (
            bars.column(0)
            .cast(pa.timestamp("ns", tz="UTC"))
            .cast("int64")
            .to_numpy(zero_copy_only=False)
        )
        fstamps = (
            features.column("opened_at")
            .cast(pa.timestamp("ns", tz="UTC"))
            .cast("int64")
            .to_numpy(zero_copy_only=False)
        )
        counts["misaligned_rows"] += int((stamps != fstamps).sum())
        valid = np.ones(len(stamps), bool)
        for name in fschema.names:
            if name != "opened_at":
                valid &= np.isfinite(features.column(name).to_numpy(zero_copy_only=False))
        differences = np.diff(stamps)
        if previous is not None and len(stamps):
            differences = np.r_[stamps[0] - previous, differences]
        counts["duplicate_adjacent_timestamps"] += int((differences == 0).sum())
        counts["backwards_timestamps"] += int((differences < 0).sum())
        counts["non_minute_aligned_rows"] += int((stamps % MINUTE_NS != 0).sum())
        # Check state reset through the first 63 rows of every contiguous segment.
        for stamp, complete in zip(stamps, valid, strict=True):
            run_length = (
                run_length + 1 if previous is not None and stamp - previous == MINUTE_NS else 1
            )
            counts["complete_features_during_63_bar_warmup"] += int(complete and run_length < 64)
            previous = stamp
        counts["rows"] += len(stamps)
        counts["complete_feature_rows"] += int(valid.sum())
        for fold in config["folds"]:
            start = int(utc(fold["tuning_end"]).timestamp()) * 1_000_000_000
            end = int(utc(fold["test_end"]).timestamp()) * 1_000_000_000
            mask = (stamps >= start) & (stamps < end)
            windows[fold["name"]]["bar_rows"] += int(mask.sum())
            windows[fold["name"]]["complete_feature_rows"] += int((mask & valid).sum())
    return fact(
        {
            "checks": dict(counts),
            "windows": windows,
            "bars_timezone": str(bschema.field("opened_at").type),
            "features_timezone": str(fschema.field("opened_at").type),
        },
        "measured",
        [f"bars/{asset}.parquet", f"features/{asset}.parquet"],
    )


def audit_equity(path, recorded, config):
    peak, dd, final, previous = float(config["initial_equity"]), 0.0, None, None
    errors, count = 0, 0
    for batch in batches(path, config):
        for row in batch.to_pylist():
            value = float(row["equity"])
            if not math.isfinite(value):
                raise ValueError("Nonfinite recorded equity")
            timestamp = next(v for k, v in row.items() if k != "equity")
            errors += int(previous is not None and timestamp <= previous)
            previous = timestamp
            peak = max(peak, value)
            dd = max(dd, 1 - value / peak)
            final, count = value, count + 1
    return {
        "status": "measured",
        "mark_rows": count,
        "chronological_errors": errors,
        "recomputed_net_return": final / config["initial_equity"] - 1
        if final is not None
        else None,
        "recomputed_close_drawdown": dd if count else None,
        "drawdown_matches_result": math.isclose(
            dd, recorded["maximum_mark_to_market_drawdown"], abs_tol=1e-9
        )
        if recorded and count
        else None,
        "net_return_matches_result": math.isclose(
            final / config["initial_equity"] - 1, recorded["net_return"], abs_tol=1e-9
        )
        if recorded and count
        else None,
    }


def summarize_opportunities(path, assets, config, sample_counts):
    counts = {
        a: {
            "rows": 0,
            "long": 0,
            "short": 0,
            "probability_sum": 0.0,
            "probability_min": None,
            "probability_max": None,
            "histogram": [0] * 10,
        }
        for a in assets
    }
    for batch in batches(path, config):
        for row in batch.to_pylist():
            r = counts[row["asset"]]
            p = float(row["probability"])
            if not math.isfinite(p) or not 0 <= p <= 1:
                raise ValueError("Invalid recorded opportunity probability")
            r["rows"] += 1
            r["long" if row["direction"] == 1 else "short"] += 1
            r["probability_sum"] += p
            r["probability_min"] = (
                p if r["probability_min"] is None else min(p, r["probability_min"])
            )
            r["probability_max"] = (
                p if r["probability_max"] is None else max(p, r["probability_max"])
            )
            r["histogram"][min(int(p * 10), 9)] += 1
    for asset, r in counts.items():
        r["probability_mean"] = r.pop("probability_sum") / r["rows"] if r["rows"] else None
        denominator = sample_counts.get(asset)
        r["complete_feature_rows"] = denominator
        r["accepted_opportunities_per_complete_feature_row"] = (
            r["rows"] / denominator if denominator else None
        )
        r["distribution_scope"] = "chosen-side probability CONDITIONAL on accepted opportunity"
        r["histogram_bins"] = "[0,.1),[.1,.2),...,[.9,1]; fixed descriptive bins"
    return counts


def missing_minutes(quality, fold):
    if quality is None or "missing_intervals" not in quality:
        return None
    start, end = utc(fold["tuning_end"]), utc(fold["test_end"])
    return sum(
        max(
            0,
            (min(end, utc(r["end_exclusive"])) - max(start, utc(r["start"]))).total_seconds() / 60,
        )
        for r in quality["missing_intervals"]
    )


def window_diagnostics(e, config, fold, alignment):
    name, assets = fold["name"], config["assets"]
    result_path = f"results/{name}.json"
    result = e.read(result_path)
    choices = {a: e.read(f"choices/{name}/{a}.json") for a in assets}
    frozen_path = f"evaluation/{name}-frozen.json"
    frozen = e.read(frozen_path)
    frozen_checks = fact(None, "unavailable", [frozen_path], "frozen lock missing")
    if frozen:
        hashed = hashlib.sha256(json.dumps(frozen["choices"], sort_keys=True).encode()).hexdigest()
        frozen_checks = fact(
            {
                "lock_hash_valid": hashed == frozen["choice_hash"],
                "choices_equal_frozen_lock": choices == frozen["choices"],
                "holdout_flag_matches": frozen.get("holdout") == fold["holdout"],
            },
            "measured",
            [frozen_path, *[f"choices/{name}/{a}.json" for a in assets]],
        )
    window = {
        "fold": fold,
        "recorded_fold_integrity": fact(
            result.get("fold") == fold if result else None,
            "measured" if result else "unavailable",
            [result_path],
            "result boundaries must match the frozen config",
        ),
        "frozen_integrity": frozen_checks,
        "selection_gates": {},
        "portfolio": {},
        "equity_audits": {},
        "by_asset": {},
        "opportunities": {},
        "models": {},
        "predictive": fact(
            result.get("predictive") if result else None,
            "measured" if result else "unavailable",
            [result_path],
        ),
        "missing_open_minutes_by_asset": {
            a: missing_minutes(e.read(f"quality/{a}.json"), fold) for a in assets
        },
    }
    sample_counts = {}
    for asset in assets:
        choice_path = f"choices/{name}/{asset}.json"
        window["selection_gates"][asset] = {
            **gate_attribution(choices[asset]),
            "sources": [choice_path],
        }
        aligned = alignment[asset]["value"]
        sample_counts[asset] = (
            aligned["windows"][name]["complete_feature_rows"]
            if aligned and not aligned["checks"]["misaligned_rows"]
            else None
        )
        window["models"][asset] = []
        for horizon in config["horizons"]:
            for number in range(len(config["models"])):
                relative = f"models/{name}/{asset}/h{horizon}-m{number}.json"
                meta = e.read(relative)
                if meta:
                    window["models"][asset].append(
                        {
                            "sources": [relative],
                            "status": "measured",
                            "horizon": horizon,
                            "model_number": number,
                            "training": meta.get("training"),
                            "calibrator": meta.get("calibrator"),
                            "chronology": audit_training(meta.get("training", {}), fold, horizon),
                            "calibration_caveat": "fitted scores use calibration fitting rows",
                        }
                    )
        if choices[asset]:
            window.setdefault("thresholds", {})[asset] = {
                strategy: {
                    k: choices[asset][strategy].get(k)
                    for k in ("threshold", "regime_filter", "horizon", "model_number", "active")
                }
                for strategy in ("xgb_baseline", "selected")
            }
            # Same predictions were reused across thresholds and regimes. Retain each model once.
            unique = {}
            for i, candidate in enumerate(choices[asset].get("candidates", [])):
                key = f"h{candidate['horizon']}-m{candidate['model_number']}"
                unique.setdefault(
                    key,
                    fact(
                        candidate.get("predictive"),
                        "measured",
                        [f"{choice_path}#/candidates/{i}/predictive"],
                    ),
                )
            window.setdefault("tuning_predictive", {})[asset] = unique
    expected_keys = [
        f"{s}:cost{m:g}"
        for s in ("trend_baseline", "xgb_baseline", "selected")
        for m in config["cost_multipliers"]
    ] + ["cash"]
    for key in expected_keys:
        recorded = result.get("strategies", {}).get(key) if result else None
        window["portfolio"][key] = fact(
            recorded,
            "measured" if recorded else "unavailable",
            [result_path],
            None if recorded else "strategy result missing",
        )
        if key == "cash":
            window["by_asset"][key] = fact(
                None,
                "unavailable",
                [result_path],
                "cash is a portfolio comparator with no asset allocation",
            )
            continue
        equity_relative = f"evaluation/{name}/{key.replace(':', '-')}-equity.parquet"
        equity_path = e.path(equity_relative)
        window["equity_audits"][key] = (
            {**audit_equity(equity_path, recorded, config), "sources": [equity_relative]}
            if equity_path
            else fact(None, "unavailable", [equity_relative], "equity curve missing")
        )
        relative = f"evaluation/{name}/{key.replace(':', '-')}-trades.parquet"
        path = e.path(relative)
        if path:
            metrics, checks = summarize_trades(path, assets, config["initial_equity"], fold, config)
            window["by_asset"][key] = fact(
                metrics,
                "measured",
                [relative],
                "PnL/initial portfolio equity is contribution, not standalone return",
            )
            window["by_asset"][key]["chronological_errors"] = checks
            if recorded:
                window["by_asset"][key]["reconciliation"] = {
                    "trade_count_matches": sum(m["trades"] for m in metrics.values())
                    == recorded["trades"],
                    "net_return_matches": math.isclose(
                        sum(m["net_return_contribution"] for m in metrics.values()),
                        recorded["net_return"],
                        abs_tol=1e-9,
                    ),
                    "unresolved_count_matches": (
                        sum(m["unresolved_terminal_positions"] for m in metrics.values())
                        == recorded.get("unresolved_terminal_positions")
                        if all(
                            m["unresolved_terminal_positions"] is not None for m in metrics.values()
                        )
                        else None
                    ),
                }
        else:
            window["by_asset"][key] = fact(None, "unavailable", [relative], "trade ledger missing")
    for strategy in ("trend_baseline", "xgb_baseline", "selected"):
        relative = f"evaluation/{name}/{strategy}-opportunities.parquet"
        path = e.path(relative)
        window["opportunities"][strategy] = fact(
            summarize_opportunities(path, assets, config, sample_counts) if path else None,
            "measured" if path else "unavailable",
            [relative],
            "accepted signals only; rejection counts and full probability distribution unavailable",
        )
    return window


AUDIT_NOTES = [
    (
        "inferred",
        "Current features use past/current completed OHLC and reset after gaps; "
        "targets are separate. Frozen artifacts do not record an executed-source commit/hash.",
    ),
    (
        "inferred",
        "Current purged_masks requires timestamp+horizon strictly before each train, "
        "calibration and tuning boundary. Recorded model extrema are checked separately. "
        "Individual sampled indices and label-validity masks were not saved.",
    ),
    (
        "inferred",
        "Adjacent 15/60-minute labels can share up to 14/59 future bars. "
        "Boundary purging does not make within-window labels independent; "
        "effective sample size unknown.",
    ),
    (
        "inferred",
        "Signal timestamp is completed bar open+1 minute; entry is next contiguous "
        "bar open at that timestamp. Current fills reject missing entry bars; "
        "SL wins ambiguous OHLC ordering; adverse gaps use worse prices. "
        "Tick-level evidence is unavailable.",
    ),
    (
        "unavailable",
        "Historical bar publication/revision timestamps were not recorded. "
        "The snapshot and opened_at alignment cannot prove original decision-time availability.",
    ),
    (
        "inferred",
        "Current execution excludes known session/swap/day crossings. Costs and frozen "
        "current contract schedules are scenarios; historical sessions, holidays, "
        "bid/ask, contract "
        "changes, fill ordering and dynamic leverage are unverified. Price type is unknown.",
    ),
    (
        "inferred",
        "Terminal unresolved positions are marked rather than filled. Current outcomes "
        "checks gaps before exhaustion, so dataset-end paths require scrutiny. "
        "Recorded unresolved counts alone cannot prove correct terminal classification. "
        "No outcomes are rerun.",
    ),
    (
        "unavailable",
        "No rejected-decision trace, full class-probability stream, per-row labels, "
        "reliability bins, ECE, historical source-code hash or independent sample size recorded.",
    ),
    (
        "inferred",
        "Cost scenarios share opportunities, but changed sizing, minimum quantities, equity "
        "and daily budgets can change executed trade counts. "
        "These are not paired fixed-trade cost shifts.",
    ),
    (
        "inferred",
        "Current evaluation disables the expected-payoff gate for XGB baseline; "
        "selected signals would retain it if activated. Development candidate tuning used "
        "that gate even for the stored baseline candidate. Trend directions use fixed indicators, "
        "but cross-asset ranking still uses training payoff profiles. These policies differ.",
    ),
    (
        "inferred",
        "2026 is diagnostic evidence only. No prediction, threshold search, calibration fit, "
        "candidate selection, model training or backtest is performed by diagnostics.",
    ),
]


def build(config, source, manifest):
    e = Evidence(source)
    quality, prepared, alignment = {}, {}, {}
    for asset in config["assets"]:
        resources(config)
        q = e.read(f"quality/{asset}.json")
        quality[asset] = fact(
            {k: v for k, v in q.items() if k != "missing_intervals"} if q else None,
            "measured" if q else "unavailable",
            [f"quality/{asset}.json"],
        )
        prepared[asset] = e.read(f"prepared/{asset}.json")
        alignment[asset] = scan_alignment(e, asset, config)
    snapshot = e.read("snapshot.json")
    checks = {}
    if snapshot:
        for relative, part in snapshot.get("parts", {}).items():
            e.path(relative)
            checks[relative] = manifest.get(relative, {}).get("sha256") == part.get("sha256")
        fingerprint = hashlib.sha256(
            json.dumps(snapshot["parts"], sort_keys=True).encode()
        ).hexdigest()
    else:
        fingerprint = None
    windows = [window_diagnostics(e, config, fold, alignment) for fold in config["folds"]]
    return {
        "schema_version": 1,
        "diagnostics_version": "phase5b-v1",
        "mode": "DEMO/SIMULATION; local artifact reads only",
        "source_directory": source.relative_to(ROOT).as_posix(),
        "evidence_conventions": {
            "measured": "recorded artifact or deterministic aggregation",
            "inferred": "interpretation of recorded evidence/current code",
            "unavailable": "missing provenance; null is not zero",
        },
        "source_manifest": manifest,
        "current_code_sha256": {name: digest(ROOT / "src/trading" / name) for name in CODE_FILES},
        "frozen_config": config,
        "chronological_protocol": chronological_protocol(config),
        "quality": quality,
        "prepared": prepared,
        "snapshot_integrity": {
            "complete": snapshot.get("complete") if snapshot else None,
            "part_checksums": checks,
            "prepared_snapshot_fingerprints_match": {
                a: p.get("source_snapshot_fingerprint") == fingerprint
                if p and fingerprint
                else None
                for a, p in prepared.items()
            },
        },
        "alignment": alignment,
        "windows": windows,
        "audit": [
            {
                "status": status,
                "text": text,
                "sources": [
                    "code:research/data.py",
                    "code:research/models.py",
                    "code:research/execution.py",
                    "code:research/runner.py",
                    "contracts.json",
                    "snapshot.json",
                ],
            }
            for status, text in AUDIT_NOTES
        ],
        "missing_artifacts": sorted(e.missing),
        "next_iteration": "Use development/calibration windows only: record gate funnels, "
        "full probabilities/labels and code/data hashes; "
        "validate historical execution/calendar/cost "
        "assumptions; investigate inactivity and probability drift using 2024/2025 development "
        "evidence with purged chronological validation. Pre-register changes in a new research "
        "version. Keep 2026 untouched; it cannot become a threshold-selection or tuning set.",
    }


def chronological_protocol(config):
    tests, holdouts = [], []
    for fold in config["folds"]:
        bounds = [utc(fold[k]) for k in ("train_end", "calibration_end", "tuning_end", "test_end")]
        if not all(a < b for a, b in zip(bounds[:-1], bounds[1:], strict=True)):
            raise ValueError("Chronological boundaries must increase")
        tests.append((bounds[2], bounds[3]))
        if fold["holdout"]:
            holdouts.append(bounds[2])
        if "start" in config and bounds[0] <= utc(config["start"]):
            raise ValueError("Chronological train boundary outside snapshot")
        if "end" in config and bounds[3] > utc(config["end"]):
            raise ValueError("Chronological test boundary outside snapshot")
    for i, (start, end) in enumerate(tests):
        if any(start < other_end and other_start < end for other_start, other_end in tests[:i]):
            raise ValueError("Chronological evaluation windows overlap")
    if holdouts:
        for fold in config["folds"]:
            if not fold["holdout"] and utc(fold["test_end"]) > min(holdouts):
                raise ValueError("Development window crosses final holdout")
    return fact(
        {
            "boundaries_strictly_increase": True,
            "evaluation_windows_disjoint": True,
            "development_ends_before_holdout": True if holdouts else None,
        },
        "measured",
        ["config.json"],
    )


def fmt(value, percent=False):
    return "unavailable" if value is None else f"{value:.2%}" if percent else f"{value:.3f}"


def markdown(report):
    lines = [
        "# Phase 5B research diagnostics",
        "",
        report["mode"],
        "",
        "2026 is read-only evidence. No models, thresholds or trading decisions were changed.",
        "All tables are measured unless explicitly labelled inferred/unavailable. JSON includes "
        "source paths, SHA-256 hashes, sample counts and checks. Null means unavailable.",
        "",
        "## Available artifacts",
        "",
        "| Category | Files | Bytes |",
        "| --- | ---: | ---: |",
    ]
    categories = {}
    for name, info in report["source_manifest"].items():
        group = name.split("/")[0] if "/" in name else "root metadata/report"
        n, size = categories.get(group, (0, 0))
        categories[group] = n + 1, size + info["bytes"]
    lines += [f"| {k} | {n} | {size} |" for k, (n, size) in sorted(categories.items())]
    lines += [
        "",
        "No rejected-signal trace or full probability stream was found in the runner's "
        "artifact schema. Opportunity probabilities describe accepted signals only.",
        "",
        "## Portfolio outcomes and cost sensitivity",
        "",
        "| Window | Strategy/cost | Trades | Net | Close MTM DD | PF |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for w in report["windows"]:
        for key, wrapped in w["portfolio"].items():
            m = wrapped["value"] or {}
            lines.append(
                f"| {w['fold']['name']} | {key} | {m.get('trades', 'unavailable')} | "
                f"{fmt(m.get('net_return'), True)} | "
                f"{fmt(m.get('maximum_mark_to_market_drawdown'), True)} | "
                f"{fmt(m.get('profit_factor'))} |"
            )
    lines += [
        "",
        "Profit factor is undefined without losing trades. Cash has no per-asset allocation. "
        "Stored block-bootstrap intervals and predictive sample/class counts are in JSON. "
        "Small and dependent trade samples cannot establish a reliable edge. Cost scenarios can "
        "execute different trades, so improving PF at higher costs does not establish robustness.",
        "",
        "## Per-asset executed attribution",
        "",
        "Contribution = asset net PnL / initial portfolio equity. It is not a standalone backtest. "
        "Per-asset minute-close MTM drawdown is unavailable. Realized DD below uses only the "
        "asset's closed PnL on constant starting capital and omits intratrade marks.",
        "",
        "| Window | Strategy/cost | Asset | Trades | Contribution | Realized DD | PF | Days |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for w in report["windows"]:
        for key, wrapped in w["by_asset"].items():
            for asset in report["frozen_config"]["assets"] if key != "cash" else []:
                m = (wrapped["value"] or {}).get(asset, {})
                lines.append(
                    f"| {w['fold']['name']} | {key} | {asset} | "
                    f"{m.get('trades', 'unavailable')} | "
                    f"{fmt(m.get('net_return_contribution'), True)} | "
                    f"{fmt(m.get('realized_attribution_drawdown'), True)} | "
                    f"{fmt(m.get('profit_factor'))} | {m.get('active_entry_days', 'unavailable')} |"
                )
    lines += [
        "",
        "## Selected-strategy zero trades",
        "",
        "The recorded active flag controls whether selected signals are generated. Candidate "
        "reasons below are exclusive, in order: <30 trades, unresolved exit, nonpositive score. "
        "Score = tuning net return - 0.5 × drawdown. Marginal counts also appear in JSON.",
        "",
        "| Window | Asset | Active | Candidates | <30 trades | Unresolved | Score <=0 | Positive |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for w in report["windows"]:
        for asset, g in w["selection_gates"].items():
            c = g.get("exclusive_reasons_in_order", {})
            lines.append(
                f"| {w['fold']['name']} | {asset} | "
                f"{g.get('recorded_selected_active', 'unavailable')} | "
                f"{g.get('candidate_count', 'unavailable')} | "
                f"{c.get('below_30_trades', 'unavailable')} | "
                f"{c.get('unresolved_exit', 'unavailable')} | "
                f"{c.get('nonpositive_score', 'unavailable')} | "
                f"{c.get('positive_eligible', 'unavailable')} |"
            )
    lines += [
        "",
        "Inferred from current evaluator control flow: an inactive choice skips selected "
        "signal generation. Probability/economic/regime/calendar/execution rejection counts "
        "were not stored. They are unavailable, not zero; candidate inactivity cannot be "
        "attributed to a particular missing gate trace.",
        "",
        "## Data completeness and time alignment",
        "",
        "| Asset | Raw rows | Open-session missing minutes | Outside sessions | Complete rows |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for asset, wrapped in report["quality"].items():
        q, p = wrapped["value"] or {}, report["prepared"][asset] or {}
        lines.append(
            f"| {asset} | {q.get('candles', 'unavailable')} | "
            f"{q.get('unresolved_missing_minutes', 'unavailable')} | "
            f"{q.get('outside_exposed_sessions', 'unavailable')} | "
            f"{p.get('complete_feature_rows', 'unavailable')} |"
        )
    lines += [
        "",
        "| Window | Asset | Bars | Complete features | Missing open minutes |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for w in report["windows"]:
        for asset, wrapped in report["alignment"].items():
            m = (wrapped["value"] or {}).get("windows", {}).get(w["fold"]["name"], {})
            lines.append(
                f"| {w['fold']['name']} | {asset} | {m.get('bar_rows', 'unavailable')} | "
                f"{m.get('complete_feature_rows', 'unavailable')} | "
                f"{w['missing_open_minutes_by_asset'][asset]} |"
            )
    lines += [
        "",
        "Alignment, chronology, frozen-choice and checksum checks (false/nonzero needs review):",
        "",
    ]
    for asset, a in report["alignment"].items():
        lines.append(f"- {asset}: {json.dumps((a['value'] or {}).get('checks'))}")
    checks = report["snapshot_integrity"]
    lines.append(
        f"- Snapshot checksum matches: {sum(checks['part_checksums'].values())}/"
        f"{len(checks['part_checksums'])}; prepared fingerprints: "
        f"{json.dumps(checks['prepared_snapshot_fingerprints_match'])}."
    )
    for w in report["windows"]:
        lines.append(
            f"- {w['fold']['name']} frozen integrity: {json.dumps(w['frozen_integrity']['value'])}."
        )
        lines.append(
            f"- {w['fold']['name']} result fold matches config: "
            f"{w['recorded_fold_integrity']['value']}."
        )
    lines += [
        "",
        "## XGB probabilities, thresholds and calibration",
        "",
        "Full distributions and reliability/ECE are unavailable without all prediction/label rows. "
        "JSON retains all stored training/calibration and tuning metrics per asset/model/window; "
        "calibration-fitted scores are in-sample for the calibrator. No thresholds were searched.",
        "",
        "| Window | Asset | Fixed baseline threshold | Accepted XGB signals | Complete features | "
        "Accepted rate | Accepted p min/max | Labelled eval n | Eval Brier |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
    ]
    for w in report["windows"]:
        opportunities = w["opportunities"]["xgb_baseline"]["value"] or {}
        for asset in report["frozen_config"]["assets"]:
            m = opportunities.get(asset, {})
            prediction = (w["predictive"]["value"] or {}).get(f"xgb_baseline:{asset}", {})
            threshold = (
                w.get("thresholds", {}).get(asset, {}).get("xgb_baseline", {}).get("threshold")
            )
            lines.append(
                f"| {w['fold']['name']} | {asset} | {fmt(threshold)} | "
                f"{m.get('rows', 'unavailable')} | "
                f"{m.get('complete_feature_rows', 'unavailable')} | "
                f"{fmt(m.get('accepted_opportunities_per_complete_feature_row'), True)} | "
                f"{fmt(m.get('probability_min'))}/{fmt(m.get('probability_max'))} | "
                f"{prediction.get('rows', 'unavailable')} | "
                f"{fmt(prediction.get('multiclass_brier'))} |"
            )
    lines += [
        "",
        "Baseline model calibration evidence (fitted scores use fitting rows):",
        "",
        "| Window | Asset | Train n | Cal n | Raw Brier | Fitted Brier | Boundary checks |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for w in report["windows"]:
        for asset, models in w["models"].items():
            chosen = w.get("thresholds", {}).get(asset, {}).get("xgb_baseline", {})
            m = next(
                (
                    m
                    for m in models
                    if m["horizon"] == chosen.get("horizon")
                    and m["model_number"] == chosen.get("model_number")
                ),
                {},
            )
            t = m.get("training") or {}
            boundary = m.get("chronology", {}).get("value")
            passed = str(all(boundary.values())) if boundary else "unavailable"
            lines.append(
                f"| {w['fold']['name']} | {asset} | {t.get('train_rows', 'unavailable')} | "
                f"{t.get('calibration_rows', 'unavailable')} | "
                f"{fmt(t.get('calibration_raw', {}).get('multiclass_brier'))} | "
                f"{fmt(t.get('calibration_fitted', {}).get('multiclass_brier'))} | {passed} |"
            )
    lines += [
        "",
        "## Recorded execution and reconciliation",
        "",
        "| Window | Strategy/cost | Gaps | Ambiguous | Unresolved | Equity n | "
        "Ledger agrees | Equity agrees | Trade time errors |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | --- | ---: |",
    ]
    for w in report["windows"]:
        for key, record in w["portfolio"].items():
            if key == "cash":
                continue
            p = record["value"] or {}
            a, eq = w["by_asset"][key], w["equity_audits"][key]
            reconciliation = a.get("reconciliation")
            ledger_ok = str(all(reconciliation.values())) if reconciliation else "unavailable"
            eq_ok = str(eq.get("drawdown_matches_result") and eq.get("net_return_matches_result"))
            if eq.get("status") == "unavailable":
                eq_ok = "unavailable"
            errors = a.get("chronological_errors")
            lines.append(
                f"| {w['fold']['name']} | {key} | {p.get('gap_exits', 'unavailable')} | "
                f"{p.get('ambiguous_trades', 'unavailable')} | "
                f"{p.get('unresolved_terminal_positions', 'unavailable')} | "
                f"{eq.get('mark_rows', 'unavailable')} | {ledger_ok} | {eq_ok} | "
                f"{sum(errors.values()) if errors is not None else 'unavailable'} |"
            )
    lines += ["", "## Audit and limitations", ""]
    lines += [f"- **{a['status']}**: {a['text']}" for a in report["audit"]]
    lines += [
        "",
        "Missing expected files: " + (", ".join(report["missing_artifacts"]) or "none"),
        "",
        "## Next development iteration",
        "",
        report["next_iteration"],
        "",
    ]
    return "\n".join(lines)


def validate_paths(source, output):
    source, output = source.resolve(), output.resolve()
    base = (ROOT / "data/research").resolve()
    if not source.is_relative_to(base) or not output.is_relative_to(base) or output == base:
        raise ValueError("Research diagnostics must stay under data/research")
    if source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError("Diagnostics output must be separate from all frozen source artifacts")
    for name in ("diagnostics.json", "diagnostics.md"):
        if not (output / name).resolve().is_relative_to(output):
            raise ValueError("Diagnostics output symlink escapes target")
    return source, output


def run(config, source, output):
    resources(config)
    source, output = validate_paths(source, output)
    frozen = Evidence(source).read("config.json")
    if frozen != config:
        raise ValueError("Frozen experiment config missing or differs; refusing diagnostics")
    if config["threads"] != 1:
        raise ValueError("Single-thread research settings required")
    chronological_protocol(config)
    before = inventory(source)
    code_before = {name: digest(ROOT / "src/trading" / name) for name in CODE_FILES}
    report = build(config, source, before)
    if inventory(source) != before:
        raise ValueError("Source artifacts changed during diagnostics; no output published")
    if report["current_code_sha256"] != code_before:
        raise ValueError("Diagnostic code changed during run; retry")
    artifacts = {"diagnostics.json": encoded(report), "diagnostics.md": markdown(report)}
    # Preflight all output files before writing either. Never replace different evidence.
    for name, content in artifacts.items():
        target = output / name
        if target.exists() and target.read_bytes() != content.encode("utf-8"):
            raise ValueError("Diagnostic version differs; use a new --diagnostics-output directory")
    resources(config)
    output.mkdir(parents=True, exist_ok=True)
    for name, content in artifacts.items():
        target = output / name
        if not target.exists():
            temporary = output / (name + ".tmp")
            if temporary.is_symlink():
                raise ValueError("Refusing temporary output symlink")
            temporary.write_bytes(content.encode("utf-8"))
            os.replace(temporary, target)
    print(f"DIAGNOSTICS_COMPLETE {output.relative_to(ROOT).as_posix()}", flush=True)
    return report


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/phase5-research.json")
    parser.add_argument("--diagnostics-output", default="data/research/phase5b-v1")
    args = parser.parse_args(argv)
    try:
        config = json.loads((ROOT / args.config).read_text(encoding="utf-8"))
        run(config, ROOT / config["output"], ROOT / args.diagnostics_output)
    except ResearchSafetyStop as error:
        print(f"RESEARCH_SAFETY_STOP: {error}", flush=True)
        return 2
    except (ValueError, OSError) as error:
        print(f"DIAGNOSTICS_STOPPED: {error}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
