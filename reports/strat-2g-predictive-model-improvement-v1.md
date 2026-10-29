# STRAT-2G predictive model improvement v1

**INCONCLUSIVE — insufficient evidence to establish reliable improvement**

## 1. Executive summary

Engineering execution: **PASS**. Research is exploratory on the repeatedly examined January-February 2022 EURUSD period. No independent out-of-sample proof, profitability claim or production promotion is supported. Nine model/target configurations were predefined, not chosen from favorable historical outcomes.

## 1.20 GiB execution completion and final interpretation

**Engineering execution: PASS. Research outcome: INCONCLUSIVE.** All nine predefined configurations completed under the newly authorized, explicitly opt-in profile. No experiment definition, split, selection rule, feature/model parameter or production confidence gate was changed.

### Exact operational profile and scope

New config: `config/strat2g-eurusd-2022-offline-120-v1.json`, profile ID `strat2g-eurusd-2022-offline-120-v1`. It requires **1.20 GiB system-available physical RAM, maximum 256 MiB worker RSS, minimum15 GiB free disk, one numerical/Arrow thread**, and the exact EURUSD M1 January1-March1,2022 exclusive source. Its purpose and protocol checksum bind it to STRAT-2G. Every other scientific/source field must equal the previous approved config. It cannot authorize another dataset, scope, estimator, production workflow or weaker RSS/disk limit.

The shared/general **1.50 GiB** floor and previous **1.30 GiB** profiles are unchanged. The original1.30 config's full hash was checked against its saved earlier audit. The frozen experiment protocol still contains its original resource-profile reference, because the protocol file was deliberately not edited: the separately recorded **user-authorized operational override** is1.20 only. Frozen protocol SHA-256 remains `c0514b40d6802b9ed14293852882ca5e8213fdb377a588000786eee591c35dcb`. New resource-config SHA-256 is `c74102566d91cf6ce40c604245eaa23cf12927d90d89c6ce0ce9535070b6996f`.

Actual command:

```powershell
.\.venv\Scripts\python.exe -B -m tools.strat2g_validation --resource-profile 120 --evidence-version 2
```

Startup admission measured **1.789074 GiB available RAM** and **26.971851 GiB free disk**. Both parent admission/runtime monitoring and every child used the same new120 profile. Native RAM, OS peak RSS, disk and wall-time checks were retained; the parent continued polling the real worker PID, not the venv launcher. New tests deliberately crossed the1.20 floor and256 MiB RSS cap and confirmed fail-closed stops with measurements retained. No safety monitor was disabled and no service/process was terminated to free memory.

### Actual historical data and decisions

**58,683 raw rows** were streamed in bounded1,024-row batches. All targets/models share **11,675 training**, **13,428 calibration**, **12,075 selection** and **12,304 final exploratory evaluation** decisions. The final slice contains **12 observed UTC days**. The common30-minute maturity/purge policy and60-minute embargo remain in force. Buffers reached at most **61 feature bars and31 pending rows**. The final30 unresolved rows are explicitly unavailable; no terminal label was fabricated.

Availability counters reconcile to the source:2,656 session-buffer exclusions +2,712 pending rows invalidated by gaps/boundaries +3,681 predictor-warmup rows +11,675 train +13,428 calibration +12,075 selection +12,304 evaluation +119 outside-window +3 purged +30 terminal unavailable =58,683. Reasons are engineering-policy categories, not verified historical session classifications. All target class counts and all completed candidate/raw/calibrated/baseline metrics are presented in the detailed sections below.

Selection was frozen before final outcomes: **linear12** for h5_cost and h15_cost; **reference8** for h30_vol. The interaction model is not substituted after observing final metrics. Every experiment remains exploratory because these January-February dates have already been examined repeatedly.

### Selected candidates on the final exploratory slice

| Target | Selected family | N | Selected BA | Reference BA | Prior BA | Selected log loss | Reference log loss | Prior log loss |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| h5_cost | linear12 | 12304 | 0.333333 | 0.333333 | 0.333333 | 0.932829 | 0.939158 | 0.977239 |
| h15_cost | linear12 | 12304 | 0.350703 | 0.336043 | 0.333333 | 1.084392 | 1.085373 | 1.110409 |
| h30_vol | reference8 | 12304 | 0.332820 | 0.332820 | 0.333333 | 1.079029 | 1.079029 | 1.077677 |

The15-minute model improves balanced accuracy by **0.014660** over the original8-feature reference. Its paired2-day-block95% interval is **[+0.010648,+0.019354]** (5-day sensitivity **[+0.010690,+0.018460]**). This is a measured exploratory discrimination gain under the chosen resampling method. However, selected-minus-reference log-loss difference is only about **-0.000982**, with2-day interval **[-0.024298,+0.024833]** and5-day interval **[-0.026181,+0.026548]**. Hence the protocol's joint loss-and-balanced-accuracy improvement requirement is **not met**.

For h5_cost, selected and reference balanced accuracy remain one-third, despite better probability scores versus training priors; the loss advantage over the reference remains uncertain. For h30_vol, selection retains the reference, so no alternative-model improvement is established. The modest h15 discrimination result should not be dismissed, but twelve days, multiple exploratory comparisons, unresolved probability-score superiority and unverified calendar/price provenance do not establish reliable general improvement. This is why the final conclusion is **INCONCLUSIVE**, rather than a forced PASS or a claim of no predictive information.

### Signals, confidence and cost coverage

Every candidate produced **zero directional signals** at the unchanged0.55 gate on the common final evaluation sample. Counts are descriptive predictions, not orders; no paper/live/portfolio execution was initiated. Actual directional-confidence maxima:

| Target / calibrated candidate | Max long probability | Max short probability | Directional probability >=0.55 |
| --- | ---: | ---: | ---: |
| h15_cost/interaction16_cal | 0.396843 | 0.401594 | 0 |
| h15_cost/linear12_cal | 0.412021 | 0.382596 | 0 |
| h15_cost/reference8_cal | 0.498058 | 0.418439 | 0 |
| h30_vol/interaction16_cal | 0.321151 | 0.478818 | 0 |
| h30_vol/linear12_cal | 0.327807 | 0.409312 | 0 |
| h30_vol/reference8_cal | 0.329273 | 0.431100 | 0 |
| h5_cost/interaction16_cal | 0.297029 | 0.445033 | 0 |
| h5_cost/linear12_cal | 0.308324 | 0.416059 | 0 |
| h5_cost/reference8_cal | 0.328064 | 0.356913 | 0 |

Unconditional forward movement magnitudes on the same final rows:

| Target | Mean absolute movement, bps | Fraction whose absolute move does not exceed2.1 bps cost |
| --- | ---: | ---: |
| h15_cost | 4.0821 | 38.95% |
| h30_vol | 5.4967 | 29.22% |
| h5_cost | 2.3559 | 59.77% |

Costs are material, especially for the5-minute horizon: approximately59.77% of observed absolute moves do not exceed the reference round-trip cost. **Absolute future movement is not a forecast edge**: the direction is unknown at prediction time. Since no candidate meets the fixed gate, there are no model-selected gross/net means, turnover or conditional cost sensitivities to estimate; these stay undefined, not zero profit. The per-candidate tables below retain those NA outcomes. No threshold relaxation or favorable target selection was used to manufacture trades.

### Resource, integrity and recovery result

Peak worker RSS: **98.47 MiB**. Minimum sampled system-available RAM across execution and verification: **1.433155 GiB**. Minimum free disk: **26.815910 GiB**. All limits held; no resource, checksum, corruption or unexpected execution failure occurred. Actual headroom never approached1.20 GiB in this run, so these measurements do not establish reliability at exactly the floor under arbitrary Windows background pressure.

The intentional `os._exit(86)` happened before commit35 after staging; committed generation34 was verified and preserved. A fresh worker resumed to **generation59,60 committed transactions, complete=true**. The completed repeat changed no committed byte. An independent uninterrupted run matched **181/181 committed file hashes**, including manifest, all states, events and receipts, and canonical logical state hash `1d57fc72db1726189cc47ca001d0a107fa04154864136b189c6f67055f623d3b`.

The independent reader verified **73,137 prediction events and60 metric groups** across the selection/final slices. It recomputed forward returns from entry/exit prices, neutral-zone/label definitions, horizons/maturity, chronological splits, normalized probabilities, confusion/loss/Brier totals and model choices using selection data only. No final target influenced model choice or calibration. Original evidence remains unchanged: **47 STRAT-2C hashes/mtimes,721 STRAT-2D hashes,181 STRAT-2E hashes,181 STRAT-2F hashes**.

### Tests, implementation delta and evidence

**53 focused tests passed**,13 known dependency warnings,7.89 seconds:20 new resource-policy/parent-monitor tests,14 STRAT-2G matrix tests and19 previous1.30-policy tests. New tests check opt-in isolation, scope/protocol misuse rejection, actual1.20 runtime comparisons, RSS rejection, parent monitor factory, original1.30 admission and general1.50 defaults. Ruff lint/format and git diff checks pass. Prior synthetic broad suites remain preserved and are not represented as newly rerun tests.

Changes for this execution task: new STRAT-2G-only `resources.py` and opt-in JSON; the G pipeline/runner route through that policy; the reusable launcher accepts an optional monitor factory while preserving its legacy default; report resource text reflects the actual profile; new resource-policy tests. Core STRAT-2D/F numerical/safety implementations, existing configs and frozen G protocol are unchanged. No prior checkpoint was migrated or rewritten.

New outputs: `data/research/strat2d-strat2g-resumed-v2`, `...-reference-v2`, and `...-evidence-v2`. Evidence includes the exact resource config/protocol snapshots, summaries, selection/calibration metrics, uncertainty, parent/worker resource logs, prefix/resumed/reference hashes, independent verification, forward-movement/confidence diagnostics, test XML and final assessment. The previous blocked report is archived as `report-before-120-execution.md`; version1 failure evidence is preserved. Only the requested canonical report is updated.

Historical calendar/holiday uncertainty, unknown historical price/quote assumptions, repeated use of this small period, and no independent confirmation remain limitations. Do not promote a candidate. The next phase should pre-register a simple candidate/calibration comparison after resolving provenance, on a newly authorized development window; no date expansion is authorized here. No2026 holdout access, annual backtest, broker connection, live/paper trading, Docker/service startup, source modification, commit or push occurred.

---

## 2. Exploratory research protocol

The versioned protocol is `config/strat2g-protocol-v1.json`, written before implementing/running experiments and snapshotted in the evidence directory. The experiment evaluates three targets independently, each with three trainable families and four fixed baselines. It never ranks target definitions by their final scores. Model selection uses calibrated log loss on the selection slice only: start with the reference, prefer a more complex family only if it improves selection loss by more than 0.005. Minimum selection evidence is 1,000 rows and five observed days. Otherwise no candidate is selected. Parameters, target costs/horizons, feature transforms and seeds were fixed before historical execution.

All candidates are frozen after January calibration; selection changes no fitted weights or temperatures. Choices are frozen at the first final-evaluation observation and independently recomputed from selection-only metrics. Every model is reported for transparency, but only the previously selected model may be the subject of a final selected-model comparison. Repeated scrutiny of these dates means the final slice remains exploratory.

## 3. Dataset and chronological splits

The only historical input is the sealed 58,683-row EURUSD M1 preparation, job 1d06fff895ee69a12b38, January 1-March 1, 2022 exclusive. The existing resource configuration is reused without modification. There is no other historical period and no 2026 holdout.

| Role | UTC bounds, right-exclusive |
| --- | --- |
| train | ['2022-01-01T00:00:00Z', '2022-01-15T00:00:00Z'] |
| calibration | ['2022-01-15T00:00:00Z', '2022-02-01T00:00:00Z'] |
| selection | ['2022-02-01T00:00:00Z', '2022-02-15T00:00:00Z'] |
| evaluation | ['2022-02-15T00:00:00Z', '2022-03-01T00:00:00Z'] |

Calibration, selection and evaluation each embargo their first 60 minutes. Every sample requires all 30 subsequent bars contiguous and safely admitted, and the common latest information-use time must be strictly before its split end. This deliberately over-purges the 5/15-minute targets to ensure identical eligible timestamps across targets/models. Labels mature after the shared 30-minute delay; no fitted update occurs before that information is observed.

## 4. Target definitions and class balance

Entry reference is the next bar open; exit is the close of the h-th subsequent bar. Gross return is exit/entry-1. LONG/SHORT requires gross return beyond the positive/negative neutral zone; otherwise FLAT. Labels are deterministic. Prices remain EURUSD price units, costs/returns are basis points of notional, not pips. A 2.1 bps cost on a 1.10 entry is 0.000231 price units; it is not 2.1 pips. There is no barrier-stop label in this new matrix.

| Target | h (minutes) | Neutral zone (bps) |
| --- | --- | --- |
| h5_cost | 5 | max(2.1, 0.0 * causal sigma20 * sqrt(h) * 10000) |
| h15_cost | 15 | max(2.1, 0.0 * causal sigma20 * sqrt(h) * 10000) |
| h30_vol | 30 | max(2.1, 0.5 * causal sigma20 * sqrt(h) * 10000) |

| Target | Window | N | FLAT/LONG/SHORT |
| --- | --- | --- | --- |
| h15_cost | train | 11675 | [5670, 3008, 2997] |
| h15_cost | calibration | 13428 | [6730, 3184, 3514] |
| h15_cost | selection | 12075 | [5234, 3471, 3370] |
| h15_cost | evaluation | 12304 | [4792, 3741, 3771] |
| h30_vol | train | 11675 | [5455, 3097, 3123] |
| h30_vol | calibration | 13428 | [6303, 3268, 3857] |
| h30_vol | selection | 12075 | [5252, 3486, 3337] |
| h30_vol | evaluation | 12304 | [5369, 3528, 3407] |
| h5_cost | train | 11675 | [8193, 1720, 1762] |
| h5_cost | calibration | 13428 | [9652, 1816, 1960] |
| h5_cost | selection | 12075 | [7947, 2027, 2101] |
| h5_cost | evaluation | 12304 | [7354, 2443, 2507] |

Input/path availability counters: `{"calibration": 13428, "common_path_predictor_warmup": 3681, "evaluation": 12304, "outside": 119, "purged": 3, "selection": 12075, "session_buffer_excluded": 2656, "train": 11675, "unobservable_pending_rows": 2712}`; raw input rows 58683; terminal unavailable pending rows 30. These counters describe common-path eligibility, not an assertion that every shorter target was unavailable when a longer path failed. All models within a target and all targets share each admitted decision timestamp.

Boundary policy: reset features/pending labels at every observed gap or calendar exclusion; exclude New York 16:30-17:30 daily, Friday from16:00, Saturday, and Sunday before18:00. These predeclared conservative buffers avoid known candidate rollover/weekend boundaries. Any target path crossing them is excluded. Exact historical sessions, exceptional closures and holiday hours remain unverified; the buffers cannot prove the absence of unknown intraday boundary changes. A strict verified-calendar performance policy would still block promotion. No missing bar or historical hour is invented.

## 5. Causal feature engineering

The original eight published features remain the reference input. New features use only the current completed bar and a maximum 61 observed contiguous bars. They reset at gaps/exclusions. A new sample requires at least31 bars and two fully completed UTC-aligned five-minute aggregates. Partial five-minute periods are discarded; future bars never complete an aggregate early.

| New feature vector (12) | Transform |
| --- | --- |
| momentum1/5/15/30 | close/lagged_close-1, divided by0.001 |
| vol-normalized momentum5 | return5/(max(sigma20,1e-6)*sqrt5) |
| realized volatility20 | population SD of last20 one-minute returns, divided by0.0005 |
| current high-low range | (high-low)/close, divided by0.001 |
| rolling price position20 | 2*(close-low20)/(high20-low20)-1; flat range ->0 |
| mean reversion20 | -(close/mean_close20-1)/(vol_floor*sqrt20) |
| completed5m momentum | last completed5m close / preceding completed5m close-1, divided by0.001 |
| UTC time-of-day sin/cos | known completed-bar decision minute; two features |

New normalized features are clipped to [-5,5], without fitting normalization on later data. Four fixed interaction terms are z5*position20, momentum5*range, mean_reversion20 squared, and sigma20*abs(momentum5), each also clipped to [-5,5]. These compact nonlinear features do not imply that nonlinear predictive information exists.

## 6. Candidate support matrix

| Family | Dimensions excluding intercept | Initial learning rate | L2 | Support |
| --- | --- | --- | --- | --- |
| reference8 | 8 | 0.005000 | 0.001000 | Original OnlineModel update, original8 predictors, alternative labels |
| linear12 | 12 | 0.003000 | 0.010000 | Deterministic regularized online softmax |
| interaction16 | 16 | 0.003000 | 0.010000 | Same incremental kernel with four fixed interactions |

All start from zero coefficients, seed metadata20261027, float64 model arithmetic and original decreasing learning-rate/temperature-update formulas. Calibration changes only a scalar log-temperature with original bounds. Every checkpoint binds schema strat2g-model-state-v1, family, target, exact feature/scaling spec and parameters. Mismatches are rejected; there is no silent estimator substitution or migration. Nonincremental large ensembles/neural frameworks are unsupported and not added. The old production model and old barrier-label metrics remain preserved contextual references, not directly comparable scores for a different target.

## 7. Configurations and experiment counts

Exactly **3 targets x3 trainable families =9** training/calibration configurations. Raw and calibrated views are two views of each fitted candidate, not18 separately tuned models. Four baselines per target: training majority, training-class prior probabilities, sign(return5) momentum and its mean-reversion inverse. All share the same eligible target timestamps. Hard one-hot baselines use a 1e-12 probability floor for log loss and must be judged separately from probabilistic calibration.

Selection choices, frozen before final outcomes: `{"h15_cost": "linear12", "h30_vol": "reference8", "h5_cost": "linear12"}`.

## 8. Predictive metrics and baselines

**selection** (all exploratory):

| Target | Model | N | Accuracy | Balanced accuracy | Log loss | Brier [0,2] | Top-label ECE |
| --- | --- | --- | --- | --- | --- | --- | --- |
| h15_cost | interaction16_cal | 12075 | 0.444638 | 0.354971 | 1.053478 | 0.633365 | 0.041818 |
| h15_cost | interaction16_raw | 12075 | 0.444638 | 0.354971 | 1.068026 | 0.642228 | 0.076877 |
| h15_cost | linear12_cal | 12075 | 0.442981 | 0.350173 | 1.053147 | 0.633223 | 0.042982 |
| h15_cost | linear12_raw | 12075 | 0.442981 | 0.350173 | 1.068255 | 0.642654 | 0.081913 |
| h15_cost | majority | 12075 | 0.433458 | 0.333333 | 15.654146 | 1.133085 | 0.566542 |
| h15_cost | mean_reversion | 12075 | 0.289689 | 0.336142 | 19.626606 | 1.420621 | 0.710311 |
| h15_cost | momentum | 12075 | 0.289193 | 0.335462 | 19.640336 | 1.421615 | 0.710807 |
| h15_cost | prior | 12075 | 0.433458 | 0.333333 | 1.082422 | 0.655709 | 0.052196 |
| h15_cost | reference8_cal | 12075 | 0.435280 | 0.336074 | 1.068505 | 0.645199 | 0.010749 |
| h15_cost | reference8_raw | 12075 | 0.435280 | 0.336074 | 1.074861 | 0.649283 | 0.066081 |
| h30_vol | interaction16_cal | 12075 | 0.438758 | 0.340696 | 1.080819 | 0.652936 | 0.019178 |
| h30_vol | interaction16_raw | 12075 | 0.438758 | 0.340696 | 1.094612 | 0.661267 | 0.066474 |
| h30_vol | linear12_cal | 12075 | 0.435776 | 0.335789 | 1.081491 | 0.653507 | 0.030332 |
| h30_vol | linear12_raw | 12075 | 0.435776 | 0.335789 | 1.092438 | 0.660361 | 0.068255 |
| h30_vol | majority | 12075 | 0.434948 | 0.333333 | 15.612957 | 1.130104 | 0.565052 |
| h30_vol | mean_reversion | 12075 | 0.284555 | 0.331540 | 19.768480 | 1.430890 | 0.715445 |
| h30_vol | momentum | 12075 | 0.288613 | 0.336135 | 19.656354 | 1.422774 | 0.711387 |
| h30_vol | prior | 12075 | 0.434948 | 0.333333 | 1.078482 | 0.652772 | 0.032289 |
| h30_vol | reference8_cal | 12075 | 0.434203 | 0.332835 | 1.081878 | 0.654287 | 0.012879 |
| h30_vol | reference8_raw | 12075 | 0.434203 | 0.332835 | 1.088966 | 0.658468 | 0.046678 |
| h5_cost | interaction16_cal | 12075 | 0.658385 | 0.334510 | 0.842131 | 0.483779 | 0.026618 |
| h5_cost | interaction16_raw | 12075 | 0.658385 | 0.334510 | 0.849445 | 0.487995 | 0.056368 |
| h5_cost | linear12_cal | 12075 | 0.658385 | 0.334276 | 0.842538 | 0.484104 | 0.028181 |
| h5_cost | linear12_raw | 12075 | 0.658385 | 0.334276 | 0.850364 | 0.488493 | 0.056233 |
| h5_cost | majority | 12075 | 0.658137 | 0.333333 | 9.446034 | 0.683727 | 0.341863 |
| h5_cost | mean_reversion | 12075 | 0.178882 | 0.323938 | 22.688329 | 1.642236 | 0.821118 |
| h5_cost | momentum | 12075 | 0.191718 | 0.349022 | 22.333645 | 1.616563 | 0.808282 |
| h5_cost | prior | 12075 | 0.658137 | 0.333333 | 0.883606 | 0.511259 | 0.043619 |
| h5_cost | reference8_cal | 12075 | 0.658054 | 0.333291 | 0.872596 | 0.503541 | 0.096237 |
| h5_cost | reference8_raw | 12075 | 0.658054 | 0.333291 | 0.866533 | 0.499999 | 0.071987 |

**evaluation** (all exploratory):

| Target | Model | N | Accuracy | Balanced accuracy | Log loss | Brier [0,2] | Top-label ECE |
| --- | --- | --- | --- | --- | --- | --- | --- |
| h15_cost | interaction16_cal | 12304 | 0.404746 | 0.354693 | 1.083523 | 0.655650 | 0.066112 |
| h15_cost | interaction16_raw | 12304 | 0.404746 | 0.354693 | 1.109789 | 0.673021 | 0.117141 |
| h15_cost | linear12_cal | 12304 | 0.402227 | 0.350703 | 1.084392 | 0.656378 | 0.071897 |
| h15_cost | linear12_raw | 12304 | 0.402227 | 0.350703 | 1.111986 | 0.674803 | 0.124112 |
| h15_cost | majority | 12304 | 0.389467 | 0.333333 | 16.869655 | 1.221066 | 0.610533 |
| h15_cost | mean_reversion | 12304 | 0.318921 | 0.346559 | 18.818917 | 1.362159 | 0.681079 |
| h15_cost | momentum | 12304 | 0.297627 | 0.323258 | 19.407289 | 1.404746 | 0.702373 |
| h15_cost | prior | 12304 | 0.389467 | 0.333333 | 1.110409 | 0.675821 | 0.096186 |
| h15_cost | reference8_cal | 12304 | 0.391824 | 0.336043 | 1.085373 | 0.657361 | 0.048837 |
| h15_cost | reference8_raw | 12304 | 0.391824 | 0.336043 | 1.097900 | 0.666299 | 0.101343 |
| h30_vol | interaction16_cal | 12304 | 0.436606 | 0.335629 | 1.081677 | 0.653892 | 0.030550 |
| h30_vol | interaction16_raw | 12304 | 0.436606 | 0.335629 | 1.096297 | 0.663039 | 0.068228 |
| h30_vol | linear12_cal | 12304 | 0.437500 | 0.335776 | 1.082124 | 0.654396 | 0.036144 |
| h30_vol | linear12_raw | 12304 | 0.437500 | 0.335776 | 1.093700 | 0.661878 | 0.066328 |
| h30_vol | majority | 12304 | 0.436362 | 0.333333 | 15.573889 | 1.127276 | 0.563638 |
| h30_vol | mean_reversion | 12304 | 0.298358 | 0.349680 | 19.387078 | 1.403283 | 0.701642 |
| h30_vol | momentum | 12304 | 0.272269 | 0.318939 | 20.107946 | 1.455462 | 0.727731 |
| h30_vol | prior | 12304 | 0.436362 | 0.333333 | 1.077677 | 0.652199 | 0.030876 |
| h30_vol | reference8_cal | 12304 | 0.435549 | 0.332820 | 1.079029 | 0.652619 | 0.011036 |
| h30_vol | reference8_raw | 12304 | 0.435549 | 0.332820 | 1.085146 | 0.656336 | 0.045975 |
| h5_cost | interaction16_cal | 12304 | 0.597854 | 0.333599 | 0.931768 | 0.546075 | 0.050644 |
| h5_cost | interaction16_raw | 12304 | 0.597854 | 0.333599 | 0.966891 | 0.564313 | 0.119129 |
| h5_cost | linear12_cal | 12304 | 0.597692 | 0.333333 | 0.932829 | 0.546693 | 0.052401 |
| h5_cost | linear12_raw | 12304 | 0.597692 | 0.333333 | 0.968688 | 0.565058 | 0.119731 |
| h5_cost | majority | 12304 | 0.597692 | 0.333333 | 11.116186 | 0.804616 | 0.402308 |
| h5_cost | mean_reversion | 12304 | 0.210338 | 0.337335 | 21.819165 | 1.579324 | 0.789662 |
| h5_cost | momentum | 12304 | 0.207250 | 0.331969 | 21.904501 | 1.585501 | 0.792750 |
| h5_cost | prior | 12304 | 0.597692 | 0.333333 | 0.977239 | 0.578070 | 0.104064 |
| h5_cost | reference8_cal | 12304 | 0.597692 | 0.333333 | 0.939158 | 0.551976 | 0.105242 |
| h5_cost | reference8_raw | 12304 | 0.597692 | 0.333333 | 0.956783 | 0.564539 | 0.099907 |

h15_cost / interaction16_cal: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[4546, 69, 177], [3417, 153, 171], [3416, 74, 281]]`; precision `[0.3995078653660251, 0.5168918918918919, 0.4467408585055644]`; recall `[0.9486644407345576, 0.0408981555733761, 0.0745160434897905]`. Undefined values are null.

h15_cost / interaction16_raw: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[4546, 69, 177], [3417, 153, 171], [3416, 74, 281]]`; precision `[0.3995078653660251, 0.5168918918918919, 0.4467408585055644]`; recall `[0.9486644407345576, 0.0408981555733761, 0.0745160434897905]`. Undefined values are null.

h15_cost / linear12_cal: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[4614, 96, 82], [3485, 197, 59], [3533, 100, 138]]`; precision `[0.3966643741403026, 0.5012722646310432, 0.4946236559139785]`; recall `[0.9628547579298832, 0.052659716653301256, 0.03659506762132061]`. Undefined values are null.

h15_cost / linear12_raw: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[4614, 96, 82], [3485, 197, 59], [3533, 100, 138]]`; precision `[0.3966643741403026, 0.5012722646310432, 0.4946236559139785]`; recall `[0.9628547579298832, 0.052659716653301256, 0.03659506762132061]`. Undefined values are null.

h15_cost / majority: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[4792, 0, 0], [3741, 0, 0], [3771, 0, 0]]`; precision `[0.3894668400520156, None, None]`; recall `[1.0, 0.0, 0.0]`. Undefined values are null.

h15_cost / mean_reversion: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[89, 2272, 2431], [53, 1944, 1744], [51, 1829, 1891]]`; precision `[0.46113989637305697, 0.3215880893300248, 0.3117375535773162]`; recall `[0.01857262103505843, 0.5196471531676022, 0.5014584990718642]`. Undefined values are null.

h15_cost / momentum: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[89, 2431, 2272], [53, 1744, 1944], [51, 1891, 1829]]`; precision `[0.46113989637305697, 0.28750412133201453, 0.30256410256410254]`; recall `[0.01857262103505843, 0.4661855118952152, 0.4850172368072129]`. Undefined values are null.

h15_cost / prior: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[4792, 0, 0], [3741, 0, 0], [3771, 0, 0]]`; precision `[0.3894668400520156, None, None]`; recall `[1.0, 0.0, 0.0]`. Undefined values are null.

h15_cost / reference8_cal: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[4785, 3, 4], [3714, 20, 7], [3748, 7, 16]]`; precision `[0.3907079284722789, 0.6666666666666666, 0.5925925925925926]`; recall `[0.9985392320534223, 0.005346164127238706, 0.0042429063908777515]`. Undefined values are null.

h15_cost / reference8_raw: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[4785, 3, 4], [3714, 20, 7], [3748, 7, 16]]`; precision `[0.3907079284722789, 0.6666666666666666, 0.5925925925925926]`; recall `[0.9985392320534223, 0.005346164127238706, 0.0042429063908777515]`. Undefined values are null.

h30_vol / interaction16_cal: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[5313, 0, 56], [3481, 0, 47], [3348, 0, 59]]`; precision `[0.43757206391039366, None, 0.36419753086419754]`; recall `[0.9895697522816167, 0.0, 0.017317287936601117]`. Undefined values are null.

h30_vol / interaction16_raw: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[5313, 0, 56], [3481, 0, 47], [3348, 0, 59]]`; precision `[0.43757206391039366, None, 0.36419753086419754]`; recall `[0.9895697522816167, 0.0, 0.017317287936601117]`. Undefined values are null.

h30_vol / linear12_cal: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[5339, 0, 30], [3499, 0, 29], [3363, 0, 44]]`; precision `[0.4375870830259815, None, 0.42718446601941745]`; recall `[0.9944123672937232, 0.0, 0.012914587613736424]`. Undefined values are null.

h30_vol / linear12_raw: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[5339, 0, 30], [3499, 0, 29], [3363, 0, 44]]`; precision `[0.4375870830259815, None, 0.42718446601941745]`; recall `[0.9944123672937232, 0.0, 0.012914587613736424]`. Undefined values are null.

h30_vol / majority: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[5369, 0, 0], [3528, 0, 0], [3407, 0, 0]]`; precision `[0.43636215864759426, None, None]`; recall `[1.0, 0.0, 0.0]`. Undefined values are null.

h30_vol / mean_reversion: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[93, 2590, 2686], [56, 1835, 1637], [44, 1620, 1743]]`; precision `[0.48186528497409326, 0.30355665839536805, 0.2873392680514342]`; recall `[0.017321661389458, 0.520124716553288, 0.5115937775168771]`. Undefined values are null.

h30_vol / momentum: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[93, 2686, 2590], [56, 1637, 1835], [44, 1743, 1620]]`; precision `[0.48186528497409326, 0.26986482030992415, 0.2679900744416873]`; recall `[0.017321661389458, 0.46400226757369617, 0.47549163486938656]`. Undefined values are null.

h30_vol / prior: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[5369, 0, 0], [3528, 0, 0], [3407, 0, 0]]`; precision `[0.43636215864759426, None, None]`; recall `[1.0, 0.0, 0.0]`. Undefined values are null.

h30_vol / reference8_cal: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[5356, 0, 13], [3528, 0, 0], [3404, 0, 3]]`; precision `[0.4358723958333333, None, 0.1875]`; recall `[0.9975786924939467, 0.0, 0.0008805400645729381]`. Undefined values are null.

h30_vol / reference8_raw: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[5356, 0, 13], [3528, 0, 0], [3404, 0, 3]]`; precision `[0.4358723958333333, None, 0.1875]`; recall `[0.9975786924939467, 0.0, 0.0008805400645729381]`. Undefined values are null.

h5_cost / interaction16_cal: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[7354, 0, 0], [2443, 0, 0], [2505, 0, 2]]`; precision `[0.5977889774020484, None, 1.0]`; recall `[1.0, 0.0, 0.0007977662544874351]`. Undefined values are null.

h5_cost / interaction16_raw: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[7354, 0, 0], [2443, 0, 0], [2505, 0, 2]]`; precision `[0.5977889774020484, None, 1.0]`; recall `[1.0, 0.0, 0.0007977662544874351]`. Undefined values are null.

h5_cost / linear12_cal: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[7354, 0, 0], [2443, 0, 0], [2507, 0, 0]]`; precision `[0.5976918075422627, None, None]`; recall `[1.0, 0.0, 0.0]`. Undefined values are null.

h5_cost / linear12_raw: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[7354, 0, 0], [2443, 0, 0], [2507, 0, 0]]`; precision `[0.5976918075422627, None, None]`; recall `[1.0, 0.0, 0.0]`. Undefined values are null.

h5_cost / majority: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[7354, 0, 0], [2443, 0, 0], [2507, 0, 0]]`; precision `[0.5976918075422627, None, None]`; recall `[1.0, 0.0, 0.0]`. Undefined values are null.

h5_cost / mean_reversion: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[127, 3532, 3695], [29, 1252, 1162], [37, 1261, 1209]]`; precision `[0.6580310880829016, 0.20711331679073613, 0.19930761622156282]`; recall `[0.017269513190100626, 0.5124846500204666, 0.48224970083765456]`. Undefined values are null.

h5_cost / momentum: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[127, 3695, 3532], [29, 1162, 1252], [37, 1209, 1261]]`; precision `[0.6580310880829016, 0.19155951203428948, 0.2086021505376344]`; recall `[0.017269513190100626, 0.47564469914040114, 0.5029916234543279]`. Undefined values are null.

h5_cost / prior: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[7354, 0, 0], [2443, 0, 0], [2507, 0, 0]]`; precision `[0.5976918075422627, None, None]`; recall `[1.0, 0.0, 0.0]`. Undefined values are null.

h5_cost / reference8_cal: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[7354, 0, 0], [2443, 0, 0], [2507, 0, 0]]`; precision `[0.5976918075422627, None, None]`; recall `[1.0, 0.0, 0.0]`. Undefined values are null.

h5_cost / reference8_raw: true-row/predicted-column confusion matrix FLAT,LONG,SHORT `[[7354, 0, 0], [2443, 0, 0], [2507, 0, 0]]`; precision `[0.5976918075422627, None, None]`; recall `[1.0, 0.0, 0.0]`. Undefined values are null.

Final evaluation by descriptive UTC bands (these are not verified broker sessions):

| Target | UTC band | Model | N | Balanced accuracy | Log loss |
| --- | --- | --- | --- | --- | --- |
| h15_cost | utc_00_07 | interaction16_cal | 3852 | 0.333333 | 1.029672 |
| h15_cost | utc_00_07 | linear12_cal | 3852 | 0.333333 | 1.029870 |
| h15_cost | utc_00_07 | prior | 3852 | 0.333333 | 1.024573 |
| h15_cost | utc_00_07 | reference8_cal | 3852 | 0.337776 | 1.023083 |
| h15_cost | utc_07_13 | interaction16_cal | 3600 | 0.345730 | 1.122911 |
| h15_cost | utc_07_13 | linear12_cal | 3600 | 0.344419 | 1.123972 |
| h15_cost | utc_07_13 | prior | 3600 | 0.333333 | 1.156323 |
| h15_cost | utc_07_13 | reference8_cal | 3600 | 0.335129 | 1.121291 |
| h15_cost | utc_13_17 | interaction16_cal | 2400 | 0.366910 | 1.098789 |
| h15_cost | utc_13_17 | linear12_cal | 2400 | 0.363533 | 1.100730 |
| h15_cost | utc_13_17 | prior | 2400 | 0.333333 | 1.184816 |
| h15_cost | utc_13_17 | reference8_cal | 2400 | 0.335854 | 1.130904 |
| h15_cost | utc_17_24 | interaction16_cal | 2452 | 0.333333 | 1.095350 |
| h15_cost | utc_17_24 | linear12_cal | 2452 | 0.334017 | 1.095939 |
| h15_cost | utc_17_24 | prior | 2452 | 0.333333 | 1.105014 |
| h15_cost | utc_17_24 | reference8_cal | 2452 | 0.334667 | 1.085930 |
| h30_vol | utc_00_07 | interaction16_cal | 3852 | 0.333333 | 1.077591 |
| h30_vol | utc_00_07 | linear12_cal | 3852 | 0.333333 | 1.081518 |
| h30_vol | utc_00_07 | prior | 3852 | 0.333333 | 1.064213 |
| h30_vol | utc_00_07 | reference8_cal | 3852 | 0.333145 | 1.072308 |
| h30_vol | utc_07_13 | interaction16_cal | 3600 | 0.336233 | 1.101723 |
| h30_vol | utc_07_13 | linear12_cal | 3600 | 0.336588 | 1.099792 |
| h30_vol | utc_07_13 | prior | 3600 | 0.333333 | 1.096263 |
| h30_vol | utc_07_13 | reference8_cal | 3600 | 0.332045 | 1.095851 |
| h30_vol | utc_13_17 | interaction16_cal | 2400 | 0.339251 | 1.066771 |
| h30_vol | utc_13_17 | linear12_cal | 2400 | 0.340259 | 1.064293 |
| h30_vol | utc_13_17 | prior | 2400 | 0.333333 | 1.063569 |
| h30_vol | utc_13_17 | reference8_cal | 2400 | 0.332731 | 1.062096 |
| h30_vol | utc_17_24 | interaction16_cal | 2452 | 0.333333 | 1.073253 |
| h30_vol | utc_17_24 | linear12_cal | 2452 | 0.333333 | 1.074590 |
| h30_vol | utc_17_24 | prior | 2452 | 0.333333 | 1.085350 |
| h30_vol | utc_17_24 | reference8_cal | 2452 | 0.333284 | 1.081463 |
| h5_cost | utc_00_07 | interaction16_cal | 3852 | 0.333333 | 0.752956 |
| h5_cost | utc_00_07 | linear12_cal | 3852 | 0.333333 | 0.754348 |
| h5_cost | utc_00_07 | prior | 3852 | 0.333333 | 0.747019 |
| h5_cost | utc_00_07 | reference8_cal | 3852 | 0.333333 | 0.772389 |
| h5_cost | utc_07_13 | interaction16_cal | 3600 | 0.334109 | 1.039536 |
| h5_cost | utc_07_13 | linear12_cal | 3600 | 0.333333 | 1.040635 |
| h5_cost | utc_07_13 | prior | 3600 | 0.333333 | 1.102868 |
| h5_cost | utc_07_13 | reference8_cal | 3600 | 0.333333 | 1.031859 |
| h5_cost | utc_13_17 | interaction16_cal | 2400 | 0.333333 | 1.080806 |
| h5_cost | utc_13_17 | linear12_cal | 2400 | 0.333333 | 1.081297 |
| h5_cost | utc_13_17 | prior | 2400 | 0.333333 | 1.217617 |
| h5_cost | utc_13_17 | reference8_cal | 2400 | 0.333333 | 1.105845 |
| h5_cost | utc_17_24 | interaction16_cal | 2452 | 0.333333 | 0.908574 |
| h5_cost | utc_17_24 | linear12_cal | 2452 | 0.333333 | 0.909616 |
| h5_cost | utc_17_24 | prior | 2452 | 0.333333 | 0.919179 |
| h5_cost | utc_17_24 | reference8_cal | 2452 | 0.333333 | 0.901891 |

January supplies fitting/calibration observations; February supplies both selection and final exploratory evaluation. There is only one final evaluation month and no cross-month confirmation. Absolute loss comparisons across different target definitions are not a valid model ranking because their class entropies differ.

## 9. Probability calibration

Both raw and scalar-temperature probabilities are reported on the same final rows. Calibration is fitted only in January; final observations never fit it. Temperature preserves class argmax. Top-label and per-class reliability bins/ECE use ten fixed bins; proper loss/Brier scores mix discrimination and calibration. Reliability curves and full accumulators are stored in journal states. Low ECE alone is not evidence of directional utility.

| Target | Family | Temperature | Train updates | Calibration updates |
| --- | --- | --- | --- | --- |
| h15_cost | interaction16 | 1.383831 | 11675 | 13428 |
| h15_cost | linear12 | 1.384050 | 11675 | 13428 |
| h15_cost | reference8 | 1.468970 | 11675 | 13428 |
| h30_vol | interaction16 | 1.344726 | 11675 | 13428 |
| h30_vol | linear12 | 1.264850 | 11675 | 13428 |
| h30_vol | reference8 | 1.294193 | 11675 | 13428 |
| h5_cost | interaction16 | 1.299813 | 11675 | 13428 |
| h5_cost | linear12 | 1.297650 | 11675 | 13428 |
| h5_cost | reference8 | 1.405086 | 11675 | 13428 |

h15_cost selected linear12: per-class ECE `[0.08360146734335053, 0.049434215269492605, 0.03417356815267904]`; reliability `(mean_probability,observed_fraction,n)` points `[[0.36942960148960574, 0.334915611814346, 3792], [0.44625538237282425, 0.3344891971192318, 3749], [0.5511708482163862, 0.48307692307692307, 2925], [0.6243488511953227, 0.5505984766050055, 1838]]`.

h30_vol selected reference8: per-class ECE `[0.011478874655795163, 0.039691252167680886, 0.03022355723348496]`; reliability `(mean_probability,observed_fraction,n)` points `[[0.394056787826402, 0.5185185185185185, 27], [0.44598168963933515, 0.43527494908350306, 12275], [0.5007374277654605, 1.0, 2]]`.

h5_cost selected linear12: per-class ECE `[0.05240052589560559, 0.02702294305365535, 0.020219171910011085]`; reliability `(mean_probability,observed_fraction,n)` points `[[0.48799912545859375, 0.46348314606741575, 1068], [0.5415180413970587, 0.4920362656211713, 4081], [0.6509606026095928, 0.5715320536037668, 2761], [0.7490388611012397, 0.7613152804642166, 2585], [0.8129248810558185, 0.7213930348258707, 1809]]`.

## 10. Exploratory signal quality

The production confidence gate is unchanged at0.55, with directional probability also required to exceed neutral. Signals below are hypothetical predictions on target-admissible common paths, not orders, fills or a portfolio run. Directional consistency means selected class equals the directional target; non-overlapping exposure is not assumed. A fixed horizon cooldown is a turnover proxy, not a strategy optimization.

| Target | Candidate | Signals | Long | Short | Label precision | Gross forward bps | Net scenario bps | Horizon-spaced count |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| h15_cost | interaction16 | 0 | 0 | 0 | NA | NA | NA | 0 |
| h15_cost | linear12 | 0 | 0 | 0 | NA | NA | NA | 0 |
| h15_cost | reference8 | 0 | 0 | 0 | NA | NA | NA | 0 |
| h30_vol | interaction16 | 0 | 0 | 0 | NA | NA | NA | 0 |
| h30_vol | linear12 | 0 | 0 | 0 | NA | NA | NA | 0 |
| h30_vol | reference8 | 0 | 0 | 0 | NA | NA | NA | 0 |
| h5_cost | interaction16 | 0 | 0 | 0 | NA | NA | NA | 0 |
| h5_cost | linear12 | 0 | 0 | 0 | NA | NA | NA | 0 |
| h5_cost | reference8 | 0 | 0 | 0 | NA | NA | NA | 0 |

Confidence distributions and per-class probability bins are retained for all models in the committed metric state; signal occurrence can cluster in a small number of days, so row counts are not independent evidence.

## 11. Transaction-cost feasibility

Round-trip scenario cost remains 1 spread +2*0.25 slippage +0.6 commission =2.1 bps. This is an explicit reference-price approximation, not verified historical bid/ask costs. Gross directional movement, label correctness and expected filled trading return are different quantities. Sensitivity subtracts fixed total costs of1.1/2.1/3.1/5.6 bps from the same gross conditional outcomes; it does not refit or change decisions. Costs, financing, sizing, capital and gaps are not a realized trading simulation.

| Target | Candidate | Mean net bps at costs1.1 /2.1 /3.1 /5.6 |
| --- | --- | --- |
| h15_cost | interaction16 | NA: no signals |
| h15_cost | linear12 | NA: no signals |
| h15_cost | reference8 | NA: no signals |
| h30_vol | interaction16 | NA: no signals |
| h30_vol | linear12 | NA: no signals |
| h30_vol | reference8 | NA: no signals |
| h5_cost | interaction16 | NA: no signals |
| h5_cost | linear12 | NA: no signals |
| h5_cost | reference8 | NA: no signals |

Historical calendar/holiday and source-price assumptions remain unverified. Any apparent positive scenario mean is provisional and cannot establish profitability; if costs consume a measured conditional gross edge, that must not be presented as an executable advantage. No live, paper or broker-connected trading occurs.

## 12. Statistical uncertainty

Final selected-model differences versus reference8 and training-prior use paired circular blocks of2 and5 adjacent observed UTC days,1,000 fixed-seed resamples each. Within-day overlapping minute targets remain together, and multi-day blocks retain short-range adjacent dependence. There is no iid minute-level interval. Few days, sparse directional events, residual long dependence, wrap-around and nine exploratory configurations create uncertainty and selection risk. Intervals are descriptive, not multiplicity-adjusted confirmation tests. [Block bootstrap definitions](https://bashtage.github.io/arch/doc/bootstrap/timeseries-bootstraps.html).

| Target | Selected | Observed days | Block days | Comparator | Loss diff lower | Loss diff upper | BA diff lower | BA diff upper |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| h15_cost | linear12 | 12 | 2 | prior | -0.039096 | -0.012348 | 0.013750 | 0.021023 |
| h15_cost | linear12 | 12 | 2 | reference8_cal | -0.024298 | 0.024833 | 0.010648 | 0.019354 |
| h15_cost | linear12 | 12 | 5 | prior | -0.040190 | -0.013672 | 0.013705 | 0.020655 |
| h15_cost | linear12 | 12 | 5 | reference8_cal | -0.026181 | 0.026548 | 0.010690 | 0.018460 |
| h30_vol | reference8 | 12 | 2 | prior | -0.005982 | 0.007782 | -0.000969 | -0.000176 |
| h30_vol | reference8 | 12 | 2 | reference8_cal | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| h30_vol | reference8 | 12 | 5 | prior | -0.004425 | 0.006697 | -0.000979 | -0.000132 |
| h30_vol | reference8 | 12 | 5 | reference8_cal | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| h5_cost | linear12 | 12 | 2 | prior | -0.056680 | -0.031741 | 0.000000 | 0.000000 |
| h5_cost | linear12 | 12 | 2 | reference8_cal | -0.032579 | 0.027002 | 0.000000 | 0.000000 |
| h5_cost | linear12 | 12 | 5 | prior | -0.057726 | -0.032367 | 0.000000 | 0.000000 |
| h5_cost | linear12 | 12 | 5 | reference8_cal | -0.035472 | 0.027432 | 0.000000 | 0.000000 |

Negative loss differences and positive balanced-accuracy differences favor the selected candidate. Exploratory PASS requires both criteria against both comparators under both block lengths. Insufficient selection or unresolved intervals is INCONCLUSIVE; the conclusion never means independently validated profitability.

## 13. Resource measurements

Runtime profile strat2g-eurusd-2022-offline-120-v1 requires minimum available RAM 1.20 GiB, maximum worker RSS 256 MiB, disk floor 15 GiB and one numerical/Arrow thread. General training/backtest workflows still require 1.50 GiB; previous profiles remain at 1.30 GiB. External real-PID polling and internal bounded guards fail closed. Memory holds at most 61 feature bars, 31 pending raw/feature rows, nine compact models, one 1024-row frame and bounded metrics/day/event buffers.

| Worker | Peak RSS MiB | Minimum available RAM GiB | Minimum disk GiB | Elapsed seconds |
| --- | --- | --- | --- | --- |
| matrix-crash | 98.136719 | 1.618614 | 26.956478 | NA |
| matrix-reference | 98.472656 | 1.433155 | 26.833126 | 90.534454 |
| matrix-repeat | 79.304688 | 1.574745 | 26.901596 | 2.478740 |
| matrix-resume | 97.812500 | 1.514252 | 26.901604 | 49.110519 |

RSS is OS working-set high-water; system minima are sampled and background fluctuations are not code effects. No full dataset is loaded merely because these two months are small.

## 14. Checkpoint and deterministic replay

Atomic immutable transactions publish cursor, feature history, pending target buffer, all nine model/calibrator states, selected-model identities, metrics and event sequence together. Code, protocol, source hashes and numerical-library versions bind the journal identity. Orphan staging is preserved and ignored after interruption. A crash cannot commit half of a model batch. Old journals are never migrated or rewritten.

Actual hard process exit before commit35, new-process resume, completed repeat and independent uninterrupted run passed. Generation **59**, complete=true; **181/181** committed hashes and canonical state match. Independent streamed verification checks price units, target formula/horizon, common maturity, chronological splits, event sequence, probability normalization, confusion/loss/Brier totals and selection-only provenance. Preserved old evidence: `{'strat2d-eurusd-2022-offline-130-reference-v1': 721, 'strat2d-strat2e-execution-calendar-reference-v2': 181, 'strat2d-strat2f-diagnostics-reference-v2': 181}`, plus47 STRAT-2C hashes/mtimes.

## 15. Automated tests

strat2g-tests-v1.xml: `{"tests": 12, "failures": 2, "errors": 0, "skipped": 0}`.

strat2g-tests-v2.xml: `{"tests": 12, "failures": 0, "errors": 0, "skipped": 0}`.

strat2g-regression-v3.xml: `{"tests": 99, "failures": 0, "errors": 0, "skipped": 0}`.

strat2g-final-v4.xml: `{"tests": 14, "failures": 0, "errors": 0, "skipped": 0}`.

The initial two failures were a reused synthetic fixture with a0.34 threshold; the unchanged0.55 safety assertion correctly rejected it. Only the synthetic fixture was corrected. Tests cover target price units/costs, volatility zones, future perturbation, completed timeframe boundaries, gap/session reset, purging/embargo, stable model/schema compatibility, bounded state, selection simplicity/ties without final inputs, interruption before/after publication, repeated resume and independent event verification. Existing Phase6 causal/chunk/holdout and STRAT-2F metric/calibration/resource tests are retained. Known NumPy timedelta deprecations are recorded, not silenced. Ruff check/format and git diff --check are required before historical execution.

## 16. PASS/FAIL/INCONCLUSIVE assessment

| Subsystem | Status |
| --- | --- |
| Protocol/fixed matrix/causal feature and target implementation | Implemented and synthetically tested |
| Chronological selection and no final-fit leakage | Tested; historical verification PASS |
| Historical bounded matrix / checkpoint / replay | PASS |
| Historical verified-calendar / profitability evidence | UNVERIFIED / BLOCKED |
| Research conclusion | INCONCLUSIVE — insufficient evidence to establish reliable improvement |

## 17. Recommended next development phase

Resolve exact historical calendar/holiday and price-type evidence first. If exploratory differences warrant further research, pre-register a stable simple candidate and an independent protocol on a newly authorized development period before inspecting outcomes. No expansion of dates is authorized by this report, and2026 remains excluded. Do not promote an experimental model or relax the production0.55 threshold. A small repeatedly inspected period cannot supply independent confirmation.

Added: config/strat2g-protocol-v1.json; src/trading/research/strat2g/{__init__,core,pipeline,analysis,report}.py; tools/strat2g_validation.py; tests/test_strat2g.py; this report. Previous Phase5 data, STRAT-2C/D/E/F evidence, core model definitions and safety profiles are unchanged. No dependencies, Docker services, broker connections, paper/live orders, full-year backtests, commits or pushes were introduced.

Executed command (version2 paths now exist; do not reset them):

```powershell
.\.venv\Scripts\python.exe -B -m tools.strat2g_validation --resource-profile 120 --evidence-version 2
```
