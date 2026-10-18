from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from trading.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


def check_database_connection() -> None:
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
