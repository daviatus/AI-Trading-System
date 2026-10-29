# STRAT-2I data integrity and economic signal research v1

## 1. Executive summary

Engineering **PASS**. Research **INCONCLUSIVE — important data, calendar or economic questions remain unresolved**.

Only EURUSD M1 January–February 2022 was accessed. This period has been repeatedly examined; none of these results is independent out-of-sample confirmation. No training, new model, threshold change, trade execution or target selection occurred. All previous evidence is immutable.

## 2. Source integrity

Read-only, checksum-whitelisted Phase 5 junction files `cache/EURUSD/2022-01.parquet` and `2022-02.parquet` were audited directly, then compared to the sealed STRAT-2C stream. Source timestamps require explicit UTC and minute alignment. Strict ordering makes adjacent duplicate detection sufficient: any reversal, exact duplicate or conflicting duplicate stops rather than sorting/deduplicating. OHLC finite/positive/ordering checks are fatal; precision and 20bps open-versus-previous-close jumps are warnings. Tick volume is audited but not used as reliable traded volume.

| Check | Result |
| --- | --- |
| rows | 58683 |
| duplicates | 0 |
| conflicts | 0 |
| out_of_order | 0 |
| invalid_ohlc | 0 |
| precision_warnings | 0 |
| zero_volume | 0 |
| invalid_volume | 0 |
| jumps_contiguous | 0 |
| jumps_across_gap | 1 |
| max_jump_bps | 134.402640 |
| maximum_spacing_minutes | 2887 |
| max_segment | 1435 |
| dst_offsets | [-18000.0] |

A jump following a closure is a multi-minute move, not a one-minute return. Warnings do not establish a bad quote. The absence of detected defects does not attest original collection completeness or historical bid/ask meaning.

## 3. Gap/missing reconciliation

| Category | Minutes |
| --- | --- |
| internal | 23516 |
| leading/trailing window edges | 2761 |
| full-window absent | 26277 |

| Rule category | Internal absent | Window-edge absent | Total absent |
| --- | --- | --- | --- |
| open | 378 | 0 | 378 |
| session_break | 50 | 0 | 50 |
| weekend | 23088 | 2761 | 25849 |

There are 301 internal gap intervals; 260 contain at least one expected-open minute. Interval categories overlap when a gap spans more than one calendar classification; minute categories are disjoint. The 16 observed outside-session rows are observations, not absent minutes; never add them to missing counts. All classifications depend on the unverified frozen calendar. A missing expected-open record may reflect collection failure, no observed tick, or incorrect historical hours; this audit cannot distinguish those causes.

Counterfactual raw decision windows interrupted by an internal gap, by horizon: `{"15": 2520, "30": 3903, "5": 1145}`. These count the last min(horizon, segment length) observed decision bars before each gap; they are not final model-eligible counts and exclude window-end censoring.

Daily session coverage (UTC days, not an attested broker trading day) is recorded below. Short Friday/Sunday days are not automatically irregular collection. Partial expected sessions are hypothesis-based flags.

| UTC day | Observed open | Missing expected open | Observed outside | Absent weekend | Absent session break |
| --- | --- | --- | --- | --- | --- |
| 2022-01-01 | 0 | 0 | 0 | 1440 | 0 |
| 2022-01-02 | 110 | 9 | 0 | 1321 | 0 |
| 2022-01-03 | 1428 | 10 | 0 | 0 | 2 |
| 2022-01-04 | 1417 | 21 | 0 | 0 | 2 |
| 2022-01-05 | 1426 | 12 | 0 | 0 | 2 |
| 2022-01-06 | 1430 | 8 | 0 | 0 | 2 |
| 2022-01-07 | 1311 | 4 | 0 | 125 | 0 |
| 2022-01-08 | 0 | 0 | 0 | 1440 | 0 |
| 2022-01-09 | 111 | 8 | 0 | 1321 | 0 |
| 2022-01-10 | 1423 | 15 | 0 | 0 | 2 |
| 2022-01-11 | 1426 | 12 | 0 | 0 | 2 |
| 2022-01-12 | 1424 | 14 | 0 | 0 | 2 |
| 2022-01-13 | 1437 | 1 | 0 | 0 | 2 |
| 2022-01-14 | 1315 | 0 | 0 | 125 | 0 |
| 2022-01-15 | 0 | 0 | 0 | 1440 | 0 |
| 2022-01-16 | 103 | 16 | 0 | 1321 | 0 |
| 2022-01-17 | 1414 | 24 | 0 | 0 | 2 |
| 2022-01-18 | 1435 | 3 | 0 | 0 | 2 |
| 2022-01-19 | 1436 | 2 | 0 | 0 | 2 |
| 2022-01-20 | 1429 | 9 | 0 | 0 | 2 |
| 2022-01-21 | 1315 | 0 | 0 | 125 | 0 |
| 2022-01-22 | 0 | 0 | 0 | 1440 | 0 |
| 2022-01-23 | 114 | 5 | 0 | 1321 | 0 |
| 2022-01-24 | 1426 | 12 | 0 | 0 | 2 |
| 2022-01-25 | 1433 | 5 | 0 | 0 | 2 |
| 2022-01-26 | 1429 | 9 | 0 | 0 | 2 |
| 2022-01-27 | 1434 | 4 | 1 | 0 | 1 |
| 2022-01-28 | 1312 | 3 | 0 | 125 | 0 |
| 2022-01-29 | 0 | 0 | 0 | 1440 | 0 |
| 2022-01-30 | 103 | 16 | 0 | 1321 | 0 |
| 2022-01-31 | 1423 | 15 | 1 | 0 | 1 |
| 2022-02-01 | 1425 | 13 | 1 | 0 | 1 |
| 2022-02-02 | 1428 | 10 | 1 | 0 | 1 |
| 2022-02-03 | 1435 | 3 | 1 | 0 | 1 |
| 2022-02-04 | 1315 | 0 | 0 | 125 | 0 |
| 2022-02-05 | 0 | 0 | 0 | 1440 | 0 |
| 2022-02-06 | 118 | 1 | 0 | 1321 | 0 |
| 2022-02-07 | 1432 | 6 | 0 | 0 | 2 |
| 2022-02-08 | 1427 | 11 | 1 | 0 | 1 |
| 2022-02-09 | 1427 | 11 | 1 | 0 | 1 |
| 2022-02-10 | 1431 | 7 | 1 | 0 | 1 |
| 2022-02-11 | 1315 | 0 | 0 | 125 | 0 |
| 2022-02-12 | 0 | 0 | 0 | 1440 | 0 |
| 2022-02-13 | 113 | 6 | 0 | 1321 | 0 |
| 2022-02-14 | 1426 | 12 | 1 | 0 | 1 |
| 2022-02-15 | 1430 | 8 | 1 | 0 | 1 |
| 2022-02-16 | 1431 | 7 | 1 | 0 | 1 |
| 2022-02-17 | 1433 | 5 | 0 | 0 | 2 |
| 2022-02-18 | 1315 | 0 | 0 | 125 | 0 |
| 2022-02-19 | 0 | 0 | 0 | 1440 | 0 |
| 2022-02-20 | 111 | 8 | 0 | 1321 | 0 |
| 2022-02-21 | 1434 | 4 | 1 | 0 | 1 |
| 2022-02-22 | 1433 | 5 | 1 | 0 | 1 |
| 2022-02-23 | 1434 | 4 | 1 | 0 | 1 |
| 2022-02-24 | 1428 | 10 | 1 | 0 | 1 |
| 2022-02-25 | 1315 | 0 | 0 | 125 | 0 |
| 2022-02-26 | 0 | 0 | 0 | 1440 | 0 |
| 2022-02-27 | 119 | 0 | 0 | 1321 | 0 |
| 2022-02-28 | 1428 | 10 | 1 | 0 | 1 |

## 4. Historical calendar and timestamp evidence

**UNVERIFIED historical source-broker/account rules.** Frozen metadata identifies EURUSD/cTrader demo-source semantics but does not attest the actual broker/account or a dated 2022 schedule. The broker cannot responsibly be inferred from symbolId, commission, platform or generic FX hours. We did not connect to a broker or read credentials.

[Spotware Open API model messages](https://help.ctrader.com/open-api/model-messages/) specify Unix-minute opening-tick timestamps and symbol-timezone weekly intervals with inclusive starts/exclusive ends. The local parser converts explicitly to UTC. Stored semantic lineage is supported by code and metadata, not an independent source-account attestation. [NIST DST rules](https://www.nist.gov/pml/time-and-frequency-division/popular-links/daylight-saving-time-dst) imply New York UTC−5 throughout these two months; there is no in-window DST transition. Broker-server clocks need not be UTC. A [December 2021 broker notice](https://www.ic.com/blog/holiday-trading-schedule-dec-2021-jan-2022/) uses GMT+2 and warns of holiday pricing interruptions, but cannot establish this unidentified account's FX schedule. No holiday hours are invented from it.

## 5. Calendar corrections and feature continuity

No correction is justified by account-specific evidence, so none is proposed or applied. The 16 21:59 UTC / 16:59 New York boundary candles remain preserved and flagged. Their validity and the 378 hypothesized missing open minutes remain unresolved. The versioned I evidence registry retains the frozen calendar fingerprint and supporting/unsupported claims. Any later correction requires dated account/symbol rules, explicit affected timestamps, separate rule version and preparation lineage, boundary/DST regression tests and eligible-observation comparison before metrics. Extending a session merely to absorb observed candles would be circular.

The prepared-feature audit checks finite model predictors during the first 63 observed open bars after any gap or excluded-session reset. H's independent source verifier additionally recreates its 61-bar causal features and contiguous 30-minute label paths. This addresses features spanning discontinuities without changing existing artifacts.

## 6. Frozen model economic diagnostics

The I audit consumes the exact frozen H events, not newly fitted predictions. January scores are post-fit descriptive reuse. February selection and final slices are also previously examined development data. A forced direction uses the larger LONG/SHORT probability even when FLAT dominates; it is an opportunity diagnostic, not an order. Threshold groups additionally require direction≥FLAT. Favorable/adverse extrema describe observed paths, not attainable fills.

| Population | n | Gross bps | Gross pips | Favorable pips | Adverse pips | Scaled move | Base net bps | Base cost coverage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| confidence:evaluation:3 | 1058 | -0.091187 | -0.103214 | 3.317675 | 3.334216 | -0.057347 | -2.191156 | 0.257089 |
| confidence:evaluation:4 | 2605 | -0.132801 | -0.151593 | 3.070019 | 3.395086 | -0.054888 | -2.232803 | 0.245298 |
| confidence:evaluation:5 | 2819 | -0.100717 | -0.115360 | 4.335864 | 4.605427 | -0.073384 | -2.200755 | 0.299397 |
| confidence:evaluation:6 | 4560 | 0.551839 | 0.621250 | 5.504912 | 5.102259 | 0.078657 | -1.548163 | 0.376535 |
| confidence:evaluation:7 | 1257 | 1.456783 | 1.645505 | 6.266428 | 4.849324 | 0.220745 | -0.643174 | 0.454256 |
| confidence:evaluation:8 | 5 | 0.973234 | 1.100000 | 5.620000 | 8.160000 | 0.231636 | -1.126868 | 0.400000 |
| phase:calibration | 13428 | 0.185305 | 0.209756 | 3.323414 | 3.052614 | 0.068989 | -1.914677 | 0.270629 |
| phase:evaluation | 12304 | 0.294708 | 0.331396 | 4.611322 | 4.450358 | 0.018432 | -1.805295 | 0.328755 |
| phase:selection | 12075 | -0.131780 | -0.149540 | 3.907536 | 4.061764 | -0.024523 | -2.231791 | 0.280497 |
| phase:train | 11675 | 0.023198 | 0.027289 | 3.356728 | 3.295820 | 0.029555 | -2.076806 | 0.266296 |
| prediction:evaluation:0 | 11632 | 0.188911 | 0.212595 | 4.468226 | 4.435256 | 0.003039 | -1.911089 | 0.318948 |
| prediction:evaluation:1 | 393 | 2.318872 | 2.600763 | 7.174809 | 4.881170 | 0.291038 | 0.218628 | 0.501272 |
| prediction:evaluation:2 | 279 | 1.854335 | 2.087814 | 6.966308 | 4.473118 | 0.276188 | -0.245470 | 0.494624 |
| threshold:evaluation:0.3 | 672 | 2.126006 | 2.387798 | 7.088244 | 4.711756 | 0.284873 | 0.025945 | 0.498512 |
| threshold:evaluation:0.35 | 426 | 2.760645 | 3.102582 | 7.605164 | 4.371127 | 0.366059 | 0.660609 | 0.528169 |
| threshold:evaluation:0.4 | 5 | 0.973234 | 1.100000 | 5.620000 | 8.160000 | 0.231636 | -1.126868 | 0.400000 |

The confidence groups are directional maximum probability bins [index/20,(index+1)/20). Predicted-class groups identify FLAT/LONG/SHORT argmax; all price outcomes are signed by the diagnostic forced direction. Full pip histograms and extrema appear in `analysis.json` with fixed protocol edges.

No 0.55 group is present when there are zero eligible signals; this is a zero count, not a missing calculation. No realized trades or portfolio P&L were generated in I. H's unchanged zero-fill result is not replaced by these counterfactuals.

## 7. Costs, units and conditional execution assumptions

EURUSD quote units are USD/EUR. One pip=0.0001 and pipette=0.00001. Gross pips=side×(exit−entry)/0.0001. For quantity Q in EUR units and a USD account, cash P&L=side×Q×price difference minus USD fees; a 100,000 EUR lot has USD10 per pip. API volume and lotSize are in cents of base units, not directly lots. Other account currencies require verified causal FX conversion and are unsupported here.

Snapshot commissionType=2 in **Open API** means USD per lot, not the differently numbered Algo enumeration. Stored preciseTradingCommissionRate300000000 / 10^8 implies USD3 per lot if applicable; the legacy commission300 scale is not independently inferred. Assuming a per-side fee, lotSize10,000,000 cents=100,000EUR. At reference price P, that would imply roundtrip fee 6/(100000×P)×10000=0.6/P bps, rather than constant 0.6bps. Historical applicability, fee side, minimums and price-side evidence remain unverified. [Open API commission/volume fields](https://help.ctrader.com/open-api/model-messages/) support these unit distinctions. We retain the established 0.6bps reference scenario; we do not silently replace it with a purported historical fee.

For each conditional reference-mid scenario, entry fill=Pentry×[1+side×(spread/2+slip)/10000], exit fill=Pexit×[1−side×(spread/2+slip)/10000], fees per EUR=(entry fill+exit fill)×roundtrip commission/20000. Exact cash-flow accounting includes changing exit notional; H's simple gross-minus-constant-bps approximation is reported only as prior context. These are not observed historical bid/ask fills. Do not add spread twice to observed bid/ask quotes in a future implementation.

| Scenario total bps | Spread bps | Slippage per side bps | Roundtrip fee bps | Flat-price break-even gross bps |
| --- | --- | --- | --- | --- |
| 1.100000 | 0.500000 | 0 | 0.600000 | 1.100000 |
| 2.100000 | 1 | 0.250000 | 0.600000 | 2.100000 |
| 3.100000 | 1.500000 | 0.500000 | 0.600000 | 3.100000 |
| 5.600000 | 3 | 1 | 0.600000 | 5.600000 |

Break-even movement in pips at reference P is approximately cost_bps×P, since a basis point is P/10000 price units; the exact net equation above is used for observed-price diagnostics. With exact fills the required future exit for side d is P×(1+d×a)×(d+c)/(d−c)/(1−d×a), a=(spread/2+slip)/10000 and c=fee/20000. Expected signed gross cash must exceed expected costs; classification accuracy alone cannot establish that.

| Cost bps | Mean net bps | Mean net pips | Mean cost pips | Cost coverage |
| --- | --- | --- | --- | --- |
| 1.1 | -0.805294 | -0.911995 | 1.243391 | 0.407510 |
| 2.1 | -1.805295 | -2.042351 | 2.373747 | 0.328755 |
| 3.1 | -2.805296 | -3.172706 | 3.504102 | 0.255933 |
| 5.6 | -5.305300 | -5.998595 | 6.329992 | 0.135159 |

## 8. Future cost-aware target design

Exactly three theory-driven targets were specified before I execution, all horizon 15 minutes. No new model is trained and no target winner is selected. Their class balances are descriptive availability checks, not a search for a favorable result.

| Target | Horizon | Base neutral cost bps | Causal volatility multiplier |
| --- | --- | --- | --- |
| cost15_reference | 15 | 2.100000 | 0.000000 |
| cost15_stress | 15 | 3.100000 | 0.000000 |
| cost15_volatility | 15 | 2.100000 | 0.500000 |

Formula: g=10,000×(close[t+15]/open[t+1]−1); z=max(cost_bps,k×max(causal sigma20[t],1e−6)×sqrt(15)×10,000). LONG when g>z, SHORT when g<−z, FLAT otherwise. Use no future price in features. Require observed contiguous future path, conservative session buffers and the same common 30-minute admission as H. Purge common label end strictly before split right, embargo 60 minutes after nontraining split left. No unavailable label is fabricated. Current unknown exceptional sessions still limit admission confidence.

| Target | Final development FLAT/LONG/SHORT |
| --- | --- |
| cost15_reference | [4792, 3741, 3771] |
| cost15_stress | [6507, 2852, 2945] |
| cost15_volatility | [5748, 3273, 3283] |

## 9. Temporal and dependent uncertainty

Whole observed UTC days are resampled in circular blocks of 2 and 5 days, 1,000 replicates, fixed seed. Minute labels and their overlapping 15-minute paths stay together. This handles some serial dependence but not structural changes or unknown holidays; observed-day blocks can bridge weekends. Approximate count floor(days/block length) is a coarse block-count proxy, not a measured effective sample size. Threshold intervals require ten observed days and five signal-bearing days. Sparse cells receive no interval. No IID minute-based standard errors.

```json
{
  "blocks": {
    "2": {
      "approximate_nonoverlapping_day_blocks": 6,
      "intervals": {
        "0.3": {
          "lower": -1.1185304046954923,
          "upper": 1.0797857954412893,
          "valid_replicates": 1000
        },
        "0.35": {
          "lower": -0.756387611122403,
          "upper": 1.9553247927271578,
          "valid_replicates": 1000
        },
        "0.4": {
          "status": "INSUFFICIENT_DAYS_OR_SIGNALS"
        },
        "0.45": {
          "status": "INSUFFICIENT_DAYS_OR_SIGNALS"
        },
        "0.5": {
          "status": "INSUFFICIENT_DAYS_OR_SIGNALS"
        },
        "0.55": {
          "status": "INSUFFICIENT_DAYS_OR_SIGNALS"
        },
        "forced": {
          "lower": -2.0474793611380755,
          "upper": -1.5119597653868944,
          "valid_replicates": 1000
        }
      }
    },
    "5": {
      "approximate_nonoverlapping_day_blocks": 2,
      "intervals": {
        "0.3": {
          "lower": -0.9570280264584727,
          "upper": 0.9632668828457105,
          "valid_replicates": 1000
        },
        "0.35": {
          "lower": -0.5684099299792355,
          "upper": 1.6965417873999438,
          "valid_replicates": 1000
        },
        "0.4": {
          "status": "INSUFFICIENT_DAYS_OR_SIGNALS"
        },
        "0.45": {
          "status": "INSUFFICIENT_DAYS_OR_SIGNALS"
        },
        "0.5": {
          "status": "INSUFFICIENT_DAYS_OR_SIGNALS"
        },
        "0.55": {
          "status": "INSUFFICIENT_DAYS_OR_SIGNALS"
        },
        "forced": {
          "lower": -2.0099589930905113,
          "upper": -1.5679722897716988,
          "valid_replicates": 1000
        }
      }
    }
  },
  "days": 12
}
```

| Subdivision | n | Gross pips | Base net bps | Coverage | Clusters | Nonoverlap count |
| --- | --- | --- | --- | --- | --- | --- |
| month:2022-01 | 25103 | 0.124893 | -1.990081 | 0.268613 | 32 | 1691 |
| month:2022-02 | 24379 | 0.093187 | -2.016540 | 0.304853 | 25 | 1639 |
| session:evaluation:00_07 | 3852 | -0.249974 | -2.319710 | 0.243250 | 11 | 259 |
| session:evaluation:07_13 | 3600 | 0.305194 | -1.831244 | 0.362222 | 10 | 240 |
| session:evaluation:13_17 | 2400 | 1.256167 | -0.985399 | 0.422500 | 10 | 160 |
| session:evaluation:17_24 | 2452 | 0.378018 | -1.761579 | 0.322186 | 18 | 177 |
| volatility:evaluation:0 | 805 | -0.789317 | -2.793785 | 0.110559 | 38 | 81 |
| volatility:evaluation:1 | 4006 | 0.252297 | -1.876214 | 0.275087 | 115 | 362 |
| volatility:evaluation:2 | 5614 | -0.072408 | -2.165749 | 0.342180 | 110 | 463 |
| volatility:evaluation:3 | 1879 | 2.186642 | -0.153658 | 0.496541 | 48 | 161 |
| week:2022-W01 | 5760 | -0.031528 | -2.127994 | 0.276736 | 10 | 389 |
| week:2022-W02 | 5915 | 0.084565 | -2.026959 | 0.256128 | 9 | 400 |
| week:2022-W03 | 6206 | 0.198759 | -1.925752 | 0.258460 | 6 | 417 |
| week:2022-W04 | 5974 | 0.311634 | -1.822583 | 0.280047 | 9 | 403 |
| week:2022-W05 | 6059 | -0.181235 | -2.261959 | 0.284535 | 7 | 407 |
| week:2022-W06 | 6003 | -0.265667 | -2.332748 | 0.267200 | 8 | 404 |
| week:2022-W07 | 6245 | 0.203939 | -1.920227 | 0.284548 | 6 | 421 |
| week:2022-W08 | 6059 | 0.515465 | -1.640607 | 0.359135 | 8 | 409 |
| week:2022-W09 | 1261 | 0.229421 | -1.896301 | 0.402855 | 1 | 85 |

## 10. Selection-bias limitations

Nine G configurations were considered and this period was repeatedly examined through H. Small balanced-accuracy improvements do not prove economic advantage. Confidence tables and target balances cannot be used to change the selected candidate or production gate. Overlapping opportunities, unknown price side/fees and no portfolio accounting prevent conversion of descriptive net movements into realized profitability.

## 11. Resources and execution

Unchanged H profile: 1.00 GiB minimum available physical RAM, 256 MiB worker RSS, 15 GiB disk, one thread, batch 1,024. Peak externally sampled worker RSS: 101.05859375 MiB; minimum sampled available RAM 1.670196533203125 GiB; minimum disk 26.4044189453125 GiB. Continuous parent sampling plus bounded worker checks fail closed. Dedicated I paths retain the required H resource-prefix namespace. General 1.50 and prior resource policies are untouched.

State is bounded: one previous raw row, ≤64 day records, ≤256 fixed economic groups, 1,024-row Arrow batches, source/H journal metadata. No full historical DataFrame, prediction list or equity curve is retained. Numeric verification uses a second bounded pass; source is never an output destination.

## 12. Checkpoint/replay and numerical verification

Rows audited 58683; frozen prediction events 49482; final generation 179; matching committed replay files 541; state hash `72170e6e9d0a9ee6df098330ad0005410c04724debccadfab96caf4012d0a3d3`.

Two hard exits test before raw generation35 publication and after economic generation140 publication. Resume preserves both committed prefixes; repeated resume changes nothing. Each transaction atomically commits cursor/aggregate state, chained receipt and evidence events. Independent reference run compares every committed file. Independent verification zips original raw and prepared rows, recomputes grid omissions, checks OHLC, reruns H price/feature/probability verification, and independently recalculates exact cash costs, pips, target classes and counts. No model checkpoint is migrated or rewritten.

## 13. Automated tests and implementation

Synthetic tests exercise timestamps, duplicate conflicts, OHLC, calendar/DST/boundaries, gap-label exclusions, price/pip/quantity/fee units, targets, causal features, serial blocks, bounded state, journal corruption and before/after-commit resume/replay. Exact test/static results and any failures are recorded in the final review below. New implementation: `src/trading/research/strat2i`, `tools/strat2i_validation.py`, `tests/test_strat2i.py`; no dependencies or previous research code changed.

## 14. Assessments

| Subsystem | Status |
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
| historical broker calendar and fees | UNVERIFIED |
| economic usefulness at unchanged gate | INCONCLUSIVE |
| target design and confirmation review | PASS — design only |

## 15. Independent-confirmation readiness

Not ready to execute independent confirmation. `config/strat2i-confirmation-protocol-proposal-v2.json` is a separate proposal; H's frozen protocol is unchanged. It clarifies exactly 12 selected features (not the broader interaction policy), fee/price-side attestation, exceptional-session exclusions, whole-week sensitivity, ≥12 weekly clusters for economic evidence and no substitution of I targets into confirmation. Candidate/model/0.55 stay frozen. Existing proposed future dates remain design only; untouched provenance must be audited and later explicit authorization is required. No such data was opened or retrieved here.

## 16. Remaining blockers and next steps

Obtain dated source-account/symbol session and holiday rules with timestamp/price-side provenance; verify historical bid/ask/fee/FX assumptions or retain conditional costs and blocked profitability claims. Require adequate signals at the unchanged gate and dependence-aware economic evidence. Freeze and preregister one independent test before any later authorized new-period access. If these prerequisites remain absent, defer confirmation; do not manufacture trades or select a target from these results. Recommended STRAT-2J: provenance and cost attestation plus prospective-study design review, not model promotion.

No annual backtest, holdout access, new historical period, broker/live/paper connection, Docker, source modification, commit or push.

## Final consolidated validation review ? 2026-10-29

**Engineering PASS. Research INCONCLUSIVE ? important data, calendar or economic questions remain unresolved.** Independent confirmation is not justified for execution yet. This does not establish absence of all possible forecast information, nor profitability.

Actual invocation:

```powershell
$env:OMP_NUM_THREADS='1'; $env:OPENBLAS_NUM_THREADS='1'; $env:MKL_NUM_THREADS='1'; $env:NUMEXPR_NUM_THREADS='1'
.\.venv\Scripts\python.exe -B -m tools.strat2i_validation --evidence-version 1
```

### Integrity reconciliation and implications

The first observation is `2022-01-02T22:01:00+00:00` and the last `2022-02-28T23:59:00+00:00`. All **58,683** source observations are UTC-aligned, ordered and unique. No conflicting duplicates, invalid/nonfinite/nonpositive OHLC, nonfinite/negative volume or off-grid 5-digit prices were detected. No rows were repaired or removed. Precision tests use 1e-6 of a pipette-grid integer as floating-point tolerance; this is not a claim about executable tick sizes or spreads.

The full 59-day UTC window contains **84,960 minute cells = 58,683 observed + 26,277 absent**. Internal gaps alone account for **301 intervals / 23,516 minutes**: **23,088** hypothesized weekend minutes + **50** hypothesized scheduled-break minutes + **378** hypothesized expected-open minutes. An additional **2,761 leading-edge minutes** (no trailing-edge absence) are outside the internal-gap definition. Thus full-window weekend absence is **25,849**, not 23,088. Of the 301 gaps, **260 touch expected-open time**; 41 do not. A single interval can contain several rule categories, while each missing minute has exactly one classification.

The **16 outside-session observations are present candles**, all at 21:59 UTC / 16:59 New York. They are separate from missing-minute counts. Under this hypothesis, **59,045 open cells = 58,667 observed open + 378 missing open**, and **25,915 closed cells = 16 observed outside + 25,899 absent closed**. These identities explain the previously reported numbers without assigning provider fault. Actual broker hours/holiday exceptions and whether a minute had no tick remain unverified. Generic FX schedules and a current metadata contract do not validate those interpretations.

There were **zero >=20bps open-versus-previous-close jumps across contiguous bars** and **one across a gap**; the maximum was 134.402640 bps. The warning is preserved in transaction events: `[{"bps": 134.40264014691115, "event": "jump_warning", "interpretation": "warning only, not proof of bad price", "minute": 27433321, "spacing": 2887}]`. A closure-spanning movement is not a one-minute defect. Maximum observed spacing was 2887 minutes. No extreme was silently censored. The new audit checked **6191** early post-reset prepared-feature rows and found **zero** fully finite model-feature warmup violations. Independent H feature/path verification also passed. Raw label-window interruption counts are h5=1,145, h15=2,520, h30=3,903; these are counterfactual decisions near internal gaps, not unique missing minutes or final model sample counts.

### Economic findings and uncertainty

Only **12,304** final-development observations support the primary economic summaries. The complete H input has 49,482 post-fit/descriptive events; January observations include fitted/calibrated data and are not causal out-of-sample model predictions. No model was refit. The frozen H metrics remain exploratory (balanced accuracy 0.35070 versus reference 0.33604); the I numerical verifier reconfirmed H prediction provenance and metrics.

The all-observation forced-direction diagnostic has mean gross **0.294708 bps = 0.331396 pips**. Mean observed favorable/adverse excursion is **4.611322 / 4.450358 pips**. Base mean exact modeled cost is **2.373747 pips**, and mean exact net movement is **-1.805295 bps / -2.042351 pips**. Only **32.875%** of forced directions cover base modeled costs. Gross sign hit rate **51.902%** is not sufficient when positive/negative magnitudes and costs are included.

The forced-rule net intervals are negative under both 2-day and 5-day dependent bootstraps. At descriptive gates 0.30/0.35, base mean net is +0.025945/+0.660609 bps, but their intervals cross zero and their means become negative under 3.1bps stress. Gate0.40 has only five observations; gates0.45/0.50/**0.55 have zero**. No lower gate is selected or recommended. Twelve observed days give only roughly six two-day or two five-day nonoverlapping blocks, not thousands of independent minutes. Target counts (FLAT/LONG/SHORT) were reference [4792, 3741, 3771], stressed-cost [6507, 2852, 2945], volatility-adjusted [5748, 3273, 3283]; no target winner was chosen.

The exact price-proportional fee/fill calculation differs slightly from H's constant-bps subtraction: base net -1.805295 versus -1.805292 bps. This small arithmetic distinction does not change the conclusion. The table's nominal round-trip cost is a flat-price cost reference; exact future break-even movement uses the equation in section7. No historical bid/ask quotes, broker fees or account FX conversion rates were invented, and no trades were executed.

### Resource, replay and evidence

Peak externally monitored worker RSS **101.05859375 MiB**; minimum sampled available physical RAM **1.670196533203125 GiB (1,793,359,872 bytes)**; minimum free disk **26.4044189453125 GiB**. All parent and child records enforce the unchanged H profile of 1.00 GiB minimum available RAM / 256 MiB worker RSS / 15 GiB free disk. Actual headroom in this run does not prove safety at all future low-memory conditions.

There are **180 committed transactions**, head **179**, `complete=true`. Hard exit86 before raw commit35 preserved head34 and staging; hard exit86 after economic commit140 preserved that exact committed state. Prefixes remained byte-identical after automatic resume. Repeated resume was unchanged. Independent reference replay matched **541/541 files** and canonical state `72170e6e9d0a9ee6df098330ad0005410c04724debccadfab96caf4012d0a3d3`. A separate final receipt-chain/file-hash pass reconfirmed both complete journals and their saved maps.

Independent verification matched every raw/prepared row, independently recomputed omitted-minute categories, reran H's 49,482 source-path/probability checks (24,379 February frozen G predictions matched), and separately calculated all 12,304 final pip/cash/target outcomes. Both original monthly source files retained their hashes and modification times; all 47 C prepared files and protected D/E/F/G/H journals/reports and original Phase6 configuration passed preservation checks. New outputs are isolated under `data/research/strat2d-strat2g-strat2h-strat2i-{resumed,reference,evidence}-v1`. The H prefix is required by the unchanged resource validator; the additional I namespace prevents overwriting H outputs.

### Tests and remaining blockers

Broad synthetic/regression suite: **89 passed**, 38 existing NumPy-timedelta deprecation warnings, 36.31s. Final I suite after adding economic-stage crash coverage: **21 passed**, 23 existing warnings, 27.78s. This represents **91 distinct semantic cases**, not 110 independent tests. The initial I run had 16 passes and one fixture failure: its reversal timestamp was outside the declared window, so scope validation correctly fired first; moving the fixture inside the window tested the intended reversal branch without relaxing correctness. Initial evidence is retained. Ruff lint and formatting passed on all eight I source/tool/test files. Test outputs: `.tmp/strat2i-first-tests-v1.xml`, `.tmp/strat2i-tests-v1.xml`, `.tmp/strat2i-final-tests-v1.xml`.

```powershell
$env:PYTHONPATH="$pwd\src"
..\AI-Trading-System\.venv-research\Scripts\python.exe -B -m pytest tests/test_strat2i.py tests/test_strat2h.py tests/test_strat2h_resources.py tests/test_strat2g.py tests/test_strat2g_resources.py -q -p no:cacheprovider --basetemp=.tmp/strat2i-tests-v1 --junitxml=.tmp/strat2i-tests-v1.xml
..\AI-Trading-System\.venv-research\Scripts\python.exe -B -m pytest tests/test_strat2i.py -q -p no:cacheprovider --basetemp=.tmp/strat2i-final-tests-v1 --junitxml=.tmp/strat2i-final-tests-v1.xml
..\AI-Trading-System\.venv\Scripts\ruff.exe check src/trading/research/strat2i tools/strat2i_validation.py tests/test_strat2i.py
..\AI-Trading-System\.venv\Scripts\ruff.exe format --check src/trading/research/strat2i tools/strat2i_validation.py tests/test_strat2i.py
```

Those temp/XML paths already exist; use new versioned names for any future rerun. A fresh full audit would use `--evidence-version 2`; never reset v1. No required independent-confirmation run is authorized.

| Subsystem | Final assessment |
| --- | --- |
| Raw and prepared timestamp/OHLC identity and precision checks | PASS |
| Reconciliation of known gaps/missing/outside counts | PASS ? classification remains calendar-conditional |
| Prepared/H feature continuity and label-path checks | PASS |
| Historical broker/calendar/holiday/price-side attestation | UNVERIFIED |
| Pip, price, position-size and conditional cost arithmetic | PASS ? historical fee/fill applicability unverified |
| Economic advantage at unchanged0.55 gate | INCONCLUSIVE ? zero signals |
| Forced-direction advantage after reference costs | FAIL for that specific hypothetical rule; dependent intervals negative |
| Lower-gate economic advantage | INCONCLUSIVE ? sparse evidence and intervals cross zero |
| Future target design, no winner selected | PASS ? research design only |
| Memory/disk monitoring and bounded state | PASS |
| Two hard crashes, resume, repeat-resume and independent replay | PASS |
| Independent confirmation readiness | BLOCKED / INCONCLUSIVE |

Exact remaining blockers: (1) dated source broker/account/symbol and price-side provenance, (2) verified historical hours/holiday exceptions or preregistered conservatively justified exclusions, (3) attested fees/bid-ask/account-currency conversions, (4) adequate unchanged-gate signals with dependence-aware positive net evidence, (5) genuinely unexamined period with provenance audit and later explicit access authorization. The separate `config/strat2i-confirmation-protocol-proposal-v2.json` addresses design ambiguities without changing frozen G/H specifications. Recommended STRAT-2J is source/cost provenance and independent-study preregistration review. Do not promote the model, tune the gate, select a new target from these results or expand the dataset automatically.

Files added: `config/strat2i-data-quality-economic-research-v1.json`, `config/strat2i-confirmation-protocol-proposal-v2.json`, `src/trading/research/strat2i/{__init__,quality,economics,pipeline,analysis,report}.py`, `tools/strat2i_validation.py`, `tests/test_strat2i.py`, and this report. No previous research source/configuration/evidence was altered; no dependencies installed, commits or pushes.
