from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from trading.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


def get_explicit_postgres_engine() -> Engine:
    """Return PostgreSQL only when a complete target was explicitly configured."""
    settings = get_settings()
    if "database_url" not in settings.model_fields_set:
        raise ValueError(
            "DATABASE_URL must be explicitly configured; refusing the default database target"
        )

    url = make_url(settings.database_url)
    if url.get_backend_name() != "postgresql" or not url.host or not url.database:
        raise ValueError("DATABASE_URL must identify an explicit PostgreSQL host and database")
    return create_engine(url, pool_pre_ping=True)


def check_database_connection() -> None:
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
