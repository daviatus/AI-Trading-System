from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SessionWindowSettings(BaseModel):
    open: str
    close: str

    @field_validator("open", "close")
    @classmethod
    def validate_clock_time(cls, value: str) -> str:
        if value == "24:00":
            return value
        try:
            from datetime import time

            time.fromisoformat(value)
        except ValueError as error:
            raise ValueError("Session times must use HH:MM 24-hour format") from error
        return value


class MarketScheduleSettings(BaseModel):
    timezone: str = "UTC"
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    sessions: list[SessionWindowSettings] = Field(
        default_factory=lambda: [SessionWindowSettings(open="00:00", close="24:00")]
    )

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"Unknown market-session timezone: {value}") from error
        return value

    @field_validator("weekdays")
    @classmethod
    def validate_weekdays(cls, value: list[int]) -> list[int]:
        if not value or any(day < 0 or day > 6 for day in value):
            raise ValueError("Market session weekdays must be integers from 0 (Mon) to 6 (Sun)")
        return sorted(set(value))

    @field_validator("sessions")
    @classmethod
    def require_sessions(cls, value: list[SessionWindowSettings]) -> list[SessionWindowSettings]:
        if not value:
            raise ValueError("Market schedule must define at least one session")
        return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    trading_mode: Literal["DEMO", "SIMULATION"] = "DEMO"
    live_execution_enabled: bool = False
    database_url: str = "postgresql+psycopg://trading:change-me@localhost:5432/trading"
    log_level: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    broker_symbol_mappings: dict[str, dict[str, str]] = Field(
        default_factory=dict,
        validation_alias="BROKER_SYMBOL_MAPPINGS",
    )
    market_sessions: dict[str, MarketScheduleSettings] = Field(
        default_factory=dict,
        validation_alias="MARKET_SESSIONS",
    )
    price_jump_thresholds: dict[str, float] = Field(
        default_factory=dict,
        validation_alias="PRICE_JUMP_THRESHOLDS",
    )

    @field_validator("live_execution_enabled")
    @classmethod
    def live_execution_must_remain_disabled(cls, enabled: bool) -> bool:
        if enabled:
            raise ValueError("Live order execution is not implemented and must remain disabled")
        return enabled

    @field_validator("broker_symbol_mappings")
    @classmethod
    def validate_broker_symbol_mappings(
        cls,
        mappings: dict[str, dict[str, str]],
    ) -> dict[str, dict[str, str]]:
        valid_symbols = {"WTI", "XAUUSD", "EURUSD", "SPX500"}
        for provider, symbols in mappings.items():
            if not provider.strip():
                raise ValueError("Broker mapping provider names cannot be empty")
            unknown_symbols = set(symbols) - valid_symbols
            if unknown_symbols:
                raise ValueError(f"Unknown canonical symbols in broker mappings: {unknown_symbols}")
            if any(not broker_symbol.strip() for broker_symbol in symbols.values()):
                raise ValueError("Broker-specific symbol mappings cannot be empty")
        return mappings

    @field_validator("market_sessions")
    @classmethod
    def validate_market_sessions(
        cls,
        schedules: dict[str, MarketScheduleSettings],
    ) -> dict[str, MarketScheduleSettings]:
        valid_symbols = {"WTI", "XAUUSD", "EURUSD", "SPX500"}
        unknown_symbols = set(schedules) - valid_symbols
        if unknown_symbols:
            raise ValueError(f"Unknown market-session symbols: {unknown_symbols}")
        return schedules

    @field_validator("price_jump_thresholds")
    @classmethod
    def validate_price_jump_thresholds(cls, thresholds: dict[str, float]) -> dict[str, float]:
        valid_symbols = {"WTI", "XAUUSD", "EURUSD", "SPX500"}
        unknown_symbols = set(thresholds) - valid_symbols
        if unknown_symbols:
            raise ValueError(f"Unknown price-jump threshold symbols: {unknown_symbols}")
        if any(not 0 < threshold < 10 for threshold in thresholds.values()):
            raise ValueError("Price-jump thresholds must be fractions between 0 and 10")
        return thresholds


@lru_cache
def get_settings() -> Settings:
    return Settings()
