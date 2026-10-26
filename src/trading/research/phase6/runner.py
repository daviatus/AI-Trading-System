"""Phase 6 offline research CLI; standard-library bootstrap precedes scientific imports."""

import argparse
import gc
import json
import os
from dataclasses import replace
from pathlib import Path

from trading.research.phase6.controller import (
    canonical,
    code_manifest,
    controller_lock,
    digest,
    experiment_queue,
    initialize,
    run_queue,
    source_manifest,
    validate_config,
    write_json,
)
from trading.research.safety import ROOT, ResearchSafetyStop, resources


def protected_manifest():
    """Hash frozen evidence without parsing or using holdout candles in development."""
    paths = [ROOT / "config/phase5-research.json"]
    for folder in ("phase5-v1", "phase5b-v1"):
        base = ROOT / "data/research" / folder
        paths.extend(
            p
            for p in base.rglob("*")
            if p.is_file() and not any(part.startswith(("tests-", "logs")) for part in p.parts)
        )
    return {str(p.relative_to(ROOT)).replace("\\", "/"): digest(p) for p in sorted(paths)}


def audit(config):
    output, source = validate_config(config)
    baseline = protected_manifest()
    write_json(output / "protected-evidence.json", baseline, immutable=True)
    evidence = json.loads((ROOT / "data/research/phase5b-v1/diagnostics.json").read_text())
    summary = {
        "schema_version": 1,
        "mode": "offline_read_only_audit",
        "phase5b_evidence_sha256": digest(ROOT / "data/research/phase5b-v1/diagnostics.json"),
        "protected_files": len(baseline),
        "source": str(source.relative_to(ROOT)),
        "historical_candidate_evaluations": 216,
        "under_30_trades": 206,
        "remaining_nonpositive_scores": 10,
        "historical_full_probabilities": "unavailable_not_reconstructed",
        "historical_gate_rejections": "unavailable_not_reconstructed",
        "phase5b_missing_artifacts": evidence.get("missing_artifacts"),
        "development_source_manifest": source_manifest(config),
        "confirmed": [
            "Full per-asset concat, full minute calendar grids and outcome arrays dominate memory.",
            "Prior calibration is chronological; horizon purge exists, explicit embargo absent.",
            "Existing single-position ledger lacks explicit margin/cap scenarios and gate streams.",
            "Phase5B has accepted probabilities only; full historical scores are unavailable.",
            "Current exposed broker calendar is not a verified historical holiday calendar.",
            "2026 already inspected and excluded from Phase6 development.",
        ],
        "hypotheses_requiring_new_development_runs": [
            "Calibration drift, economic gates and horizons may contribute to inactivity.",
            "A simpler model or different horizon may improve cross-period activity and quality.",
            "An individually weaker asset may improve portfolio opportunity diversification.",
        ],
        "live_trading_modified": False,
        "database_accessed": False,
        "collector_accessed": False,
    }
    write_json(output / "audit.json", summary, immutable=True)
    if protected_manifest() != baseline:
        raise ValueError("Frozen research evidence changed during audit")
    return summary


def load_asset(source, asset, fold, config):
    """Monthly pruning, 8192-row Arrow batches and a hard pre-concat row bound."""
    import pandas as pd
    import pyarrow.parquet as pq

    start, end = pd.Timestamp(fold["train_start"]), pd.Timestamp(fold["test_end"])
    if end > pd.Timestamp("2026-01-01", tz="UTC"):
        raise ValueError("2026 is excluded before reading any candle partition")
    parts, rows = [], 0
    snapshot = json.loads((source / "snapshot.json").read_text())
    paths = [
        source / relative
        for relative in sorted(snapshot["parts"])
        if relative.startswith(f"cache/{asset}/") and relative.endswith(".parquet")
    ]
    for path in paths:
        month = pd.Timestamp(path.stem + "-01", tz="UTC")
        if month >= end or month + pd.offsets.MonthBegin() <= start:
            continue
        if not path.resolve().is_relative_to(source.resolve()):
            raise ValueError("Snapshot path traversal")
        relative = path.relative_to(source).as_posix()
        if digest(path) != snapshot["parts"][relative]["sha256"]:
            raise ValueError("Development partition differs from frozen provenance")
        resources(config)
        parquet = pq.ParquetFile(path)
        for batch in parquet.iter_batches(batch_size=8192, use_threads=False):
            frame = batch.to_pandas()
            stamps = pd.to_datetime(frame.opened_at, utc=True)
            if not isinstance(frame.opened_at.dtype, pd.DatetimeTZDtype):
                raise ValueError("Candle timestamps must retain explicit source timezone")
            frame["opened_at"] = stamps
            frame = frame.loc[(stamps >= start) & (stamps < end)]
            rows += len(frame)
            if rows > config["max_rows_per_asset"]:
                raise ResearchSafetyStop("Per-asset row budget exceeded; use narrower windows")
            if len(frame):
                parts.append(frame)
    if not parts:
        raise ValueError("No development candles available")
    return pd.concat(parts, ignore_index=True).set_index("opened_at")


def _window_mask(index, fold, window, embargo):
    """Signal eligibility uses clock boundaries only, never future label availability."""
    import pandas as pd

    left, right = (
        ("calibration_end", "tuning_end") if window == "tuning" else ("tuning_end", "test_end")
    )
    return (index >= pd.Timestamp(fold[left]) + pd.Timedelta(minutes=embargo)) & (
        index < pd.Timestamp(fold[right])
    )


def _selection_score(result, minimum):
    metrics = result.metrics
    if metrics.get("completed_trades", metrics.get("trades", 0)) < minimum:
        return None, "insufficient_tuning_trades"
    if metrics.get("unresolved_terminal_positions", 0):
        return None, "unresolved_terminal_position"
    score = metrics["net_return"] - 0.5 * metrics["maximum_mark_to_market_drawdown"]
    return (score, "eligible") if score > 0 else (None, "nonpositive_tuning_score")


def execute_experiment(config, job, attempt, *, synthetic_bars=None, synthetic_contracts=None):
    resources(config)
    with controller_lock(ROOT / "data/research/phase6-control-v1", lock_name=".worker.lock"):
        try:
            return _execute_experiment(
                config,
                job,
                attempt,
                synthetic_bars=synthetic_bars,
                synthetic_contracts=synthetic_contracts,
            )
        except Exception as error:
            output, _ = validate_config(config)
            target = Path(attempt).resolve()
            if target.is_relative_to(output):
                write_json(
                    target / "worker-failure.json",
                    {
                        "exception_type": type(error).__name__,
                        "status": "safety_stopped"
                        if isinstance(error, ResearchSafetyStop)
                        else "failed",
                        "evidence_preserved": True,
                    },
                    immutable=True,
                )
            raise


def _execute_experiment(config, job, attempt, *, synthetic_bars=None, synthetic_contracts=None):
    import importlib.metadata

    import numpy as np
    import pandas as pd

    from trading.research.phase6.execution import (
        CostScenario,
        InstrumentSpec,
        SimulationConfig,
        backtest,
        compare_portfolios,
    )
    from trading.research.phase6.features import FeatureSpec, audit_candles, make_features
    from trading.research.phase6.instrumentation import JsonlSink
    from trading.research.phase6.labels import LabelSpec, make_labels, purged_split
    from trading.research.phase6.models import fit_model, load_model, probability_metrics
    from trading.research.phase6.signals import generate_signals, payoff_profile

    output, source = validate_config(config)
    attempt = Path(attempt).resolve()
    if not attempt.is_relative_to(output):
        raise ValueError("Worker output outside Phase 6 directory")
    attempt.mkdir(parents=True, exist_ok=True)
    write_json(attempt / "experiment.json", job, immutable=True)
    write_json(attempt / "effective-config.json", config, immutable=True)
    write_json(attempt / "code-manifest.json", code_manifest(), immutable=True)
    fold = job["fold"]
    contracts = (
        synthetic_contracts
        if synthetic_contracts is not None
        else json.loads((source / "contracts.json").read_text())
    )
    specs = {
        asset: InstrumentSpec.from_ctrader(
            asset, contracts[asset], config["instrument_leverage_caps"][asset]
        )
        for asset in config["assets"]
    }
    costs = {asset: CostScenario(**config["costs"][asset]) for asset in config["assets"]}
    simulation = SimulationConfig(
        initial_equity=config["initial_equity"],
        risk_fraction=config["risk_fraction"],
        leverage=config["selection_leverage"],
        reward_risk=config["reward_risk"],
    )
    choices, candidate_audit, predictions, streams = {}, [], {}, {}
    tuning_candidates, test_candidates = [], []
    tune_bars, test_bars = {}, {}
    for asset in config["assets"]:
        resources(config)
        bars = (
            synthetic_bars[asset]
            if synthetic_bars is not None
            else load_asset(source, asset, fold, config)
        )
        quality = audit_candles(
            bars,
            metadata=contracts[asset],
            start=fold["train_start"],
            end=fold["test_end"],
            max_rows=config["max_rows_per_asset"],
        )
        write_json(attempt / "quality" / f"{asset}.json", quality, immutable=True)
        features = make_features(
            bars, FeatureSpec(max_rows=config["max_rows_per_asset"]), metadata=contracts[asset]
        )
        label_parameters = {**job["label"]}
        label_parameters["cost_bps"] = (
            config["costs"][asset]["spread_bps"]
            + 2 * config["costs"][asset]["slippage_bps_per_side"]
            + config["costs"][asset]["commission_bps_roundtrip"]
        )
        label_spec = LabelSpec(**label_parameters, max_rows=config["max_rows_per_asset"])
        # Barrier scale is ATR/close; volatility horizon uses per-minute return sigma.
        volatility = (
            features["atr_fraction_14"]
            if label_spec.kind == "barrier"
            else (features["volatility_20"])
        )
        labels = make_labels(bars, label_spec, volatility=volatility, metadata=contracts[asset])
        masks = purged_split(
            features.index, labels.label_end, fold, embargo_minutes=config["embargo_minutes"]
        )
        bundle = fit_model(
            features,
            labels.label.to_numpy(),
            masks,
            job["model"],
            config,
            asset=asset,
            label_end=labels.label_end,
        )
        bundle.save(attempt / "models" / asset)
        # Only tuning predictions are opened before freezing selection.
        raw_masks = {
            window: _window_mask(features.index, fold, window, config["embargo_minutes"])
            for window in ("tuning", "test")
        }
        probability = bundle.predict(features, raw_masks["tuning"])
        predictions[asset] = {}
        valid = masks["tuning"] & (labels.label.to_numpy() >= 0)
        valid &= np.isfinite(probability).all(axis=1)
        predictions[asset]["tuning"] = probability_metrics(
            probability[valid], labels.label.to_numpy()[valid]
        )
        profile = payoff_profile(labels, masks["train"])
        features["reference_close"] = bars.close.to_numpy()
        horizon = label_spec.horizon_minutes
        tune_left, tune_right = (
            pd.Timestamp(fold["calibration_end"]),
            pd.Timestamp(fold["tuning_end"]),
        )
        test_left, test_right = tune_right, pd.Timestamp(fold["test_end"])
        tune_bars[asset] = bars.loc[(bars.index >= tune_left) & (bars.index < tune_right)]
        test_bars[asset] = bars.loc[(bars.index >= test_left) & (bars.index < test_right)]
        best = None
        for threshold in config["thresholds"]:
            for regime_filter in config["regime_filters"]:
                resources(config)
                stream_id = f"{asset}-tuning-p{threshold:g}-{regime_filter}"
                with JsonlSink(
                    attempt / "streams" / f"{stream_id}.jsonl",
                    context={
                        "experiment_id": job["id"],
                        "model_version": job["model"]["version"],
                        "calibration_id": digest(attempt / "models" / asset / "model.json"),
                    },
                    max_bytes=config["max_diagnostic_bytes"],
                ) as sink:
                    candidates = generate_signals(
                        asset,
                        features,
                        probability,
                        raw_masks["tuning"],
                        threshold=threshold,
                        horizon=horizon,
                        stop_multiple=config["stop_multiple"],
                        profile=profile,
                        model_id=job["model"]["kind"],
                        window=fold["name"] + ":tuning",
                        regime_filter=regime_filter,
                        diagnostic=sink,
                        metadata=contracts[asset],
                        model_version=job["model"]["version"],
                        feature_columns=bundle.metadata["features"],
                    )
                    result = backtest(
                        candidates,
                        {asset: tune_bars[asset]},
                        {asset: specs[asset]},
                        {asset: costs[asset]},
                        simulation,
                        tune_left,
                        tune_right,
                        diagnostic=sink,
                    )
                streams[stream_id] = sink.summary()
                score, reason = _selection_score(result, config["minimum_tuning_trades"])
                candidate_audit.append(
                    {
                        "asset": asset,
                        "threshold": threshold,
                        "regime_filter": regime_filter,
                        "score": score,
                        "reason": reason,
                        "metrics": result.metrics,
                    }
                )
                if best is None or (
                    score is not None and (best["score"] is None or score > best["score"])
                ):
                    best = {
                        "threshold": threshold,
                        "regime_filter": regime_filter,
                        "score": score,
                        "active": score is not None,
                        "reason": reason,
                    }
                    best_candidates = candidates
        choices[asset] = best
        if best["active"]:
            tuning_candidates.append(best_candidates)
        # Freeze per-asset choice before opening any test predictions.
        write_json(attempt / "choices" / f"{asset}.json", best, immutable=True)
        evaluation_features = features.loc[raw_masks["test"]]
        evaluation_features.to_parquet(attempt / "models" / asset / "evaluation-features.parquet")
        evaluation_labels = labels.loc[raw_masks["test"]].copy()
        evaluation_labels["metric_eligible"] = masks["test"][raw_masks["test"]]
        evaluation_labels.to_parquet(attempt / "models" / asset / "evaluation-labels.parquet")
        write_json(
            attempt / "models" / asset / "payoff-profile.json", profile.tolist(), immutable=True
        )
        labels.to_parquet(attempt / "models" / asset / "labels.parquet")
        del features, labels, probability, bundle, bars, evaluation_features, evaluation_labels
        gc.collect()
    columns = ["time", "asset", "direction", "score", "probability", "stop_distance", "horizon"]
    tuning = (
        pd.concat(tuning_candidates, ignore_index=True)
        if tuning_candidates
        else (pd.DataFrame(columns=columns))
    )
    development = compare_portfolios(
        tuning,
        tune_bars,
        specs,
        costs,
        simulation,
        pd.Timestamp(fold["calibration_end"]),
        pd.Timestamp(fold["tuning_end"]),
    )
    subset = None
    subset_score = None
    for name, result in sorted(development.items()):
        score, _ = _selection_score(result, config["minimum_tuning_trades"])
        if score is not None and (subset_score is None or score > subset_score):
            subset, subset_score = name, score
    frozen = {
        "choices": choices,
        "portfolio": subset,
        "score": subset_score,
        "development_only": True,
        "candidate_trials": len(candidate_audit),
    }
    write_json(attempt / "frozen-selection.json", frozen, immutable=True)
    # Second sequential pass starts only after the whole portfolio policy is frozen.
    for asset in config["assets"]:
        resources(config)
        directory = attempt / "models" / asset
        bundle = load_model(directory)
        features = pd.read_parquet(directory / "evaluation-features.parquet")
        labels = pd.read_parquet(directory / "evaluation-labels.parquet")
        profile = np.asarray(json.loads((directory / "payoff-profile.json").read_text()))
        probability = bundle.predict(features)
        valid = labels.metric_eligible.to_numpy() & (labels.label.to_numpy() >= 0)
        valid &= np.isfinite(probability).all(axis=1)
        predictions[asset]["test"] = probability_metrics(
            probability[valid], labels.label.to_numpy()[valid]
        )
        best = choices[asset]
        stream_id = f"{asset}-evaluation"
        with JsonlSink(
            attempt / "streams" / f"{stream_id}.jsonl",
            context={
                "experiment_id": job["id"],
                "model_version": job["model"]["version"],
                "calibration_id": digest(directory / "model.json"),
            },
            max_bytes=config["max_diagnostic_bytes"],
        ) as sink:
            candidates = generate_signals(
                asset,
                features,
                probability,
                np.ones(len(features), bool),
                threshold=best["threshold"],
                horizon=horizon,
                stop_multiple=config["stop_multiple"],
                profile=profile,
                model_id=job["model"]["kind"],
                window=fold["name"] + ":evaluation",
                regime_filter=best["regime_filter"],
                diagnostic=sink,
                metadata=contracts[asset],
                model_version=job["model"]["version"],
                feature_columns=bundle.metadata["features"],
            )
        streams[stream_id] = sink.summary()
        candidates["policy_active"] = best["active"]
        test_candidates.append(candidates)
        del features, labels, probability, bundle
        gc.collect()
    tests = pd.concat(test_candidates, ignore_index=True)
    tests.to_parquet(attempt / "opportunities.parquet", index=False)
    comparison = compare_portfolios(
        tests,
        test_bars,
        specs,
        costs,
        simulation,
        pd.Timestamp(fold["tuning_end"]),
        pd.Timestamp(fold["test_end"]),
    )
    portfolio_directory = attempt / "portfolios"
    portfolio_directory.mkdir(exist_ok=True)
    for name, replay in comparison.items():
        replay.trades.to_parquet(portfolio_directory / f"{name}-trades.parquet", index=False)
        replay.curve.rename("equity").to_frame().to_parquet(
            portfolio_directory / f"{name}-equity.parquet"
        )
    # Asset names are parsed from each selected replay's actual instruments, not string heuristics.
    chosen_assets = set(development[subset].metrics.get("instruments", [])) if subset else set()
    if subset and not chosen_assets:
        chosen_assets = set(subset.split("+"))
    selected = tests.loc[tests.asset.isin(chosen_assets) & tests.policy_active]
    scenarios = {}
    for leverage in config["leverage_scenarios"]:
        for multiplier in config["cost_multipliers"]:
            resources(config)
            scenario_costs = {
                asset: replace(
                    cost,
                    spread_bps=cost.spread_bps * multiplier,
                    commission_bps_roundtrip=cost.commission_bps_roundtrip * multiplier,
                    slippage_bps_per_side=cost.slippage_bps_per_side * multiplier,
                    financing_bps_per_day=cost.financing_bps_per_day * multiplier,
                )
                for asset, cost in costs.items()
            }
            name = f"leverage{leverage}-cost{multiplier}"
            with JsonlSink(
                attempt / "streams" / f"{name}.jsonl",
                context={"experiment_id": job["id"], "scenario": name},
                max_bytes=config["max_diagnostic_bytes"],
            ) as sink:
                result = backtest(
                    selected,
                    test_bars,
                    specs,
                    scenario_costs,
                    replace(simulation, leverage=leverage),
                    pd.Timestamp(fold["tuning_end"]),
                    pd.Timestamp(fold["test_end"]),
                    diagnostic=sink,
                )
            streams[name] = sink.summary()
            scenarios[name] = result.metrics
            result.trades.to_parquet(attempt / f"{name}-trades.parquet", index=False)
            result.curve.rename("equity").to_frame().to_parquet(attempt / f"{name}-equity.parquet")
    rejected = ["development_evidence_only_requires_new_untouched_validation"]
    if subset is None:
        rejected.append("no_eligible_positive_tuning_portfolio_cash_selected")
    if any(not summary["complete"] for summary in streams.values()):
        rejected.append("incomplete_diagnostic_stream")
    from trading.research.phase6.validation import assess

    diagnostics = {
        "schema_version": 1,
        "experiment_id": job["id"],
        "synthetic": synthetic_bars is not None,
        "accepted": False,
        "rejection_reasons": rejected,
        "selection": frozen,
        "candidate_evaluations": candidate_audit,
        "prediction_quality": predictions,
        "instrument_and_portfolio_comparison": {
            name: result.metrics for name, result in comparison.items()
        },
        "selected_scenarios": scenarios,
        "research_acceptance_assessments": {
            name: assess(metrics, config) for name, metrics in scenarios.items()
        },
        "streams": streams,
        "cash": {"trades": 0, "net_return": 0, "maximum_mark_to_market_drawdown": 0},
        "multiple_testing": {
            "registered_jobs": len(experiment_queue(config)),
            "threshold_trials_this_job": len(candidate_audit),
            "portfolio_subset_trials": len(development),
            "confidence_intervals": "descriptive_not_selection_adjusted",
        },
        "environment": {
            name: importlib.metadata.version(name)
            for name in ("numpy", "pandas", "pyarrow", "scipy", "xgboost")
        },
        "limitations": config["evidence_limits"],
    }
    write_json(attempt / "diagnostics.json", diagnostics, immutable=True)
    lines = [
        "# Phase 6 development diagnostics",
        "",
        f"Experiment: `{job['id']}`. Synthetic: `{synthetic_bars is not None}`.",
        "",
        "Development evidence only. No model is approved for trading.",
        "",
        "| Portfolio (fixed per-asset threshold) | Trades | Net return | OHLC DD | PF |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, replay in comparison.items():
        metric = replay.metrics
        pf = metric["profit_factor"]
        pf_text = f"{pf:.3f}" if pf is not None else "undefined"
        lines.append(
            f"| {name} | {metric['completed_trades']} | {metric['net_return']:.3%} | "
            f"{metric['maximum_mark_to_market_drawdown']:.3%} | {pf_text} |"
        )
    lines.extend(
        [
            "",
            f"Frozen development-selected portfolio: `{subset or 'cash'}`.",
            "",
            "Fixed-threshold asset comparisons remain descriptive when policy is inactive.",
            "Costs, fills, margin and calendars are unverified reference-price scenarios.",
            "Partial evaluation years do not establish the 100-trade annual criterion.",
            "Confidence intervals are descriptive and do not adjust for selection.",
            "",
        ]
    )
    (attempt / "diagnostics.md").write_text("\n".join(lines), encoding="utf-8")
    write_json(
        attempt / "artifact-manifest.json",
        {
            str(p.relative_to(attempt)).replace("\\", "/"): digest(p)
            for p in sorted(attempt.rglob("*"))
            if p.is_file() and p.name != "worker.log"
        },
        immutable=True,
    )
    return diagnostics


def smoke(config, output):
    """Small deterministic end-to-end synthetic exercise; no historical model claims."""
    import numpy as np
    import pandas as pd

    config = dict(config)
    config.update(
        thresholds=[0.45],
        leverage_scenarios=[1, 2, 5, 10, 20, 30],
        cost_multipliers=[1],
        minimum_tuning_trades=2,
    )
    index = pd.date_range("2024-01-08", periods=3600, freq="min", tz="UTC")
    bounds = [index[i].isoformat() for i in (0, 1200, 1800, 2400, 3599)]
    fold = dict(
        zip(
            ("train_start", "train_end", "calibration_end", "tuning_end", "test_end"),
            bounds,
            strict=True,
        ),
        name="synthetic_development",
    )
    config["folds"] = [fold]
    job = {
        "id": "synthetic-phase6a-v1",
        "fold": fold,
        "model": {"kind": "trend", "version": "phase6a-v1"},
        "label": {
            "kind": "barrier",
            "horizon_minutes": 15,
            "stop_multiple": 2.0,
            "reward_risk": 3.0,
        },
    }
    bars, contracts = {}, {}
    for number, asset in enumerate(config["assets"]):
        x = np.arange(len(index))
        price = (1.1, 2000.0, 70.0)[number] * (1 + 0.002 * np.sin(x / 35 + number) + 0.000001 * x)
        bars[asset] = pd.DataFrame(
            {
                "open": price,
                "close": price * (1 + 0.00005 * np.sin(x)),
                "high": price * 1.0002,
                "low": price * 0.9998,
                "volume": 1.0,
            },
            index=index,
        )
        contracts[asset] = {
            "digits": (5, 2, 3)[number],
            "specifications": {
                "minVolume": 1,
                "stepVolume": 1,
                "maxVolume": 100000000,
                "scheduleTimeZone": "UTC",
                "schedule": [{"startSecond": 0, "endSecond": 604800}],
                "holiday": [],
                "swapTime": 1439,
            },
        }
    return execute_experiment(
        config, job, output, synthetic_bars=bars, synthetic_contracts=contracts
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["audit", "plan", "smoke", "run", "worker"])
    parser.add_argument("--config", default="config/phase6a-research-v1.json")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--full-batch", action="store_true")
    parser.add_argument("--job-id")
    parser.add_argument("--attempt")
    parser.add_argument("--smoke-output", default="data/research/phase6a-v1/smoke-v1")
    args = parser.parse_args(argv)
    config_path = (ROOT / args.config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output, _ = validate_config(config)
    resources(config)
    for key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[key] = "1"
    if args.command == "audit":
        result = audit(config)
        print(f"AUDIT_COMPLETE protected_files={result['protected_files']}")
    elif args.command == "plan":
        _, jobs = initialize(config)
        print(f"PLAN_READY jobs={len(jobs)}; no training started")
    elif args.command == "run":
        result = run_queue(config, config_path, limit=args.limit, full_batch=args.full_batch)
        print(canonical(result))
    elif args.command == "smoke":
        target = (ROOT / args.smoke_output).resolve()
        if not target.is_relative_to(output):
            raise ValueError("Smoke outputs must stay within configured Phase6 directory")
        result = smoke(config, target)
        print(f"SYNTHETIC_SMOKE_COMPLETE accepted={result['accepted']}")
    elif args.command == "worker":
        jobs = {job["id"]: job for job in experiment_queue(config)}
        if args.job_id not in jobs or not args.attempt:
            raise ValueError("Worker requires a registered experiment and attempt")
        attempt = Path(args.attempt).resolve()
        if attempt.parent != output / "experiments" / args.job_id:
            raise ValueError("Unregistered worker attempt directory")
        if json.loads((output / "code-manifest.json").read_text()) != code_manifest():
            raise ValueError("Code changed after registration")
        execute_experiment(config, jobs[args.job_id], attempt)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ResearchSafetyStop as error:
        print(f"RESEARCH_SAFETY_STOP: {error}", flush=True)
        raise SystemExit(2) from None
    except Exception as error:
        # Broker/DB secrets are never read; avoid exception payloads regardless.
        print(
            f"PHASE6_STOPPED {type(error).__name__}; inspect preserved attempt evidence", flush=True
        )
        raise SystemExit(1) from None
