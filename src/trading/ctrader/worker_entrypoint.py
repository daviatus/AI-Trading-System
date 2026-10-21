"""Fail-closed container entrypoint for the supervised cTrader DEMO read-only worker."""

import os
import stat
import sys
from collections.abc import Mapping, MutableMapping
from pathlib import Path

from sqlalchemy.engine import make_url

from trading.ctrader import encrypted_store
from trading.ctrader.oauth import CTraderSecretStore, OAuthError, OAuthManager
from trading.ctrader.worker_secrets import SecretStagingError, stage_secrets

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


class WorkerStartupError(Exception):
    """A transient dependency failure safe for Docker to retry."""


def verify_database_identity(env: Mapping[str, str], raw: str) -> None:
    """Optionally pin the isolated deployment before the CLI may perform any writes."""
    expected = tuple(
        env.get(f"CTRADER_EXPECTED_{key}", "").strip() for key in ("DATABASE", "USER", "CLUSTER_ID")
    )
    if not any(expected):
        return
    url = make_url(raw)
    if (
        not all(expected)
        or url.host != "host.docker.internal"
        or url.port != 15432
        or url.database != expected[0]
        or url.username != expected[1]
        or url.drivername != "postgresql+psycopg"
        or url.query
    ):
        raise WorkerConfigurationError("Database target does not match the isolated deployment")
    import psycopg

    try:
        with psycopg.connect(
            host=url.host,
            port=url.port,
            dbname=url.database,
            user=url.username,
            password=url.password,
            connect_timeout=5,
            options="-c default_transaction_read_only=on",
        ) as connection:
            identity = connection.execute(
                "SELECT current_database(),current_user,system_identifier::text "
                "FROM pg_control_system()"
            ).fetchone()
    except psycopg.Error:
        raise WorkerStartupError("Isolated database identity check unavailable") from None
    if identity != expected:
        raise WorkerConfigurationError("Database identity does not match the isolated deployment")


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
    try:
        stage_secrets(env)
    except SecretStagingError as error:
        raise WorkerConfigurationError(str(error)) from None
    url = validate_environment(env)
    verify_database_identity(env, url)
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
    except WorkerStartupError as error:
        print(f"cTrader worker: startup error: {error}", file=sys.stderr)
        return 1
    from trading.ctrader.cli import main as cli_main

    return cli_main(list(WORKER_ARGUMENTS))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
