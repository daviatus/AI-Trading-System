# STRAT-2C–2I technical closure review — 2026-10-29

## 1. Executive summary

The research program implemented and historically exercised a bounded, offline, deterministic pipeline for EURUSD M1 January–February 2022. Preparation, incremental fitting, calibration, simulation, diagnostics, atomic recovery and independent replay are implemented. Historical engineering validation passed through STRAT-2I. Independent predictive generalization and profitable trading have **not** been demonstrated.

This review introduces no models, experiments, historical data access, parameter changes or refactoring. STRAT-NN-1 is a proposed next track, **not implemented**. The Git checkpoint packages reviewed code/tests/configurations/reports; large local research artifacts remain excluded, with verified identities recorded in the accompanying artifact manifest.

## 2. Repository and chronology

Verified worktree: `C:\Users\jager\ai_trader\AI-Trading-STRAT-2C`; branch `feature/strat-2c-multimonth-checkpoint`. Audit-start HEAD: `86d086d92e2226a199e51bfbb690a97b41b87fd0`. Remote `origin` is `https://github.com/daviatus/AI-Trading-System.git`. At review start this branch had no upstream and no remote branch. A normal upstream-setting push creates this same feature branch; there is no merge or history rewrite.

The related `AI-Trading-System` worktree is actually on `feature/3e-ctrader-supervised-worker`, not main. Another worktree, `.worktrees/phase6-diagnostics`, is on `fix/phase6-diagnostics-20261024`. Their worktrees, branches and files are untouched. Main remains separate.

| Phase | Objective and actual implementation | Tests reported at that phase | Historical status, resources and replay | Scientific conclusion / open issues |
| --- | --- | --- | --- | --- |
| 2C | Multi-month preparation without full-data loading; new `phase6/streaming.py`, feature continuity hooks, prepare CLI, provenance/resource/command restrictions and useful credential-safe startup diagnostics | Implementation broad suite 242 passed; completed historical validation suite 98 passed; earlier targeted startup/preparation suites recorded separately | 58,683 input/output rows; 9 chunks, generation8 complete; 47/47 replay hashes; preparation peak152.4 MiB, sampled RAM minimum1.68 GiB | Causal preparation engineering passed; calendar correctness and trading value were not established |
| 2D initial | New incremental `online_softmax_v1`, versioned calendar/quality gate, journal, bounded reader and single-position next-bar portfolio | 138 passed in initial implementation report | Initial historical attempt blocked at1.35 GiB versus1.50 floor; no historical peak claimed then | Synthetic fitting/accounting/recovery supported; historical validation still pending at that point |
| 2D memory | Lazy prepared verification, projected frames, cached native ctypes APIs, compact model constants and streamed canonical JSON | 52 targeted passed; exact baseline/optimized state comparisons | Synthetic pipeline median112.26→84.35 MiB; startup101.35→76.75 MiB; historical pre-optimization baseline145.43 MiB; optimized execution initially resource-blocked | Optimizations preserved numerical semantics; synthetic memory savings were not substituted for historical measurements |
| 2D admitted | Dedicated opt-in1.30 GiB /256 MiB profile; real worker PID monitoring corrected a launcher/worker attribution error | 71 targeted passed including19 profile cases | Four chronological passes over same58,683 rows; 240 transactions, head239; 721/721 replay; corrected monitored historical peak90.20 MiB; minimum RAM1.456 GiB | Zero fills; historical accounting invariants passed, but no historical execution-cost evidence from fills |
| 2E | Ordered model→confidence→order→execution funnel; deterministic synthetic long/short/cost/capital/gap/open-position crash tests; calendar evidence registry | 98 targeted pytest plus4 lightweight unittest cases; no double-counting claim | Earlier1.25 GiB admission stop preserved; later1.30 run passed:58,683 rows,28,617 equity records reconciled;181/181 replay; peak87.68 MiB, sampled RAM minimum1.570 GiB | All25,830 predictions failed0.55; calibrated max LONG0.47835, SHORT0.41099. Synthetic fills validate execution path; actual historical fills remain zero. Broker calendar unverified |
| 2F | Frozen-model reconstruction, class/calibration/feature metrics, four simple baselines, thresholds and dependent day-block uncertainty | 135 broad passed;18 focused cases passed after test-helper import corrections | 25,446 eligible final observations;181/181 replay; peak93.32 MiB | BA≈0.33334; calibrated loss1.05039 versus prior1.06685; no demonstrated directional advantage. Engineering PASS, research INCONCLUSIVE |
| 2G | Frozen protocol:3 targets ×3 lightweight feature/model families; causal61-bar history,31-bar delayed-label buffer, chronological selection separate from final development evaluation | 99 regression passed;14 final cases; later53 profile-focused cases; overlapping suites are not additive totals | All9 configurations completed;73,137 independently checked prediction events;181/181 replay; peak98.47 MiB; minimum sampled RAM1.43316 GiB under isolated1.20 profile | Selected h15 `linear12`: BA0.35070 versus reference0.33604. Loss difference versus reference remains uncertain. No0.55 directional signals; INCONCLUSIVE |
| 2H | Frozen-candidate identity, conditional returns/confidence/temporal/fee sensitivity; no refitting; future confirmation design | 124 distinct cases across initial broad/focused work; later1.00-profile validation56 passed | Initial1.20 attempt stopped at1.195037842 GiB before transactions. Explicitly authorized H1.00 profile later completed58,683 rows/49,482 events,60 transactions;181/181 replay; peak96.73 MiB, minimum RAM1.528141 GiB | Raw LONG/SHORT maxima0.443710/0.402011 are already below0.55; temperature softening is not the sole explanation. Conditional economics insufficient; INCONCLUSIVE |
| 2I | Direct read-only checksum-whitelisted two-month source audit; raw/prepared identity; exact pip/cash cost diagnostics;3 predefined future target designs; separate confirmation proposal | 89 broad plus21 final I cases, representing91 distinct semantic cases; initial fixture issue preserved and corrected | 58,683 raw/prepared rows identical;49,482 frozen events;180 transactions/head179;541/541 replay; peak101.06 MiB, minimum RAM1.670196533 GiB, disk26.4044 GiB | No duplicate/OHLC/precision defects detected; calendar/price/fee provenance remains unresolved. Forced-direction reference-cost advantage contradicted; overall readiness INCONCLUSIVE |

Original phase reports retain both early blocked attempts and later completions. Their historical test counts are evidence for those runs, not a single summed test total or a promise that all modules were always tested together. The current checkpoint suite and exact results are recorded in the reviewed change manifest.

## 3. End-to-end architecture and module map

```mermaid
flowchart LR
    A[Whitelisted monthly OHLCV] --> B[Checksums and integrity]
    B --> C[Streaming causal features]
    C --> D[Gap-safe delayed labels]
    D --> E[Chronological fitting]
    E --> F[Past-only calibration]
    F --> G[Frozen probabilities]
    G --> H[Unchanged 0.55 gate]
    H --> I[Next-bar simulator]
    I --> J[Cash equity fills costs]
    C --> K[Atomic checkpoints]
    E --> K
    J --> K
    K --> L[Resume and independent replay]
    G --> M[Read-only scientific diagnostics]
    M --> L
```

| Layer | Actual modules / tests / entrypoints | Contract |
| --- | --- | --- |
| Historical registration | `research/data.py`, `phase6/runner.py`, `phase6/controller.py`; startup/controller tests | Registered monthly provenance/checksums, explicit offline scope, prepare-only command isolation; no collector or broker startup |
| Preparation | `phase6/streaming.py`, `phase6/features.py`, `phase6/labels.py`; `test_phase6_streaming.py`, `test_strat2c_*` | Arrow batches; ordered UTC minute opens; completed-bar decisions; bounded feature history and exact recursive EMA seeds; gap/session resets; pending forward outcomes |
| Sealed prepared input | `strat2d/prepared.py`, `strat2d/pipeline.py:input_batches/source_manifest` | Hash/envelope/receipt validation, chronological alignment, bounded column projection, calendar fingerprint matching |
| Calendar and audit | `strat2d/calendar.py`, `strat2i/quality.py`; `test_strat2d.py`, `test_strat2i.py` | Explicit version/timezone/verification state; no candle filling; absence categories are hypotheses, not provider-fault attribution |
| Model fitting | `strat2d/model.py`, D pipeline; G `core.py`/`pipeline.py` | Float64 SGD softmax with fixed scales/seed and decreasing learning rate; compact weights; no retained training history; causal label maturity and split checks |
| Calibration / predictions | Same model plus G/F pipelines | Past calibration only, bounded scalar temperature, probabilities normalized; model frozen before selection/final evaluation |
| Qualification and simulation | `strat2d/portfolio.py`; E `audit.py`; `test_strat2e.py` | Direction must beat FLAT and0.55; pending order fills on next observed admissible bar; one position, exposure/capital limits, gap censorship; no execution API imports |
| Accounting | `strat2d/portfolio.py` | Cash/equity, realized/unrealized P&L, fees, spread/slippage, fills/trades, drawdown, pending/open state; events persisted incrementally |
| Persistence | C streaming envelope/receipts; `strat2d/journal.py` reused by D–I | Exclusive writes, fsync, directory-rename commit, chained SHA-256 receipts, canonical logical hashes; preserve orphan staging and reject incompatible identity |
| Independent diagnostics | `strat2f/{metrics,analysis,verify}.py`, G/H/I analysis, I economics | Incremental sufficient statistics, explicit benchmarks, pip/price units, day-block resampling, independent arithmetic/source checks |
| Resource monitoring | `safety.py`, `strat2d/runtime.py`/Monitor, G/H `resources.py`, `tools/strat2d_historical_130_validation.py` | Lazy startup gates, cached native Windows probes; actual child PID external polling; fail closed on RAM/disk/RSS/time/probe failure |
| Orchestration | `tools/strat2{c,d,e,f,g,h,i}*_validation.py`, D CLI runner | Synthetic-first, exclusive versioned outputs, crash/resume/reference verification; existing historical output versions must never be reset |

The original Phase6 batch model/execution interfaces remain. STRAT-2D is a separate explicit baseline, not a silent replacement for Phase6 logistic/tree/XGBoost definitions. Simulation support is substantially stronger than evidence for trading utility.

## 4. Historical coverage and integrity

Authorized observations: EURUSD M1 `[2022-01-01T00:00:00Z, 2022-03-01T00:00:00Z)`,58,683 rows. Jan–Feb has84,960 UTC minute cells:58,683 observed and26,277 absent. Internal gaps count301 intervals/23,516 minutes:23,088 hypothesized weekend +50 scheduled-break +378 expected-open minutes. An additional2,761 leading-edge minutes explain full-window absence; there is no trailing-edge omission.260 internal gaps touch expected-open time.

The16 outside-session observations are present candles at21:59 UTC/16:59 New York, not missing minutes. All remain preserved. No authoritative source-account 2022 schedule/holiday/price-side attestation was established. The exact broker cannot be inferred from cTrader, symbol metadata or generic forex hours. No calendar correction is justified merely by seeing candles. No missing candles were fabricated. US DST does not change inside this interval.

I matched all raw/prepared rows and checked6,191 early post-reset feature rows with zero finite-feature warmup violations. One≥20bps open-versus-prior-close jump spans a gap; none spans contiguous bars. These engineering checks do not prove original collection completeness or executable quote semantics.

The2026 holdout remains excluded and untouched. This closure audit does not read new candles, traverse the Phase5 junction or run historical experiments; it verifies only named existing output artifacts and reports.

## 5. Model/target experiments and selection

G targets: h5 cost-aware±2.1bps; h15 cost-aware±2.1bps; h30 neutral zone max(2.1bps,0.5×causal sigma20×sqrt30). Each uses reference8, linear12 and interaction16 features with the same online-softmax kernel: nine configurations, not eighteen models because raw/calibrated probabilities are two views.

G chronological roles: train Jan1–15; calibration Jan15–Feb1; selection Feb1–15; final development evaluation Feb15–Mar1, exclusive right endpoints. Nontraining roles embargo60 minutes. All candidates require common30-minute path maturity and strict split-end purging, intentionally over-purging h5/h15 for comparable timestamps. Selection uses selection-only calibrated log loss, minimum1,000 rows/five days, simplicity preference and0.005 complexity hurdle. The h15 selected family is linear12; the final slice does not select a replacement.

Frozen h15 identity: model schema `strat2g-model-state-v1`, kernel `online_softmax_v1`, learning rate0.003, L2=0.01, seed20261027,12 features,11,675 training rows and13,428 calibration rows. Candidate canonical SHA-256 `bc72d1443ecca6ca69040ef814c521e23077d45a12ebb4f4a7fe4b95b2c967a6`. Temperature≈1.38405. Exact specification and checkpoint/receipt identities are in H's frozen protocols and the artifact manifest.

I defined three future h15 targets—reference cost2.1bps, stress cost3.1bps and volatility-scaled cost neutral zone—without fitting models or choosing a winner. They must not silently replace the frozen confirmation target.

## 6. Training/backtesting capability and limits

Compatible incremental softmax fitting/calibration/inference, bounded simulation, transaction accounting and recovery have synthetic and historical engineering evidence. Synthetic tests deliberately force profitable/losing long/short entries, costs, rejected capital, open-position crashes and next-bar semantics. Historical baseline runs have zero fills, so they do **not** validate actual historical entry/exit fills or demonstrate realized trading advantage.

Nonincremental logistic, tree and XGBoost definitions are explicitly unsupported by the streaming baseline; there is no silent substitution. More instruments, broker price-side conventions, multi-position portfolio netting, financing and non-USD conversion require separate implementations/validation. No live/paper/broker execution capability is authorized by this work.

## 7. Resource history and policy

Hardware target: Windows11 Home,8GiB RAM, Python3.12, one numerical/Arrow worker. General training/backtest available-RAM minimum is1.50GiB. Preparation-only C uses1.25GiB. Explicit legacy D/E/F profile1.30, G profile1.20 and H profile1.00 remain separately scoped. H/I worker RSS ceiling256MiB; disk floor15GiB; batch1,024. These exceptions are not global defaults or authorization for neural training.

Memory fixes include avoiding a prepared-reader dependency on SQLAlchemy/feature imports, bounding frames/events, caching ctypes structures to eliminate pointer-type cache growth, and streaming JSON serialization. The first external D probe measured the venv launcher rather than worker; its tiny RSS figures are invalid worker measurements. Real-PID publication/polling corrected that and was validated separately. OS available RAM and process RSS remain different quantities. A successful run with>1.5GiB available does not prove operation near1.00GiB.

## 8. Recovery, tests and reproducibility

Data/protocol/code/library identities bind journals. Before/after-publication crashes, immutable prefixes, repeated resume, corrupted receipts/state, deterministic logical hashes and independent uninterrupted replay are covered. I has180 commits, head179,541 matching files and state hash `72170e6e9d0a9ee6df098330ad0005410c04724debccadfab96caf4012d0a3d3`. Independent arithmetic verification complements byte equality; hashes alone do not establish scientific correctness.

This checkpoint runs the full relevant synthetic Phase6, STRAT2 and research suites sequentially with single-thread numerical settings, plus Ruff and whitespace checks. Exact commands/results, candidate files and artifact verification are in `strat-2c-to-2i-reviewed-change-manifest-2026-10-29.json`. No new historical training/backtest was run for Git closure.

Two existing test-isolation defects surfaced in the combined run: E's no-scientific-import assertion inspected an already-populated interpreter, and I's mocked admission test depended on real historical output paths being absent. The fixes exercise E's actual function in a fresh subprocess and isolate I's output/config fixture in temporary paths. Research algorithms and published evidence are unchanged. Failed and final test XML files remain local.

Git `core.autocrlf=true` would otherwise change exact protocol/code bytes on later checkouts. Narrow `.gitattributes` rules mark the research files `-text`, preserving their existing LF/CRLF bytes; no source/report is renormalized. Git's index-vs-working-file byte hashes are reviewed before commit. Historical dependency fingerprints still require the corresponding code/environment version, rather than silently accepting newer code.

## 9. Predictive and economic findings

G selected h15 calibrated balanced accuracy0.350703 versus reference0.336043 and class prior0.333333. Loss1.084392 versus reference1.085373 has a candidate/reference dependent confidence interval crossing zero. No prediction reached the unchanged0.55 directional gate. Temperature softens outputs, but raw maxima also fall below this gate; lowering it is not an evidence-based solution.

I forced-direction mean gross movement is0.294708bps/0.331396pips; exact conditional base-cost net is−1.805295bps/−2.042351pips. This diagnostic ignores FLAT dominance and is not a portfolio strategy; its dependent net intervals are negative. Only32.875% cover modeled base costs. Descriptive0.30/0.35 gates have sparse positive base means but intervals cross zero and means turn negative under3.1bps stress. Gate0.40 has only five observations;0.45/0.50/0.55 have none. No gate was selected or promoted.

Actual historical bid/ask/fees are unavailable. Open API commissionType2 means USD per lot; the snapshot's precise rate conditionally implies USD3 per100,000EUR lot, not automatically constant0.6bps. Historical application and charging side remain unverified. Price, pips, gross opportunity, net conditional opportunity and executed profit are distinct.

Two months were repeatedly reused through multiple phases and nine candidate configurations. Overlapping minute labels are not independent;12 final observed days give only roughly six2-day or two5-day nonoverlapping blocks. Circular observed-day bootstrap does not model all regime changes and can bridge weekends. No independent profitability or generalization claim is justified.

## 10. Architecture risks and technical debt

- D/E/F/G/H/I orchestration repeats PID publication, audit output, guard dispatch and report writing. Resource adapters are layered D→G→H; I must use an H-prefixed path despite having its own namespace. This is deliberate scope protection but overly coupled. Refactor only in a later version with compatibility tests.
- Multiple feature implementations exist: original batch, C streaming/EMA, and G causal engineered features. Preserve their scientific identities; do not assume their scales/features are interchangeable.
- Code-wide fingerprints and environment-sensitive float/serialization behavior make old resume intentionally strict. Even adding a module within a globbed package can change identity. Keep new NN code in a new namespace; never edit old artifacts to make them load.
- Journals scan/hash the entire committed chain at startup; append-only storage and metadata budgets are bounded but startup cost grows. There is no authenticated artifact catalog or portable storage locator; data/evidence are excluded from Git.
- Configuration and protocols contain hashes/paths to local artifacts. A clone supports synthetic tests, not immediate historical replay without independently provisioned, hash-verified inputs and compatible environments. The Phase5 junction is a local dependency and is never versioned.
- Historical reports include incremental updates, older blocked status tables, encoding artifacts and historical test totals. Read their completion sections and this review; preserve original bytes as evidence rather than tidying them destructively.
- Known NumPy-timedelta deprecations remain. Tests and CLI modules are source-worktree oriented; setuptools package discovery does not include every standalone research `tools` module as a console entrypoint.
- Future-label-based exclusions must remain offline metric/label admission only; never leak them into real-time order eligibility. Model/optimizer state publication must stay atomic.
- Calendar, price-side, cost and untouched-period provenance are research blockers, not problems solved by a more complex estimator.

## 11. Reusable infrastructure

Retain the sealed prepared reader, causal buffering, strict split/label maturity logic, journal receipts/atomic transactions, numerical metric oracles, synthetic execution/accounting scenarios, native RSS monitoring, exclusive versioned outputs and independent replay. Use the artifact manifest to locate evidence without touching raw junction targets. Keep production trading thresholds and previous frozen protocols unchanged.

## 12. Recommendation for STRAT-NN-1

Start with an explicitly versioned **design and synthetic feasibility** study: one small causal model, fixed baseline comparison, deterministic optimizer/RNG state and bounded checkpoint adapter in a new namespace. A compact NumPy MLP is a candidate to investigate using existing dependencies; do not presume a large framework fits the256MiB cap, install one automatically or substitute a neural model into old pipelines.

Preregister architecture, features, targets, loss, calibration, split/purge/embargo and stopping rules before experiment outcomes. Profile startup/fitting/inference/checkpoint RSS in fresh synthetic processes. Require exact resumed/uninterrupted state equivalence where feasible and explicit deterministic numerical tolerances otherwise. Ordinary1.50GiB policy applies unless a separately scoped policy is explicitly authorized and tested; H/I exceptions must not be extended automatically.

Before any new historical period, resolve calendar/price/cost provenance and untouched-data status, then obtain explicit authorization. January–February may only support clearly labeled exploratory comparisons.2026 is prohibited. NN capacity is not a remedy for insufficient economic labels, costs or independent evidence.

Legacy E report publication: the original `reports/strat-2e-trade-execution-calendar-validation-v1.md` is preserved locally, byte-for-byte, and deliberately untracked because131 lines contain doubled CR terminators. The reviewed Git reading copy is `reports/publication/strat-2e-trade-execution-calendar-validation-v1.md`; only its line endings are normalized. Both hashes and the transformation are in the artifact manifest. This does not rewrite original evidence.
