# Architecture

## Data flow
Market data providers / cTrader demo -> ingest -> validation -> Parquet + PostgreSQL -> features -> rule strategies + ML signals -> portfolio risk engine -> paper broker OR cTrader demo adapter -> order reconciliation -> monitoring/dashboard.

Research pipeline: versioned dataset -> purged time-series / walk-forward splits -> training -> event-driven backtest -> robustness evaluation -> candidate registry -> explicit human approval -> paper/demo deployment.

## Modules
- `src/trading/config`: typed configuration, environment separation and secret loading
- `src/trading/domain`: canonical instruments, bars, quotes, orders, fills, positions, events
- `src/trading/data`: provider interfaces, ingestion, validation, parquet storage
- `src/trading/storage`: PostgreSQL repositories and migrations
- `src/trading/features`: leak-free indicator generation
- `src/trading/strategies`: deterministic rule-based strategies
- `src/trading/ml`: feature pipelines, models, training and registry
- `src/trading/backtest`: event-driven simulation, transaction costs
- `src/trading/risk`: position sizing, limits and kill switch
- `src/trading/execution`: paper broker, cTrader demo adapter and reconciliation
- `src/trading/portfolio`: exposure, PnL and multi-asset controls
- `src/trading/news`: optional RSS/news adapters, dedup and timestamped scoring
- `src/trading/api`: local read-only dashboard API
- `src/trading/monitoring`: structured logs, health and alerts

## Interface design
Define protocols for `MarketDataProvider`, `BrokerAdapter`, `Strategy`, `RiskPolicy`, `ModelRegistry`, `OrderRepository`. Broker-dependent values are resolved through an `InstrumentSpecification` service. Strategies never call broker APIs directly. All proposed orders must pass independent risk checks.

## Engineering standards
Python 3.12+, type hints, Ruff, pytest, mypy (gradual), SQLAlchemy + Alembic, pydantic-settings, pyarrow, pandas, PostgreSQL. Prefer small composable modules. Pin dependencies via lockfile. Use `Decimal` for money/quantities when practical. Async streaming boundary separated from deterministic simulation.

## Reliability
Idempotency keys for orders; explicit order state machine; reconnect/backoff; stale quote detection; heartbeat; durable event log; broker-vs-local reconciliation; circuit breaker. A disconnected system must not assume a rejected/cancelled order without broker confirmation.
