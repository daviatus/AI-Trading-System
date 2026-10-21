import os
import stat
from pathlib import Path
from unittest.mock import MagicMock

import psycopg
import pytest

from trading.ctrader import worker_entrypoint as entrypoint
from trading.ctrader.worker_secrets import SECRET_FILES, SecretStagingError, stage_secrets

URL = "postgresql+psycopg://test:synthetic-secret@host.docker.internal:15432/disposable"
EXPECTED = {
    "CTRADER_EXPECTED_DATABASE": "disposable",
    "CTRADER_EXPECTED_USER": "test",
    "CTRADER_EXPECTED_CLUSTER_ID": "123",
}


@pytest.fixture
def staging(tmp_path):
    source = tmp_path / "host"
    target = tmp_path / "private"
    source.mkdir()
    target.mkdir(mode=0o700)
    for name in SECRET_FILES:
        (source / name).write_bytes(b"synthetic-secret")
    return source, target, {"CTRADER_SECRET_SOURCE_DIR": str(source)}


def test_staging_is_opt_in(tmp_path):
    stage_secrets({}, tmp_path / "absent")


def test_staging_preserves_sources_and_atomically_restages(staging):
    source, target, env = staging
    for _ in range(2):
        stage_secrets(env, target)
        for name, variable in SECRET_FILES.items():
            assert (source / name).read_bytes() == b"synthetic-secret"
            assert Path(env[variable]).read_bytes() == b"synthetic-secret"
            if os.name == "posix":
                assert stat.S_IMODE((target / name).stat().st_mode) == 0o400
    assert not list(target.glob(".staging-*"))


@pytest.mark.parametrize("failure", ["missing", "empty", "oversize", "directory"])
def test_staging_failure_is_sanitized(staging, failure):
    source, target, env = staging
    path = source / next(iter(SECRET_FILES))
    path.unlink()
    if failure == "empty":
        path.write_bytes(b" \n")
    elif failure == "oversize":
        path.write_bytes(b"synthetic-secret" * 4096)
    elif failure == "directory":
        path.mkdir()
    with pytest.raises(SecretStagingError) as caught:
        stage_secrets(env, target)
    assert str(caught.value) == "Unable to stage worker secrets in the private directory"
    assert not list(target.glob(".staging-*"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions and symlinks")
@pytest.mark.parametrize("failure", ["permissions", "directory-symlink", "source-symlink"])
def test_staging_rejects_unsafe_paths(staging, failure):
    source, target, env = staging
    if failure == "permissions":
        target.chmod(0o755)
    elif failure == "directory-symlink":
        link = target.with_name("link")
        link.symlink_to(target)
        target = link
    else:
        path = source / next(iter(SECRET_FILES))
        path.unlink()
        path.symlink_to(source / list(SECRET_FILES)[1])
    with pytest.raises(SecretStagingError):
        stage_secrets(env, target)


def test_database_identity_is_read_only_before_cli(monkeypatch):
    connect = MagicMock()
    connection = connect.return_value.__enter__.return_value
    connection.execute.return_value.fetchone.return_value = ("disposable", "test", "123")
    monkeypatch.setattr(psycopg, "connect", connect)
    entrypoint.verify_database_identity(EXPECTED, URL)
    assert connect.call_args.kwargs["port"] == 15432
    assert connect.call_args.kwargs["options"] == "-c default_transaction_read_only=on"
    assert connection.execute.call_args.args[0].startswith("SELECT ")


@pytest.mark.parametrize(
    "url,env",
    [
        (URL.replace("15432", "5432"), EXPECTED),
        (URL.replace("host.docker.internal", "wrong-host"), EXPECTED),
        (URL + "?host=main-db", EXPECTED),
        (URL.replace("disposable", "wrong-db"), EXPECTED),
        (URL, {"CTRADER_EXPECTED_DATABASE": "disposable"}),
    ],
)
def test_wrong_database_target_is_rejected_without_connecting(monkeypatch, url, env):
    connect = MagicMock()
    monkeypatch.setattr(psycopg, "connect", connect)
    with pytest.raises(entrypoint.WorkerConfigurationError):
        entrypoint.verify_database_identity(env, url)
    connect.assert_not_called()


@pytest.mark.parametrize("failure,code", [("mismatch", 2), ("unavailable", 1), ("staging", 2)])
def test_startup_failures_do_not_invoke_cli_or_leak(monkeypatch, capsys, failure, code):
    monkeypatch.setattr(os, "environ", dict(EXPECTED))
    monkeypatch.setattr(entrypoint, "validate_environment", lambda _: URL)
    connect = MagicMock()
    if failure == "unavailable":
        connect.side_effect = psycopg.OperationalError("synthetic-secret")
    elif failure == "staging":

        def fail(_):
            raise SecretStagingError("Unable to stage worker secrets in the private directory")

        monkeypatch.setattr(entrypoint, "stage_secrets", fail)
    else:
        connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = (
            "wrong",
            "test",
            "123",
        )
    monkeypatch.setattr(psycopg, "connect", connect)
    cli = MagicMock()
    monkeypatch.setattr("trading.ctrader.cli.main", cli)
    assert entrypoint.main([]) == code
    cli.assert_not_called()
    output = capsys.readouterr()
    assert not output.out
    assert "synthetic-secret" not in output.err and "Traceback" not in output.err
