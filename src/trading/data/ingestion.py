import asyncio
from datetime import UTC, datetime, timedelta

import structlog

from trading.data.domain import (
    Candle,
    Instrument,
    InstrumentCode,
    Timeframe,
    configured_instruments,
)
from trading.data.providers import MarketDataProvider
from trading.data.repository import MarketDataRepository
from trading.data.validation import CandleGap

logger = structlog.get_logger(__name__)


async def ingest_historical(
    provider: MarketDataProvider,
    repository: MarketDataRepository,
    instrument: InstrumentCode,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    *,
    batch_size: int = 500,
    instrument_record: Instrument | None = None,
) -> list[CandleGap]:
    """Ingest a UTC half-open time range; checkpoint only its contiguous prefix."""
    _validate_bounds(start, end, timeframe, batch_size)
    start = start.astimezone(UTC)
    end = end.astimezone(UTC)
    record = instrument_record or configured_instruments()[instrument]
    interval = timedelta(minutes=timeframe.value)
    previous_checkpoint = await asyncio.to_thread(
        repository.get_checkpoint,
        provider.name,
        instrument,
        timeframe,
        provider.source_key,
    )
    if previous_checkpoint is not None:
        previous_checkpoint = previous_checkpoint.astimezone(UTC)
    query_start = (
        max(start, previous_checkpoint + interval) if previous_checkpoint is not None else start
    )
    if query_start >= end:
        logger.info(
            "market_data_ingestion_already_current",
            provider=provider.name,
            instrument=instrument.value,
            timeframe=timeframe.label,
            source_key=provider.source_key,
        )
        return []

    contiguous_checkpoint = previous_checkpoint
    expected_timestamp = (
        previous_checkpoint + interval if previous_checkpoint is not None else start
    )
    previous_observed: datetime | None = None
    previous_candle: Candle | None = None
    batch: list[Candle] = []
    gaps: list[CandleGap] = []
    received = 0
    stored = 0

    async for candle in provider.history(instrument, timeframe, query_start, end):
        if candle.instrument != instrument or candle.timeframe != timeframe:
            raise ValueError("Market data provider returned an unexpected instrument/timeframe")
        if not query_start <= candle.opened_at < end:
            raise ValueError("Market data provider returned a candle outside the requested range")
        if previous_observed is not None and candle.opened_at == previous_observed:
            if previous_candle != candle:
                raise ValueError(f"Provider returned conflicting candle at {candle.opened_at}")
            continue
        if previous_observed is not None and candle.opened_at < previous_observed:
            raise ValueError("Provider history must be ordered by ascending candle timestamp")

        if previous_observed is None and candle.opened_at > expected_timestamp:
            gap = CandleGap(
                missing_from=expected_timestamp,
                missing_through=candle.opened_at - interval,
                missing_count=int((candle.opened_at - expected_timestamp) // interval),
            )
            gaps.append(gap)
            logger.warning(
                "market_data_gap_detected",
                instrument=instrument.value,
                timeframe=timeframe.label,
                missing_from=gap.missing_from.isoformat(),
                missing_through=gap.missing_through.isoformat(),
                missing_count=gap.missing_count,
            )
        elif previous_observed is not None:
            difference = candle.opened_at - previous_observed
            if difference > interval:
                missing_count = int(difference // interval) - 1
                gap = CandleGap(
                    missing_from=previous_observed + interval,
                    missing_through=candle.opened_at - interval,
                    missing_count=missing_count,
                )
                gaps.append(gap)
                logger.warning(
                    "market_data_gap_detected",
                    instrument=instrument.value,
                    timeframe=timeframe.label,
                    missing_from=gap.missing_from.isoformat(),
                    missing_through=gap.missing_through.isoformat(),
                    missing_count=gap.missing_count,
                )

        if candle.opened_at == expected_timestamp:
            contiguous_checkpoint = candle.opened_at
            expected_timestamp += interval

        previous_observed = candle.opened_at
        previous_candle = candle
        batch.append(candle)
        received += 1
        if len(batch) >= batch_size:
            stored += await _persist_batch(
                repository,
                record,
                timeframe,
                provider,
                batch,
                contiguous_checkpoint,
            )
            batch.clear()

    if batch:
        stored += await _persist_batch(
            repository,
            record,
            timeframe,
            provider,
            batch,
            contiguous_checkpoint,
        )

    logger.info(
        "market_data_ingestion_completed",
        provider=provider.name,
        instrument=instrument.value,
        timeframe=timeframe.label,
        received=received,
        inserted=stored,
        gaps=len(gaps),
        checkpoint=contiguous_checkpoint.isoformat() if contiguous_checkpoint else None,
    )
    return gaps


async def _persist_batch(
    repository: MarketDataRepository,
    instrument: Instrument,
    timeframe: Timeframe,
    provider: MarketDataProvider,
    candles: list[Candle],
    checkpoint: datetime | None,
) -> int:
    return await asyncio.to_thread(
        repository.persist_batch,
        instrument,
        timeframe,
        provider.name,
        provider.source_key,
        provider.source_reference,
        list(candles),
        checkpoint,
    )


def _validate_bounds(
    start: datetime,
    end: datetime,
    timeframe: Timeframe,
    batch_size: int,
) -> None:
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("Ingestion start must be timezone-aware")
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("Ingestion end must be timezone-aware")
    if start >= end:
        raise ValueError("Ingestion start must be earlier than end")
    start_utc = start.astimezone(UTC)
    if start_utc.second or start_utc.microsecond or start_utc.minute % timeframe.value:
        raise ValueError(f"Ingestion start must align to {timeframe.label} candle boundaries")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
