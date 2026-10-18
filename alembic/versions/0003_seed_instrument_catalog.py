"""Seed the canonical supported instrument catalog."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_seed_instrument_catalog"
down_revision: str | None = "0002_market_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO instruments (symbol, display_name, broker_symbol_mappings) VALUES
                ('WTI', 'WTI crude oil', '{}'::jsonb),
                ('XAUUSD', 'Gold', '{}'::jsonb),
                ('EURUSD', 'EUR/USD', '{}'::jsonb),
                ('SPX500', 'S&P 500', '{}'::jsonb)
            ON CONFLICT (symbol) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM instruments
            WHERE symbol IN ('WTI', 'XAUUSD', 'EURUSD', 'SPX500')
              AND NOT EXISTS (
                  SELECT 1 FROM ohlcv_candles
                  WHERE ohlcv_candles.instrument_symbol = instruments.symbol
              )
              AND NOT EXISTS (
                  SELECT 1 FROM data_ingestion_checkpoints
                  WHERE data_ingestion_checkpoints.instrument_symbol = instruments.symbol
              )
            """
        )
    )
