"""Evidence-only reporting; exploratory outcomes never promote a model."""

# ruff: noqa: E501
# Long string literals are report prose.
import json
import xml.etree.ElementTree as ET

from trading.research.safety import ROOT

REPORT = ROOT / "reports/strat-2g-predictive-model-improvement-v1.md"


def fmt(v):
    return "NA" if v is None else f"{v:.6f}" if isinstance(v, float) else str(v)


def table(headers, rows):
    return [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
        *["| " + " | ".join(fmt(v) for v in row) + " |" for row in rows],
        "",
    ]


def render(result, audit, protocol):
    passed = result["status"] == "PASS"
    a = json.loads((audit / "analysis.json").read_text()) if passed else None
    s = json.loads((ROOT / result["state_path"]).read_text()) if passed else None
    classification = (
        a["classification"]
        if a
        else "INCONCLUSIVE "
        + chr(8212)
        + " insufficient evidence to establish reliable improvement"
    )
    r = [
        "# STRAT-2G predictive model improvement v1",
        "",
        f"**{classification}**",
        "",
        "## 1. Executive summary",
        "",
        f"Engineering execution: **{result['status']}**. Research is exploratory on the repeatedly examined January-February 2022 EURUSD period. No independent out-of-sample proof, profitability claim or production promotion is supported. Nine model/target configurations were predefined, not chosen from favorable historical outcomes.",
        "",
    ]
    if not passed:
        r += [
            "Historical execution failed or was blocked. No missing result is invented. Failure evidence:",
            "",
            "```json",
            json.dumps(result.get("failure", {}), indent=2),
            "```",
            "",
        ]
    r += [
        "## 2. Exploratory research protocol",
        "",
        "The versioned protocol is `config/strat2g-protocol-v1.json`, written before implementing/running experiments and snapshotted in the evidence directory. The experiment evaluates three targets independently, each with three trainable families and four fixed baselines. It never ranks target definitions by their final scores. Model selection uses calibrated log loss on the selection slice only: start with the reference, prefer a more complex family only if it improves selection loss by more than 0.005. Minimum selection evidence is 1,000 rows and five observed days. Otherwise no candidate is selected. Parameters, target costs/horizons, feature transforms and seeds were fixed before historical execution.",
        "",
        "All candidates are frozen after January calibration; selection changes no fitted weights or temperatures. Choices are frozen at the first final-evaluation observation and independently recomputed from selection-only metrics. Every model is reported for transparency, but only the previously selected model may be the subject of a final selected-model comparison. Repeated scrutiny of these dates means the final slice remains exploratory.",
        "",
        "## 3. Dataset and chronological splits",
        "",
        "The only historical input is the sealed 58,683-row EURUSD M1 preparation, job 1d06fff895ee69a12b38, January 1-March 1, 2022 exclusive. The existing resource configuration is reused without modification. There is no other historical period and no 2026 holdout.",
        "",
    ]
    r += table(
        ["Role", "UTC bounds, right-exclusive"],
        [[name, str(bounds)] for name, bounds in protocol["splits"].items()],
    )
    r += [
        "Calibration, selection and evaluation each embargo their first 60 minutes. Every sample requires all 30 subsequent bars contiguous and safely admitted, and the common latest information-use time must be strictly before its split end. This deliberately over-purges the 5/15-minute targets to ensure identical eligible timestamps across targets/models. Labels mature after the shared 30-minute delay; no fitted update occurs before that information is observed.",
        "",
        "## 4. Target definitions and class balance",
        "",
        "Entry reference is the next bar open; exit is the close of the h-th subsequent bar. Gross return is exit/entry-1. LONG/SHORT requires gross return beyond the positive/negative neutral zone; otherwise FLAT. Labels are deterministic. Prices remain EURUSD price units, costs/returns are basis points of notional, not pips. A 2.1 bps cost on a 1.10 entry is 0.000231 price units; it is not 2.1 pips. There is no barrier-stop label in this new matrix.",
        "",
    ]
    r += table(
        ["Target", "h (minutes)", "Neutral zone (bps)"],
        [
            [
                t["id"],
                t["horizon"],
                "max(2.1, "
                + str(t["volatility_multiple"])
                + " * causal sigma20 * sqrt(h) * 10000)",
            ]
            for t in protocol["targets"]
        ],
    )
    if passed:
        balance = []
        for t, models in s["models"].items():
            m = models["reference8"]["state"]
            balance.extend(
                [
                    [t, "train", m["train_rows"], m["class_counts"]],
                    [t, "calibration", m["calibration_rows"], m["calibration_class_counts"]],
                ]
            )
            for phase in ("selection", "evaluation"):
                z = a["metrics"][phase][t]["prior"]
                balance.append([t, phase, z["n"], z["class_counts"]])
        r += table(["Target", "Window", "N", "FLAT/LONG/SHORT"], balance)
        r += [
            "Input/path availability counters: `"
            + json.dumps(s["counts"], sort_keys=True)
            + "`; raw input rows "
            + str(s["rows"])
            + "; terminal unavailable pending rows "
            + str(s["tail_unavailable"])
            + ". These counters describe common-path eligibility, not an assertion that every shorter target was unavailable when a longer path failed. All models within a target and all targets share each admitted decision timestamp.",
            "",
        ]
    else:
        r += ["Historical availability and class counts: **not measured in this attempt**.", ""]
    r += [
        "Boundary policy: reset features/pending labels at every observed gap or calendar exclusion; exclude New York 16:30-17:30 daily, Friday from16:00, Saturday, and Sunday before18:00. These predeclared conservative buffers avoid known candidate rollover/weekend boundaries. Any target path crossing them is excluded. Exact historical sessions, exceptional closures and holiday hours remain unverified; the buffers cannot prove the absence of unknown intraday boundary changes. A strict verified-calendar performance policy would still block promotion. No missing bar or historical hour is invented.",
        "",
        "## 5. Causal feature engineering",
        "",
        "The original eight published features remain the reference input. New features use only the current completed bar and a maximum 61 observed contiguous bars. They reset at gaps/exclusions. A new sample requires at least31 bars and two fully completed UTC-aligned five-minute aggregates. Partial five-minute periods are discarded; future bars never complete an aggregate early.",
        "",
    ]
    r += table(
        ["New feature vector (12)", "Transform"],
        [
            ["momentum1/5/15/30", "close/lagged_close-1, divided by0.001"],
            ["vol-normalized momentum5", "return5/(max(sigma20,1e-6)*sqrt5)"],
            [
                "realized volatility20",
                "population SD of last20 one-minute returns, divided by0.0005",
            ],
            ["current high-low range", "(high-low)/close, divided by0.001"],
            ["rolling price position20", "2*(close-low20)/(high20-low20)-1; flat range ->0"],
            ["mean reversion20", "-(close/mean_close20-1)/(vol_floor*sqrt20)"],
            [
                "completed5m momentum",
                "last completed5m close / preceding completed5m close-1, divided by0.001",
            ],
            ["UTC time-of-day sin/cos", "known completed-bar decision minute; two features"],
        ],
    )
    r += [
        "New normalized features are clipped to [-5,5], without fitting normalization on later data. Four fixed interaction terms are z5*position20, momentum5*range, mean_reversion20 squared, and sigma20*abs(momentum5), each also clipped to [-5,5]. These compact nonlinear features do not imply that nonlinear predictive information exists.",
        "",
        "## 6. Candidate support matrix",
        "",
    ]
    r += table(
        ["Family", "Dimensions excluding intercept", "Initial learning rate", "L2", "Support"],
        [
            [
                "reference8",
                8,
                0.005,
                0.001,
                "Original OnlineModel update, original8 predictors, alternative labels",
            ],
            ["linear12", 12, 0.003, 0.01, "Deterministic regularized online softmax"],
            [
                "interaction16",
                16,
                0.003,
                0.01,
                "Same incremental kernel with four fixed interactions",
            ],
        ],
    )
    r += [
        "All start from zero coefficients, seed metadata20261027, float64 model arithmetic and original decreasing learning-rate/temperature-update formulas. Calibration changes only a scalar log-temperature with original bounds. Every checkpoint binds schema strat2g-model-state-v1, family, target, exact feature/scaling spec and parameters. Mismatches are rejected; there is no silent estimator substitution or migration. Nonincremental large ensembles/neural frameworks are unsupported and not added. The old production model and old barrier-label metrics remain preserved contextual references, not directly comparable scores for a different target.",
        "",
        "## 7. Configurations and experiment counts",
        "",
        "Exactly **3 targets x3 trainable families =9** training/calibration configurations. Raw and calibrated views are two views of each fitted candidate, not18 separately tuned models. Four baselines per target: training majority, training-class prior probabilities, sign(return5) momentum and its mean-reversion inverse. All share the same eligible target timestamps. Hard one-hot baselines use a 1e-12 probability floor for log loss and must be judged separately from probabilistic calibration.",
        "",
    ]
    if passed:
        r += [
            "Selection choices, frozen before final outcomes: `"
            + json.dumps(s["choices"], sort_keys=True)
            + "`.",
            "",
        ]
    r += ["## 8. Predictive metrics and baselines", ""]
    if passed:
        for phase in ("selection", "evaluation"):
            r += [f"**{phase}** (all exploratory):", ""]
            r += table(
                [
                    "Target",
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
                        t,
                        name,
                        m["n"],
                        m["accuracy"],
                        m["balanced_accuracy"],
                        m["log_loss"],
                        m["brier"],
                        m["ece_top_label"],
                    ]
                    for t, models in a["metrics"][phase].items()
                    for name, m in models.items()
                ],
            )
        for t, models in a["metrics"]["evaluation"].items():
            for name, m in models.items():
                r += [
                    f"{t} / {name}: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `{m['confusion_matrix']}`; precision `{m['precision']}`; recall `{m['recall']}`. Undefined values are null.",
                    "",
                ]
        r += [
            "Final evaluation by descriptive UTC bands (these are not verified broker sessions):",
            "",
        ]
        r += table(
            ["Target", "UTC band", "Model", "N", "Balanced accuracy", "Log loss"],
            [
                [t, band, name, m["n"], m["balanced_accuracy"], m["log_loss"]]
                for t, groups in a["sessions"].items()
                for band, models in groups.items()
                for name, m in models.items()
            ],
        )
    else:
        r += [
            "Historical candidate/baseline metrics were not executed. No predictive improvement is inferred.",
            "",
        ]
    r += [
        "January supplies fitting/calibration observations; February supplies both selection and final exploratory evaluation. There is only one final evaluation month and no cross-month confirmation. Absolute loss comparisons across different target definitions are not a valid model ranking because their class entropies differ.",
        "",
        "## 9. Probability calibration",
        "",
        "Both raw and scalar-temperature probabilities are reported on the same final rows. Calibration is fitted only in January; final observations never fit it. Temperature preserves class argmax. Top-label and per-class reliability bins/ECE use ten fixed bins; proper loss/Brier scores mix discrimination and calibration. Reliability curves and full accumulators are stored in journal states. Low ECE alone is not evidence of directional utility.",
        "",
    ]
    if passed:
        r += table(
            ["Target", "Family", "Temperature", "Train updates", "Calibration updates"],
            [
                [
                    t,
                    name,
                    __import__("math").exp(m["state"]["log_temperature"]),
                    m["state"]["train_rows"],
                    m["state"]["calibration_rows"],
                ]
                for t, models in s["models"].items()
                for name, m in models.items()
            ],
        )
        for t, choice in s["choices"].items():
            if choice is not None:
                m = a["metrics"]["evaluation"][t][choice + "_cal"]
                r += [
                    f"{t} selected {choice}: per-class ECE `{m['ece_by_class']}`; reliability `(mean_probability,observed_fraction,n)` points `"
                    + json.dumps(
                        [
                            [b["mean_probability"], b["observed_fraction"], b["n"]]
                            for b in m["reliability"]
                            if b["n"]
                        ]
                    )
                    + "`.",
                    "",
                ]
    r += [
        "## 10. Exploratory signal quality",
        "",
        "The production confidence gate is unchanged at0.55, with directional probability also required to exceed neutral. Signals below are hypothetical predictions on target-admissible common paths, not orders, fills or a portfolio run. Directional consistency means selected class equals the directional target; non-overlapping exposure is not assumed. A fixed horizon cooldown is a turnover proxy, not a strategy optimization.",
        "",
    ]
    if passed:
        rows = []
        for t, models in s["signals"].items():
            for name, m in models.items():
                n = m["n"]
                rows.append(
                    [
                        t,
                        name,
                        n,
                        m["long"],
                        m["short"],
                        m["correct"] / n if n else None,
                        m["gross_bps"] / n if n else None,
                        m["net_bps"] / n if n else None,
                        m["spaced_signals"],
                    ]
                )
        r += table(
            [
                "Target",
                "Candidate",
                "Signals",
                "Long",
                "Short",
                "Label precision",
                "Gross forward bps",
                "Net scenario bps",
                "Horizon-spaced count",
            ],
            rows,
        )
        r += [
            "Confidence distributions and per-class probability bins are retained for all models in the committed metric state; signal occurrence can cluster in a small number of days, so row counts are not independent evidence.",
            "",
        ]
    r += [
        "## 11. Transaction-cost feasibility",
        "",
        "Round-trip scenario cost remains 1 spread +2*0.25 slippage +0.6 commission =2.1 bps. This is an explicit reference-price approximation, not verified historical bid/ask costs. Gross directional movement, label correctness and expected filled trading return are different quantities. Sensitivity subtracts fixed total costs of1.1/2.1/3.1/5.6 bps from the same gross conditional outcomes; it does not refit or change decisions. Costs, financing, sizing, capital and gaps are not a realized trading simulation.",
        "",
    ]
    if passed:
        r += table(
            ["Target", "Candidate", "Mean net bps at costs1.1 /2.1 /3.1 /5.6"],
            [
                [
                    t,
                    name,
                    str([m["gross_bps"] / m["n"] - c for c in (1.1, 2.1, 3.1, 5.6)])
                    if m["n"]
                    else "NA: no signals",
                ]
                for t, models in s["signals"].items()
                for name, m in models.items()
            ],
        )
    r += [
        "Historical calendar/holiday and source-price assumptions remain unverified. Any apparent positive scenario mean is provisional and cannot establish profitability; if costs consume a measured conditional gross edge, that must not be presented as an executable advantage. No live, paper or broker-connected trading occurs.",
        "",
        "## 12. Statistical uncertainty",
        "",
        "Final selected-model differences versus reference8 and training-prior use paired circular blocks of2 and5 adjacent observed UTC days,1,000 fixed-seed resamples each. Within-day overlapping minute targets remain together, and multi-day blocks retain short-range adjacent dependence. There is no iid minute-level interval. Few days, sparse directional events, residual long dependence, wrap-around and nine exploratory configurations create uncertainty and selection risk. Intervals are descriptive, not multiplicity-adjusted confirmation tests. [Block bootstrap definitions](https://bashtage.github.io/arch/doc/bootstrap/timeseries-bootstraps.html).",
        "",
    ]
    if passed:
        rows = []
        for t, u in a["uncertainty"].items():
            if "intervals" not in u:
                continue
            for length, comparisons in u["intervals"].items():
                for base, m in comparisons.items():
                    rows.append(
                        [
                            t,
                            u["selected"],
                            u["days"],
                            length,
                            base,
                            m["log_loss"]["lower"],
                            m["log_loss"]["upper"],
                            m["balanced_accuracy"]["lower"],
                            m["balanced_accuracy"]["upper"],
                        ]
                    )
        r += table(
            [
                "Target",
                "Selected",
                "Observed days",
                "Block days",
                "Comparator",
                "Loss diff lower",
                "Loss diff upper",
                "BA diff lower",
                "BA diff upper",
            ],
            rows,
        )
        r += [
            "Negative loss differences and positive balanced-accuracy differences favor the selected candidate. Exploratory PASS requires both criteria against both comparators under both block lengths. Insufficient selection or unresolved intervals is INCONCLUSIVE; the conclusion never means independently validated profitability.",
            "",
        ]
    r += [
        "## 13. Resource measurements",
        "",
        f"Runtime profile {result['config']['resource_profile']} requires minimum available RAM {result['config']['minimum_available_ram_gib']:.2f} GiB, maximum worker RSS 256 MiB, disk floor 15 GiB and one numerical/Arrow thread. General training/backtest workflows still require 1.50 GiB; previous profiles remain at 1.30 GiB. External real-PID polling and internal bounded guards fail closed. Memory holds at most 61 feature bars, 31 pending raw/feature rows, nine compact models, one 1024-row frame and bounded metrics/day/event buffers.",
        "",
    ]
    measures = []
    for path in sorted(audit.glob("*-parent-monitor.json")):
        role = path.name.removesuffix("-parent-monitor.json")
        p = json.loads(path.read_text())
        cp = audit / (role + "-result.json")
        if not cp.exists():
            cp = audit / (role + "-crash.json")
        c = json.loads(cp.read_text()) if cp.exists() else {}
        m = c.get("metrics", {})
        pm = p["metrics"]
        measures.append(
            [
                role,
                max(p.get("worker_peak_rss_bytes", 0), m.get("peak_rss_bytes", 0)) / 2**20
                if c
                else None,
                min(
                    pm["minimum_available_ram_gib"],
                    m.get("minimum_available_ram_gib", pm["minimum_available_ram_gib"]),
                ),
                pm["minimum_free_disk_gib"],
                c.get("elapsed_seconds"),
            ]
        )
    r += table(
        [
            "Worker",
            "Peak RSS MiB",
            "Minimum available RAM GiB",
            "Minimum disk GiB",
            "Elapsed seconds",
        ],
        measures,
    )
    r += [
        "RSS is OS working-set high-water; system minima are sampled and background fluctuations are not code effects. No full dataset is loaded merely because these two months are small.",
        "",
        "## 14. Checkpoint and deterministic replay",
        "",
        "Atomic immutable transactions publish cursor, feature history, pending target buffer, all nine model/calibrator states, selected-model identities, metrics and event sequence together. Code, protocol, source hashes and numerical-library versions bind the journal identity. Orphan staging is preserved and ignored after interruption. A crash cannot commit half of a model batch. Old journals are never migrated or rewritten.",
        "",
    ]
    if passed:
        r += [
            f"Actual hard process exit before commit35, new-process resume, completed repeat and independent uninterrupted run passed. Generation **{result['generation']}**, complete=true; **{result['matching_files']}/{result['matching_files']}** committed hashes and canonical state match. Independent streamed verification checks price units, target formula/horizon, common maturity, chronological splits, event sequence, probability normalization, confusion/loss/Brier totals and selection-only provenance. Preserved old evidence: `{result['preserved']}`, plus47 STRAT-2C hashes/mtimes.",
            "",
        ]
    else:
        r += [
            "Fresh historical crash/resume and independent replay remain NOT COMPLETED. Preserved failure metrics above are not successful historical validation.",
            "",
        ]
    r += ["## 15. Automated tests", ""]
    for name in (
        "strat2g-tests-v1.xml",
        "strat2g-tests-v2.xml",
        "strat2g-regression-v3.xml",
        "strat2g-final-v4.xml",
    ):
        p = ROOT / ".tmp" / name
        if p.exists():
            tree = ET.parse(p).getroot()
            suites = list(tree) if tree.tag == "testsuites" else [tree]
            counts = {
                k: sum(int(x.get(k, 0)) for x in suites)
                for k in ("tests", "failures", "errors", "skipped")
            }
            r += [name + ": `" + json.dumps(counts) + "`.", ""]
    r += [
        "The initial two failures were a reused synthetic fixture with a0.34 threshold; the unchanged0.55 safety assertion correctly rejected it. Only the synthetic fixture was corrected. Tests cover target price units/costs, volatility zones, future perturbation, completed timeframe boundaries, gap/session reset, purging/embargo, stable model/schema compatibility, bounded state, selection simplicity/ties without final inputs, interruption before/after publication, repeated resume and independent event verification. Existing Phase6 causal/chunk/holdout and STRAT-2F metric/calibration/resource tests are retained. Known NumPy timedelta deprecations are recorded, not silenced. Ruff check/format and git diff --check are required before historical execution.",
        "",
        "## 16. PASS/FAIL/INCONCLUSIVE assessment",
        "",
    ]
    r += table(
        ["Subsystem", "Status"],
        [
            [
                "Protocol/fixed matrix/causal feature and target implementation",
                "Implemented and synthetically tested",
            ],
            [
                "Chronological selection and no final-fit leakage",
                "Tested; historical verification " + ("PASS" if passed else "NOT COMPLETED"),
            ],
            [
                "Historical bounded matrix / checkpoint / replay",
                "PASS" if passed else "FAIL TO COMPLETE / blocked",
            ],
            ["Historical verified-calendar / profitability evidence", "UNVERIFIED / BLOCKED"],
            ["Research conclusion", classification],
        ],
    )
    r += [
        "## 17. Recommended next development phase",
        "",
        "Resolve exact historical calendar/holiday and price-type evidence first. If exploratory differences warrant further research, pre-register a stable simple candidate and an independent protocol on a newly authorized development period before inspecting outcomes. No expansion of dates is authorized by this report, and2026 remains excluded. Do not promote an experimental model or relax the production0.55 threshold. A small repeatedly inspected period cannot supply independent confirmation.",
        "",
        "Added: config/strat2g-protocol-v1.json; src/trading/research/strat2g/{__init__,core,pipeline,analysis,report}.py; tools/strat2g_validation.py; tests/test_strat2g.py; this report. Previous Phase5 data, STRAT-2C/D/E/F evidence, core model definitions and safety profiles are unchanged. No dependencies, Docker services, broker connections, paper/live orders, full-year backtests, commits or pushes were introduced.",
        "",
        "Autonomous command:",
        "",
        "```powershell",
        r".\.venv\Scripts\python.exe -B -m tools.strat2g_validation",
        "```",
        "",
    ]
    destination = REPORT if not REPORT.exists() else audit / "report.md"
    with destination.open("x", encoding="utf-8") as h:
        h.write("\n".join(r))
