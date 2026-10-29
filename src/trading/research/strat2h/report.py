"""Consolidated evidence report; prior research reports are never rewritten."""

# Prose templates intentionally retain complete paragraphs.
# ruff: noqa: E501

import json
import math
import xml.etree.ElementTree as ET

from trading.research.safety import ROOT


def number(x):
    return "unavailable" if x is None else f"{x:.6f}" if isinstance(x, float) else str(x)


def table(headers, rows):
    return "\n".join(
        [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
            *("| " + " | ".join(number(v) for v in row) + " |" for row in rows),
        ]
    )


def render(result, audit):
    protocol = json.loads((ROOT / "config/strat2h-diagnostics-protocol-v1.json").read_text("utf-8"))
    analysis = (
        json.loads((audit / "analysis.json").read_text("utf-8"))
        if (audit / "analysis.json").exists()
        else None
    )
    measurements = [result["metrics"]]
    peaks, durations = [], {}
    for path in sorted(audit.glob("*-parent-monitor.json")):
        v = json.loads(path.read_text("utf-8"))
        measurements.append(v["metrics"])
        if v["worker_peak_rss_bytes"]:
            peaks.append(v["worker_peak_rss_bytes"] / 2**20)
    for path in sorted(audit.glob("*-result.json")):
        v = json.loads(path.read_text("utf-8"))
        measurements.append(v["metrics"])
        durations[path.stem] = v["elapsed_seconds"]
    for path in audit.glob("*-failure.json"):
        measurements.append(json.loads(path.read_text("utf-8"))["metrics"])
    ram = [
        v["minimum_available_ram_gib"]
        for v in measurements
        if v.get("minimum_available_ram_gib") is not None
    ]
    disk = [
        v["minimum_free_disk_gib"]
        for v in measurements
        if v.get("minimum_free_disk_gib") is not None
    ]
    resource = (
        json.loads((audit / "resource-config.json").read_text("utf-8"))
        if (audit / "resource-config.json").exists()
        else {"minimum_available_ram_gib": 1.2}
    )
    frozen = protocol["frozen"]
    classification = analysis["status"] if analysis else "INCONCLUSIVE"
    lines = [
        "# STRAT-2H signal quality and independent validation preparation v1",
        "",
        "## 1. Executive summary",
        "",
        f"Engineering execution: **{result['status']}**. Research conclusion: **{classification} — "
        + (
            "available diagnostics contradict the proposed predictive advantage"
            if classification == "FAIL"
            else "signal quality or economic evidence remains insufficient"
        )
        + "**.",
        "",
        "January–February 2022 is repeatedly examined exploratory development data. No new historical period was accessed. The selected model, temperature, features, costs and 0.55 production gate remain frozen. No fitting, trading or portfolio simulation occurs in STRAT-2H.",
        "",
    ]
    if result.get("failure"):
        lines += [
            "Execution stopped safely; preserved failure:",
            "",
            "```json",
            json.dumps(result["failure"], indent=2),
            "```",
            "",
            "Historical diagnostics/recovery/replay are NOT claimed complete for this attempt. Prior STRAT-2G results below are explicitly inherited context.",
            "",
        ]
    lines += [
        "## 2. Frozen candidate and protocol",
        "",
        f"Selected `linear12 / h15_cost`, schema `{frozen['model_schema']}`, kernel `online_softmax_v1`, seed 20261027. Learning rate 0.003, L2 0.01, 12 clipped causal features with unit scales. State hash `{frozen['candidate_sha256']}`.",
        "",
        f"Frozen journal `{frozen['journal']}`, generation 59; manifest `{frozen['manifest_sha256']}`, receipt `{frozen['receipt_sha256']}`. Model training used 11,675 rows; temperature calibration used 13,428 rows. Temperature exp({frozen['log_temperature']}) = {math.exp(frozen['log_temperature']):.8f}.",
        "",
        "Model features: " + ", ".join(f"`{x}`" for x in frozen["spec"]["features"]) + ".",
        "",
        "Training Jan 1–15; calibration Jan 15–Feb 1; selection Feb 1–15; evaluation Feb 15–Mar 1, UTC exclusive right boundaries. All nontraining roles have 60-minute forward embargo. All labels require a common 30-minute contiguous safe path and common end strictly before the role's right boundary. Target entry is next bar open, exit is close of the fifteenth subsequent bar; LONG/SHORT beyond ±2.1 bps, otherwise FLAT.",
        "",
        "G selection: selection-only calibrated log loss; minimum 1,000 rows and five observed days; simplicity ordering reference8, linear12, interaction16; more complexity needs >0.005 loss improvement. Nine configurations were examined. H never selects again. January frozen-model scores are post-fit descriptive reuse, not generalization estimates.",
        "",
        "Protocols: `config/strat2h-diagnostics-protocol-v1.json` and `config/strat2h-confirmation-protocol-v1.json`. H protocol hash: `"
        + result["protocol_sha256"]
        + "`.",
        "",
        "## 3. Signal quality and causal movement diagnostics",
        "",
        "Underlying return distributions by argmax class remain unsigned price movements. Directional probability bins and forced-direction diagnostics use LONG when pLONG≥pSHORT, otherwise SHORT, including observations dominated by FLAT. Gated tables additionally require directional probability≥pFLAT. These forced directions are diagnostic counterfactuals, never orders. MFE/MAE use observed highs/lows inside the 15-bar path, not attainable fills.",
        "",
    ]
    if analysis:
        groups = analysis["groups"]
        evaluation = groups["phase:evaluation"]
        lines += [
            table(
                [
                    "Phase",
                    "Rows",
                    "Classes FLAT/LONG/SHORT",
                    "BA calibrated",
                    "Log loss cal",
                    "Log loss reference",
                    "Log loss prior",
                ],
                [
                    (
                        p,
                        groups["phase:" + p]["metrics"]["cal"]["n"],
                        groups["phase:" + p]["metrics"]["cal"]["class_counts"],
                        groups["phase:" + p]["metrics"]["cal"]["balanced_accuracy"],
                        groups["phase:" + p]["metrics"]["cal"]["log_loss"],
                        groups["phase:" + p]["metrics"]["reference"]["log_loss"],
                        groups["phase:" + p]["metrics"]["prior"]["log_loss"],
                    )
                    for p in ("train", "calibration", "selection", "evaluation")
                    if "phase:" + p in groups
                ],
            ),
            "",
            table(
                [
                    "Predicted class",
                    "n",
                    "Mean underlying bps",
                    "Mean abs bps",
                    "Up fraction",
                    "Observed high bps",
                    "Observed low adverse bps",
                ],
                [
                    (
                        name,
                        s.get("n"),
                        s.get("mean_signed_bps"),
                        s.get("mean_absolute_bps"),
                        s.get("sign_hit_rate"),
                        s.get("mean_favorable_bps"),
                        s.get("mean_adverse_bps"),
                    )
                    for name, s in zip(
                        ("FLAT", "LONG", "SHORT"),
                        evaluation["views"]["cal"]["predicted_class_returns"],
                        strict=True,
                    )
                ],
            ),
            "",
            "Complete fixed-bin histograms, class reliability, feature moments, gap-age, UTC-session and volatility-regime groups are in `analysis.json`. Return histogram edges (bps): "
            + str(protocol["return_histogram_bps_edges"])
            + ".",
            "",
        ]
    else:
        lines += [
            "New H historical metrics unavailable. Inherited G context: h15 linear12 final exploratory BA 0.35070 versus reference 0.33604; log-loss advantage over reference remained uncertain. No independent predictive or economic claim follows.",
            "",
        ]
    lines += [
        "## 4. Raw/calibrated probabilities and confidence gate",
        "",
        "Scalar temperature >1 softens logits without changing the argmax class. Class imbalance and weak directional logit separation also constrain confidence; a high gate can legitimately yield no signals. Neither threshold nor calibrator is changed.",
        "",
        "Frozen model audit (FLAT/LONG/SHORT): "
        + json.dumps(protocol["model_audit"], sort_keys=True)
        + ". The training/calibration priors and learned intercept favor FLAT. The 0.55 gate requires "
        "directional versus all-other-class log odds >= log(0.55/0.45). Raw maxima below that gate "
        "would establish that temperature alone cannot explain missing signals: learned feature "
        "logits are also insufficiently directional. The numerical audit and probability comparison "
        "do not imply an implementation error or justify retuning.",
        "",
    ]
    if analysis:
        lines += [
            table(
                ["View", "Max pFLAT", "Max pLONG", "Max pSHORT", "Log loss", "Top-label ECE"],
                [
                    (
                        v,
                        *evaluation["views"][v]["max_probability"],
                        evaluation["metrics"][v]["log_loss"],
                        evaluation["metrics"][v]["ece_top_label"],
                    )
                    for v in ("raw", "cal")
                ],
            ),
            "",
            table(
                [
                    "Directional probability bin",
                    "n",
                    "Directional class precision",
                    "Sign hit rate",
                    "Mean signed bps",
                    "Net @2.1bps",
                ],
                [
                    (
                        f"[{s['lower']:.2f},{s['upper']:.2f})",
                        s["n"],
                        s.get("label_precision"),
                        s.get("sign_hit_rate"),
                        s.get("mean_signed_bps"),
                        s.get("net_mean_bps_by_cost", {}).get("2.1"),
                    )
                    for s in evaluation["views"]["cal"]["direction_probability_bins"]
                    if s["n"]
                ],
            ),
            "",
            "Descriptive thresholds below are predeclared and were not selected using their outcomes:",
            "",
            table(
                [
                    "Gate",
                    "n",
                    "LONG",
                    "SHORT",
                    "Precision",
                    "Net bps @2.1",
                    "Clusters",
                    "Nonoverlap windows",
                    "Per 1,000",
                ],
                [
                    (
                        t,
                        s["n"],
                        s["long"],
                        s["short"],
                        s.get("label_precision"),
                        s.get("net_mean_bps_by_cost", {}).get("2.1"),
                        s["clusters"],
                        s["nonoverlap_count"],
                        s["frequency_per_1000_predictions"],
                    )
                    for t, s in evaluation["thresholds"].items()
                ],
            ),
            "",
        ]
    lines += [
        "## 5. Economic feasibility",
        "",
        "Base round-trip cost is 1.0 bps spread + 2×0.25 bps slippage + 0.6 bps commission = 2.1 bps. Sensitivities 1.1/2.1/3.1/5.6 bps correspond to spread/slippage scenarios, not observed bid/ask costs. Break-even gross movement is the chosen total cost; at EURUSD price P, a 2.1 bps move costs P×0.00021 price units. One pip is 0.0001 price units, not one basis point. Break-even hit rate requires both gain/loss magnitudes: (loss+cost)/(gain+loss); accuracy alone is insufficient.",
        "",
    ]
    if analysis:
        f = evaluation["views"]["cal"]["forced_direction"]
        lines += [
            f"Counterfactual all-observation directional rule: n={f['n']}, mean gross={number(f['mean_signed_bps'])} bps; mean MFE={number(f['mean_favorable_bps'])}, MAE={number(f['mean_adverse_bps'])}, volatility-scaled signed movement={number(f['mean_volatility_scaled_signed_move'])}.",
            "",
            table(
                ["Round-trip bps", "Mean hypothetical net bps"], f["net_mean_bps_by_cost"].items()
            ),
            "",
            "Empirical positive/nonpositive magnitudes imply break-even positive fractions "
            + str(f["break_even_positive_fraction_by_cost"])
            + ". These hold conditional magnitudes "
            "fixed; they do not establish attainability. Zero returns are included in the nonpositive group.",
            "",
        ]
    lines += [
        "Cost components (spread / slippage per side / round-trip commission): "
        + str(protocol["cost_scenarios_components"])
        + ".",
        "",
        "These overlapping hypothetical positions ignore capital, mutually exclusive positions and executable bid/ask prices. Cluster counts and greedily thinned 15-minute nonoverlap counts are turnover proxies; nonoverlap outcomes are not separately backtested. No realized portfolio return was generated. Historical calendar and price-side uncertainty block realizable-profit conclusions.",
        "",
        "## 6. Temporal stability",
        "",
        "January includes fitted/calibration observations and cannot be compared to February as independent performance. Weekly, month, fixed UTC-hour bands and predeclared causal sigma20 regimes (0.5/1/2 bps cutoffs) are descriptive. No regime or session was chosen for deployment. Full per-class calibration, priors, feature means/standard deviations/clipping and gap-age (<120 versus ≥120 bars since reset) are retained for every group.",
        "",
    ]
    if analysis:
        lines += [
            table(
                ["Group", "n", "BA", "Log loss", "Top ECE", "Forced net bps"],
                [
                    (
                        k,
                        v["metrics"]["cal"]["n"],
                        v["metrics"]["cal"]["balanced_accuracy"],
                        v["metrics"]["cal"]["log_loss"],
                        v["metrics"]["cal"]["ece_top_label"],
                        v["views"]["cal"]["forced_direction"]
                        .get("net_mean_bps_by_cost", {})
                        .get("2.1"),
                    )
                    for k, v in groups.items()
                    if k.startswith(
                        (
                            "month:",
                            "week:",
                            "session:evaluation:",
                            "volatility:evaluation:",
                            "gap_age:evaluation:",
                        )
                    )
                ],
            ),
            "",
        ]
    if analysis and "month:2022-01" in groups and "month:2022-02" in groups:
        jan = groups["month:2022-01"]["features"]
        feb = groups["month:2022-02"]["features"]
        rows = []
        for name, a, b in zip(frozen["spec"]["features"], jan, feb, strict=True):
            scale = math.sqrt((a["standard_deviation"] ** 2 + b["standard_deviation"] ** 2) / 2)
            rows.append(
                (
                    name,
                    a["mean"],
                    b["mean"],
                    (b["mean"] - a["mean"]) / scale if scale else None,
                    a["clipped_fraction"],
                    b["clipped_fraction"],
                )
            )
        lines += [
            "Feature drift uses pooled month standard deviations; it is descriptive post-fit "
            "data reuse, not an independent-sample significance test.",
            "",
            table(
                [
                    "Feature",
                    "January mean",
                    "February mean",
                    "Standardized shift",
                    "Jan clipped",
                    "Feb clipped",
                ],
                rows,
            ),
            "",
        ]
    lines += [
        "## 7. Statistical uncertainty and selection bias",
        "",
        "1,000 paired circular observed-UTC-day block replicates, lengths 2 and 5 days, seed 20261028+length. Minute labels remain together inside day blocks; 15-minute overlapping outcomes are never treated as independent. This models some serial dependence, not all structural breaks. Twelve final observed days give few effective blocks. Threshold intervals require at least five signal-bearing days and ten observed days; sparse cells have no interval. No IID minute-based standard errors. The nine G configurations and repeated reuse of this period prevent independent confirmation and make subgroup effects exploratory.",
        "",
    ]
    if analysis:
        lines += ["```json", json.dumps(analysis["uncertainty"], indent=2), "```", ""]
    lines += [
        "## 8. Historical calendar verification",
        "",
        "**UNVERIFIED** source-broker historical sessions/holidays and price-side semantics. The current weekly calendar and G conservative rollover/weekend buffers are unchanged. Inherited quality: 301 internal gaps / 23,516 minutes; 378 expected-open missing minutes and 16 out-of-session candles under the current hypothesis. All 16 closing-boundary observations are at 21:59 UTC / 16:59 New York. Their existence does not prove the broker was closed or the timestamps wrong.",
        "",
        "[Spotware's specification](https://help.ctrader.com/open-api/model-messages/) distinguishes UTC-minute bar timestamps from timezone-specific symbol intervals, with inclusive start and exclusive end. Local conversion in `src/trading/ctrader/client.py` uses UTC; registration filters M1 demo-source data with unknown price side. Code lineage supports the units, but does not independently attest the stored source history.",
        "",
        "[NIST DST rules](https://www.nist.gov/pml/time-and-frequency-division/popular-links/daylight-saving-time-dst) imply no New York DST change during January–February 2022; UTC−5 applies throughout. A UTC/server-time shift is not inferred from gaps.",
        "",
        "A [dated December 2021 broker notice](https://www.ic.com/blog/holiday-trading-schedule-dec-2021-jan-2022/) uses platform GMT+2 and warns of holiday pricing interruptions. Its retrieved text does not verify the source account's FX sessions. [Current OANDA hours](https://www.oanda.com/us-en/trading/hours-of-operation/) and [current IC Markets hours](https://www.icmarkets.eu/en/trading-pricing/trading-hours) cannot establish this broker/account's 2022 schedule. Weekend patterns support a hypothesis; exact Sunday reopening, Friday close and holiday exceptions remain unverified. Bank holidays are not automatically FX closures.",
        "",
        "`config/strat2h-calendar-review-v1.json` records authority and limits. Proposed migration is documentation-first: obtain dated source-account rules, attest timezone/price side, create a new fingerprint and regression suite, publish a separate lineage and compare exclusions. Never shift observations, invent candles or rewrite old evidence.",
        "",
        "## 9. Resources and runtime",
        "",
        "Unchanged opt-in `config/strat2g-eurusd-2022-offline-120-v1.json`: available physical RAM≥1.20 GiB, worker peak RSS≤256 MiB, free disk≥15 GiB, batch 1,024, one thread; parent samples every 0.1 seconds and worker checks every 64 bars and at persistence/read boundaries. New H outputs retain the resource adapter's required G prefix, with a distinct `strat2h` namespace. General 1.50 and earlier 1.30 policies are unchanged.",
        "",
        f"Peak externally measured worker RSS: {number(max(peaks) if peaks else None)} MiB. Minimum sampled system-available RAM: {number(min(ram) if ram else None)} GiB. Minimum disk: {number(min(disk) if disk else None)} GiB. Process RSS and system RAM are distinct measurements; sampled minima cannot prove absence of shorter fluctuations.",
        "",
        "Run durations (seconds): `"
        + json.dumps(durations, sort_keys=True)
        + "`. Bounded state: 61 OHLC feature bars, 31 pending bars, ≤1,024 events per transaction, ≤64 day records, ≤100 fixed groups; no complete dataset, prediction history or equity curve is kept in RAM.",
        "",
        "## 10. Checkpoint, resume and independent verification",
        "",
        f"Committed H files matching independent replay: {result.get('matching_files', 'NOT RUN')}; generation {result.get('generation', 'NOT RUN')}; rows {result.get('rows', 'NOT RUN')}; diagnostic events {result.get('events', 'NOT RUN')}. Canonical state hash: `{result.get('logical_state_hash', 'NOT RUN')}`.",
        "",
        "Atomic fsync + directory rename publishes state and events together with chained receipts. Hard exit 86 before generation 35 commit must leave generation 34 and preserved staging. Resume must keep all committed prefix bytes, repeated resume must be unchanged, independent uninterrupted replay must match every committed hash. The separate verifier streams source OHLC/features again, recomputes paths/returns/labels and matrix probabilities, joins every February observation to frozen G events, and reconciles class metrics, confidence bins and threshold counts/sums. Code/environment/protocol/source/model identity changes reject resume. No old checkpoint migration is needed.",
        "",
        "Evidence: `"
        + result["evidence"]
        + "`; `resumed-hashes.json`, `reference-hashes.json`, `independent-verification.json`, per-process monitors, failure records and summary. Prior C/D/E/F/G journals/reports and original research configuration are compared before/after admitted execution.",
        "",
        "## 11. Tests and implementation",
        "",
    ]
    testpath = ROOT / ".tmp/strat2h-tests-v1.xml"
    if testpath.exists():
        suites = ET.parse(testpath).getroot()
        suites = [suites] if suites.tag == "testsuite" else list(suites)
        totals = {
            k: sum(int(s.get(k, 0)) for s in suites)
            for k in ("tests", "failures", "errors", "skipped")
        }
        lines += [
            "Synthetic/regression pytest results: `"
            + json.dumps(totals)
            + "`. XML `.tmp/strat2h-tests-v1.xml`. Ruff lint/format are executed separately and recorded in the final report amendment.",
            "",
        ]
    lines += [
        "New code: `src/trading/research/strat2h/{pipeline,statistics,analysis,report}.py`, `tools/strat2h_validation.py`, `tests/test_strat2h.py`; new diagnostic, confirmation and calendar JSON protocols. No existing G pipeline or evidence was changed. No dependencies added.",
        "",
        "Tests cover frozen identity, future-data invariance, units/costs, bins, sessions/DST/gaps, purge/embargo, dependent resampling, numerical summaries, corruption, interrupted/repeated resume and immutable replay. Existing G model/resource and D/E execution tests remain applicable; synthetic observations are distinct from historical evidence.",
        "",
        "## 12. Future independent confirmation protocol",
        "",
        "`config/strat2h-confirmation-protocol-v1.json` freezes the same candidate/artifacts and proposes March–May 2022 solely as a design: immediate chronological follow-on over three months with a DST transition. No data from that period was opened, retrieved or evaluated. Its untouched status must first be audited; later explicit user authorization is required. The current Jan–Feb resource profile cannot authorize those dates.",
        "",
        "One scheduled evaluation; no refitting/recalibration, threshold changes, candidate switching, optional stopping or outcome-driven extension. Minimum 20,000 eligible labels, 40 observed days, 1,000 per actual class. Compare frozen reference and training prior plus simple descriptive benchmarks. Paired 2/5-day blocks with 5,000 replicates must show jointly lower log loss and higher balanced accuracy versus both primary benchmarks. Economic evidence additionally needs ≥200 unchanged-gate signals on ≥20 days and ≥100 nonoverlap windows, verified calendar/price/execution semantics and positive lower net-movement bounds at base and 3.1bps costs. Insufficient counts mean INCONCLUSIVE; adverse evidence can reject. No-signal outcomes never justify lowering the gate. The protocol remains valid if H disappoints, and never authorizes profitability claims or automatic execution.",
        "",
        "## 13. Assessments and blockers",
        "",
        table(
            ["Subsystem", "Assessment"],
            [(k, v) for k, v in sorted(result["checks"].items())]
            + [
                ("historical calendar/price-side attestation", "UNVERIFIED / BLOCKED"),
                ("independent confirmation design", "PASS — design only"),
                ("economic/predictive confirmation", "INCONCLUSIVE"),
            ],
        ),
        "",
        "Missing checks are NOT RUN. Calendar attestation, independently authorized unexamined observations and adequate signal/day counts remain blockers. No production readiness or profitability is established.",
        "",
        "## 14. Recommended next phase",
        "",
        "STRAT-2I: documentation-based source calendar/price-side attestation and confirmation preregistration audit. Resolve those gates before requesting explicit access to a genuinely unexamined confirmation period. Do not add models, tune the 0.55 gate or promote the selected candidate based on these exploratory diagnostics.",
        "",
        "Reproducible H invocation (fresh output version required):",
        "",
        "```powershell",
        "$env:OMP_NUM_THREADS='1'; $env:OPENBLAS_NUM_THREADS='1'; $env:MKL_NUM_THREADS='1'; $env:NUMEXPR_NUM_THREADS='1'",
        ".\\.venv\\Scripts\\python.exe -B -m tools.strat2h_validation --resource-profile "
        + ("100" if resource["minimum_available_ram_gib"] == 1.0 else "120")
        + " --evidence-version "
        + str(int(audit.name.rsplit("-v", 1)[1]) + 1),
        "```",
        "",
        "No commits or pushes. No broker, Docker, collectors, source writes, holdout access or annual backtest.",
        "",
    ]
    text = "\n".join(lines)
    path = ROOT / "reports/strat-2h-signal-quality-independent-validation-v1.md"
    if path.exists():
        with (audit / "report.md").open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    else:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
