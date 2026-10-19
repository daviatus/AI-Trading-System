"""Track canonical price types, source metadata, and historical import jobs."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_historical_source_jobs"
down_revision: str | None = "0004_candle_source_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ohlcv_candles",
        sa.Column("price_type", sa.String(length=16), server_default="unknown", nullable=False),
    )
    op.add_column(
        "ohlcv_candles",
        sa.Column(
            "quality_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.alter_column("ohlcv_candles", "volume", existing_type=sa.Numeric(24, 8), nullable=True)
    op.drop_constraint("ck_ohlcv_nonnegative_volume", "ohlcv_candles", type_="check")
    op.create_check_constraint(
        "ck_ohlcv_nonnegative_volume",
        "ohlcv_candles",
        "volume IS NULL OR volume >= 0",
    )
    op.drop_constraint(
        "uq_ohlcv_instrument_tf_time_source",
        "ohlcv_candles",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_ohlcv_instrument_tf_time_source_price",
        "ohlcv_candles",
        [
            "instrument_symbol",
            "timeframe_minutes",
            "opened_at",
            "source_provider",
            "price_type",
        ],
    )

    op.create_table(
        "market_data_sources",
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("format", sa.String(length=100), nullable=False),
        sa.Column("timestamp_timezone", sa.String(length=64), nullable=False),
        sa.Column("fixed_offset", sa.Boolean(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("source_id"),
    )
    op.create_table(
        "market_data_import_jobs",
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("instrument_symbol", sa.String(length=16), nullable=False),
        sa.Column("timeframe_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("price_type", sa.String(length=16), nullable=False),
        sa.Column("source_reference", sa.String(length=1024), nullable=False),
        sa.Column("source_checksum", sa.String(length=64), nullable=False),
        sa.Column("requested_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("rows_seen", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("rows_stored", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("gap_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("coverage_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("coverage_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "quality_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("timeframe_minutes IN (1, 5)", name="ck_import_job_timeframe"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="ck_import_job_status",
        ),
        sa.CheckConstraint("rows_seen >= 0 AND rows_stored >= 0", name="ck_import_job_rows"),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["market_data_sources.source_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_symbol"],
            ["instruments.symbol"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index(
        "ix_market_data_import_jobs_source_id",
        "market_data_import_jobs",
        ["source_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1 FROM market_data_import_jobs
                UNION ALL
                SELECT 1 FROM market_data_sources
            )
            """
        )
    ).scalar():
        raise RuntimeError("Cannot downgrade while historical source/job records exist.")
    if bind.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM ohlcv_candles
                WHERE volume IS NULL
                   OR price_type <> 'unknown'
                   OR quality_metadata <> '{}'::jsonb
            )
            """
        )
    ).scalar():
        raise RuntimeError("Cannot downgrade while candles contain Phase 3A.1 metadata.")
    op.drop_index("ix_market_data_import_jobs_source_id", table_name="market_data_import_jobs")
    op.drop_table("market_data_import_jobs")
    op.drop_table("market_data_sources")
    op.drop_constraint(
        "uq_ohlcv_instrument_tf_time_source_price",
        "ohlcv_candles",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_ohlcv_instrument_tf_time_source",
        "ohlcv_candles",
        ["instrument_symbol", "timeframe_minutes", "opened_at", "source_provider"],
    )
    op.drop_constraint("ck_ohlcv_nonnegative_volume", "ohlcv_candles", type_="check")
    op.create_check_constraint(
        "ck_ohlcv_nonnegative_volume",
        "ohlcv_candles",
        "volume >= 0",
    )
    op.alter_column("ohlcv_candles", "volume", existing_type=sa.Numeric(24, 8), nullable=False)
    op.drop_column("ohlcv_candles", "quality_metadata")
    op.drop_column("ohlcv_candles", "price_type")
