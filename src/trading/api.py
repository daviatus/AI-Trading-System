import structlog
from fastapi import FastAPI, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from trading.config import get_settings
from trading.db import check_database_connection
from trading.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
logger = structlog.get_logger(__name__)

app = FastAPI(title="AI Trading System", version="0.1.0")


@app.get("/health/live", tags=["health"])
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def readiness() -> dict[str, str]:
    try:
        check_database_connection()
    except SQLAlchemyError as error:
        logger.warning("database_not_ready", error=str(error))
        raise HTTPException(status_code=503, detail="Database is unavailable") from error
    return {"status": "ready"}
