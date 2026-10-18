# First Copilot Agent prompt

Read `PROJECT_SPEC.md`, `ARCHITECTURE.md`, `AGENTS.md`, `DEVELOPMENT_PLAN.md`, `RISK_MANAGEMENT.md`, and `AI_MODELS.md` completely.

Implement **Phase 0 only** from `DEVELOPMENT_PLAN.md`.

First, provide a concise implementation plan and proposed directory tree. Then create a Python 3.12+ package with `pyproject.toml`, `.gitignore`, `.env.example`, `README.md`, typed domain entities (`Instrument`, `Bar`, `Quote`, `OrderIntent`, `OrderStatus`, `Fill`, `Position`), interfaces/protocols (`MarketDataProvider`, `BrokerAdapter`, `Strategy`, `RiskPolicy`), configuration models, and pytest tests using synthetic fixtures. Include Ruff and a minimal GitHub Actions workflow.

Use Windows PowerShell commands in README. Keep dependencies minimal and pinned/locked where feasible. All broker calls must remain unimplemented interfaces or fake adapters. Do not implement live trading, broker authentication, machine learning, PostgreSQL or the dashboard in this phase. Do not use n8n or Supabase.

Run the available tests and linter, fix failures, and summarize the resulting structure, assumptions, and next steps. Ask before starting Phase 1.
