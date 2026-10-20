# Project rules

- Follow the existing Python package architecture, typing, formatting, and test conventions.
- Reuse existing data validation, provider clients, repositories, and transaction helpers.
- Use UTC timestamps and deterministic market-data handling.
- Keep market-data persistence source-aware; never mix provider series.
- Add focused tests for new behavior; automated tests must not use a user's database or broker API.
- Never hardcode secrets or print credentials, tokens, or connection strings.
- Do not add automatic schema migrations or destructive database operations.
- cTrader is DEMO ONLY and READ-ONLY in the current development phase.
- Never place, modify, or cancel orders; do not implement real-money trading.
- Do not commit or push unless explicitly requested.
