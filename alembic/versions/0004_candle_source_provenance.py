"""Retain candles from multiple providers for source reconciliation."""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_candle_source_provenance"
down_revision: str | None = "0003_seed_instrument_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_ohlcv_instrument_tf_opened_at", "ohlcv_candles", type_="unique")
    op.create_unique_constraint(
        "uq_ohlcv_instrument_tf_time_source",
        "ohlcv_candles",
        ["instrument_symbol", "timeframe_minutes", "opened_at", "source_provider"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_ohlcv_instrument_tf_time_source",
        "ohlcv_candles",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_ohlcv_instrument_tf_opened_at",
        "ohlcv_candles",
        ["instrument_symbol", "timeframe_minutes", "opened_at"],
    )
