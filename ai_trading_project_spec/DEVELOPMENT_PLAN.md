# Phased Development Plan

## Phase 0 — Repository and contracts
Scaffold Python package, `pyproject.toml`, tests, linting, `.env.example`, README, architecture decision records, CI. Define canonical types and provider/broker protocols. Use synthetic fixtures; no external API.

## Phase 1 — Data foundation
PostgreSQL migrations, Parquet bar store, instrument catalog, CSV/sample provider, UTC normalization, duplicate/gap checks, data lineage and tests. Implement 1m -> 5m aggregation with session boundaries.

## Phase 2 — Backtester
Event-driven replay, realistic order states, spreads, fees, slippage, position sizing, deterministic tests and performance reports. Benchmark strategies.

## Phase 3 — ML research
Feature pipeline, walk-forward evaluation, XGBoost/LightGBM experiments, reproducible model registry and manual approval.

## Phase 4 — Execution safety
Paper broker, risk engine, kill switch, order lifecycle, reconciliation, fault injection tests.

## Phase 5 — cTrader demo
Only after Spotware app approval: OAuth with localhost callback, secure local secret storage, dynamic symbol/account discovery, demo-only enforcement, quote subscription, history limitations, demo orders and reconciliation. Follow current official cTrader API docs and rate limits; do not invent endpoints.

## Phase 6 — Dashboard and operations
Local dashboard, metrics, alerts, Windows startup/shutdown instructions, backup/restore and health checks.

## Phase 7 — Paper evaluation
Run weeks of monitored demo/paper operation, compare expected vs executed prices, reject unstable models and audit costs. No automatic switch to live.

## Exit gates
Each phase requires tests, docs, demo or fixture run, and human review before advancing.
