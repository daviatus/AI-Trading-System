# Phase 6A data, labels and chronology audit

This is a source/evidence audit, not a new historical model evaluation. Existing
Phase 5 and 5B artifacts were read without modification. The inspected 2026
results are diagnostic evidence only and cannot establish independent validation.

## Confirmed existing behavior

- `research/data.py` snapshots each instrument by month with an indexed, read-only
  query, a 50,001-row sentinel, checksums and the existing resource gates. However,
  `load_bars` subsequently concatenates the complete cached instrument history.
  The complete-calendar quality grid and a list of feature segments add temporary
  allocations. Sequential instruments alone do not bound per-instrument memory.
- Existing features use completed current/past OHLC, reset after missing or invalid
  minutes, enforce a 63-row warmup and use float32. The shared feature creator
  calculates volume indicators; volume reliability is not established by OHLC.
- Existing targets use the next contiguous candle open, ATR stop/target paths and
  roundtrip cost scenarios. Long/short/neutral are separate from feature inputs.
  Same-bar stop/target ambiguity takes the stop; targets crossing missing data are
  unavailable. The old terminal gap/exhaustion ordering remains a limitation;
  Phase 6 does not modify or silently reinterpret that frozen evidence.
- Existing `purged_masks` removes labels reaching training/calibration/tuning
  boundaries. It has no explicit embargo, and its test mask does not purge the
  test-end boundary. Existing `label_end_times` uses the full fixed horizon,
  rather than each barrier path's actual latest information-use time.
- The vectorized schedule function matches the broker calendar's weekly windows,
  timezone conversion and dated/recurring holidays. Exposed current contract
  metadata cannot prove historical schedule applicability. Missing closed-market
  minutes must remain separate from unresolved expected-open absences.

## Confirmed Phase 5B evidence and missing evidence

`reports/phase5b-diagnostics.md`, `data/research/phase5b-v1/diagnostics.md` and their
referenced artifacts report 206 of 216 candidates below 30 tuning trades. The
remaining ten have nonpositive selection scores; all nine selected choices are
inactive. This confirms selection inactivity, not its underlying signal gate.
Full historical probabilities, rejected-signal traces, per-row label masks,
sample identities and historical source-code hashes were not saved. They must
be generated in future runs; missing values cannot be reconstructed as observed.

The recorded quality audit counts 16,974 EURUSD, 27,137 XAUUSD and 69,446 WTI
expected-open missing minutes using exposed schedules. Phase 5B recorded no
duplicate/backward/nonminute prepared timestamps, no feature alignment errors,
and no complete features in initial 63-row gap warmups. Its 183 snapshot hashes
and three prepared fingerprints matched. These are recorded findings; this audit
does not claim to rerun a five-year scan or verify original publication times.

Historical bid/ask spreads, financing, price-type certainty, revisions, dynamic
contract specifications and tick-level fill ordering remain unavailable. Missing
data is never interpolated. A positive small-sample 2026 result is not evidence
of a reliable or independently validated edge.

## New isolated Phase 6 definitions

`phase6/features.py` rejects naive/local/nonminute, duplicate/backward or malformed
OHLC before calculation and rejects 2026+ raw candles. It bounds a batch at
400,000 rows by default and emits float32. Indicator state resets at every absent
minute, including scheduled closures. Supplying broker metadata explicitly
excludes outside-session rows, leaving unavailable features at those timestamps.
The compact family includes lagged returns, rolling volatility, ATR fraction,
EMA spread/slope, RSI, candle structure, UTC cyclic time and descriptive
volatility/trend regimes. Volume is excluded by default and requires an explicit
reliability assertion plus finite nonnegative values.

Raw inputs use `opened_at`; both features and labels use `decision_time =
opened_at + 1 minute`. Current completed OHLC is assumed available at that instant;
publication/revision data is absent, so this is an explicit research convention.
The bounded quality audit uses batches of at most 10,080 calendar minutes and
separates known-closed absences, expected-open absences and unclassified absences.
Staleness requires an explicit aware UTC clock. It never infers historical
spreads, provenance or holiday verification from prices.

`phase6/labels.py` supports these versioned experiments:

| Kind | Definition | Label end |
| --- | --- | --- |
| forward | Next open to horizon close, net of explicit roundtrip cost; absolute return threshold | Horizon candle close |
| volatility | Same return with additional causal sigma × sqrt(horizon) × multiplier threshold | Horizon candle close |
| barrier | Two directional stop/target outcomes; adverse-first ambiguous bar, worse adverse open fill, conservative target fill, horizon timeout | Latest actual close used by either directional path |

Codes are `-1` unavailable, `0` no-trade, `1` long and `2` short. Directional net
returns must exceed the threshold and the other side's return; ties are no-trade.
Volatility input must exactly match decision timestamps. For volatility labels it
is per-minute return sigma; for barrier labels the caller explicitly supplies an
ATR/price or other causal stop fraction. Missing future minutes or unresolved
dataset-end paths yield unavailable labels, never synthetic terminal fills.
Future target returns must never be passed to signal generation.

`purged_split(index, label_end, boundaries, embargo_minutes=60)` accepts aware UTC
`train_start`, `train_end`, `calibration_end`, `tuning_end`, `test_end`. All windows,
including test, require actual label end strictly before the right boundary.
Calibration, tuning and test each discard the initial embargo interval. Any
development endpoint after 2026-01-01 UTC is rejected. Earlier labels that would
consume 2026 are unavailable. Chronological separation reduces boundary leakage;
overlapping labels within a window still reduce effective sample size and must
not be presented as independent observations.

The existing 1.5 GiB available RAM / 15 GiB free disk floors remain unchanged.
These numerical helpers are called only after the Phase 6 controller's resource
gate; row limits are additional bounds, not substitutes for the resource gate.
No database, broker, collector or existing research configuration is modified.
