# STRAT-2F model diagnostics and signal quality v1

**INCONCLUSIVE Ă˘â‚¬â€ť insufficient evidence or unresolved uncertainty**

## 1. Executive summary

Engineering status: **PASS**. This is retrospective research on an already inspected development window. No threshold, model parameter, feature scale or production behavior was changed. No profitability conclusion is supported.

## Historical completion and interpretation (version 2)

**Engineering validation: PASS. Research outcome: INCONCLUSIVE.** The previously blocked historical workflow is now complete. The prior consolidated report and version-1 failure evidence remain preserved; the earlier report is archived in the version-2 evidence directory. No completed functionality was reimplemented and no model, threshold or resource policy changed.

Actual invocation: `.\.venv\Scripts\python.exe -B -m tools.strat2f_validation --evidence-version 2`. The precheck measured **1.716404 GiB available RAM** and **27.057327 GiB free disk**. Runtime guards remained 1.30 GiB physical available RAM, 256 MiB worker RSS and 15 GiB free disk, single-threaded with 1,024-row batches.

**Actual data and verification:** 58,683 source rows streamed; 12,212 fitted training rows and 13,915 calibration rows reconstructed; 25,446 identically admitted February rows evaluated for every model; 24 observed UTC-day blocks. The reconstructed model/calibrator exactly matches the sealed STRAT-2D model. A deliberate hard process exit before commit 35 left generation 34 intact, then a fresh worker resumed. Repeated completed resume changed no committed output. The independent uninterrupted run matched **181/181 committed hashes** and the independent numerical reader verified every one of the **25,446 evaluation events**. Completion: **generation 59, 60 transactions, complete=true**. Canonical final state hash: `85111038e84964dc649fe0cec0e14b2efb16d6dd3540e684c21e830cbcee8f1d`.

### What the actual results show

| February result | Raw softmax | Calibrated softmax | Training-class prior |
| --- | ---: | ---: | ---: |
| Log loss (lower better) | 1.069986 | 1.050393 | 1.066851 |
| Multiclass Brier (lower better) | 0.646514 | 0.632482 | 0.644760 |
| Top-label ECE | 0.108183 | 0.011838 | 0.065933 |
| Balanced accuracy | 0.333337 | 0.333337 | 0.333333 |

Calibration is **beneficial for probability scoring in this observed month**, not an identified sign/normalization defect. The 2-day paired-block 95% interval for calibrated-minus-prior log loss is **[-0.028792, -0.008013]**, and the 5-day sensitivity interval also favors calibrated probabilities. However, the balanced-accuracy difference interval is **[-0.000231, 0.000300]**, spanning zero; raw and calibrated class rankings are identical. February class balance shifted to **11,985 neutral / 6,735 long / 6,726 short** (47.10% neutral), compared with 53.69% neutral in training. Improved probability levels may partly track this frequency change; the experiment does not isolate feature information from intercept/frequency calibration. An intercept-only, separately calibrated prior control was not included and is a useful future registered comparison.

The momentum and mean-reversion rule balanced accuracies are **0.330830 / 0.339446**. Their paired day-block accuracy intervals versus prior also span zero. Their hard one-hot probability losses are not evidence of calibrated forecast quality. Overall, there is **no demonstrated useful directional discrimination** here, while there is a modest observed evaluation-period probability-scoring improvement. That mixed result supports INCONCLUSIVE rather than forcing a PASS or claiming absence of all predictive information.

At descriptive thresholds 0.30 and 0.35, **18 evaluable signals** occur (8 long, 10 short), with **4/18 correct labels**, across **6 observed days**. Their mean gross barrier outcome is **-5.6800 bps**, already negative before the 2.1 bps reference cost; mean net label outcome is **-7.7800 bps**. These are overlapping, hypothetical label outcomes, not filled trades or portfolio P&L. At 0.40 there are 7 signals, at 0.45 one, and at 0.50/0.55/0.60 none. No threshold was selected or changed.

Sparse threshold intervals must be interpreted cautiously: for the single observation at 0.45, nonempty bootstrap replicates all repeat that observation and the interval collapses. This is **not** strong precision evidence. Empty-replicate counts and all interval details are retained below. No economic edge has been established, and unverified broker sessions/holidays, missing-data assumptions and absent historical bid/ask execution evidence remain blockers to profitability conclusions.

There was **no normalization clipping** in any window; the reported nonfinite predictor rows were excluded (1,792 train, 1,687 calibration, 2,727 test). The original 0.55 confidence gate remains untouched, with the same maximum calibrated long/short probabilities of **0.47834645 / 0.41099432**. Lowering it did not uncover a defensible economic signal in these descriptive results.

### Completed execution and limits

Peak measured worker RSS: **93.32 MiB**. Minimum sampled system-available RAM: **1.447483 GiB**. Minimum free disk: **27.025982 GiB**. All remained inside unchanged limits; no unexpected exception, corruption, checksum mismatch or resource rejection occurred during version 2. The intentional crash returned 86; the remaining workers returned 0, and the orchestration exited 0. All **47 STRAT-2C hashes/mtimes, 721 STRAT-2D file hashes and 181 STRAT-2E file hashes** remained unchanged.

The prior synthetic/regression evidence (135 broad cases, 18 final focused cases and five verifier tests, with overlap between suites) remains preserved. It was not rerun as a new test claim: no implementation changed. Fresh checks were historical reconstruction, hard crash/resume, completed repeat, independent replay, independent metric/label/threshold verification, source preservation, Ruff lint/format and git diff --check; all passed. The earlier three corrected test-helper failures remain documented in the archived evidence.

Evidence: `data/research/strat2d-strat2f-diagnostics-evidence-v2/` contains `summary.json`, `analysis.json`, `independent-verification.json`, prefix/resumed/reference hashes, per-worker resource logs, tests, the generated report, the archived prior report and `final-assessment.json`. Scientific outputs are in the new resumed/reference journals with the same version suffix. The original failed version-1 attempt is not erased. No commits, pushes, broker connections, Docker, holdout access or annual backtests occurred.

**STRAT-2G recommendation:** first resolve exact historical calendar and price/label provenance; then pre-register a calibration-versus-frequency-control experiment on a separately authorized, previously uninspected development window. Do not change the production threshold based on February. Do not expand dates or access the 2026 holdout without authorization. Historical diagnostics are complete; evidence for economically useful predictive signals remains insufficient.

---

## 2. Exact datasets and chronological splits

Only the sealed 58,683 EURUSD M1 rows from 2022-01-01 UTC through 2022-03-01 UTC exclusive are authorized. Fit: January 1-15. Calibration: January 15-February 1. Evaluation: February 1-March 1. Calibration/evaluation discard their first 60 minutes. Labels must end strictly before each window end; missing/closed paths and nonfinite predictors are excluded. The prepared source, sealed model, all code/dependency hashes and label alignment are validated before use.

February was already inspected in STRAT-2D/E; it is not an untouched confirmation sample. January optimizer statistics are not out-of-sample predictions: chronological SGD/calibration consume target labels offline within their respective fitting windows. They must not be read as causal online forecast scores. Only February metrics compare the six frozen models on identical admissible rows. There is one out-of-sample evaluation month, not multiple independent folds.

## 3. Labels and class distributions

The prepared 15-minute barrier labels are 0 neutral/no-trade, 1 long, 2 short, or -1 unavailable. Stops use twice the causal ATR fraction, targets use 3R, stops win same-bar ambiguity, and a constant 2.1 bps round-trip scenario is deducted. The label selects the better positive net directional outcome; it is not simply the sign of a fixed-horizon return. Both directional paths must be observable; label_end is their latest information-use time. No missing candle is filled.

| Window | Neutral | Long | Short | Eligible |
| --- | --- | --- | --- | --- |
| Train | 6557 | 2832 | 2823 | 12212 |
| Calibration | 7577 | 2981 | 3357 | 13915 |
| February | 11985 | 6735 | 6726 | 25446 |

Admission counts: `{"calibration_eligible": 13915, "calibration_nonfinite_predictors": 1687, "calibration_unavailable_label": 205, "outside_windows": 61, "test_eligible": 25446, "test_label_boundary": 1, "test_nonfinite_predictors": 2727, "test_unavailable_label": 383, "train_eligible": 12212, "train_nonfinite_predictors": 1792, "train_unavailable_label": 254}`. Reasons are first-failure partitions; missing predictors may already encode session exclusions and unavailable labels may already encode gaps. Feature statistics cover all decision-window candidates, not only the target-admissible subset.

## 4. Model and feature diagnostics

| Window | Feature | Finite | Nonfinite | Mean | SD | Min | Max | Clipped at +/-20 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| calibration | atr_fraction_14 | 14120 | 1687 | 0.000128 | 0.000066 | 0.000022 | 0.000784 | 0 |
| calibration | body_fraction | 14120 | 1687 | -0.000001 | 0.000105 | -0.001383 | 0.001161 | 0 |
| calibration | ema_trend_spread | 14120 | 1687 | -0.000007 | 0.000112 | -0.000786 | 0.000624 | 0 |
| calibration | range_fraction | 14120 | 1687 | 0.000126 | 0.000091 | 0.000000 | 0.001479 | 0 |
| calibration | return_1 | 14120 | 1687 | -0.000001 | 0.000106 | -0.001381 | 0.001171 | 0 |
| calibration | return_5 | 14120 | 1687 | -0.000005 | 0.000229 | -0.002262 | 0.001500 | 0 |
| calibration | rsi_14 | 14120 | 1687 | 49.537210 | 16.119796 | 0.000000 | 100.000000 | 0 |
| calibration | volatility_20 | 14120 | 1687 | 0.000093 | 0.000047 | 0.000016 | 0.000515 | 0 |
| test | atr_fraction_14 | 25830 | 2727 | 0.000166 | 0.000104 | 0.000021 | 0.000943 | 0 |
| test | body_fraction | 25830 | 2727 | -0.000000 | 0.000145 | -0.002481 | 0.001844 | 0 |
| test | ema_trend_spread | 25830 | 2727 | 0.000004 | 0.000162 | -0.001236 | 0.001620 | 0 |
| test | range_fraction | 25830 | 2727 | 0.000164 | 0.000138 | 0.000000 | 0.002840 | 0 |
| test | return_1 | 25830 | 2727 | 0.000001 | 0.000146 | -0.002501 | 0.001847 | 0 |
| test | return_5 | 25830 | 2727 | 0.000003 | 0.000326 | -0.004049 | 0.003366 | 0 |
| test | rsi_14 | 25830 | 2727 | 50.287990 | 16.807199 | 0.000000 | 100.000000 | 0 |
| test | volatility_20 | 25830 | 2727 | 0.000121 | 0.000075 | 0.000018 | 0.000671 | 0 |
| train | atr_fraction_14 | 12466 | 1792 | 0.000130 | 0.000075 | 0.000016 | 0.000894 | 0 |
| train | body_fraction | 12466 | 1792 | 0.000000 | 0.000113 | -0.001867 | 0.002120 | 0 |
| train | ema_trend_spread | 12466 | 1792 | 0.000003 | 0.000119 | -0.000691 | 0.000891 | 0 |
| train | range_fraction | 12466 | 1792 | 0.000127 | 0.000104 | 0.000000 | 0.002279 | 0 |
| train | return_1 | 12466 | 1792 | 0.000000 | 0.000114 | -0.001846 | 0.001991 | 0 |
| train | return_5 | 12466 | 1792 | 0.000003 | 0.000240 | -0.001705 | 0.001967 | 0 |
| train | rsi_14 | 12466 | 1792 | 50.257676 | 16.579630 | 0.000000 | 100.000000 | 0 |
| train | volatility_20 | 12466 | 1792 | 0.000097 | 0.000056 | 0.000017 | 0.000742 | 0 |

Normalization uses unchanged fixed scales: returns, volatility, ATR, EMA spread, body/range fractions use 0.001; RSI uses 100. Values are clipped to [-20,20], with an intercept appended. Per-feature 12-bin normalized histograms and Welford moments are retained in checkpoints. Nonfinite observations are excluded before model updates; they are not imputed. All raw distribution statistics are descriptive and never used to refit these scales.

| Coefficient on scaled/clipped feature | Neutral | Long | Short |
| --- | --- | --- | --- |
| intercept | 0.656410 | -0.374860 | -0.281550 |
| return_1 | 0.024554 | -0.049623 | 0.025068 |
| return_5 | -0.016091 | -0.069467 | 0.085558 |
| volatility_20 | -0.206427 | 0.137929 | 0.068497 |
| atr_fraction_14 | -0.304478 | 0.204373 | 0.100105 |
| ema_trend_spread | -0.072300 | -0.008286 | 0.080586 |
| rsi_14 | 0.286322 | -0.267724 | -0.018599 |
| body_fraction | 0.022552 | -0.046913 | 0.024361 |
| range_fraction | -0.286814 | 0.197533 | 0.089280 |

SGD seed metadata is 20261027. The deterministic baseline starts at zero weights, uses learning rate 0.005/sqrt(1+n*0.0001), and L2=0.001 on non-intercept coefficients. Final next rate: 0.00335487. Final coefficient norm: 1.084406. Maximum absolute inference logit: 0.911891; maximum normalization error: 2.22e-16. The reconstructed final model/calibrator hash exactly matches the sealed STRAT-2D model.

The checkpoint trace records cumulative optimizer loss, sample counts, coefficient norm, learning rate and temperature after every bounded chunk. Nonstationary one-pass losses need not decrease monotonically. This verifies finite updates and reproducibility, not optimizer convergence to a global optimum or validation of the chosen hyperparameters. Raw coefficients are correlated-feature associations, not causal effects.

## 5. Why confidence is compressed

Final temperature is **1.74987704**. Positive temperature preserves argmax while flattening distributions when T>1. Exact calibration-gradient finite-difference tests pass. No numerical/calibration-sign defect was found. The full 25,830 prediction set has maximum calibrated long/short probabilities 0.47834645/0.41099432; raw maxima are 0.59090761/0.47033767. No calibrated directional prediction reaches 0.55. Calibration suppresses confidence, but the neutral-heavy labels/intercepts and limited directional separation already constrain raw rankings. A class winner is not automatically a high-confidence signal.

These explanations are separate: model arithmetic is reproducible and numerically stable; temperature changes confidence but not discrimination; class imbalance favors neutral predictions; benchmark and uncertainty comparisons below assess information content; the unchanged 0.55 gate is a conservative admission rule. Lowering it cannot itself establish predictive information.

## 6-7. Predictive performance and benchmark comparison

| Model | N | Accuracy | Balanced accuracy | Log loss | Brier [0,2] | Top-label ECE |
| --- | --- | --- | --- | --- | --- | --- |
| majority | 25446 | 0.470997 | 0.333333 | 14.616882 | 1.058005 | 0.529003 |
| mean_reversion | 25446 | 0.273953 | 0.339446 | 20.061429 | 1.452095 | 0.726047 |
| momentum | 25446 | 0.267115 | 0.330830 | 20.250370 | 1.465771 | 0.732885 |
| prior | 25446 | 0.470997 | 0.333333 | 1.066851 | 0.644760 | 0.065933 |
| softmax_cal | 25446 | 0.470880 | 0.333337 | 1.050393 | 0.632482 | 0.011838 |
| softmax_raw | 25446 | 0.470880 | 0.333337 | 1.069986 | 0.646514 | 0.108183 |

Prior probabilities and majority class use training labels only. Momentum predicts long for return_5>0, short for return_5<0 and neutral at zero; mean reversion reverses those directions. No deadband or rule parameter was tuned. Majority and rule baselines use hard one-hot probabilities: zero-class log loss is floored at 1e-12, so their log loss is a deliberately severe hard-probability score; compare their discrimination separately from calibrated probability forecasts.

**majority**: confusion matrix (true rows / predicted columns ordered neutral,long,short) `[[11985, 0, 0], [6735, 0, 0], [6726, 0, 0]]`; precision `[0.4709974062721056, None, None]`; recall `[1.0, 0.0, 0.0]`. Undefined precision is null, not zero.

**mean_reversion**: confusion matrix (true rows / predicted columns ordered neutral,long,short) `[[267, 5736, 5982], [115, 3365, 3255], [112, 3275, 3339]]`; precision `[0.5404858299595142, 0.27189722042663217, 0.2655057251908397]`; recall `[0.02227784730913642, 0.49962880475129917, 0.49643175735950046]`. Undefined precision is null, not zero.

**momentum**: confusion matrix (true rows / predicted columns ordered neutral,long,short) `[[267, 5982, 5736], [115, 3255, 3365], [112, 3339, 3275]]`; precision `[0.5404858299595142, 0.2588263358778626, 0.2646250808015514]`; recall `[0.02227784730913642, 0.48329621380846327, 0.48691644365150166]`. Undefined precision is null, not zero.

**prior**: confusion matrix (true rows / predicted columns ordered neutral,long,short) `[[11985, 0, 0], [6735, 0, 0], [6726, 0, 0]]`; precision `[0.4709974062721056, None, None]`; recall `[1.0, 0.0, 0.0]`. Undefined precision is null, not zero.

**softmax_cal**: confusion matrix (true rows / predicted columns ordered neutral,long,short) `[[11978, 5, 2], [6731, 0, 4], [6719, 3, 4]]`; precision `[0.4710555293377379, 0.0, 0.4]`; recall `[0.9994159365874009, 0.0, 0.0005947071067499256]`. Undefined precision is null, not zero.

**softmax_raw**: confusion matrix (true rows / predicted columns ordered neutral,long,short) `[[11978, 5, 2], [6731, 0, 4], [6719, 3, 4]]`; precision `[0.4710555293377379, 0.0, 0.4]`; recall `[0.9994159365874009, 0.0, 0.0005947071067499256]`. Undefined precision is null, not zero.

February performance by descriptive UTC hour band (not independently verified broker market sessions):

| UTC band | Model | N | Balanced accuracy | Log loss |
| --- | --- | --- | --- | --- |
| utc_00_07 | majority | 7657 | 0.333333 | 10.977350 |
| utc_00_07 | mean_reversion | 7657 | 0.337499 | 21.741792 |
| utc_00_07 | momentum | 7657 | 0.330979 | 21.850050 |
| utc_00_07 | prior | 7657 | 0.333333 | 0.956061 |
| utc_00_07 | softmax_cal | 7657 | 0.333261 | 0.971722 |
| utc_00_07 | softmax_raw | 7657 | 0.333261 | 0.939862 |
| utc_07_13 | majority | 7200 | 0.333333 | 16.682229 |
| utc_07_13 | mean_reversion | 7200 | 0.342947 | 19.000165 |
| utc_07_13 | momentum | 7200 | 0.326575 | 19.422305 |
| utc_07_13 | prior | 7200 | 0.333333 | 1.129671 |
| utc_07_13 | softmax_cal | 7200 | 0.333262 | 1.098831 |
| utc_07_13 | softmax_raw | 7200 | 0.333262 | 1.151350 |
| utc_13_17 | majority | 4800 | 0.333333 | 18.098319 |
| utc_13_17 | mean_reversion | 4800 | 0.348157 | 18.178909 |
| utc_13_17 | momentum | 4800 | 0.315482 | 19.059648 |
| utc_13_17 | prior | 4800 | 0.333333 | 1.172858 |
| utc_13_17 | softmax_cal | 4800 | 0.333351 | 1.117975 |
| utc_13_17 | softmax_raw | 4800 | 0.333351 | 1.177948 |
| utc_17_24 | majority | 5789 | 0.333333 | 13.975407 |
| utc_17_24 | mean_reversion | 5789 | 0.321863 | 20.719686 |
| utc_17_24 | momentum | 5789 | 0.349232 | 20.151697 |
| utc_17_24 | prior | 5789 | 0.333333 | 1.047363 |
| utc_17_24 | softmax_cal | 5789 | 0.333217 | 1.038170 |
| utc_17_24 | softmax_raw | 5789 | 0.333217 | 1.051383 |

## 8. Calibration curves and confidence diagnostics

Reliability curve points use fixed decile bins [lower,upper), with probability 1 included in the last bin. ECE is sum(bin_count/N * abs(observed_fraction - mean_probability)); top-label and one-versus-rest class ECE are reported separately. Brier is the unscaled sum of three squared probability errors, range [0,2]. Proper scores mix discrimination and calibration; a lower Brier/log loss alone does not isolate calibration. [scikit-learn calibration definitions](https://scikit-learn.org/stable/modules/calibration.html).

**softmax_raw**, per-class ECE `[0.10805074836452407, 0.07196195802106087, 0.03553357488027788]`:

| Top-confidence bin | N | Mean confidence | Observed accuracy |
| --- | --- | --- | --- |
| 0.0-0.1 | 0 | NA | NA |
| 0.1-0.2 | 0 | NA | NA |
| 0.2-0.3 | 0 | NA | NA |
| 0.3-0.4 | 27 | 0.389272 | 0.111111 |
| 0.4-0.5 | 604 | 0.470290 | 0.251656 |
| 0.5-0.6 | 18453 | 0.569892 | 0.428873 |
| 0.6-0.7 | 6362 | 0.613323 | 0.615058 |
| 0.7-0.8 | 0 | NA | NA |
| 0.8-0.9 | 0 | NA | NA |
| 0.9-1.0 | 0 | NA | NA |

Class 0 curve points `(mean_probability, observed_fraction, n)`: `[[0.1957395271121868, 0.0, 1], [0.29390672611712754, 1.0, 1], [0.37459788651920467, 0.225, 40], [0.4710160135198977, 0.25084745762711863, 590], [0.5698913403303534, 0.42889659657489704, 18452], [0.6133229904323334, 0.6150581578120088, 6362]]`.

Class 1 curve points `(mean_probability, observed_fraction, n)`: `[[0.18100233849310565, 0.23079997715982414, 17513], [0.21807681651267488, 0.33796532381438044, 7844], [0.32563966071704703, 0.5121951219512195, 82], [0.4573385218612613, 0.0, 6], [0.5909076067261538, 0.0, 1]]`.

Class 2 curve points `(mean_probability, observed_fraction, n)`: `[[0.1923261524772026, 0.0, 7], [0.2283262541245632, 0.26380489347405034, 25299], [0.3281940004607291, 0.36363636363636365, 132], [0.42642818820611883, 0.5, 8]]`.

**softmax_cal**, per-class ECE `[0.01168133862928993, 0.013612851922377728, 0.017599916260023607]`:

| Top-confidence bin | N | Mean confidence | Observed accuracy |
| --- | --- | --- | --- |
| 0.0-0.1 | 0 | NA | NA |
| 0.1-0.2 | 0 | NA | NA |
| 0.2-0.3 | 0 | NA | NA |
| 0.3-0.4 | 166 | 0.384895 | 0.222892 |
| 0.4-0.5 | 24490 | 0.470879 | 0.465578 |
| 0.5-0.6 | 790 | 0.504429 | 0.687342 |
| 0.6-0.7 | 0 | NA | NA |
| 0.7-0.8 | 0 | NA | NA |
| 0.8-0.9 | 0 | NA | NA |
| 0.9-1.0 | 0 | NA | NA |

Class 0 curve points `(mean_probability, observed_fraction, n)`: `[[0.2544066171359091, 0.0, 1], [0.3813791799464725, 0.23255813953488372, 172], [0.47089372102692717, 0.46571090144181676, 24483], [0.5044294363408548, 0.6873417721518987, 790]]`.

Class 1 curve points `(mean_probability, observed_fraction, n)`: `[[0.250669942803297, 0.2627844287639737, 25226], [0.3164938658007953, 0.4953271028037383, 214], [0.42038240902733587, 0.0, 6]]`.

Class 2 curve points `(mean_probability, observed_fraction, n)`: `[[0.2765513207851531, 0.2608608205953339, 24860], [0.31389634168919045, 0.41196581196581195, 585], [0.4109943218315877, 0.0, 1]]`.

**prior**, per-class ECE `[0.06593348137910103, 0.03277509576362311, 0.033158385615833834]`:

| Top-confidence bin | N | Mean confidence | Observed accuracy |
| --- | --- | --- | --- |
| 0.0-0.1 | 0 | NA | NA |
| 0.1-0.2 | 0 | NA | NA |
| 0.2-0.3 | 0 | NA | NA |
| 0.3-0.4 | 0 | NA | NA |
| 0.4-0.5 | 0 | NA | NA |
| 0.5-0.6 | 25446 | 0.536931 | 0.470997 |
| 0.6-0.7 | 0 | NA | NA |
| 0.7-0.8 | 0 | NA | NA |
| 0.8-0.9 | 0 | NA | NA |
| 0.9-1.0 | 0 | NA | NA |

Class 0 curve points `(mean_probability, observed_fraction, n)`: `[[0.5369308876512067, 0.4709974062721056, 25446]]`.

Class 1 curve points `(mean_probability, observed_fraction, n)`: `[[0.23190304618403074, 0.2646781419476539, 25446]]`.

Class 2 curve points `(mean_probability, observed_fraction, n)`: `[[0.23116606616440666, 0.2643244517802405, 25446]]`.

Confidence histogram across all ready predictions (not only labeled evaluation rows), bins 0.0-0.1 through 0.9-1.0: `[0, 0, 0, 166, 24855, 809, 0, 0, 0, 0]`.

## 9. Descriptive threshold sensitivity

Candidate thresholds were fixed before this run. All rows below use February development data only and the same admissible labels, calibrated probabilities, class-dominance, positive ATR, known-calendar horizon and terminal-window checks. No candidate was selected or installed. Target-admissibility is for retrospective evaluation only, not a live trading filter. Precision means the selected direction equals the 3-class label; it is not the probability of a positive trade return. Fixed 15-minute spacing is an illustrative cooldown count, not a position/capital simulation.

| Threshold | Signals | Long | Short | Precision | Fraction | 15m spaced | Mean gross bps | Mean net label bps |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.3 | 18 | 8 | 10 | 0.222222 | 0.000707 | 9 | -5.680044 | -7.780044 |
| 0.35 | 18 | 8 | 10 | 0.222222 | 0.000707 | 9 | -5.680044 | -7.780044 |
| 0.4 | 7 | 6 | 1 | 0.000000 | 0.000275 | 5 | -7.463519 | -9.563519 |
| 0.45 | 1 | 1 | 0 | 0.000000 | 0.000039 | 1 | -6.787574 | -8.887574 |
| 0.5 | 0 | 0 | 0 | NA | 0.000000 | 0 | NA | NA |
| 0.55 | 0 | 0 | 0 | NA | 0.000000 | 0 | NA | NA |
| 0.6 | 0 | 0 | 0 | NA | 0.000000 | 0 | NA | NA |

Threshold uncertainty: 2 adjacent-day blocks, 95% exploratory percentile intervals:

| Threshold | Precision lower | Precision upper | Net bps lower | Net bps upper | Nonempty replicates |
| --- | --- | --- | --- | --- | --- |
| 0.3 | 0.000000 | 0.375000 | -9.256724 | -3.667276 | 1000 |
| 0.35 | 0.000000 | 0.375000 | -9.256724 | -3.667276 | 1000 |
| 0.4 | 0.000000 | 0.000000 | -14.549128 | -7.697241 | 998 |
| 0.45 | 0.000000 | 0.000000 | -8.887574 | -8.887574 | 659 |
| 0.5 | NA | NA | NA | NA | 0 |
| 0.55 | NA | NA | NA | NA | 0 |
| 0.6 | NA | NA | NA | NA | 0 |

Threshold uncertainty: 5 adjacent-day blocks, 95% exploratory percentile intervals:

| Threshold | Precision lower | Precision upper | Net bps lower | Net bps upper | Nonempty replicates |
| --- | --- | --- | --- | --- | --- |
| 0.3 | 0.000000 | 0.375000 | -9.249424 | -3.565956 | 1000 |
| 0.35 | 0.000000 | 0.375000 | -9.249424 | -3.565956 | 1000 |
| 0.4 | 0.000000 | 0.000000 | -13.350265 | -7.808301 | 995 |
| 0.45 | 0.000000 | 0.000000 | -8.887574 | -8.887574 | 657 |
| 0.5 | NA | NA | NA | NA | 0 |
| 0.55 | NA | NA | NA | NA | 0 |
| 0.6 | NA | NA | NA | NA | 0 |

## 10. Transaction-cost feasibility

Reference round-trip costs remain 1.0 spread + 2*0.25 slippage + 0.6 commission = **2.1 bps**. Gross directional outcome is reconstructed from the selected barrier net label plus those costs; this is a reference-price path outcome, not an actual filled portfolio return or a fixed-horizon price change. Capital, financing, FX conversion, bid/ask history and gap execution are not estimated here. Historical actual fills remain zero.

| Threshold | Net-positive fraction | Conditional break-even win fraction | Signals/observed day | Net at costs 1.1/1.6/2.1/3.1/5.6 bps |
| --- | --- | --- | --- | --- |
| 0.3 | 0.222222 | 0.656050 | 0.750000 | {"1.1": -6.780043780059152, "1.6": -7.280043780059152, "2.1": -7.780043780059152, "3.1": -8.780043780059152, "5.6": -11.280043780059152} |
| 0.35 | 0.222222 | 0.656050 | 0.750000 | {"1.1": -6.780043780059152, "1.6": -7.280043780059152, "2.1": -7.780043780059152, "3.1": -8.780043780059152, "5.6": -11.280043780059152} |
| 0.4 | 0.000000 | NA | 0.291667 | {"1.1": -8.563518664505247, "1.6": -9.063518664505247, "2.1": -9.563518664505247, "3.1": -10.563518664505247, "5.6": -13.063518664505246} |
| 0.45 | 0.000000 | NA | 0.041667 | {"1.1": -7.887573802284896, "1.6": -8.387573802284896, "2.1": -8.887573802284896, "3.1": -9.887573802284896, "5.6": -12.387573802284896} |
| 0.5 | NA | NA | 0.000000 | {"1.1": null, "1.6": null, "2.1": null, "3.1": null, "5.6": null} |
| 0.55 | NA | NA | 0.000000 | {"1.1": null, "1.6": null, "2.1": null, "3.1": null, "5.6": null} |
| 0.6 | NA | NA | 0.000000 | {"1.1": null, "1.6": null, "2.1": null, "3.1": null, "5.6": null} |

Cost scenarios correspond to (spread, one-way slippage, commission round trip): (0.5,0,0.6), (0.5,0.25,0.6), (1,0.25,0.6), (1.5,0.5,0.6), (3,1,0.6). These are predeclared hypothetical sensitivity cases, not historical quotes. Break-even uses (mean absolute nonpositive gross outcome + cost)/(mean positive gross outcome + mean absolute nonpositive gross outcome); it is conditional on this selected sample, undefined without both groups, and must not be confused with the 3-class classification accuracy. Repeated overlapping barrier signals double-count market exposure, so summing their net labels is not a realized P&L backtest. Economic conclusions remain provisional/blocked by calendar and execution-data uncertainty.

## 11. Time dependence and statistical uncertainty

There are 24 observed UTC-day blocks, not 25,446 independent observations. All observations within a day remain together. Paired circular blocks of 2 and 5 consecutive observed days are resampled with a fixed seed, 1,000 repetitions each; comparisons use the same sampled days across models. Label overlap within days is preserved and multi-day blocks address adjacent-day dependence. Weekend gaps, end-to-start wrap-around, short span, residual longer dependence, sparse signals and block-length sensitivity limit inference. No iid minute-level confidence interval is used. [Time-series block bootstrap reference](https://bashtage.github.io/arch/doc/bootstrap/timeseries-bootstraps.html).

Paired differences, first minus second, using 2-day blocks. Negative loss differences and positive balanced-accuracy differences favor the first model:

| Comparison | Loss lower | Loss upper | Balanced accuracy lower | Balanced accuracy upper |
| --- | --- | --- | --- | --- |
| mean_reversion-prior | 18.381446 | 19.454172 | -0.002093 | 0.014090 |
| momentum-prior | 18.602971 | 19.579483 | -0.010645 | 0.005258 |
| softmax_cal-prior | -0.028792 | -0.008013 | -0.000231 | 0.000300 |
| softmax_cal-softmax_raw | -0.033937 | -0.008765 | 0.000000 | 0.000000 |
| softmax_raw-prior | -0.001979 | 0.008581 | -0.000231 | 0.000300 |

Paired differences, first minus second, using 5-day blocks. Negative loss differences and positive balanced-accuracy differences favor the first model:

| Comparison | Loss lower | Loss upper | Balanced accuracy lower | Balanced accuracy upper |
| --- | --- | --- | --- | --- |
| mean_reversion-prior | 18.373441 | 19.452482 | -0.001105 | 0.012199 |
| momentum-prior | 18.582488 | 19.658351 | -0.008037 | 0.004617 |
| softmax_cal-prior | -0.029008 | -0.007522 | -0.000220 | 0.000309 |
| softmax_cal-softmax_raw | -0.035553 | -0.007567 | 0.000000 | 0.000000 |
| softmax_raw-prior | -0.001328 | 0.007439 | -0.000220 | 0.000309 |

The overall INCONCLUSIVE classification is conservative because the single previously inspected month and unresolved historical calendar do not provide adequate confirmatory/economic evidence. The numerical benchmark results above must still be read directly, including any underperformance. This is not a claim that a useful edge exists, and no threshold recommendation follows.

## 12. Resource and runtime measurements

Policy: existing explicit offline profile, 1.30 GiB available physical RAM, 256 MiB worker RSS ceiling, 15 GiB free disk, single numerical/Arrow threads and 1,024-row batches. The default 1.5 GiB requirement remains unchanged. Worker guards and the parent real-PID poll preserve fail-closed stops. Source data and full history are never loaded together; only one Arrow batch, bounded metrics, a maximum of 64 daily sufficient-statistic records, per-chunk optimizer trace and bounded event buffer are retained.

| Run | Peak worker RSS MiB | Minimum available RAM GiB | Min disk GiB | Elapsed seconds |
| --- | --- | --- | --- | --- |
| diagnostics-crash | 92.335938 | 1.514713 | 27.053947 | NA |
| diagnostics-reference | 93.316406 | 1.447483 | 27.025990 | 52.220330 |
| diagnostics-repeat | 78.578125 | 1.515255 | 27.042019 | 3.349564 |
| diagnostics-resume | 91.769531 | 1.491955 | 27.042027 | 30.688256 |

Resource minima are sampled; RSS is the OS working-set high-water mark. OS background fluctuations are not attributed to model code. Parent verification/bootstrap metrics are separately preserved in summary.json.

## 13. Tests and independent verification

Pytest results: `{"tests": 135, "failures": 0, "errors": 0, "skipped": 0}`. Synthetic tests cover analytic metric oracles, probability normalization/extremes, calibration-gradient finite differences, fixed baselines, label purge/embargo, bounded feature statistics, threshold costs/cooldown, deterministic day-block bootstrap, full model reconstruction, checkpoint interrupts, repeated resume and byte-identical replay. Existing Phase 6 causal-feature, chunk/month, future perturbation and holdout-exclusion regressions are included.

The broad run passed 135 tests. A follow-up policy test initially failed three cases because test-helper imports were missing; those imports were corrected and all 18 focused STRAT-2F cases passed in a new run. These include the original 15 cases plus three new label/cost identity rejection cases. Failed and final XML evidence is preserved; no unresolved test failure remains.

Fresh static checks: Ruff lint/format and git diff --check. Historical execution, if successful, includes an actual os._exit(86) before commit 35, new-process resume, repeated completed resume, a separate independent uninterrupted process and every committed hash comparison. The independent stdlib event verifier recalculates confusion matrices, loss/Brier sums, probability normalization, chronology, label purge/alignment and threshold counts from each published evaluation event. Hash equality alone is not used as proof of metric correctness.

Historical generation **59**, complete=true; **181/181** matching committed file hashes. Canonical state hash `85111038e84964dc649fe0cec0e14b2efb16d6dd3540e684c21e830cbcee8f1d`. Model reconstruction exactly matches the old frozen model. Preservation: 47 STRAT-2C files, 721 sealed STRAT-2D files, 181 STRAT-2E files.

## 14. PASS/FAIL assessment

| Subsystem | Status |
| --- | --- |
| Synthetic arithmetic/causality/resource regressions | See test totals above |
| Bounded historical reconstruction/metrics | PASS |
| Checkpoint crash/resume/replay and independent numerical verification | PASS |
| Calendar/economic profitability evidence | BLOCKED; historical calendar remains unverified |
| Measurable predictive advantage with adequate confirmatory evidence | INCONCLUSIVE Ă˘â‚¬â€ť insufficient evidence or unresolved uncertainty |

## 15. Remaining blockers

One inspected development month, overlapping targets, sparse directional signals, unverified historical broker sessions/holidays, no bid/ask spread history and no actual historical fills prevent supported profitability claims. Session bands are descriptive UTC partitions. Alternative data windows, estimators or threshold selections were not tried. Full convergence and broad generalization are unproven.

## 16. Recommended STRAT-2G direction

Resolve historical calendar and label/price-type provenance first. If more research is warranted, pre-register a new, explicitly authorized chronological development window and benchmark/calibration comparison before observing its outcomes. Investigate whether calibration should shrink toward training priors or be fit on larger independent past blocks; do not install any change based on this February diagnostic. Preserve the current production threshold until separately validated. Do not access the 2026 holdout or expand dates without authorization.

Files added: src/trading/research/strat2f/{__init__,metrics,pipeline,analysis,verify,report}.py, tools/strat2f_validation.py, tests/test_strat2f.py and this report. No core model/strategy/safety configuration was edited. Evidence, journals, tests and comparisons are versioned under data/research/strat2d-strat2f-*. No commit, push, live/broker connection, Docker startup, annual backtest or source/published-artifact modification occurred.

Command:

```powershell
.\.venv\Scripts\python.exe -B -m tools.strat2f_validation
```
