# Instructions for Coding Agents (GitHub Copilot)

You are implementing a local-first trading platform described in `PROJECT_SPEC.md`, `ARCHITECTURE.md`, `RISK_MANAGEMENT.md`, `AI_MODELS.md`, and `DEVELOPMENT_PLAN.md`.

## Mandatory rules
1. Implement only the explicitly requested phase. Never scaffold the entire platform at once.
2. Before editing code, propose a short plan and list files to be changed.
3. Never create or submit real-money orders. No LIVE execution implementation in v1.
4. Do not fabricate cTrader endpoints, credentials, contract sizes or broker fees. Consult official docs when integration begins.
5. Never commit secrets. Provide `.env.example` with placeholders only.
6. All order intentions go through the independent risk engine.
7. Prevent look-ahead bias and future leakage; test temporal boundaries.
8. Use tests with deterministic fixtures and document how to run them on Windows PowerShell.
9. Prefer narrow interfaces and dependency injection for providers and brokers.
10. Do not claim a backtest is profitable without net-of-cost evidence and out-of-sample evaluation.
11. At the end, summarize changes, test results, assumptions and remaining risks.

## Phase 0 first
Create the Python project skeleton, domain dataclasses/types, interfaces, settings, README, pytest fixtures and a few basic tests. Do not connect to any broker or external market data service yet.
