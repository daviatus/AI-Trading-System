"""Fail-closed container entrypoint for the supervised cTrader DEMO read-only worker."""

import os
import stat
import sys
from collections.abc import Mapping, MutableMapping
from pathlib import Path

from sqlalchemy.engine import make_url

from trading.ctrader import encrypted_store
from trading.ctrader.oauth import CTraderSecretStore, OAuthError, OAuthManager

MAIN_POSTGRES_PORT = 5432
WORKER_INTERVAL_SECONDS = "300"
MAX_SECRET_BYTES = 4096
WORKER_ARGUMENTS = (
    "worker",
    "--instrument",
    "EURUSD",
    "--environment",
    "demo",
    "--timeframe",
    "M1",
    "--interval-seconds",
    WORKER_INTERVAL_SECONDS,
    "--save-postgres",
)


class WorkerConfigurationError(Exception):
    """A configuration failure whose message never contains secret values."""


def _read_database_url(env: Mapping[str, str]) -> str:
    direct = env.get("DATABASE_URL", "").strip()
    file_path = env.get("DATABASE_URL_FILE", "").strip()
    if direct and file_path:
        raise WorkerConfigurationError("Set only one of DATABASE_URL and DATABASE_URL_FILE")
    if file_path:
        path = Path(file_path)
        try:
            info = os.stat(path)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SECRET_BYTES:
                raise ValueError
            direct = path.read_text(encoding="utf-8").strip()
        except (OSError, ValueError) as error:
            raise WorkerConfigurationError("DATABASE_URL_FILE is unreadable or invalid") from error
    if not direct:
        raise WorkerConfigurationError("DATABASE_URL or DATABASE_URL_FILE is required")
    return direct


def validated_database_url(env: Mapping[str, str]) -> str:
    """Return the isolated test database URL or raise without echoing it."""
    raw = _read_database_url(env)
    try:
        url = make_url(raw)
        backend = url.get_backend_name()
        port = url.port
    except Exception as error:
        raise WorkerConfigurationError("DATABASE_URL is not a valid database URL") from error
    if backend != "postgresql" or not url.host or not url.database:
        raise WorkerConfigurationError(
            "DATABASE_URL must identify an explicit PostgreSQL host and database"
        )
    if port is None:
        raise WorkerConfigurationError("DATABASE_URL must include an explicit port")
    if port == MAIN_POSTGRES_PORT:
        raise WorkerConfigurationError(
            f"DATABASE_URL must not use the main PostgreSQL port {MAIN_POSTGRES_PORT}"
        )
    return raw


def validate_environment(env: Mapping[str, str]) -> str:
    """Validate every worker prerequisite; returns the database URL."""
    if env.get(encrypted_store.BACKEND_ENV, "").strip().lower() != (
        encrypted_store.BACKEND_ENCRYPTED_FILE
    ):
        raise WorkerConfigurationError(
            f"{encrypted_store.BACKEND_ENV} must be '{encrypted_store.BACKEND_ENCRYPTED_FILE}'"
        )
    if env.get("TRADING_MODE", "DEMO").strip().upper() != "DEMO":
        raise WorkerConfigurationError("TRADING_MODE must be DEMO")
    if env.get("LIVE_EXECUTION_ENABLED", "false").strip().lower() not in {"", "false", "0"}:
        raise WorkerConfigurationError("LIVE_EXECUTION_ENABLED must be false")
    url = validated_database_url(env)
    try:
        backend = encrypted_store.backend_from_environment(env)
        OAuthManager(CTraderSecretStore(backend=backend)).validate_stored_configuration()
    except (encrypted_store.EncryptedStoreError, OAuthError) as error:
        raise WorkerConfigurationError(str(error)) from error
    return url


def prepare_environment(env: MutableMapping[str, str]) -> None:
    url = validate_environment(env)
    env.pop("DATABASE_URL_FILE", None)
    env["DATABASE_URL"] = url


def main(argv: list[str] | None = None) -> int:
    if argv:
        print("cTrader worker: command-line arguments are not accepted", file=sys.stderr)
        return 2
    try:
        prepare_environment(os.environ)
    except WorkerConfigurationError as error:
        print(f"cTrader worker: configuration error: {error}", file=sys.stderr)
        return 2
    from trading.ctrader.cli import main as cli_main

    return cli_main(list(WORKER_ARGUMENTS))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
