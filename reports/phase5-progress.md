# Phase 5 progress

- Started 2026-10-22. Scope: offline multi-asset research only. Main PostgreSQL,
  Docker runtimes, collectors, credentials, migrations and order execution are excluded.
- Repository audit: Phase 4A supplies causal EMA/RSI/ATR/volatility features and
  gap-aware datasets; no existing ML training or account backtester was found.
  Historical schema has a source/price-aware candle unique index. Prior Phase 4C
  verification found complete checkpoint coverage with unresolved calendar/data gaps.
- Resource plan: explicit port-15432 read-only connections; monthly bounded,
  indexed cache exports; one asset at a time; one CPU training thread; 15 GiB
  Windows free-space floor; bounded deterministic training samples.
- Experiment protocol fixed before final holdout: 15/60-minute holding horizons,
  two small XGBoost configurations, separate chronological probability calibration,
  development walk-forward windows and an untouched 2026 final holdout. Execution
  costs are sensitivity scenarios, not verified live bid/ask costs.
- Implemented research-only cache/audit, gap-reset features, triple-class cost-aware
  labels, adverse-first execution, account sizing, regime flags, XGBoost/calibration,
  portfolio ranking and checkpointed walk-forward/holdout orchestration.
- Research environment `.venv-research` has XGBoost 3.4.1, SciPy 1.18.1 and NumPy
  2.5.4. Existing collector packages were not upgraded. Core project dependencies
  are read from the existing environment via a research-only `.pth` file.
- Resource blocker: 95% physical RAM utilization; repeated measurements found
  0.28–0.53 GiB available on a 7.79 GiB host. Windows free disk remained above
  37 GiB. Full data processing and training were not safe to begin.
- Added a standard-library resource gate before scientific imports, with a 15 GiB
  disk floor and 1.5 GiB minimum available RAM. Neither floor can be lowered through
  configuration. The actual `runner all` attempt stopped at that gate before DB
  export, training or opening the final holdout.
- Validation: Ruff and syntax parsing passed; six standard-library resource-safety
  tests passed. The focused feature/execution/model suite is written but has not run
  because of the memory blocker. No trained models or performance results exist.
- Resume: obtain at least 2–3 GiB available RAM while preserving all services; run
  the resource preflight, focused tests and then the pipeline commands in the Phase
  5 report. Correct any test failures before starting snapshot/training. No extra
  approval is needed within the existing authorized offline scope.
- Final read-only runtime observations: Phase 4C stopped again at 19:16:27 UTC
  after three Docker storage-probe `TimeoutExpired` failures (last heartbeat
  19:15:02 UTC); EURUSD's container is running but unhealthy, with its original
  start time and restart count. No collector stop/restart or Docker lifecycle
  command was executed. Their failure causes are not established by this sprint.
  Collector restoration is excluded by the current Phase 5 restrictions.
