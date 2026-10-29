"""Versioned findings with explicit distinctions between engineering and evidence."""

# Paragraph templates retain complete prose.
# ruff: noqa: E501
import json

from trading.research.safety import ROOT


def table(headers, rows):
    def cell(v):
        return "unavailable" if v is None else f"{v:.6f}" if isinstance(v, float) else str(v)

    return "\n".join(
        [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
            *("| " + " | ".join(cell(v) for v in row) + " |" for row in rows),
        ]
    )


def render(result, audit):
    protocol = json.loads(
        (ROOT / "config/strat2i-data-quality-economic-research-v1.json").read_text("utf-8")
    )
    a = (
        json.loads((audit / "analysis.json").read_text())
        if (audit / "analysis.json").exists()
        else None
    )
    monitors = [json.loads(p.read_text()) for p in audit.glob("*-parent-monitor.json")]
    metrics = [result["metrics"], *(v["metrics"] for v in monitors)]
    metrics += [json.loads(p.read_text())["metrics"] for p in audit.glob("*-result.json")]
    available = [
        v["minimum_available_ram_gib"]
        for v in metrics
        if v.get("minimum_available_ram_gib") is not None
    ]
    disk = [
        v["minimum_free_disk_gib"] for v in metrics if v.get("minimum_free_disk_gib") is not None
    ]
    peak = max((v["worker_peak_rss_bytes"] / 2**20 for v in monitors), default=0) or None
    classification = a["classification"] if a else "INCONCLUSIVE"
    lines = [
        "# STRAT-2I data integrity and economic signal research v1",
        "",
        "## 1. Executive summary",
        "",
        f"Engineering **{result['status']}**. Research **{classification} — important data, calendar or economic questions remain unresolved**.",
        "",
        "Only EURUSD M1 January–February 2022 was accessed. This period has been repeatedly examined; none of these results is independent out-of-sample confirmation. No training, new model, threshold change, trade execution or target selection occurred. All previous evidence is immutable.",
        "",
    ]
    if result.get("failure"):
        lines += [
            "Execution stopped safely. New historical diagnostics are incomplete; no missing results are inferred from old evidence.",
            "",
            "```json",
            json.dumps(result, indent=2),
            "```",
            "",
        ]
    lines += [
        "## 2. Source integrity",
        "",
        "Read-only, checksum-whitelisted Phase 5 junction files `cache/EURUSD/2022-01.parquet` and `2022-02.parquet` were audited directly, then compared to the sealed STRAT-2C stream. Source timestamps require explicit UTC and minute alignment. Strict ordering makes adjacent duplicate detection sufficient: any reversal, exact duplicate or conflicting duplicate stops rather than sorting/deduplicating. OHLC finite/positive/ordering checks are fatal; precision and 20bps open-versus-previous-close jumps are warnings. Tick volume is audited but not used as reliable traded volume.",
        "",
    ]
    if a:
        q = a["quality"]
        lines += [
            table(
                ["Check", "Result"],
                [
                    (k, q[k])
                    for k in (
                        "rows",
                        "duplicates",
                        "conflicts",
                        "out_of_order",
                        "invalid_ohlc",
                        "precision_warnings",
                        "zero_volume",
                        "invalid_volume",
                        "jumps_contiguous",
                        "jumps_across_gap",
                        "max_jump_bps",
                        "maximum_spacing_minutes",
                        "max_segment",
                        "dst_offsets",
                    )
                ],
            ),
            "",
            "A jump following a closure is a multi-minute move, not a one-minute return. Warnings do not establish a bad quote. The absence of detected defects does not attest original collection completeness or historical bid/ask meaning.",
            "",
            "## 3. Gap/missing reconciliation",
            "",
            table(
                ["Category", "Minutes"],
                [
                    ("internal", q["internal_minutes"]),
                    ("leading/trailing window edges", q["edge_minutes"]),
                    ("full-window absent", q["internal_minutes"] + q["edge_minutes"]),
                ],
            ),
            "",
            table(
                ["Rule category", "Internal absent", "Window-edge absent", "Total absent"],
                [
                    (
                        k,
                        q["internal_missing"].get(k, 0),
                        q["edge_missing"].get(k, 0),
                        q["internal_missing"].get(k, 0) + q["edge_missing"].get(k, 0),
                    )
                    for k in sorted(set(q["internal_missing"]) | set(q["edge_missing"]))
                ],
            ),
            "",
            f"There are {q['internal_gaps']} internal gap intervals; {q['gaps_touching_open']} contain at least one expected-open minute. Interval categories overlap when a gap spans more than one calendar classification; minute categories are disjoint. The {q['outside']} observed outside-session rows are observations, not absent minutes; never add them to missing counts. All classifications depend on the unverified frozen calendar. A missing expected-open record may reflect collection failure, no observed tick, or incorrect historical hours; this audit cannot distinguish those causes.",
            "",
            "Counterfactual raw decision windows interrupted by an internal gap, by horizon: `"
            + json.dumps(q["gap_label_blocks"])
            + "`. These count the last min(horizon, segment length) observed decision bars before each gap; they are not final model-eligible counts and exclude window-end censoring.",
            "",
            "Daily session coverage (UTC days, not an attested broker trading day) is recorded below. Short Friday/Sunday days are not automatically irregular collection. Partial expected sessions are hypothesis-based flags.",
            "",
            table(
                [
                    "UTC day",
                    "Observed open",
                    "Missing expected open",
                    "Observed outside",
                    "Absent weekend",
                    "Absent session break",
                ],
                [
                    (
                        day,
                        v.get("observed_open", 0),
                        v.get("missing_open", 0),
                        v.get("observed_outside", 0),
                        v.get("missing_weekend", 0),
                        v.get("missing_session_break", 0),
                    )
                    for day, v in sorted(q["days"].items())
                ],
            ),
            "",
        ]
    else:
        lines += [
            "## 3. Gap/missing reconciliation",
            "",
            "NOT RUN to completion. Frozen earlier counts remain context only: 301 internal gaps, 23,516 absent internal minutes, 378 expected-open absent minutes, 16 observed outside-session rows.",
            "",
        ]
    lines += [
        "## 4. Historical calendar and timestamp evidence",
        "",
        "**UNVERIFIED historical source-broker/account rules.** Frozen metadata identifies EURUSD/cTrader demo-source semantics but does not attest the actual broker/account or a dated 2022 schedule. The broker cannot responsibly be inferred from symbolId, commission, platform or generic FX hours. We did not connect to a broker or read credentials.",
        "",
        "[Spotware Open API model messages](https://help.ctrader.com/open-api/model-messages/) specify Unix-minute opening-tick timestamps and symbol-timezone weekly intervals with inclusive starts/exclusive ends. The local parser converts explicitly to UTC. Stored semantic lineage is supported by code and metadata, not an independent source-account attestation. [NIST DST rules](https://www.nist.gov/pml/time-and-frequency-division/popular-links/daylight-saving-time-dst) imply New York UTC−5 throughout these two months; there is no in-window DST transition. Broker-server clocks need not be UTC. A [December 2021 broker notice](https://www.ic.com/blog/holiday-trading-schedule-dec-2021-jan-2022/) uses GMT+2 and warns of holiday pricing interruptions, but cannot establish this unidentified account's FX schedule. No holiday hours are invented from it.",
        "",
        "## 5. Calendar corrections and feature continuity",
        "",
        "No correction is justified by account-specific evidence, so none is proposed or applied. The 16 21:59 UTC / 16:59 New York boundary candles remain preserved and flagged. Their validity and the 378 hypothesized missing open minutes remain unresolved. The versioned I evidence registry retains the frozen calendar fingerprint and supporting/unsupported claims. Any later correction requires dated account/symbol rules, explicit affected timestamps, separate rule version and preparation lineage, boundary/DST regression tests and eligible-observation comparison before metrics. Extending a session merely to absorb observed candles would be circular.",
        "",
        "The prepared-feature audit checks finite model predictors during the first 63 observed open bars after any gap or excluded-session reset. H's independent source verifier additionally recreates its 61-bar causal features and contiguous 30-minute label paths. This addresses features spanning discontinuities without changing existing artifacts.",
        "",
        "## 6. Frozen model economic diagnostics",
        "",
        "The I audit consumes the exact frozen H events, not newly fitted predictions. January scores are post-fit descriptive reuse. February selection and final slices are also previously examined development data. A forced direction uses the larger LONG/SHORT probability even when FLAT dominates; it is an opportunity diagnostic, not an order. Threshold groups additionally require direction≥FLAT. Favorable/adverse extrema describe observed paths, not attainable fills.",
        "",
    ]
    if a:
        groups = a["groups"]
        e = groups["phase:evaluation"]
        lines += [
            table(
                [
                    "Population",
                    "n",
                    "Gross bps",
                    "Gross pips",
                    "Favorable pips",
                    "Adverse pips",
                    "Scaled move",
                    "Base net bps",
                    "Base cost coverage",
                ],
                [
                    (
                        k,
                        v["n"],
                        v["stats"]["gross_bps"]["mean"],
                        v["stats"]["gross_pips"]["mean"],
                        v["stats"]["favorable_pips"]["mean"],
                        v["stats"]["adverse_pips"]["mean"],
                        v["stats"]["scaled"]["mean"],
                        v["costs"]["2.1"]["net_bps"]["mean"],
                        v["costs"]["2.1"]["coverage_fraction"],
                    )
                    for k, v in groups.items()
                    if k.startswith(
                        (
                            "phase:",
                            "prediction:evaluation:",
                            "threshold:evaluation:",
                            "confidence:evaluation:",
                        )
                    )
                ],
            ),
            "",
            "The confidence groups are directional maximum probability bins [index/20,(index+1)/20). Predicted-class groups identify FLAT/LONG/SHORT argmax; all price outcomes are signed by the diagnostic forced direction. Full pip histograms and extrema appear in `analysis.json` with fixed protocol edges.",
            "",
            "No 0.55 group is present when there are zero eligible signals; this is a zero count, not a missing calculation. No realized trades or portfolio P&L were generated in I. H's unchanged zero-fill result is not replaced by these counterfactuals.",
            "",
        ]
    lines += [
        "## 7. Costs, units and conditional execution assumptions",
        "",
        "EURUSD quote units are USD/EUR. One pip=0.0001 and pipette=0.00001. Gross pips=side×(exit−entry)/0.0001. For quantity Q in EUR units and a USD account, cash P&L=side×Q×price difference minus USD fees; a 100,000 EUR lot has USD10 per pip. API volume and lotSize are in cents of base units, not directly lots. Other account currencies require verified causal FX conversion and are unsupported here.",
        "",
        "Snapshot commissionType=2 in **Open API** means USD per lot, not the differently numbered Algo enumeration. Stored preciseTradingCommissionRate300000000 / 10^8 implies USD3 per lot if applicable; the legacy commission300 scale is not independently inferred. Assuming a per-side fee, lotSize10,000,000 cents=100,000EUR. At reference price P, that would imply roundtrip fee 6/(100000×P)×10000=0.6/P bps, rather than constant 0.6bps. Historical applicability, fee side, minimums and price-side evidence remain unverified. [Open API commission/volume fields](https://help.ctrader.com/open-api/model-messages/) support these unit distinctions. We retain the established 0.6bps reference scenario; we do not silently replace it with a purported historical fee.",
        "",
        "For each conditional reference-mid scenario, entry fill=Pentry×[1+side×(spread/2+slip)/10000], exit fill=Pexit×[1−side×(spread/2+slip)/10000], fees per EUR=(entry fill+exit fill)×roundtrip commission/20000. Exact cash-flow accounting includes changing exit notional; H's simple gross-minus-constant-bps approximation is reported only as prior context. These are not observed historical bid/ask fills. Do not add spread twice to observed bid/ask quotes in a future implementation.",
        "",
        table(
            [
                "Scenario total bps",
                "Spread bps",
                "Slippage per side bps",
                "Roundtrip fee bps",
                "Flat-price break-even gross bps",
            ],
            [
                (
                    v["total_bps"],
                    v["spread_bps"],
                    v["slippage_per_side_bps"],
                    v["commission_roundtrip_bps"],
                    v["total_bps"],
                )
                for v in protocol["costs"]["scenarios"]
            ],
        ),
        "",
        "Break-even movement in pips at reference P is approximately cost_bps×P, since a basis point is P/10000 price units; the exact net equation above is used for observed-price diagnostics. With exact fills the required future exit for side d is P×(1+d×a)×(d+c)/(d−c)/(1−d×a), a=(spread/2+slip)/10000 and c=fee/20000. Expected signed gross cash must exceed expected costs; classification accuracy alone cannot establish that.",
        "",
    ]
    if a:
        lines += [
            table(
                ["Cost bps", "Mean net bps", "Mean net pips", "Mean cost pips", "Cost coverage"],
                [
                    (
                        k,
                        v["net_bps"]["mean"],
                        v["net_pips"]["mean"],
                        v["cost_pips"]["mean"],
                        v["coverage_fraction"],
                    )
                    for k, v in e["costs"].items()
                ],
            ),
            "",
        ]
    lines += [
        "## 8. Future cost-aware target design",
        "",
        "Exactly three theory-driven targets were specified before I execution, all horizon 15 minutes. No new model is trained and no target winner is selected. Their class balances are descriptive availability checks, not a search for a favorable result.",
        "",
        table(
            ["Target", "Horizon", "Base neutral cost bps", "Causal volatility multiplier"],
            [
                (t["id"], t["horizon_minutes"], t["cost_bps"], t["volatility_multiple"])
                for t in protocol["targets"]
            ],
        ),
        "",
        "Formula: g=10,000×(close[t+15]/open[t+1]−1); z=max(cost_bps,k×max(causal sigma20[t],1e−6)×sqrt(15)×10,000). LONG when g>z, SHORT when g<−z, FLAT otherwise. Use no future price in features. Require observed contiguous future path, conservative session buffers and the same common 30-minute admission as H. Purge common label end strictly before split right, embargo 60 minutes after nontraining split left. No unavailable label is fabricated. Current unknown exceptional sessions still limit admission confidence.",
        "",
    ]
    if a:
        lines += [
            table(["Target", "Final development FLAT/LONG/SHORT"], e["target_counts"].items()),
            "",
        ]
    lines += [
        "## 9. Temporal and dependent uncertainty",
        "",
        "Whole observed UTC days are resampled in circular blocks of 2 and 5 days, 1,000 replicates, fixed seed. Minute labels and their overlapping 15-minute paths stay together. This handles some serial dependence but not structural changes or unknown holidays; observed-day blocks can bridge weekends. Approximate count floor(days/block length) is a coarse block-count proxy, not a measured effective sample size. Threshold intervals require ten observed days and five signal-bearing days. Sparse cells receive no interval. No IID minute-based standard errors.",
        "",
    ]
    if a:
        lines += [
            "```json",
            json.dumps(a["uncertainty"], indent=2),
            "```",
            "",
            table(
                [
                    "Subdivision",
                    "n",
                    "Gross pips",
                    "Base net bps",
                    "Coverage",
                    "Clusters",
                    "Nonoverlap count",
                ],
                [
                    (
                        k,
                        v["n"],
                        v["stats"]["gross_pips"]["mean"],
                        v["costs"]["2.1"]["net_bps"]["mean"],
                        v["costs"]["2.1"]["coverage_fraction"],
                        v["clusters"],
                        v["nonoverlap"],
                    )
                    for k, v in groups.items()
                    if k.startswith(
                        ("month:", "week:", "session:evaluation:", "volatility:evaluation:")
                    )
                ],
            ),
            "",
        ]
    lines += [
        "## 10. Selection-bias limitations",
        "",
        "Nine G configurations were considered and this period was repeatedly examined through H. Small balanced-accuracy improvements do not prove economic advantage. Confidence tables and target balances cannot be used to change the selected candidate or production gate. Overlapping opportunities, unknown price side/fees and no portfolio accounting prevent conversion of descriptive net movements into realized profitability.",
        "",
        "## 11. Resources and execution",
        "",
        f"Unchanged H profile: 1.00 GiB minimum available physical RAM, 256 MiB worker RSS, 15 GiB disk, one thread, batch 1,024. Peak externally sampled worker RSS: {peak} MiB; minimum sampled available RAM {min(available) if available else None} GiB; minimum disk {min(disk) if disk else None} GiB. Continuous parent sampling plus bounded worker checks fail closed. Dedicated I paths retain the required H resource-prefix namespace. General 1.50 and prior resource policies are untouched.",
        "",
        "State is bounded: one previous raw row, ≤64 day records, ≤256 fixed economic groups, 1,024-row Arrow batches, source/H journal metadata. No full historical DataFrame, prediction list or equity curve is retained. Numeric verification uses a second bounded pass; source is never an output destination.",
        "",
        "## 12. Checkpoint/replay and numerical verification",
        "",
        f"Rows audited {result.get('rows', 'NOT RUN')}; frozen prediction events {result.get('events', 'NOT RUN')}; final generation {result.get('generation', 'NOT RUN')}; matching committed replay files {result.get('matching_files', 'NOT RUN')}; state hash `{result.get('logical_state_hash', 'NOT RUN')}`.",
        "",
        "Two hard exits test before raw generation35 publication and after economic generation140 publication. Resume preserves both committed prefixes; repeated resume changes nothing. Each transaction atomically commits cursor/aggregate state, chained receipt and evidence events. Independent reference run compares every committed file. Independent verification zips original raw and prepared rows, recomputes grid omissions, checks OHLC, reruns H price/feature/probability verification, and independently recalculates exact cash costs, pips, target classes and counts. No model checkpoint is migrated or rewritten.",
        "",
        "## 13. Automated tests and implementation",
        "",
        "Synthetic tests exercise timestamps, duplicate conflicts, OHLC, calendar/DST/boundaries, gap-label exclusions, price/pip/quantity/fee units, targets, causal features, serial blocks, bounded state, journal corruption and before/after-commit resume/replay. Exact test/static results and any failures are recorded in the final review below. New implementation: `src/trading/research/strat2i`, `tools/strat2i_validation.py`, `tests/test_strat2i.py`; no dependencies or previous research code changed.",
        "",
        "## 14. Assessments",
        "",
        table(
            ["Subsystem", "Status"],
            list(sorted(result["checks"].items()))
            + [
                ("historical broker calendar and fees", "UNVERIFIED"),
                ("economic usefulness at unchanged gate", "INCONCLUSIVE"),
                ("target design and confirmation review", "PASS — design only"),
            ],
        ),
        "",
        "## 15. Independent-confirmation readiness",
        "",
        "Not ready to execute independent confirmation. `config/strat2i-confirmation-protocol-proposal-v2.json` is a separate proposal; H's frozen protocol is unchanged. It clarifies exactly 12 selected features (not the broader interaction policy), fee/price-side attestation, exceptional-session exclusions, whole-week sensitivity, ≥12 weekly clusters for economic evidence and no substitution of I targets into confirmation. Candidate/model/0.55 stay frozen. Existing proposed future dates remain design only; untouched provenance must be audited and later explicit authorization is required. No such data was opened or retrieved here.",
        "",
        "## 16. Remaining blockers and next steps",
        "",
        "Obtain dated source-account/symbol session and holiday rules with timestamp/price-side provenance; verify historical bid/ask/fee/FX assumptions or retain conditional costs and blocked profitability claims. Require adequate signals at the unchanged gate and dependence-aware economic evidence. Freeze and preregister one independent test before any later authorized new-period access. If these prerequisites remain absent, defer confirmation; do not manufacture trades or select a target from these results. Recommended STRAT-2J: provenance and cost attestation plus prospective-study design review, not model promotion.",
        "",
        "No annual backtest, holdout access, new historical period, broker/live/paper connection, Docker, source modification, commit or push.",
        "",
    ]
    path = ROOT / "reports/strat-2i-data-integrity-economic-signal-v1.md"
    destination = audit / "report.md" if path.exists() else path
    with destination.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))
