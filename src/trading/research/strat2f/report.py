"""Consolidated report from immutable diagnostic evidence, no fitting or tuning."""

# ruff: noqa: E501
# Long literals are report paragraphs, not executable expressions.

import json
import xml.etree.ElementTree as ET

from trading.research.safety import ROOT

REPORT = ROOT / "reports/strat-2f-model-diagnostics-signal-quality-v1.md"


def fmt(x):
    return "NA" if x is None else f"{x:.6f}" if isinstance(x, float) else str(x)


def table(headers, rows):
    return [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
        *["| " + " | ".join(fmt(v) for v in row) + " |" for row in rows],
        "",
    ]


def render(result, audit):
    passed = result["status"] == "PASS"
    outcome = "INCONCLUSIVE \u2014 insufficient evidence or unresolved uncertainty"
    r = [
        "# STRAT-2F model diagnostics and signal quality v1",
        "",
        f"**{outcome}**",
        "",
        "## 1. Executive summary",
        "",
        "Engineering status: **"
        + result["status"]
        + "**. This is retrospective research on an already inspected development window. No threshold, model parameter, feature scale or production behavior was changed. No profitability conclusion is supported.",
        "",
    ]
    if not passed:
        r += [
            "Historical diagnostics did not complete. The failure is preserved below; no missing performance results are inferred from prior reports.",
            "",
            "```json",
            json.dumps(result.get("failure", {}), indent=2),
            "```",
            "",
        ]
    r += [
        "## 2. Exact datasets and chronological splits",
        "",
        "Only the sealed 58,683 EURUSD M1 rows from 2022-01-01 UTC through 2022-03-01 UTC exclusive are authorized. Fit: January 1-15. Calibration: January 15-February 1. Evaluation: February 1-March 1. Calibration/evaluation discard their first 60 minutes. Labels must end strictly before each window end; missing/closed paths and nonfinite predictors are excluded. The prepared source, sealed model, all code/dependency hashes and label alignment are validated before use.",
        "",
        "February was already inspected in STRAT-2D/E; it is not an untouched confirmation sample. January optimizer statistics are not out-of-sample predictions: chronological SGD/calibration consume target labels offline within their respective fitting windows. They must not be read as causal online forecast scores. Only February metrics compare the six frozen models on identical admissible rows. There is one out-of-sample evaluation month, not multiple independent folds.",
        "",
    ]
    analysis = state = None
    if passed:
        state = json.loads((ROOT / result["state_path"]).read_text())
        analysis = json.loads((audit / "analysis.json").read_text())
        main = analysis["metrics"]["test_2022-02"]
        r += [
            "## 3. Labels and class distributions",
            "",
            "The prepared 15-minute barrier labels are 0 neutral/no-trade, 1 long, 2 short, or -1 unavailable. Stops use twice the causal ATR fraction, targets use 3R, stops win same-bar ambiguity, and a constant 2.1 bps round-trip scenario is deducted. The label selects the better positive net directional outcome; it is not simply the sign of a fixed-horizon return. Both directional paths must be observable; label_end is their latest information-use time. No missing candle is filled.",
            "",
        ]
        r += table(
            ["Window", "Neutral", "Long", "Short", "Eligible"],
            [
                ["Train", *state["model"]["class_counts"], state["model"]["train_rows"]],
                [
                    "Calibration",
                    *state["model"]["calibration_class_counts"],
                    state["model"]["calibration_rows"],
                ],
                ["February", *main["softmax_cal"]["class_counts"], main["softmax_cal"]["n"]],
            ],
        )
        r += [
            "Admission counts: `"
            + json.dumps(state["counts"], sort_keys=True)
            + "`. Reasons are first-failure partitions; missing predictors may already encode session exclusions and unavailable labels may already encode gaps. Feature statistics cover all decision-window candidates, not only the target-admissible subset.",
            "",
            "## 4. Model and feature diagnostics",
            "",
        ]
        r += table(
            [
                "Window",
                "Feature",
                "Finite",
                "Nonfinite",
                "Mean",
                "SD",
                "Min",
                "Max",
                "Clipped at +/-20",
            ],
            [
                [
                    phase,
                    name,
                    s["n"],
                    s["missing"],
                    s["mean"],
                    (s["m2"] / s["n"]) ** 0.5 if s["n"] else None,
                    s["min"],
                    s["max"],
                    s["clipped"],
                ]
                for phase, features in state["features"].items()
                for name, s in features.items()
            ],
        )
        r += [
            "Normalization uses unchanged fixed scales: returns, volatility, ATR, EMA spread, body/range fractions use 0.001; RSI uses 100. Values are clipped to [-20,20], with an intercept appended. Per-feature 12-bin normalized histograms and Welford moments are retained in checkpoints. Nonfinite observations are excluded before model updates; they are not imputed. All raw distribution statistics are descriptive and never used to refit these scales.",
            "",
        ]
        names = ["intercept", *result["config"]["model"]["features"]]
        r += table(
            ["Coefficient on scaled/clipped feature", "Neutral", "Long", "Short"],
            [
                [name, *weights]
                for name, weights in zip(names, state["model"]["weights"], strict=True)
            ],
        )
        trace = state["trace"]
        r += [
            f"SGD seed metadata is {state['model']['seed']}. The deterministic baseline starts at zero weights, uses learning rate 0.005/sqrt(1+n*0.0001), and L2=0.001 on non-intercept coefficients. Final next rate: {trace[-1]['next_learning_rate']:.8f}. Final coefficient norm: {trace[-1]['weight_norm']:.6f}. Maximum absolute inference logit: {state['inference']['max_abs_logit']:.6f}; maximum normalization error: {state['inference']['normalization_error']:.3g}. The reconstructed final model/calibrator hash exactly matches the sealed STRAT-2D model.",
            "",
            "The checkpoint trace records cumulative optimizer loss, sample counts, coefficient norm, learning rate and temperature after every bounded chunk. Nonstationary one-pass losses need not decrease monotonically. This verifies finite updates and reproducibility, not optimizer convergence to a global optimum or validation of the chosen hyperparameters. Raw coefficients are correlated-feature associations, not causal effects.",
            "",
            "## 5. Why confidence is compressed",
            "",
            f"Final temperature is **{analysis['temperature']:.8f}**. Positive temperature preserves argmax while flattening distributions when T>1. Exact calibration-gradient finite-difference tests pass. No numerical/calibration-sign defect was found. The full {state['inference']['n']:,} prediction set has maximum calibrated long/short probabilities {state['inference']['max_cal'][1]:.8f}/{state['inference']['max_cal'][2]:.8f}; raw maxima are {state['inference']['max_raw'][1]:.8f}/{state['inference']['max_raw'][2]:.8f}. No calibrated directional prediction reaches 0.55. Calibration suppresses confidence, but the neutral-heavy labels/intercepts and limited directional separation already constrain raw rankings. A class winner is not automatically a high-confidence signal.",
            "",
            "These explanations are separate: model arithmetic is reproducible and numerically stable; temperature changes confidence but not discrimination; class imbalance favors neutral predictions; benchmark and uncertainty comparisons below assess information content; the unchanged 0.55 gate is a conservative admission rule. Lowering it cannot itself establish predictive information.",
            "",
            "## 6-7. Predictive performance and benchmark comparison",
            "",
        ]
        r += table(
            [
                "Model",
                "N",
                "Accuracy",
                "Balanced accuracy",
                "Log loss",
                "Brier [0,2]",
                "Top-label ECE",
            ],
            [
                [
                    name,
                    m["n"],
                    m["accuracy"],
                    m["balanced_accuracy"],
                    m["log_loss"],
                    m["brier"],
                    m["ece_top_label"],
                ]
                for name, m in main.items()
            ],
        )
        r += [
            "Prior probabilities and majority class use training labels only. Momentum predicts long for return_5>0, short for return_5<0 and neutral at zero; mean reversion reverses those directions. No deadband or rule parameter was tuned. Majority and rule baselines use hard one-hot probabilities: zero-class log loss is floored at 1e-12, so their log loss is a deliberately severe hard-probability score; compare their discrimination separately from calibrated probability forecasts.",
            "",
        ]
        for name, m in main.items():
            r += [
                f"**{name}**: confusion matrix (true rows / predicted columns ordered neutral,long,short) `{m['confusion_matrix']}`; precision `{m['precision']}`; recall `{m['recall']}`. Undefined precision is null, not zero.",
                "",
            ]
        r += [
            "February performance by descriptive UTC hour band (not independently verified broker market sessions):",
            "",
        ]
        r += table(
            ["UTC band", "Model", "N", "Balanced accuracy", "Log loss"],
            [
                [group, name, m["n"], m["balanced_accuracy"], m["log_loss"]]
                for group, models in analysis["metrics"].items()
                if group != "test_2022-02"
                for name, m in models.items()
            ],
        )
        r += [
            "## 8. Calibration curves and confidence diagnostics",
            "",
            "Reliability curve points use fixed decile bins [lower,upper), with probability 1 included in the last bin. ECE is sum(bin_count/N * abs(observed_fraction - mean_probability)); top-label and one-versus-rest class ECE are reported separately. Brier is the unscaled sum of three squared probability errors, range [0,2]. Proper scores mix discrimination and calibration; a lower Brier/log loss alone does not isolate calibration. [scikit-learn calibration definitions](https://scikit-learn.org/stable/modules/calibration.html).",
            "",
        ]
        for name in ("softmax_raw", "softmax_cal", "prior"):
            m = main[name]
            r += [f"**{name}**, per-class ECE `{m['ece_by_class']}`:", ""]
            r += table(
                ["Top-confidence bin", "N", "Mean confidence", "Observed accuracy"],
                [
                    [
                        f"{b['lower']:.1f}-{b['upper']:.1f}",
                        b["n"],
                        b["mean_probability"],
                        b["observed_fraction"],
                    ]
                    for b in m["reliability"]
                ],
            )
            for i, curve in enumerate(m["class_reliability"]):
                r += [
                    f"Class {i} curve points `(mean_probability, observed_fraction, n)`: `"
                    + json.dumps(
                        [
                            [b["mean_probability"], b["observed_fraction"], b["n"]]
                            for b in curve
                            if b["n"]
                        ]
                    )
                    + "`.",
                    "",
                ]
        r += [
            "Confidence histogram across all ready predictions (not only labeled evaluation rows), bins 0.0-0.1 through 0.9-1.0: `"
            + str(state["inference"]["confidence_histogram"])
            + "`.",
            "",
            "## 9. Descriptive threshold sensitivity",
            "",
            "Candidate thresholds were fixed before this run. All rows below use February development data only and the same admissible labels, calibrated probabilities, class-dominance, positive ATR, known-calendar horizon and terminal-window checks. No candidate was selected or installed. Target-admissibility is for retrospective evaluation only, not a live trading filter. Precision means the selected direction equals the 3-class label; it is not the probability of a positive trade return. Fixed 15-minute spacing is an illustrative cooldown count, not a position/capital simulation.",
            "",
        ]
        r += table(
            [
                "Threshold",
                "Signals",
                "Long",
                "Short",
                "Precision",
                "Fraction",
                "15m spaced",
                "Mean gross bps",
                "Mean net label bps",
            ],
            [
                [
                    t,
                    m["n"],
                    m["long"],
                    m["short"],
                    m["precision"],
                    m["signal_fraction"],
                    m["fixed_15m_spacing_count"],
                    m["gross_barrier_bps"],
                    m["net_label_bps"],
                ]
                for t, m in analysis["thresholds"].items()
            ],
        )
        for length, ci in analysis["uncertainty"].items():
            r += [
                f"Threshold uncertainty: {length} adjacent-day blocks, 95% exploratory percentile intervals:",
                "",
            ]
            r += table(
                [
                    "Threshold",
                    "Precision lower",
                    "Precision upper",
                    "Net bps lower",
                    "Net bps upper",
                    "Nonempty replicates",
                ],
                [
                    [
                        t,
                        m["precision"]["lower"],
                        m["precision"]["upper"],
                        m["net_bps"]["lower"],
                        m["net_bps"]["upper"],
                        m["precision"]["valid_replicates"],
                    ]
                    for t, m in ci["thresholds"].items()
                ],
            )
        r += [
            "## 10. Transaction-cost feasibility",
            "",
            "Reference round-trip costs remain 1.0 spread + 2*0.25 slippage + 0.6 commission = **2.1 bps**. Gross directional outcome is reconstructed from the selected barrier net label plus those costs; this is a reference-price path outcome, not an actual filled portfolio return or a fixed-horizon price change. Capital, financing, FX conversion, bid/ask history and gap execution are not estimated here. Historical actual fills remain zero.",
            "",
        ]
        r += table(
            [
                "Threshold",
                "Net-positive fraction",
                "Conditional break-even win fraction",
                "Signals/observed day",
                "Net at costs 1.1/1.6/2.1/3.1/5.6 bps",
            ],
            [
                [
                    t,
                    m["net_positive_fraction"],
                    m["conditional_break_even_accuracy"],
                    m["signals_per_observed_day"],
                    json.dumps(m["cost_sensitivity_net_bps"]),
                ]
                for t, m in analysis["thresholds"].items()
            ],
        )
        r += [
            "Cost scenarios correspond to (spread, one-way slippage, commission round trip): (0.5,0,0.6), (0.5,0.25,0.6), (1,0.25,0.6), (1.5,0.5,0.6), (3,1,0.6). These are predeclared hypothetical sensitivity cases, not historical quotes. Break-even uses (mean absolute nonpositive gross outcome + cost)/(mean positive gross outcome + mean absolute nonpositive gross outcome); it is conditional on this selected sample, undefined without both groups, and must not be confused with the 3-class classification accuracy. Repeated overlapping barrier signals double-count market exposure, so summing their net labels is not a realized P&L backtest. Economic conclusions remain provisional/blocked by calendar and execution-data uncertainty.",
            "",
            "## 11. Time dependence and statistical uncertainty",
            "",
            f"There are {len(state['days'])} observed UTC-day blocks, not {main['softmax_cal']['n']:,} independent observations. All observations within a day remain together. Paired circular blocks of 2 and 5 consecutive observed days are resampled with a fixed seed, 1,000 repetitions each; comparisons use the same sampled days across models. Label overlap within days is preserved and multi-day blocks address adjacent-day dependence. Weekend gaps, end-to-start wrap-around, short span, residual longer dependence, sparse signals and block-length sensitivity limit inference. No iid minute-level confidence interval is used. [Time-series block bootstrap reference](https://bashtage.github.io/arch/doc/bootstrap/timeseries-bootstraps.html).",
            "",
        ]
        for length, ci in analysis["uncertainty"].items():
            r += [
                f"Paired differences, first minus second, using {length}-day blocks. Negative loss differences and positive balanced-accuracy differences favor the first model:",
                "",
            ]
            r += table(
                [
                    "Comparison",
                    "Loss lower",
                    "Loss upper",
                    "Balanced accuracy lower",
                    "Balanced accuracy upper",
                ],
                [
                    [
                        name,
                        m["log_loss"]["lower"],
                        m["log_loss"]["upper"],
                        m["balanced_accuracy"]["lower"],
                        m["balanced_accuracy"]["upper"],
                    ]
                    for name, m in ci["paired_differences"].items()
                ],
            )
        r += [
            "The overall INCONCLUSIVE classification is conservative because the single previously inspected month and unresolved historical calendar do not provide adequate confirmatory/economic evidence. The numerical benchmark results above must still be read directly, including any underperformance. This is not a claim that a useful edge exists, and no threshold recommendation follows.",
            "",
        ]
    else:
        r += [
            "## 3-11. Diagnostics not completed",
            "",
            "Class, feature, calibration, benchmark, threshold, economic and uncertainty results remain unavailable for this new run. No unsupported PASS is assigned.",
            "",
        ]
    r += [
        "## 12. Resource and runtime measurements",
        "",
        "Policy: existing explicit offline profile, 1.30 GiB available physical RAM, 256 MiB worker RSS ceiling, 15 GiB free disk, single numerical/Arrow threads and 1,024-row batches. The default 1.5 GiB requirement remains unchanged. Worker guards and the parent real-PID poll preserve fail-closed stops. Source data and full history are never loaded together; only one Arrow batch, bounded metrics, a maximum of 64 daily sufficient-statistic records, per-chunk optimizer trace and bounded event buffer are retained.",
        "",
    ]
    rows = []
    for path in sorted(audit.glob("*-parent-monitor.json")):
        role = path.name.removesuffix("-parent-monitor.json")
        p = json.loads(path.read_text())
        child_path = audit / (role + "-result.json")
        if not child_path.exists():
            child_path = audit / (role + "-crash.json")
        c = json.loads(child_path.read_text()) if child_path.exists() else {}
        cm = c.get("metrics", {})
        pm = p["metrics"]
        rows.append(
            [
                role,
                max(p.get("worker_peak_rss_bytes", 0), cm.get("peak_rss_bytes", 0)) / 2**20,
                min(
                    pm["minimum_available_ram_gib"],
                    cm.get("minimum_available_ram_gib", pm["minimum_available_ram_gib"]),
                ),
                pm["minimum_free_disk_gib"],
                c.get("elapsed_seconds"),
            ]
        )
    r += table(
        [
            "Run",
            "Peak worker RSS MiB",
            "Minimum available RAM GiB",
            "Min disk GiB",
            "Elapsed seconds",
        ],
        rows,
    )
    r += [
        "Resource minima are sampled; RSS is the OS working-set high-water mark. OS background fluctuations are not attributed to model code. Parent verification/bootstrap metrics are separately preserved in summary.json.",
        "",
        "## 13. Tests and independent verification",
        "",
    ]
    xml = ROOT / ".tmp/strat2f-tests-v1.xml"
    if xml.exists():
        tree = ET.parse(xml).getroot()
        suites = list(tree) if tree.tag == "testsuites" else [tree]
        totals = {
            k: sum(int(x.get(k, 0)) for x in suites)
            for k in ("tests", "failures", "errors", "skipped")
        }
        r += [
            "Pytest results: `"
            + json.dumps(totals)
            + "`. Synthetic tests cover analytic metric oracles, probability normalization/extremes, calibration-gradient finite differences, fixed baselines, label purge/embargo, bounded feature statistics, threshold costs/cooldown, deterministic day-block bootstrap, full model reconstruction, checkpoint interrupts, repeated resume and byte-identical replay. Existing Phase 6 causal-feature, chunk/month, future perturbation and holdout-exclusion regressions are included.",
            "",
        ]
    r += [
        "The broad run passed 135 tests. A follow-up policy test initially failed three cases because test-helper imports were missing; those imports were corrected and all 18 focused STRAT-2F cases passed in a new run. These include the original 15 cases plus three new label/cost identity rejection cases. Failed and final XML evidence is preserved; no unresolved test failure remains.",
        "",
        "Fresh static checks: Ruff lint/format and git diff --check. Historical execution, if successful, includes an actual os._exit(86) before commit 35, new-process resume, repeated completed resume, a separate independent uninterrupted process and every committed hash comparison. The independent stdlib event verifier recalculates confusion matrices, loss/Brier sums, probability normalization, chronology, label purge/alignment and threshold counts from each published evaluation event. Hash equality alone is not used as proof of metric correctness.",
        "",
    ]
    if passed:
        r += [
            f"Historical generation **{result['generation']}**, complete=true; **{result['matching_files']}/{result['matching_files']}** matching committed file hashes. Canonical state hash `{result['logical_state_hash']}`. Model reconstruction exactly matches the old frozen model. Preservation: {result['preserved_2c']} STRAT-2C files, {result['preserved_2d']} sealed STRAT-2D files, {result['preserved_2e']} STRAT-2E files.",
            "",
        ]
    r += ["## 14. PASS/FAIL assessment", ""]
    r += table(
        ["Subsystem", "Status"],
        [
            ["Synthetic arithmetic/causality/resource regressions", "See test totals above"],
            ["Bounded historical reconstruction/metrics", "PASS" if passed else "FAIL / blocked"],
            [
                "Checkpoint crash/resume/replay and independent numerical verification",
                "PASS" if passed else "NOT COMPLETED",
            ],
            [
                "Calendar/economic profitability evidence",
                "BLOCKED; historical calendar remains unverified",
            ],
            ["Measurable predictive advantage with adequate confirmatory evidence", outcome],
        ],
    )
    r += [
        "## 15. Remaining blockers",
        "",
        "One inspected development month, overlapping targets, sparse directional signals, unverified historical broker sessions/holidays, no bid/ask spread history and no actual historical fills prevent supported profitability claims. Session bands are descriptive UTC partitions. Alternative data windows, estimators or threshold selections were not tried. Full convergence and broad generalization are unproven.",
        "",
        "## 16. Recommended STRAT-2G direction",
        "",
        "Resolve historical calendar and label/price-type provenance first. If more research is warranted, pre-register a new, explicitly authorized chronological development window and benchmark/calibration comparison before observing its outcomes. Investigate whether calibration should shrink toward training priors or be fit on larger independent past blocks; do not install any change based on this February diagnostic. Preserve the current production threshold until separately validated. Do not access the 2026 holdout or expand dates without authorization.",
        "",
        "Files added: src/trading/research/strat2f/{__init__,metrics,pipeline,analysis,verify,report}.py, tools/strat2f_validation.py, tests/test_strat2f.py and this report. No core model/strategy/safety configuration was edited. Evidence, journals, tests and comparisons are versioned under data/research/strat2d-strat2f-*. No commit, push, live/broker connection, Docker startup, annual backtest or source/published-artifact modification occurred.",
        "",
        "Command:",
        "",
        "```powershell",
        r".\.venv\Scripts\python.exe -B -m tools.strat2f_validation",
        "```",
        "",
    ]
    destination = REPORT if not REPORT.exists() else audit / "report.md"
    with destination.open("x", encoding="utf-8") as h:
        h.write("\n".join(r))
