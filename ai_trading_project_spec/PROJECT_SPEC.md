# AI Trading System — Project Specification

## Objective
Build a local-first, modular, multi-asset algorithmic trading system for one private user on Windows. Primary broker target: Pepperstone cTrader Open API. Start in simulation/demo mode; live execution is disabled by default.

## Markets
- WTI crude oil CFD (exact broker symbol discovered dynamically)
- Gold CFD (exact broker symbol discovered dynamically)
- EUR/USD
- S&P 500 index CFD (exact broker symbol discovered dynamically)

The broker's symbol IDs, contract specifications, minimum volume, volume steps, tick size, trading hours, margin and currency conversion must be fetched and persisted, never assumed from labels.

## Timeframes
1-minute and 5-minute candles. Use UTC timestamps and explicit timezone conversions for display. Preserve raw ticks/bars when licensed and available. Never silently fill missing candles.

## Core functionality
1. Market data adapters with historical and streaming interfaces, data provenance and quality checks.
2. Local storage: PostgreSQL for metadata/orders/events and Parquet for historical time series.
3. Reproducible features and deterministic predefined strategy rules.
4. XGBoost/LightGBM training, walk-forward evaluation, out-of-sample testing, model registry and approval workflow.
5. Event-driven backtesting with spread, commissions, slippage, latency assumptions, financing, contract specification and trading hours.
6. Paper execution and cTrader demo execution, with robust order/position reconciliation.
7. Portfolio-level risk engine, exposure caps, position sizing, daily loss limits, emergency stop.
8. Local dashboard and structured logs/alerts.

## Non-goals for first release
- No live-money orders.
- No autonomous production model replacement.
- No promise of profitability or minimum trade frequency.
- No unapproved scraping or undocumented broker endpoints.
- No n8n or Supabase dependency.

## Initial acceptance criteria
- Clean install on Windows 11 from documented instructions.
- Unit and integration tests run locally without real API credentials.
- Ingest sample OHLCV for all four markets with schema and gap validation.
- Replay a deterministic baseline strategy without look-ahead bias.
- Report gross/net PnL, drawdown, turnover, trade count, and costs.
- Paper broker rejects orders violating risk limits.
- Live execution cannot be enabled by configuration typo or default.
