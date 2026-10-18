import asyncio
import random
from datetime import datetime

import structlog
from sqlalchemy.exc import SQLAlchemyError

from trading.data.domain import InstrumentCode, Timeframe
from trading.data.ingestion import ingest_historical
from trading.data.providers import MarketDataProvider
from trading.data.repository import MarketDataRepository
from trading.data.validation import CandleGap

logger = structlog.get_logger(__name__)
RETRYABLE_ERRORS = (SQLAlchemyError, TimeoutError, ConnectionError, OSError)


async def backfill_historical(
    provider: MarketDataProvider,
    repository: MarketDataRepository,
    instrument: InstrumentCode,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    *,
    batch_size: int = 500,
    retries: int = 3,
    retry_base_seconds: float = 0.25,
) -> list[CandleGap]:
    if retries < 0:
        raise ValueError("retries cannot be negative")
    if retry_base_seconds < 0:
        raise ValueError("retry_base_seconds cannot be negative")
    for attempt in range(retries + 1):
        try:
            return await ingest_historical(
                provider,
                repository,
                instrument,
                timeframe,
                start,
                end,
                batch_size=batch_size,
            )
        except RETRYABLE_ERRORS as error:
            if attempt >= retries:
                logger.error(
                    "market_data_backfill_failed",
                    provider=provider.name,
                    instrument=instrument.value,
                    timeframe=timeframe.label,
                    attempts=attempt + 1,
                    error=str(error),
                )
                raise
            delay = retry_base_seconds * (2**attempt) * random.uniform(0.8, 1.2)
            logger.warning(
                "market_data_backfill_retry",
                provider=provider.name,
                instrument=instrument.value,
                timeframe=timeframe.label,
                attempt=attempt + 1,
                delay_seconds=delay,
                error=str(error),
            )
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable backfill retry state")
