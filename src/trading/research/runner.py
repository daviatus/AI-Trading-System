"""Reproducible, checkpointed Phase 5 research; no broker or application execution."""
# Scientific imports deliberately follow the standard-library resource gate.
# ruff: noqa: E402

import argparse
import gc
import hashlib
import importlib.metadata
import json
import os
import sys
from datetime import UTC, datetime

from trading.research.safety import ResearchSafetyStop, bootstrap_gate

if __name__ == "__main__":
    try:
        bootstrap_gate(sys.argv[1:])
    except ResearchSafetyStop as error:
        print(f"RESEARCH_SAFETY_STOP: {error}", flush=True)
        raise SystemExit(2) from None
    # Diagnostics must never enter the experiment writer or import training/DB code.
    if sys.argv[1:2] == ["diagnostics"]:
        from trading.research.diagnostics import cli

        raise SystemExit(cli(sys.argv[2:]))

# Bound native libraries before NumPy/XGBoost are imported.
for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_key] = "1"

import numpy as np
import pandas as pd
import xgboost as xgb

from trading.research.data import (
    ROOT,
    audit,
    features,
    load_bars,
    resources,
    session_open,
    snapshot,
    valid_ohlc,
    write_json,
)
from trading.research.execution import Costs, backtest, outcomes, targets
from trading.research.models import (
    fit_model,
    label_end_times,
    opportunities,
    payoff_profile,
    predict,
    probability_metrics,
    purged_masks,
)


def stamp(message, output):
    line = f"{datetime.now(UTC).isoformat()} {message}"
    print(line, flush=True)
    with (output / "progress.log").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def utc(value):
    return pd.Timestamp(value, tz="UTC")


def load_period(output, kind, asset, start=None, end=None):
    filters = []
    if start is not None:
        filters.append(("opened_at", ">=", pd.Timestamp(start).to_pydatetime()))
    if end is not None:
        filters.append(("opened_at", "<", pd.Timestamp(end).to_pydatetime()))
    return pd.read_parquet(output / kind / f"{asset}.parquet", filters=filters or None)


def prepare(config, output, contracts):
    manifest = json.loads((output / "snapshot.json").read_text())
    if not manifest.get("complete"):
        raise ValueError("Complete the snapshot before preparing research datasets")
    source_hash = hashlib.sha256(json.dumps(manifest["parts"], sort_keys=True).encode()).hexdigest()
    for asset in config["assets"]:
        resources(config)
        target = output / "prepared" / f"{asset}.json"
        if target.exists():
            if json.loads(target.read_text()).get("source_snapshot_fingerprint") != source_hash:
                raise ValueError("Prepared data source changed; use a new output directory")
            continue
        bars = load_bars(output, asset)
        report = audit(bars, contracts[asset], config)
        write_json(output / "quality" / f"{asset}.json", report)
        clean = bars.loc[valid_ohlc(bars) & session_open(bars.index, contracts[asset])]
        f = features(clean)
        for kind, frame in (("bars", clean), ("features", f)):
            directory = output / kind
            directory.mkdir(exist_ok=True)
            temp = directory / f"{asset}.tmp"
            frame.to_parquet(temp, compression="zstd")
            os.replace(temp, directory / f"{asset}.parquet")
        write_json(
            target,
            {
                "raw_rows": len(bars),
                "clean_rows": len(clean),
                "complete_feature_rows": int(f.notna().all(axis=1).sum()),
                "feature_columns": list(f),
                "source_snapshot_fingerprint": source_hash,
                "warmup": 64,
                "gap_policy": "reset at every absent or invalid/closed-session bar",
            },
        )
        stamp(
            f"PREPARED {asset}: {len(bars)} candles, {report['unresolved_missing_minutes']} "
            f"unresolved minutes, {f.notna().all(axis=1).sum()} complete features",
            output,
        )
        del bars, clean, f
        gc.collect()


def signal(
    asset,
    f,
    bars,
    probability,
    horizon,
    profile,
    config,
    contracts,
    mask,
    threshold,
    regime_filter,
    baseline=False,
    economic_filter=True,
):
    return opportunities(
        asset,
        f,
        bars,
        probability,
        horizon,
        profile,
        Costs(**config["costs"][asset]),
        contracts[asset],
        mask,
        threshold=threshold,
        regime_filter=regime_filter,
        baseline=baseline,
        risk_fraction=config["risk_fraction"],
        max_leverage=config["max_gross_leverage"],
        stop_atr=config["stop_atr"],
        economic_filter=economic_filter,
    )


def develop(config, output, contracts, fold, asset):
    choice_path = output / "choices" / fold["name"] / f"{asset}.json"
    if choice_path.exists():
        return json.loads(choice_path.read_text())
    resources(config)
    end = utc(fold["tuning_end"])
    bars = load_period(output, "bars", asset, end=end)
    f = load_period(output, "features", asset, end=end)
    atr = f.atr_fraction_14.to_numpy() * bars.close.to_numpy()
    rows, best, baseline = [], None, None
    for horizon in config["horizons"]:
        resources(config)
        path = outcomes(bars, atr, horizon, config["stop_atr"], config["reward_risk"])
        labels = targets(path, Costs(**config["costs"][asset]))
        masks = purged_masks(f.index, label_end_times(f.index, horizon), fold)
        profile = payoff_profile(path, labels, masks["train"], Costs(**config["costs"][asset]))
        datasets = {asset: {"bars": bars, "paths": {horizon: path}}}
        for model_number, model_config in enumerate(config["models"]):
            model_path = output / "models" / fold["name"] / asset / f"h{horizon}-m{model_number}"
            metadata_path = model_path.with_suffix(".json")
            if metadata_path.exists():
                model = xgb.Booster()
                model.load_model(model_path.with_suffix(".ubj"))
                trained = json.loads(metadata_path.read_text())
                theta = trained["calibrator"]
            else:
                model, theta, info = fit_model(f, labels, masks, model_config, config)
                model_path.parent.mkdir(parents=True, exist_ok=True)
                model.save_model(model_path.with_suffix(".ubj"))
                trained = {
                    "calibrator": theta,
                    "training": info,
                    "model": model_config,
                    "profile": profile,
                    "horizon": horizon,
                }
                write_json(metadata_path, trained)
            probability = predict(model, theta, f, masks["tuning"], config["threads"])
            available = masks["tuning"] & (labels >= 0) & np.isfinite(probability).all(axis=1)
            predictive = probability_metrics(probability[available], labels[available])
            for threshold in config["thresholds"]:
                for regime_filter in config["regime_filters"]:
                    candidates = signal(
                        asset,
                        f,
                        bars,
                        probability,
                        horizon,
                        profile,
                        config,
                        contracts,
                        masks["tuning"],
                        threshold,
                        regime_filter,
                    )
                    _, _, performance = backtest(
                        candidates, datasets, contracts, config, utc(fold["calibration_end"]), end
                    )
                    score = (
                        performance["net_return"]
                        - 0.5 * performance["maximum_mark_to_market_drawdown"]
                    )
                    if performance["trades"] < 30 or performance["unresolved_terminal_positions"]:
                        score = -1e6
                    record = {
                        "horizon": horizon,
                        "model_number": model_number,
                        "threshold": threshold,
                        "regime_filter": regime_filter,
                        "selection_score": score,
                        "performance": performance,
                        "predictive": predictive,
                        "model_file": str(model_path.with_suffix(".ubj").relative_to(output)),
                        "metadata_file": str(metadata_path.relative_to(output)),
                    }
                    rows.append(record)
                    if best is None or score > best["selection_score"]:
                        best = record
                    if (
                        horizon == config["horizons"][0]
                        and model_number == 0
                        and threshold == config["thresholds"][0]
                        and regime_filter == "all"
                    ):
                        baseline = record
            stamp(f"TUNED {fold['name']} {asset} h={horizon} model={model_number}", output)
            del model, probability
        del path, datasets
        gc.collect()
    best = {**best, "active": best["selection_score"] > 0}
    choice = {
        "selected": best,
        "xgb_baseline": baseline,
        "candidates": rows,
        "policy": "maximize tuning net return minus half drawdown; >=30 trades; "
        "choose cash if no positive selection score; no holdout optimization",
    }
    write_json(choice_path, choice)
    del bars, f
    gc.collect()
    return choice


def bootstrap(curve, seed):
    daily = curve.resample("D").last().ffill().pct_change().dropna().to_numpy()
    if len(daily) < 30:
        return None
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(500):
        starts = rng.integers(0, len(daily), size=int(np.ceil(len(daily) / 7)))
        sample = np.concatenate([daily[(s + np.arange(7)) % len(daily)] for s in starts])[
            : len(daily)
        ]
        values.append(np.prod(1 + sample) - 1)
    return {
        "method": "circular 7-calendar-day block bootstrap, 500 draws; descriptive",
        "return_95pct": np.quantile(values, [0.025, 0.975]).tolist(),
    }


def evaluate(config, output, contracts, fold, choices):
    result_path = output / "results" / f"{fold['name']}.json"
    if result_path.exists():
        return json.loads(result_path.read_text())
    resources(config)
    start, end = utc(fold["tuning_end"]), utc(fold["test_end"])
    frozen = hashlib.sha256(json.dumps(choices, sort_keys=True).encode()).hexdigest()
    lock_path = output / "evaluation" / f"{fold['name']}-frozen.json"
    if lock_path.exists():
        if json.loads(lock_path.read_text())["choice_hash"] != frozen:
            raise RuntimeError("Refusing to retune an already opened evaluation period")
    else:
        write_json(
            lock_path,
            {
                "choice_hash": frozen,
                "opened_at": str(datetime.now(UTC)),
                "holdout": fold["holdout"],
                "choices": choices,
            },
        )
    datasets, candidates = {}, {"trend_baseline": [], "xgb_baseline": [], "selected": []}
    predictive = {}
    for asset in config["assets"]:
        resources(config)
        bars = load_period(output, "bars", asset, start=start, end=end)
        f = load_period(output, "features", asset, start=start, end=end)
        paths = {}
        for horizon in config["horizons"]:
            paths[horizon] = outcomes(
                bars,
                f.atr_fraction_14.to_numpy() * bars.close.to_numpy(),
                horizon,
                config["stop_atr"],
                config["reward_risk"],
            )
        datasets[asset] = {"bars": bars, "paths": paths}
        for name in ("xgb_baseline", "selected"):
            chosen = choices[asset][name]
            if name == "selected" and not chosen["active"]:
                continue
            meta = json.loads((output / chosen["metadata_file"]).read_text())
            model = xgb.Booster()
            model.load_model(output / chosen["model_file"])
            probability = predict(
                model, meta["calibrator"], f, np.ones(len(f), bool), config["threads"]
            )
            horizon = chosen["horizon"]
            cand = signal(
                asset,
                f,
                bars,
                probability,
                horizon,
                meta["profile"],
                config,
                contracts,
                np.ones(len(f), bool),
                chosen["threshold"],
                chosen["regime_filter"],
                economic_filter=name != "xgb_baseline",
            )
            candidates[name].append(cand)
            labels = targets(paths[horizon], Costs(**config["costs"][asset]))
            available = (labels >= 0) & np.isfinite(probability).all(axis=1)
            predictive[f"{name}:{asset}"] = probability_metrics(
                probability[available], labels[available]
            )
            if name == "xgb_baseline":
                candidates["trend_baseline"].append(
                    signal(
                        asset,
                        f,
                        bars,
                        probability,
                        horizon,
                        meta["profile"],
                        config,
                        contracts,
                        np.ones(len(f), bool),
                        0.5,
                        "all",
                        baseline=True,
                    )
                )
            del model, probability
        del f
        gc.collect()
    results = {"fold": fold, "predictive": predictive, "strategies": {}}
    for strategy, parts in candidates.items():
        frame = (
            pd.concat(parts, ignore_index=True)
            if parts
            else pd.DataFrame(columns=["time", "asset", "score", "probability", "confidence"])
        )
        directory = output / "evaluation" / fold["name"]
        directory.mkdir(exist_ok=True)
        frame.to_parquet(directory / f"{strategy}-opportunities.parquet", index=False)
        for multiple in config["cost_multipliers"]:
            resources(config)
            trades, curve, performance = backtest(
                frame, datasets, contracts, config, start, end, multiple
            )
            key = f"{strategy}:cost{multiple:g}"
            performance["daily_block_bootstrap"] = bootstrap(curve, config["seed"])
            results["strategies"][key] = performance
            trades.to_parquet(directory / f"{key.replace(':', '-')}-trades.parquet", index=False)
            curve.rename("equity").to_frame().to_parquet(
                directory / f"{key.replace(':', '-')}-equity.parquet"
            )
            stamp(
                f"EVALUATED {fold['name']} {key}: trades={performance['trades']} "
                f"net={performance['net_return']:.4%}",
                output,
            )
    results["strategies"]["cash"] = {
        "trades": 0,
        "net_return": 0,
        "maximum_mark_to_market_drawdown": 0,
    }
    write_json(result_path, results)
    del datasets
    gc.collect()
    return results


def report(config, output):
    lines = [
        "# Phase 5 measured research results",
        "",
        "All amounts are USD research scenarios, "
        "not executable broker-verified profits. Full assumptions are in the frozen config.",
        "",
        "| Window | Strategy / costs | Trades | Net return | Close DD | PF |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for path in sorted((output / "results").glob("*.json")):
        result = json.loads(path.read_text())
        for name, m in result["strategies"].items():
            pf = m.get("profit_factor")
            lines.append(
                f"| {result['fold']['name']} | {name} | {m['trades']} | "
                f"{m['net_return']:.2%} | {m['maximum_mark_to_market_drawdown']:.2%} | "
                f"{pf:.3f} |"
                if pf is not None
                else f"| {result['fold']['name']} | {name} | {m['trades']} | "
                f"{m['net_return']:.2%} | "
                f"{m['maximum_mark_to_market_drawdown']:.2%} | n/a |"
            )
    lines += [
        "",
        "No final holdout tuning is permitted. Low trade counts, unresolved exits, "
        "dependent labels, historical calendars, midpoint/reference-price ambiguity, "
        "unknown historical bid/ask costs and OHLC fill ordering limit the findings.",
        "Signals use completed bars, next-bar entries and disjoint "
        "chronological calibration. All classifiers are independent by asset; "
        "one shared position limits portfolio risk.",
    ]
    (output / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["snapshot", "prepare", "run", "report", "all", "diagnostics"]
    )
    parser.add_argument("--config", default="config/phase5-research.json")
    parser.add_argument("--diagnostics-output", default="data/research/phase5b-v1")
    args = parser.parse_args(argv)
    config = json.loads((ROOT / args.config).read_text())
    if config["threads"] != 1 or config["free_floor_gib"] < 15:
        raise ValueError("Conservative research resource settings required")
    output = (ROOT / config["output"]).resolve()
    if not output.is_relative_to(ROOT / "data/research"):
        raise ValueError("Research artifacts must stay under data/research")
    resources(config)
    if args.command == "diagnostics":
        from trading.research.diagnostics import run

        run(config, output, ROOT / args.diagnostics_output)
        return
    output.mkdir(parents=True, exist_ok=True)
    saved = output / "config.json"
    if saved.exists() and json.loads(saved.read_text()) != config:
        raise ValueError("Frozen experiment config differs; choose a new output directory")
    write_json(saved, config)
    versions = {
        name: importlib.metadata.version(name)
        for name in ("numpy", "pandas", "pyarrow", "xgboost", "scipy", "sqlalchemy")
    }
    write_json(output / "environment.json", {"python": sys.version, "packages": versions})
    if args.command in ("snapshot", "all"):
        snapshot(config)
    contracts = json.loads((output / "contracts.json").read_text())
    if args.command in ("prepare", "all"):
        prepare(config, output, contracts)
    if args.command in ("run", "all"):
        for fold in config["folds"]:
            choices = {
                asset: develop(config, output, contracts, fold, asset) for asset in config["assets"]
            }
            evaluate(config, output, contracts, fold, choices)
            report(config, output)
    if args.command == "report":
        report(config, output)
    stamp(f"COMPLETE {args.command}", output)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # SQL/transport exceptions can contain credentials; never print their string.
        print(f"RESEARCH_STOPPED {type(error).__name__}; inspect safe checkpoints", flush=True)
        raise SystemExit(1) from None
