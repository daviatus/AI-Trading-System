# STRAT-2D implementation plan

1. Add an isolated `trading.research.strat2d` package; preserve all Phase 5 and
   published STRAT-2C artifacts and existing research configurations.
2. Version the frozen current calendar as an explicitly unverified historical
   hypothesis. Stream gap/session/DST/holiday audits without filling observations.
   Permit only observed contiguous engineering samples; block affected labels and
   censor uncertain positions. A verified-calendar policy remains available for
   performance research and fails closed when verification is absent.
3. Implement the new, explicitly identified `online_softmax_v1` deterministic
   incremental baseline with fixed feature scaling, chronological purging and
   separate incremental calibration. Other model kinds return unsupported.
4. Implement a single-asset, single-position streaming CFD-style simulation with
   next-bar fills, costs, risk/exposure caps, open-position marks and immutable
   ledgers. Persist pending orders, positions, balance, model identity and cursor.
5. Atomically publish immutable transaction directories containing state, model
   and event evidence plus chained receipts. Resume validates all committed hashes;
   ignored staging never advances the cursor. No mutable checkpoint is reset.
6. Exercise synthetic fitting, splits, causality, calendar edge cases, accounting,
   interruption/corruption and deterministic replay. Keep 1.5 GiB available RAM,
   15 GiB free disk, one worker, bounded batches and the 1 GiB process budget.
7. If resource gates pass, use only the completed Jan-Feb 2022 EURUSD prepared
   artifacts for an engineering validation: train before Jan 15, calibrate before
   Feb 1, test in February with embargo/purging. Compare uninterrupted and resumed
   runs in distinct versioned outputs. Publish the consolidated requested report.

Model profitability and historical calendar validity remain unproven regardless
of successful engineering checks. No holdout, annual backtest or service access.
