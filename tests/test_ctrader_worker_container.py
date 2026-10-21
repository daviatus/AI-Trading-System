import builtins
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from threading import Event

import pytest
import yaml
from cryptography.fernet import Fernet

from trading.ctrader import worker_entrypoint as entrypoint
from trading.ctrader.cli import _parser
from trading.ctrader.encrypted_store import backend_from_environment, init_key_file
from trading.ctrader.oauth import CTraderSecretStore, OAuthManager, OAuthTokens
from trading.ctrader.worker import run_foreground_worker
from trading.ctrader.worker_entrypoint import WorkerConfigurationError

ROOT = Path(__file__).resolve().parents[1]
TEST_URL = "postgresql+psycopg://user:pw-not-real@test-db:5433/trading_test"


@pytest.fixture
def env(tmp_path):
    key = tmp_path / "key"
    init_key_file(key)
    data = tmp_path / "data"
    data.mkdir()
    if os.name == "posix":
        data.chmod(0o700)
    url_file = tmp_path / "url"
    url_file.write_text(TEST_URL)
    environment = {
        "CTRADER_CREDENTIAL_BACKEND": "encrypted-file",
        "CTRADER_CREDENTIAL_FILE": str(data / "credentials.enc"),
        "CTRADER_CREDENTIAL_KEY_FILE": str(key),
        "DATABASE_URL_FILE": str(url_file),
    }
    store = CTraderSecretStore(backend_from_environment(environment))
    store.set(store.CLIENT_ID_KEY, "dummy-client-id")
    store.set(store.CLIENT_SECRET_KEY, "dummy-client-secret")
    store.set(store.TOKEN_KEY, OAuthTokens("dummy-access", "dummy-refresh", 1).to_json())
    return environment


def test_valid_environment_is_accepted(env):
    assert entrypoint.validate_environment(env) == TEST_URL


@pytest.mark.parametrize(
    "failure",
    [
        "missing-store",
        "corrupt-store",
        "wrong-key",
        "invalid-payload",
        "missing-client-id",
        "missing-client-secret",
        "blank-client-id",
        "blank-client-secret",
        "nonstring-credential",
        "missing-tokens",
        "invalid-token-json",
        "missing-access-token",
        "blank-access-token",
        "missing-refresh-token",
        "blank-refresh-token",
        "invalid-expiry",
        "boolean-expiry",
        "negative-expiry",
        "infinite-expiry",
        "string-expiry",
        "float-expiry",
        "zero-expiry",
        "missing-expiry",
        "nonstring-access-token",
        "nonstring-refresh-token",
    ],
)
def test_invalid_stored_configuration_exits_two_before_cli(env, monkeypatch, capsys, failure):
    secret = "DO-NOT-LOG-SYNTHETIC-SECRET"
    path = Path(env["CTRADER_CREDENTIAL_FILE"])
    key_path = Path(env["CTRADER_CREDENTIAL_KEY_FILE"])
    fernet = Fernet(key_path.read_bytes())
    tokens = {"access_token": secret, "refresh_token": secret, "expires_at": 1}
    values = {
        CTraderSecretStore.CLIENT_ID_KEY: secret,
        CTraderSecretStore.CLIENT_SECRET_KEY: secret,
        CTraderSecretStore.TOKEN_KEY: json.dumps(tokens),
    }
    if failure == "missing-store":
        path.unlink()
    elif failure == "corrupt-store":
        path.write_text(secret)
    elif failure == "wrong-key":
        key_path.write_bytes(Fernet.generate_key())
    elif failure == "invalid-payload":
        path.write_bytes(fernet.encrypt(json.dumps([secret]).encode()))
    else:
        if failure.startswith("missing-client-"):
            values.pop(failure.removeprefix("missing-"))
        elif failure.startswith("blank-client-"):
            values[failure.removeprefix("blank-")] = " \t"
        elif failure == "missing-tokens":
            values.pop(CTraderSecretStore.TOKEN_KEY)
        elif failure == "nonstring-credential":
            values[CTraderSecretStore.CLIENT_ID_KEY] = 123
        elif failure == "invalid-token-json":
            values[CTraderSecretStore.TOKEN_KEY] = secret
        else:
            if failure == "missing-expiry":
                tokens.pop("expires_at")
            elif failure.startswith("missing-"):
                tokens.pop(failure.removeprefix("missing-").replace("-", "_"))
            elif failure.startswith("blank-"):
                tokens[failure.removeprefix("blank-").replace("-", "_")] = " \t"
            elif failure.startswith("nonstring-"):
                tokens[failure.removeprefix("nonstring-").replace("-", "_")] = 123
            else:
                tokens["expires_at"] = {
                    "invalid-expiry": secret,
                    "boolean-expiry": True,
                    "negative-expiry": -1,
                    "infinite-expiry": float("inf"),
                    "string-expiry": "1",
                    "float-expiry": 1.5,
                    "zero-expiry": 0,
                }[failure]
            values[CTraderSecretStore.TOKEN_KEY] = json.dumps(tokens)
        payload = {f"{CTraderSecretStore.SERVICE}/{key}": value for key, value in values.items()}
        path.write_bytes(fernet.encrypt(json.dumps(payload).encode()))

    def forbidden(*_args, **_kwargs):
        pytest.fail("Invalid configuration reached CLI or token refresh")

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "trading.ctrader.cli":
            forbidden()
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(OAuthManager, "access_token", forbidden)
    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.getaddrinfo", forbidden)
    monkeypatch.setattr(os, "environ", dict(env))
    assert entrypoint.main([]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "cTrader worker: configuration error:" in output.err
    assert secret not in output.err
    assert "Traceback" not in output.err
    assert "DATABASE_URL" not in os.environ
    assert os.environ["DATABASE_URL_FILE"] == env["DATABASE_URL_FILE"]


def test_valid_expired_tokens_are_checked_without_refresh_or_storage_changes(env, monkeypatch):
    path = Path(env["CTRADER_CREDENTIAL_FILE"])
    original = path.read_bytes()

    def forbidden(*_args, **_kwargs):
        pytest.fail("Local startup validation attempted token refresh")

    monkeypatch.setattr(OAuthManager, "access_token", forbidden)
    assert entrypoint.validate_environment(env) == TEST_URL
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://u:p@test-db:5432/trading",
        "postgresql+psycopg://u:p@test-db/trading",
        "postgresql+psycopg://u:p@localhost:5432/trading",
        "sqlite:///x.db",
        "postgresql+psycopg://u:p@:5433/trading",
        "postgresql+psycopg://u:p@test-db:5433",
        "not a url",
    ],
)
def test_unsafe_database_urls_fail_closed_without_leaking(env, tmp_path, url):
    Path(env["DATABASE_URL_FILE"]).write_text(url)
    with pytest.raises(WorkerConfigurationError) as caught:
        entrypoint.validate_environment(env)
    assert ":p@" not in str(caught.value)


def test_missing_database_url_fails(env):
    del env["DATABASE_URL_FILE"]
    with pytest.raises(WorkerConfigurationError, match="required"):
        entrypoint.validate_environment(env)


def test_unreadable_url_file_and_dual_sources_fail(env, tmp_path):
    bad = {**env, "DATABASE_URL_FILE": str(tmp_path / "missing")}
    with pytest.raises(WorkerConfigurationError):
        entrypoint.validate_environment(bad)
    with pytest.raises(WorkerConfigurationError, match="only one"):
        entrypoint.validate_environment({**env, "DATABASE_URL": TEST_URL})


def test_encrypted_backend_and_key_are_required(env, tmp_path):
    with pytest.raises(WorkerConfigurationError):
        entrypoint.validate_environment({**env, "CTRADER_CREDENTIAL_BACKEND": "keyring"})
    with pytest.raises(WorkerConfigurationError):
        entrypoint.validate_environment(
            {**env, "CTRADER_CREDENTIAL_KEY_FILE": str(tmp_path / "missing-key")}
        )


def test_live_trading_settings_are_rejected(env):
    with pytest.raises(WorkerConfigurationError):
        entrypoint.validate_environment({**env, "TRADING_MODE": "LIVE"})
    with pytest.raises(WorkerConfigurationError):
        entrypoint.validate_environment({**env, "LIVE_EXECUTION_ENABLED": "true"})


def test_main_runs_fixed_read_only_demo_worker_command(env, monkeypatch):
    captured = {}
    monkeypatch.setattr(os, "environ", dict(env))
    monkeypatch.setattr(
        "trading.ctrader.cli.main", lambda argv: captured.setdefault("argv", argv) and 0
    )
    assert entrypoint.main([]) == 0
    argv = captured["argv"]
    arguments = _parser().parse_args(argv)
    assert arguments.command == "worker"
    assert arguments.interval_seconds == 300
    assert arguments.environment == "demo"
    assert arguments.save_postgres and not arguments.dry_run and not arguments.once
    assert os.environ["DATABASE_URL"] == TEST_URL
    assert "DATABASE_URL_FILE" not in os.environ


def test_main_rejects_arguments_and_bad_config(env, monkeypatch, capsys):
    monkeypatch.setattr(os, "environ", dict(env))
    assert entrypoint.main(["--once"]) == 2
    monkeypatch.setattr(os, "environ", {})
    assert entrypoint.main([]) == 2
    assert "configuration error" in capsys.readouterr().err


def test_no_order_or_live_flags_in_entrypoint():
    source = (ROOT / "src/trading/ctrader/worker_entrypoint.py").read_text().lower()
    assert "order" not in source.replace("border", "")


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load((ROOT / "compose.ctrader-worker.yaml").read_text())


def test_compose_is_isolated_from_main_stack(compose):
    assert compose["name"] == "ai-trading-ctrader-worker"
    assert set(compose["services"]) == {"ctrader-worker"}
    service = compose["services"]["ctrader-worker"]
    assert "ports" not in service and "depends_on" not in service and "env_file" not in service
    assert "host.docker.internal" not in (ROOT / "compose.ctrader-worker.yaml").read_text()


def test_compose_supervision_and_hardening(compose):
    service = compose["services"]["ctrader-worker"]
    assert service["restart"] == "unless-stopped"
    assert service["stop_signal"] == "SIGTERM"
    assert int(service["stop_grace_period"].rstrip("s")) >= 60
    assert service["user"] == "10001:10001"
    assert service["init"] is True and service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["build"]["dockerfile"] == "Dockerfile.ctrader-worker"
    assert "command" not in service and "entrypoint" not in service


def test_compose_persistence_and_secret_mounts(compose):
    service = compose["services"]["ctrader-worker"]
    assert "ctrader_credentials:/var/lib/ctrader" in service["volumes"]
    assert "ctrader_credentials" in compose["volumes"]
    assert {item["source"] for item in service["secrets"]} == {
        "ctrader_credential_key",
        "ctrader_worker_database_url",
    }
    assert all(item["target"].startswith("/run/host-secrets/") for item in service["secrets"])
    assert any("/run/secrets:" in mount and "mode=0700" in mount for mount in service["tmpfs"])
    environment = service["environment"]
    assert environment["CTRADER_CREDENTIAL_BACKEND"] == "encrypted-file"
    assert environment["CTRADER_CREDENTIAL_KEY_FILE"].startswith("/run/secrets/")
    assert not environment["CTRADER_CREDENTIAL_FILE"].startswith("/run/secrets/")
    assert environment["LIVE_EXECUTION_ENABLED"] == "false"
    assert environment["CTRADER_SECRET_SOURCE_DIR"] == "/run/host-secrets"
    assert environment["CTRADER_EXPECTED_CLUSTER_ID"].startswith("${")
    for secret in compose["secrets"].values():
        assert secret["file"].startswith("${") and ":?" in secret["file"]
    assert not any("PASSWORD" in name or name == "DATABASE_URL" for name in environment)


def test_dockerfile_installs_extra_and_runs_non_root_entrypoint():
    text = (ROOT / "Dockerfile.ctrader-worker").read_text()
    assert '".[ctrader]"' in text
    assert "USER 10001:10001" in text
    assert 'ENTRYPOINT ["python", "-m", "trading.ctrader.worker_supervisor"]' in text
    assert '"trading.ctrader.worker_supervisor", "--healthcheck"' in text
    assert "STOPSIGNAL SIGTERM" in text
    assert "EXPOSE" not in text and "alembic" not in text


def test_ctrader_extra_includes_cryptography():
    text = (ROOT / "pyproject.toml").read_text()
    assert "cryptography" in text.split("ctrader = [")[1].split("]")[0]


def test_secret_paths_are_ignored_by_git_and_docker():
    assert "secrets/" in (ROOT / ".gitignore").read_text()
    assert "secrets/" in (ROOT / ".dockerignore").read_text()


def test_worker_stops_gracefully_after_current_cycle():
    stop = Event()
    cycles = []

    def cycle():
        cycles.append(1)
        stop.set()

    run_foreground_worker(cycle, 300, stop_event=stop)
    assert cycles == [1]


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
def test_sigterm_stops_worker_process_promptly(tmp_path):
    script = (
        "import signal, sys, threading\n"
        "from trading.ctrader.worker import run_foreground_worker\n"
        "stop = threading.Event()\n"
        "signal.signal(signal.SIGTERM, lambda *_: stop.set())\n"
        "print('ready', flush=True)\n"
        "run_foreground_worker(lambda: None, 300, stop_event=stop)\n"
    )
    process = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    assert process.stdout.readline().strip() == "ready"
    time.sleep(0.3)
    process.send_signal(signal.SIGTERM)
    assert process.wait(timeout=10) == 0
