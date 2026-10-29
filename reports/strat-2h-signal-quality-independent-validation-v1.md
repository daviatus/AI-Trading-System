# STRAT-2H signal quality and independent validation preparation v1

## 1. Executive summary

Engineering execution: **PASS**. Research conclusion: **INCONCLUSIVE — signal quality or economic evidence remains insufficient**.

January–February 2022 is repeatedly examined exploratory development data. No new historical period was accessed. The selected model, temperature, features, costs and 0.55 production gate remain frozen. No fitting, trading or portfolio simulation occurs in STRAT-2H.

## 2. Frozen candidate and protocol

Selected `linear12 / h15_cost`, schema `strat2g-model-state-v1`, kernel `online_softmax_v1`, seed 20261027. Learning rate 0.003, L2 0.01, 12 clipped causal features with unit scales. State hash `bc72d1443ecca6ca69040ef814c521e23077d45a12ebb4f4a7fe4b95b2c967a6`.

Frozen journal `data/research/strat2d-strat2g-resumed-v2`, generation 59; manifest `547c3a16b7fb030f7daed32fbbffc5307d8bfa407881fe04786d169f18b9a491`, receipt `2bf8ddcc8f762dfacc4460845a04a68dbc88aea49073bcc1ec1d3dfb78503369`. Model training used 11,675 rows; temperature calibration used 13,428 rows. Temperature exp(0.325014157440135) = 1.38405024.

Model features: `return_1_bps_div10`, `return_5_bps_div10`, `return_15_bps_div10`, `return_30_bps_div10`, `return_5_vol_normalized`, `sigma20_bps_div5`, `range_bps_div10`, `price_position20`, `mean_reversion20`, `completed_5m_return_div001`, `utc_time_sin`, `utc_time_cos`.

Training Jan 1–15; calibration Jan 15–Feb 1; selection Feb 1–15; evaluation Feb 15–Mar 1, UTC exclusive right boundaries. All nontraining roles have 60-minute forward embargo. All labels require a common 30-minute contiguous safe path and common end strictly before the role's right boundary. Target entry is next bar open, exit is close of the fifteenth subsequent bar; LONG/SHORT beyond ±2.1 bps, otherwise FLAT.

G selection: selection-only calibrated log loss; minimum 1,000 rows and five observed days; simplicity ordering reference8, linear12, interaction16; more complexity needs >0.005 loss improvement. Nine configurations were examined. H never selects again. January frozen-model scores are post-fit descriptive reuse, not generalization estimates.

Protocols: `config/strat2h-diagnostics-protocol-v1.json` and `config/strat2h-confirmation-protocol-v1.json`. H protocol hash: `25be0a4cd4b192831ab4734bcd49efb5c25d27c006f9a3303479733670760d93`.

## 3. Signal quality and causal movement diagnostics

Underlying return distributions by argmax class remain unsigned price movements. Directional probability bins and forced-direction diagnostics use LONG when pLONG≥pSHORT, otherwise SHORT, including observations dominated by FLAT. Gated tables additionally require directional probability≥pFLAT. These forced directions are diagnostic counterfactuals, never orders. MFE/MAE use observed highs/lows inside the 15-bar path, not attainable fills.

| Phase | Rows | Classes FLAT/LONG/SHORT | BA calibrated | Log loss cal | Log loss reference | Log loss prior |
| --- | --- | --- | --- | --- | --- | --- |
| train | 11675 | [5670, 3008, 2997] | 0.346225 | 1.017418 | 1.044537 | 1.049253 |
| calibration | 13428 | [6730, 3184, 3514] | 0.345870 | 1.010529 | 1.036216 | 1.039421 |
| selection | 12075 | [5234, 3471, 3370] | 0.350173 | 1.053147 | 1.068505 | 1.082422 |
| evaluation | 12304 | [4792, 3741, 3771] | 0.350703 | 1.084392 | 1.085373 | 1.110409 |

| Predicted class | n | Mean underlying bps | Mean abs bps | Up fraction | Observed high bps | Observed low adverse bps |
| --- | --- | --- | --- | --- | --- | --- |
| FLAT | 11632 | -0.003824 | 4.018208 | 0.502149 | 3.991103 | 3.898348 |
| LONG | 393 | 2.318872 | 5.609807 | 0.628499 | 6.367200 | 4.319385 |
| SHORT | 279 | -1.854335 | 4.591848 | 0.379928 | 3.970405 | 6.182779 |

Complete fixed-bin histograms, class reliability, feature moments, gap-age, UTC-session and volatility-regime groups are in `analysis.json`. Return histogram edges (bps): [-50, -20, -10, -5, -2.1, -1, 0, 1, 2.1, 5, 10, 20, 50].

## 4. Raw/calibrated probabilities and confidence gate

Scalar temperature >1 softens logits without changing the argmax class. Class imbalance and weak directional logit separation also constrain confidence; a high gate can legitimately yield no signals. Neither threshold nor calibrator is changed.

Frozen model audit (FLAT/LONG/SHORT): {"calibration_priors": [0.5011915400655347, 0.237116473041406, 0.2616919868930593], "coefficient_l2_by_class": [0.5076229516508923, 0.2382240836098123, 0.3711829186020657], "intercepts": [0.625084610470833, -0.34369060806923746, -0.28139400240159623], "probability_gate_log_odds": 0.20067069546215124, "temperature": 1.3840502404504704, "training_priors": [0.48565310492505354, 0.257644539614561, 0.25670235546038545]}. The training/calibration priors and learned intercept favor FLAT. The 0.55 gate requires directional versus all-other-class log odds >= log(0.55/0.45). Raw maxima below that gate would establish that temperature alone cannot explain missing signals: learned feature logits are also insufficiently directional. The numerical audit and probability comparison do not imply an implementation error or justify retuning.

| View | Max pFLAT | Max pLONG | Max pSHORT | Log loss | Top-label ECE |
| --- | --- | --- | --- | --- | --- |
| raw | 0.806876 | 0.443710 | 0.402011 | 1.111986 | 0.124112 |
| cal | 0.699523 | 0.412021 | 0.382596 | 1.084392 | 0.071897 |

| Directional probability bin | n | Directional class precision | Sign hit rate | Mean signed bps | Net @2.1bps |
| --- | --- | --- | --- | --- | --- |
| [0.15,0.20) | 1058 | 0.257089 | 0.525520 | -0.091187 | -2.191187 |
| [0.20,0.25) | 2605 | 0.245298 | 0.511324 | -0.132801 | -2.232801 |
| [0.25,0.30) | 2819 | 0.299397 | 0.473927 | -0.100717 | -2.200717 |
| [0.30,0.35) | 4560 | 0.376535 | 0.528070 | 0.551839 | -1.548161 |
| [0.35,0.40) | 1257 | 0.454256 | 0.598250 | 1.456783 | -0.643217 |
| [0.40,0.45) | 5 | 0.400000 | 0.400000 | 0.973234 | -1.126766 |

Descriptive thresholds below are predeclared and were not selected using their outcomes:

| Gate | n | LONG | SHORT | Precision | Net bps @2.1 | Clusters | Nonoverlap windows | Per 1,000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.3 | 672 | 393 | 279 | 0.498512 | 0.026006 | 78 | 137 | 54.616385 |
| 0.35 | 426 | 216 | 210 | 0.528169 | 0.660645 | 83 | 118 | 34.622887 |
| 0.4 | 5 | 5 | 0 | 0.400000 | -1.126766 | 1 | 1 | 0.406372 |
| 0.45 | 0 | 0 | 0 | unavailable | unavailable | 0 | 0 | 0.000000 |
| 0.5 | 0 | 0 | 0 | unavailable | unavailable | 0 | 0 | 0.000000 |
| 0.55 | 0 | 0 | 0 | unavailable | unavailable | 0 | 0 | 0.000000 |

## 5. Economic feasibility

Base round-trip cost is 1.0 bps spread + 2×0.25 bps slippage + 0.6 bps commission = 2.1 bps. Sensitivities 1.1/2.1/3.1/5.6 bps correspond to spread/slippage scenarios, not observed bid/ask costs. Break-even gross movement is the chosen total cost; at EURUSD price P, a 2.1 bps move costs P×0.00021 price units. One pip is 0.0001 price units, not one basis point. Break-even hit rate requires both gain/loss magnitudes: (loss+cost)/(gain+loss); accuracy alone is insufficient.

Counterfactual all-observation directional rule: n=12304, mean gross=0.294708 bps; mean MFE=4.086628, MAE=3.943498, volatility-scaled signed movement=0.018432.

| Round-trip bps | Mean hypothetical net bps |
| --- | --- |
| 1.1 | -0.805292 |
| 2.1 | -1.805292 |
| 3.1 | -2.805292 |
| 5.6 | -5.305292 |

Empirical positive/nonpositive magnitudes imply break-even positive fractions {'1.1': 0.6177848451883662, '2.1': 0.7404318315303352, '3.1': 0.8630788178723043, '5.6': 1.169696283727227}. These hold conditional magnitudes fixed; they do not establish attainability. Zero returns are included in the nonpositive group.

Cost components (spread / slippage per side / round-trip commission): [{'spread_bps': 0.5, 'slippage_per_side_bps': 0, 'commission_roundtrip_bps': 0.6, 'total_bps': 1.1}, {'spread_bps': 1, 'slippage_per_side_bps': 0.25, 'commission_roundtrip_bps': 0.6, 'total_bps': 2.1}, {'spread_bps': 1.5, 'slippage_per_side_bps': 0.5, 'commission_roundtrip_bps': 0.6, 'total_bps': 3.1}, {'spread_bps': 3, 'slippage_per_side_bps': 1, 'commission_roundtrip_bps': 0.6, 'total_bps': 5.6}].

These overlapping hypothetical positions ignore capital, mutually exclusive positions and executable bid/ask prices. Cluster counts and greedily thinned 15-minute nonoverlap counts are turnover proxies; nonoverlap outcomes are not separately backtested. No realized portfolio return was generated. Historical calendar and price-side uncertainty block realizable-profit conclusions.

## 6. Temporal stability

January includes fitted/calibration observations and cannot be compared to February as independent performance. Weekly, month, fixed UTC-hour bands and predeclared causal sigma20 regimes (0.5/1/2 bps cutoffs) are descriptive. No regime or session was chosen for deployment. Full per-class calibration, priors, feature means/standard deviations/clipping and gap-age (<120 versus ≥120 bars since reset) are retained for every group.

| Group | n | BA | Log loss | Top ECE | Forced net bps |
| --- | --- | --- | --- | --- | --- |
| gap_age:evaluation:120plus | 11704 | 0.351037 | 1.087022 | 0.071612 | -1.805607 |
| gap_age:evaluation:under120 | 600 | 0.333333 | 1.033077 | 0.084166 | -1.799148 |
| month:2022-01 | 25103 | 0.345970 | 1.013733 | 0.025986 | -1.990088 |
| month:2022-02 | 24379 | 0.350499 | 1.068916 | 0.051178 | -2.016533 |
| session:evaluation:00_07 | 3852 | 0.333333 | 1.029870 | 0.065311 | -2.319714 |
| session:evaluation:07_13 | 3600 | 0.344419 | 1.123972 | 0.087349 | -1.831233 |
| session:evaluation:13_17 | 2400 | 0.363533 | 1.100730 | 0.055376 | -0.985398 |
| session:evaluation:17_24 | 2452 | 0.334017 | 1.095939 | 0.075727 | -1.761574 |
| volatility:evaluation:0 | 805 | 0.332773 | 0.801697 | 0.170241 | -2.793806 |
| volatility:evaluation:1 | 4006 | 0.341104 | 1.028988 | 0.025039 | -1.876221 |
| volatility:evaluation:2 | 5614 | 0.345449 | 1.135614 | 0.105138 | -2.165716 |
| volatility:evaluation:3 | 1879 | 0.347372 | 1.170583 | 0.185670 | -0.153712 |
| week:2022-W01 | 5760 | 0.341676 | 1.034961 | 0.030093 | -2.128003 |
| week:2022-W02 | 5915 | 0.351182 | 1.000335 | 0.038919 | -2.026943 |
| week:2022-W03 | 6206 | 0.342624 | 1.011472 | 0.038415 | -1.925768 |
| week:2022-W04 | 5974 | 0.349031 | 1.005643 | 0.040937 | -1.822626 |
| week:2022-W05 | 6059 | 0.347236 | 1.041867 | 0.055665 | -2.261889 |
| week:2022-W06 | 6003 | 0.350952 | 1.055417 | 0.041229 | -2.332762 |
| week:2022-W07 | 6245 | 0.352127 | 1.022658 | 0.051024 | -1.920239 |
| week:2022-W08 | 6059 | 0.351605 | 1.124543 | 0.109596 | -1.640609 |
| week:2022-W09 | 1261 | 0.340175 | 1.185665 | 0.201178 | -1.896273 |

Feature drift uses pooled month standard deviations; it is descriptive post-fit data reuse, not an independent-sample significance test.

| Feature | January mean | February mean | Standardized shift | Jan clipped | Feb clipped |
| --- | --- | --- | --- | --- | --- |
| return_1_bps_div10 | -0.000457 | 0.000493 | 0.007222 | 0.000000 | 0.000000 |
| return_5_bps_div10 | -0.002123 | 0.002397 | 0.015600 | 0.000000 | 0.000000 |
| return_15_bps_div10 | -0.006180 | 0.007834 | 0.028108 | 0.000000 | 0.000287 |
| return_30_bps_div10 | -0.011427 | 0.016476 | 0.041068 | 0.000000 | 0.001066 |
| return_5_vol_normalized | -0.015279 | 0.006088 | 0.020998 | 0.000000 | 0.000000 |
| sigma20_bps_div5 | 0.194166 | 0.246715 | 0.407199 | 0.000000 | 0.000000 |
| range_bps_div10 | 0.131024 | 0.168965 | 0.315294 | 0.000000 | 0.000000 |
| price_position20 | 0.003383 | 0.010844 | 0.011884 | 0.000000 | 0.000000 |
| mean_reversion20 | 0.011527 | -0.007795 | -0.033994 | 0.000000 | 0.000000 |
| completed_5m_return_div001 | -0.002176 | 0.002302 | 0.015331 | 0.000000 | 0.000000 |
| utc_time_sin | 0.028518 | 0.043183 | 0.019961 | 0.000000 | 0.000000 |
| utc_time_cos | -0.156238 | -0.155876 | 0.000549 | 0.000000 | 0.000000 |

## 7. Statistical uncertainty and selection bias

1,000 paired circular observed-UTC-day block replicates, lengths 2 and 5 days, seed 20261028+length. Minute labels remain together inside day blocks; 15-minute overlapping outcomes are never treated as independent. This models some serial dependence, not all structural breaks. Twelve final observed days give few effective blocks. Threshold intervals require at least five signal-bearing days and ten observed days; sparse cells have no interval. No IID minute-based standard errors. The nine G configurations and repeated reuse of this period prevent independent confirmation and make subgroup effects exploratory.

```json
{
  "evaluation": {
    "blocks": {
      "2": {
        "ba_vs_prior": {
          "lower": 0.013783143925212344,
          "upper": 0.02138240555122631,
          "valid_replicates": 1000
        },
        "ba_vs_reference": {
          "lower": 0.010276539833815029,
          "upper": 0.019489472028382294,
          "valid_replicates": 1000
        },
        "forced_net": {
          "lower": -2.047460516260196,
          "upper": -1.5119606138199928,
          "valid_replicates": 1000
        },
        "loss_vs_prior": {
          "lower": -0.038939272555681186,
          "upper": -0.012649206000239211,
          "valid_replicates": 1000
        },
        "loss_vs_reference": {
          "lower": -0.025703001818821236,
          "upper": 0.020623035402649748,
          "valid_replicates": 1000
        },
        "threshold_net:0.3": {
          "lower": -1.1185024274596609,
          "upper": 1.0798657076183984,
          "valid_replicates": 1000
        },
        "threshold_net:0.35": {
          "lower": -0.7563882633393187,
          "upper": 1.9553575168616635,
          "valid_replicates": 1000
        },
        "threshold_net:0.4": {
          "status": "INSUFFICIENT_SIGNAL_DAYS"
        },
        "threshold_net:0.45": {
          "status": "INSUFFICIENT_SIGNAL_DAYS"
        },
        "threshold_net:0.5": {
          "status": "INSUFFICIENT_SIGNAL_DAYS"
        },
        "threshold_net:0.55": {
          "status": "INSUFFICIENT_SIGNAL_DAYS"
        }
      },
      "5": {
        "ba_vs_prior": {
          "lower": 0.013705314220223641,
          "upper": 0.020926132920605557,
          "valid_replicates": 1000
        },
        "ba_vs_reference": {
          "lower": 0.010516809418910571,
          "upper": 0.018997902193422844,
          "valid_replicates": 1000
        },
        "forced_net": {
          "lower": -2.0099304237432345,
          "upper": -1.5679657651886814,
          "valid_replicates": 1000
        },
        "loss_vs_prior": {
          "lower": -0.0401902541116086,
          "upper": -0.012840063567975957,
          "valid_replicates": 1000
        },
        "loss_vs_reference": {
          "lower": -0.026178937594073082,
          "upper": 0.026048858242892357,
          "valid_replicates": 1000
        },
        "threshold_net:0.3": {
          "lower": -0.9570048235920874,
          "upper": 0.9633667740626346,
          "valid_replicates": 1000
        },
        "threshold_net:0.35": {
          "lower": -0.5683668981558518,
          "upper": 1.696586005506853,
          "valid_replicates": 1000
        },
        "threshold_net:0.4": {
          "status": "INSUFFICIENT_SIGNAL_DAYS"
        },
        "threshold_net:0.45": {
          "status": "INSUFFICIENT_SIGNAL_DAYS"
        },
        "threshold_net:0.5": {
          "status": "INSUFFICIENT_SIGNAL_DAYS"
        },
        "threshold_net:0.55": {
          "status": "INSUFFICIENT_SIGNAL_DAYS"
        }
      }
    },
    "days": 12,
    "status": "EXPLORATORY_ONLY"
  },
  "selection": {
    "blocks": {
      "2": {
        "ba_vs_prior": {
          "lower": 0.014380042951282404,
          "upper": 0.020144790861735068,
          "valid_replicates": 1000
        },
        "ba_vs_reference": {
          "lower": 0.011849695049986312,
          "upper": 0.017448014779700826,
          "valid_replicates": 1000
        },
        "forced_net": {
          "lower": -2.480215647407409,
          "upper": -1.961563558972024,
          "valid_replicates": 1000
        },
        "loss_vs_prior": {
          "lower": -0.03709462514868987,
          "upper": -0.019931508509302705,
          "valid_replicates": 1000
        },
        "loss_vs_reference": {
          "lower": -0.027915496576249428,
          "upper": -0.00047385410687567124,
          "valid_replicates": 1000
        },
        "threshold_net:0.3": {
          "lower": -2.570971793643872,
          "upper": -0.7260190320447857,
          "valid_replicates": 1000
        },
        "threshold_net:0.35": {
          "lower": -2.6532510849520037,
          "upper": 0.3175811264101027,
          "valid_replicates": 1000
        },
        "threshold_net:0.4": {
          "status": "INSUFFICIENT_SIGNAL_DAYS"
        },
        "threshold_net:0.45": {
          "status": "INSUFFICIENT_SIGNAL_DAYS"
        },
        "threshold_net:0.5": {
          "status": "INSUFFICIENT_SIGNAL_DAYS"
        },
        "threshold_net:0.55": {
          "status": "INSUFFICIENT_SIGNAL_DAYS"
        }
      },
      "5": {
        "ba_vs_prior": {
          "lower": 0.014910264033708787,
          "upper": 0.018974292827905193,
          "valid_replicates": 1000
        },
        "ba_vs_reference": {
          "lower": 0.011942448416286666,
          "upper": 0.016379032377737135,
          "valid_replicates": 1000
        },
        "forced_net": {
          "lower": -2.3811808267153665,
          "upper": -2.06427077086676,
          "valid_replicates": 1000
        },
        "loss_vs_prior": {
          "lower": -0.03546154332088825,
          "upper": -0.023087581831814363,
          "valid_replicates": 1000
        },
        "loss_vs_reference": {
          "lower": -0.02398869569513179,
          "upper": -0.005848489768719152,
          "valid_replicates": 1000
        },
        "threshold_net:0.3": {
          "lower": -2.428029102114833,
          "upper": -0.9150283458103836,
          "valid_replicates": 1000
        },
        "threshold_net:0.35": {
          "lower": -2.4723678688225457,
          "upper": 0.0871538735312828,
          "valid_replicates": 1000
        },
        "threshold_net:0.4": {
          "status": "INSUFFICIENT_SIGNAL_DAYS"
        },
        "threshold_net:0.45": {
          "status": "INSUFFICIENT_SIGNAL_DAYS"
        },
        "threshold_net:0.5": {
          "status": "INSUFFICIENT_SIGNAL_DAYS"
        },
        "threshold_net:0.55": {
          "status": "INSUFFICIENT_SIGNAL_DAYS"
        }
      }
    },
    "days": 12,
    "status": "EXPLORATORY_ONLY"
  }
}
```

## 8. Historical calendar verification

**UNVERIFIED** source-broker historical sessions/holidays and price-side semantics. The current weekly calendar and G conservative rollover/weekend buffers are unchanged. Inherited quality: 301 internal gaps / 23,516 minutes; 378 expected-open missing minutes and 16 out-of-session candles under the current hypothesis. All 16 closing-boundary observations are at 21:59 UTC / 16:59 New York. Their existence does not prove the broker was closed or the timestamps wrong.

[Spotware's specification](https://help.ctrader.com/open-api/model-messages/) distinguishes UTC-minute bar timestamps from timezone-specific symbol intervals, with inclusive start and exclusive end. Local conversion in `src/trading/ctrader/client.py` uses UTC; registration filters M1 demo-source data with unknown price side. Code lineage supports the units, but does not independently attest the stored source history.

[NIST DST rules](https://www.nist.gov/pml/time-and-frequency-division/popular-links/daylight-saving-time-dst) imply no New York DST change during January–February 2022; UTC−5 applies throughout. A UTC/server-time shift is not inferred from gaps.

A [dated December 2021 broker notice](https://www.ic.com/blog/holiday-trading-schedule-dec-2021-jan-2022/) uses platform GMT+2 and warns of holiday pricing interruptions. Its retrieved text does not verify the source account's FX sessions. [Current OANDA hours](https://www.oanda.com/us-en/trading/hours-of-operation/) and [current IC Markets hours](https://www.icmarkets.eu/en/trading-pricing/trading-hours) cannot establish this broker/account's 2022 schedule. Weekend patterns support a hypothesis; exact Sunday reopening, Friday close and holiday exceptions remain unverified. Bank holidays are not automatically FX closures.

`config/strat2h-calendar-review-v1.json` records authority and limits. Proposed migration is documentation-first: obtain dated source-account rules, attest timezone/price side, create a new fingerprint and regression suite, publish a separate lineage and compare exclusions. Never shift observations, invent candles or rewrite old evidence.

## 9. Resources and runtime

Unchanged opt-in `config/strat2g-eurusd-2022-offline-120-v1.json`: available physical RAM≥1.20 GiB, worker peak RSS≤256 MiB, free disk≥15 GiB, batch 1,024, one thread; parent samples every 0.1 seconds and worker checks every 64 bars and at persistence/read boundaries. New H outputs retain the resource adapter's required G prefix, with a distinct `strat2h` namespace. General 1.50 and earlier 1.30 policies are unchanged.

Peak externally measured worker RSS: 96.726562 MiB. Minimum sampled system-available RAM: 1.528141 GiB. Minimum disk: 26.585682 GiB. Process RSS and system RAM are distinct measurements; sampled minima cannot prove absence of shorter fluctuations.

Run durations (seconds): `{"diagnostics-reference-result": 98.94639949995326, "diagnostics-repeat-result": 14.139013799955137, "diagnostics-resume-result": 56.879608599992935, "verification-result": 82.47072030004347}`. Bounded state: 61 OHLC feature bars, 31 pending bars, ≤1,024 events per transaction, ≤64 day records, ≤100 fixed groups; no complete dataset, prediction history or equity curve is kept in RAM.

## 10. Checkpoint, resume and independent verification

Committed H files matching independent replay: 181; generation 59; rows 58683; diagnostic events 49482. Canonical state hash: `e94b471c0d9feea492249f2f5000c29db51ce7b8d7a2501ba9d06c4b0417df90`.

Atomic fsync + directory rename publishes state and events together with chained receipts. Hard exit 86 before generation 35 commit must leave generation 34 and preserved staging. Resume must keep all committed prefix bytes, repeated resume must be unchanged, independent uninterrupted replay must match every committed hash. The separate verifier streams source OHLC/features again, recomputes paths/returns/labels and matrix probabilities, joins every February observation to frozen G events, and reconciles class metrics, confidence bins and threshold counts/sums. Code/environment/protocol/source/model identity changes reject resume. No old checkpoint migration is needed.

Evidence: `data\research\strat2d-strat2g-strat2h-evidence-v2`; `resumed-hashes.json`, `reference-hashes.json`, `independent-verification.json`, per-process monitors, failure records and summary. Prior C/D/E/F/G journals/reports and original research configuration are compared before/after admitted execution.

## 11. Tests and implementation

Synthetic/regression pytest results: `{"tests": 122, "failures": 0, "errors": 0, "skipped": 0}`. XML `.tmp/strat2h-tests-v1.xml`. Ruff lint/format are executed separately and recorded in the final report amendment.

New code: `src/trading/research/strat2h/{pipeline,statistics,analysis,report}.py`, `tools/strat2h_validation.py`, `tests/test_strat2h.py`; new diagnostic, confirmation and calendar JSON protocols. No existing G pipeline or evidence was changed. No dependencies added.

Tests cover frozen identity, future-data invariance, units/costs, bins, sessions/DST/gaps, purge/embargo, dependent resampling, numerical summaries, corruption, interrupted/repeated resume and immutable replay. Existing G model/resource and D/E execution tests remain applicable; synthetic observations are distinct from historical evidence.

## 12. Future independent confirmation protocol

`config/strat2h-confirmation-protocol-v1.json` freezes the same candidate/artifacts and proposes March–May 2022 solely as a design: immediate chronological follow-on over three months with a DST transition. No data from that period was opened, retrieved or evaluated. Its untouched status must first be audited; later explicit user authorization is required. The current Jan–Feb resource profile cannot authorize those dates.

One scheduled evaluation; no refitting/recalibration, threshold changes, candidate switching, optional stopping or outcome-driven extension. Minimum 20,000 eligible labels, 40 observed days, 1,000 per actual class. Compare frozen reference and training prior plus simple descriptive benchmarks. Paired 2/5-day blocks with 5,000 replicates must show jointly lower log loss and higher balanced accuracy versus both primary benchmarks. Economic evidence additionally needs ≥200 unchanged-gate signals on ≥20 days and ≥100 nonoverlap windows, verified calendar/price/execution semantics and positive lower net-movement bounds at base and 3.1bps costs. Insufficient counts mean INCONCLUSIVE; adverse evidence can reject. No-signal outcomes never justify lowering the gate. The protocol remains valid if H disappoints, and never authorizes profitability claims or automatic execution.

## 13. Assessments and blockers

| Subsystem | Assessment |
| --- | --- |
| bounded_paths | PASS |
| frozen_inference | PASS |
| hard_crash_resume | PASS |
| immutable_prefix | PASS |
| independent_verification | PASS |
| prior_evidence_preserved | PASS |
| repeated_resume | PASS |
| replay | PASS |
| resource_admission | PASS |
| historical calendar/price-side attestation | UNVERIFIED / BLOCKED |
| independent confirmation design | PASS — design only |
| economic/predictive confirmation | INCONCLUSIVE |

Missing checks are NOT RUN. Calendar attestation, independently authorized unexamined observations and adequate signal/day counts remain blockers. No production readiness or profitability is established.

## 14. Recommended next phase

STRAT-2I: documentation-based source calendar/price-side attestation and confirmation preregistration audit. Resolve those gates before requesting explicit access to a genuinely unexamined confirmation period. Do not add models, tune the 0.55 gate or promote the selected candidate based on these exploratory diagnostics.

Reproducible H invocation (fresh output version required):

```powershell
$env:OMP_NUM_THREADS='1'; $env:OPENBLAS_NUM_THREADS='1'; $env:MKL_NUM_THREADS='1'; $env:NUMEXPR_NUM_THREADS='1'
.\.venv\Scripts\python.exe -B -m tools.strat2h_validation --resource-profile 100 --evidence-version 3
```

No commits or pushes. No broker, Docker, collectors, source writes, holdout access or annual backtest.

## Completed 1.00 GiB validation ? supersedes earlier blocked attempt

**Engineering PASS. Research INCONCLUSIVE ? signal quality or economic evidence remains insufficient.** Earlier v1 failure evidence and the previous consolidated report remain archived. This completion used the user-authorized operational resource override only; frozen diagnostic and confirmation protocols, model parameters, splits, labels, calibration and production threshold were unchanged.

Exact invocation:

```powershell
$env:OMP_NUM_THREADS='1'; $env:OPENBLAS_NUM_THREADS='1'; $env:MKL_NUM_THREADS='1'; $env:NUMEXPR_NUM_THREADS='1'
.\.venv\Scripts\python.exe -B -m tools.strat2h_validation --resource-profile 100 --evidence-version 2
```

Profile: `config/strat2h-eurusd-2022-offline-100-v1.json`. Minimum available physical RAM **1,073,741,824 bytes**, worker RSS cap **268,435,456 bytes**, disk floor **15 GiB**, batch **1,024**, one thread, offline frozen diagnostics only. `--resource-profile 100` explicitly opts in; the CLI still defaults to 120. Ordinary 1.50 GiB and prior 1.20/1.30 profiles are unchanged. The new H adapter validates an exact operational-only configuration and frozen protocol hashes, delegates scope/path checks, then uses the actual 1.00 GiB configuration for admission, every worker sample and the external parent monitor. Other pipeline validators reject this profile. Resource settings were independently checked across all recorded parent/child monitors. No monitoring or guards were disabled.

Measured peak worker RSS: **96.7265625 MiB**. Minimum sampled physical available RAM: **1.5281410217285156 GiB (1,640,828,928 bytes)**. Minimum free disk: **26.585681915283203 GiB**. These are observed values, not guarantees for future background workloads; this run alone does not justify any further threshold reduction.

Processed **58,683 input rows**, emitted **49,482 diagnostic events**, with **12,304** final exploratory evaluation observations. January contributes 25,103 post-fit descriptive observations, not chronological generalization evidence; February selection has 12,075 rows. Final checkpoint **generation 59, complete=true**, **60 committed transactions**. The planned hard exit occurred before generation 35 commit, leaving generation 34; staging was retained. All prefix hashes survived resume; repeated resume was unchanged. Independent uninterrupted replay matched **181/181 committed hashes** and canonical state `e94b471c0d9feea492249f2f5000c29db51ce7b8d7a2501ba9d06c4b0417df90`. The independent numeric verifier checked every H event against source OHLC/features and matched all 24,379 February frozen G predictions. A separate final receipt-chain and file-hash pass independently confirmed both complete journals and the saved comparison maps.

### Interpretation of actual historical results

Calibrated balanced accuracy 0.350703 versus frozen reference 0.336043 and prior 0.333333 reproduces G. Calibrated log loss 1.084392 versus reference 1.085373 and prior 1.110409. The candidate-reference loss-difference 95% interval still crosses zero for both block sizes: 2-day [-0.025703, 0.020623], 5-day [-0.026179, 0.026049]. Nine prior configurations and repeated development-data use preclude independent confirmation.

Raw directional maxima were LONG 0.443710 / SHORT 0.402011; calibrated maxima were 0.412021 / 0.382596. Thus temperature softening contributes to compression but cannot alone explain zero 0.55 signals: raw logits also never reach the gate. FLAT-favoring training priors/intercepts and weak directional separation remain material. Calibration improved log loss relative to raw 1.111986 and ECE from 0.124112 to 0.071897; reference ECE was lower at 0.048837. No implementation defect or justification to lower the gate follows.

The counterfactual forced-direction rule had gross mean +0.294708 bps and net **-1.805292 bps** at base 2.1 bps costs; its dependent 95% net intervals were negative at both block lengths. This is hypothetical overlapping movement, not a portfolio return. At descriptive gates 0.30/0.35, there were 672/426 observations with base net +0.026006/+0.660645 bps, but both dependent intervals crossed zero and both means turned negative at 3.1 bps costs. At 0.40 there were only five observations in one cluster: insufficient day-level evidence. At 0.45/0.50/**0.55** there were zero signals. No threshold was selected, recommended for deployment or changed.

January versus February post-fit descriptive class frequencies drifted: FLAT 49.40% to 41.12% (January includes fitting/calibration). ECE rose from 0.025986 to 0.051178; forced net movement remained negative in both months. The weekly/session/regime/gap-age and feature-shift tables above describe instability without claiming independent significance. Only 12 final observed UTC days support the uncertainty calculations.

Calendar status remains **UNVERIFIED**, including source-account historical hours/holidays and price-side meaning. No calendar was rewritten, no missing candles filled, and no profitability claim made. Documentation findings in the frozen calendar review remain applicable; no additional market observations were retrieved. Previously proposed confirmation dates were not accessed.

### Tests, changes and limitations

Current checks: **56 passed**, 18 pre-existing NumPy-timedelta warnings; Ruff lint and format PASS. The first profile test run had 55 passes and one failure: Python dictionary equality treated boolean `True` as integer `1`. Canonical JSON hash equality fixed that strict-validation defect before historical execution; all 56 passed on rerun. Evidence `.tmp/strat2h-100-tests-v1.xml` and `-v2.xml` preserves both results. Boundary tests admit exactly 1 GiB, stop one byte below, stop one byte over the RSS ceiling, enforce disk floor, reject scope/parameter/purpose changes, preserve general/previous floors and stop the parent before launch on failed admission.

Files added: `config/strat2h-eurusd-2022-offline-100-v1.json`, `src/trading/research/strat2h/resources.py`, `tests/test_strat2h_resources.py`. Files updated: H pipeline/analysis resource imports, `tools/strat2h_validation.py` opt-in flag/child forwarding, H report renderer and this consolidated report. Pre-change H implementation/report copies and all prior config hashes are preserved at `.tmp/strat2h-100-before-v1`. No old C/D/E/F/G research code, configuration or published evidence was changed. No dependencies added; no commits or pushes.

The completed execution is isolated in `data/research/strat2d-strat2g-strat2h-{resumed,reference,evidence}-v2`. `final-independent-review.json` records exact resource minima and the final independent integrity pass; `artifact-hashes.json` records new evidence hashes. All 47 C prepared file hashes/mtimes, protected D/E/F/G journals/reports and the original Phase 6 configuration passed preservation checks.

All requested engineering subsystems passed; predictive/economic confirmation remains **INCONCLUSIVE**, with no production promotion. Recommended next phase: STRAT-2I source calendar/price-side attestation and independent-confirmation provenance/preregistration audit. Do not add models, retune the candidate or lower 0.55 based on these exploratory comparisons. Access to any new period still requires later explicit authorization.
