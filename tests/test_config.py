import pytest
from pydantic import ValidationError

from trading.config import Settings


def test_demo_mode_is_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.trading_mode == "DEMO"
    assert settings.live_execution_enabled is False


def test_live_execution_cannot_be_enabled() -> None:
    with pytest.raises(ValidationError, match="must remain disabled"):
        Settings(_env_file=None, live_execution_enabled=True)


def test_live_mode_is_not_a_supported_configuration() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, trading_mode="LIVE")


def test_broker_symbol_mappings_are_configurable() -> None:
    settings = Settings(
        _env_file=None,
        broker_symbol_mappings={"ctrader": {"WTI": "broker-defined-wti"}},
    )

    assert settings.broker_symbol_mappings["ctrader"]["WTI"] == "broker-defined-wti"


def test_broker_symbol_mappings_reject_unknown_instruments() -> None:
    with pytest.raises(ValidationError, match="Unknown canonical symbols"):
        Settings(_env_file=None, broker_symbol_mappings={"ctrader": {"GOLD": "gold"}})
