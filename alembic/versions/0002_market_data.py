"""Add instrument catalog, OHLCV candles, and ingestion checkpoints."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_market_data"
down_revision: str | None = "0001_system_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column(
            "broker_symbol_mappings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("symbol"),
    )
    op.create_table(
        "ohlcv_candles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("instrument_symbol", sa.String(length=16), nullable=False),
        sa.Column("timeframe_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("high", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("low", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("close", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("volume", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("source_provider", sa.String(length=100), nullable=False),
        sa.Column("source_reference", sa.String(length=512), nullable=False),
        sa.CheckConstraint(
            "timeframe_minutes IN (1, 5)",
            name="ck_ohlcv_supported_timeframe",
        ),
        sa.CheckConstraint(
            "high >= low AND high >= open AND high >= close AND low <= open AND low <= close",
            name="ck_ohlcv_price_bounds",
        ),
        sa.CheckConstraint("volume >= 0", name="ck_ohlcv_nonnegative_volume"),
        sa.ForeignKeyConstraint(
            ["instrument_symbol"],
            ["instruments.symbol"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "instrument_symbol",
            "timeframe_minutes",
            "opened_at",
            name="uq_ohlcv_instrument_tf_opened_at",
        ),
    )
    op.create_table(
        "data_ingestion_checkpoints",
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("instrument_symbol", sa.String(length=16), nullable=False),
        sa.Column("timeframe_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("source_key", sa.String(length=512), nullable=False),
        sa.Column("last_candle_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "timeframe_minutes IN (1, 5)",
            name="ck_data_ingestion_checkpoints_supported_timeframe",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_symbol"],
            ["instruments.symbol"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "provider",
            "instrument_symbol",
            "timeframe_minutes",
            "source_key",
        ),
    )


def downgrade() -> None:
    op.drop_table("data_ingestion_checkpoints")
    op.drop_table("ohlcv_candles")
    op.drop_table("instruments")
