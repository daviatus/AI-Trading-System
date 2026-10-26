import json
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url

from trading.ctrader import history_backfill as h


def test_batches_are_at_most_seven_days_and_resume_exact_missing_batch():
    start = h.utc("2021-10-22T00:00:00Z")
    end = start + timedelta(days=17)
    ranges = list(h.batches(start, end))
    assert [b - a for a, b in ranges] == [timedelta(days=7), timedelta(days=7), timedelta(days=3)]
    config = {"start": start.isoformat(), "backfill_end": end.isoformat()}
    checkpoints = [{"start": a.isoformat(), "end": b.isoformat()} for a, b in ranges[:2]]
    assert h.next_batch(config, checkpoints) == ranges[2]
    assert h.next_batch(config, checkpoints + [{
        "start": ranges[2][0].isoformat(), "end": end.isoformat(),
    }]) is None


@pytest.mark.parametrize("field,value", [
    ("host", "other-host"), ("port", 5432), ("database", "main"), ("username", "postgres"),
    ("query", {"host": "main", "port": "5432"}),
])
def test_target_overrides_rejected_before_any_connection(monkeypatch, field, value):
    url = make_url(f"postgresql+psycopg://{h.USER}:not-real@host.docker.internal:15432/{h.DATABASE}")
    payload = {"database_url": url.set(**{field: value}).render_as_string(hide_password=False),
               "identity": [h.DATABASE, h.USER, "123"]}
    monkeypatch.setattr(h.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        stdout=json.dumps(payload),
    ))
    with pytest.raises(h.SafetyStop, match="target mismatch"):
        h.load_access()


def test_guard_stops_before_write_on_low_space_and_stop_marker(tmp_path, monkeypatch):
    class Connection:
        def scalar(self, *args):
            return 123

    config = {"disk_paths": [str(tmp_path)], "identity": [h.DATABASE, h.USER, "123"]}
    monkeypatch.setattr(h.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        stdout="Filesystem 1-blocks Used Available Capacity Mounted\nfs 999999999999 0 "
               "999999999999 0% /data\n",
    ))
    guard = h.Guard(tmp_path, config, Connection(),
                    disk_usage=lambda _: SimpleNamespace(free=h.FREE_FLOOR - 1))
    with pytest.raises(h.SafetyStop, match="below 10"):
        guard.check()
    (tmp_path / "stop.requested").touch()
    with pytest.raises(h.StopRequested):
        guard.check()


def test_identity_mismatch_forbids_writes():
    connection = SimpleNamespace(execute=lambda *a: SimpleNamespace(
        one=lambda: (h.DATABASE, h.USER, "wrong-cluster"),
    ))
    with pytest.raises(h.SafetyStop, match="identity mismatch"):
        h.verify_identity(connection, [h.DATABASE, h.USER, "expected-cluster"])


def test_storage_probe_recovers_from_transient_timeout_before_allowing_write(tmp_path, monkeypatch):
    calls, sleeps = [], []

    def probe(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            raise h.subprocess.TimeoutExpired("docker", 20)
        return SimpleNamespace(stdout=f"header\nfs 999999999999 0 {h.FREE_FLOOR} 0% /data\n")

    monkeypatch.setattr(h.subprocess, "run", probe)
    monkeypatch.setattr(h.time, "sleep", sleeps.append)
    monkeypatch.setattr(h, "verify_identity", lambda *args: None)
    guard = h.Guard(tmp_path, {"disk_paths": [str(tmp_path)], "identity": []},
                    SimpleNamespace(scalar=lambda *args: 123),
                    disk_usage=lambda _: SimpleNamespace(free=h.FREE_FLOOR))
    guard.check()
    assert len(calls) == 2
    assert sleeps == [1]
    assert calls[0][1]["timeout"] == 20
    assert guard.last_disk["postgres_filesystem"] == h.FREE_FLOOR


@pytest.mark.parametrize("failure", ["timeout", "exit", "malformed", "negative"])
def test_storage_probe_fails_closed_without_using_stale_space(
    tmp_path, monkeypatch, failure,
):
    calls, sleeps = [], []

    def probe(*args, **kwargs):
        calls.append(1)
        if failure == "timeout":
            raise h.subprocess.TimeoutExpired("docker", 20, stderr="SECRET-DO-NOT-PRINT")
        if failure == "exit":
            raise h.subprocess.CalledProcessError(1, "docker", stderr="SECRET-DO-NOT-PRINT")
        return SimpleNamespace(stdout="invalid" if failure == "malformed" else
                               "header\nfs 100 0 -1 0% /data\n")

    monkeypatch.setattr(h.subprocess, "run", probe)
    monkeypatch.setattr(h.time, "sleep", sleeps.append)
    guard = h.Guard(tmp_path, {"disk_paths": [str(tmp_path)], "identity": []},
                    SimpleNamespace(scalar=lambda *args: 123),
                    disk_usage=lambda _: SimpleNamespace(free=h.FREE_FLOOR))
    guard.vm_free = h.FREE_FLOOR * 100
    with pytest.raises(h.SafetyStop, match="after 3 attempts") as error:
        guard.check()
    assert "SECRET" not in str(error.value)
    assert len(calls) == 3
    assert sleeps == [1, 2]
    assert not (tmp_path / "heartbeat.json").exists()


def test_postgres_low_space_stops_immediately_without_retry(tmp_path, monkeypatch):
    calls = []

    def probe(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(stdout=f"header\nfs 999999999999 0 {h.FREE_FLOOR - 1} 0% /data\n")

    monkeypatch.setattr(h.subprocess, "run", probe)
    guard = h.Guard(tmp_path, {"disk_paths": [str(tmp_path)], "identity": []},
                    SimpleNamespace(scalar=lambda *args: 123),
                    disk_usage=lambda _: SimpleNamespace(free=h.FREE_FLOOR))
    with pytest.raises(h.SafetyStop, match="below 10 GiB"):
        guard.check()
    assert calls == [1]


def test_file_lock_excludes_second_supervisor_and_can_be_reacquired(tmp_path):
    path = tmp_path / "lock"
    with h.FileLock(path), pytest.raises(h.SafetyStop, match="already owns"):
        with h.FileLock(path):
            pytest.fail("Duplicate supervisor acquired lock")
    with h.FileLock(path):
        pass


def test_idempotent_replay_guarded_repository_calls_existing_import(monkeypatch):
    calls = []
    guard = SimpleNamespace(check=lambda: calls.append("guard"))
    monkeypatch.setattr(h.PostgresMarketDataRepository, "import_candles",
                        lambda self, *a, **kw: calls.append("import") or "result")
    repo = h.GuardedRepository(None, guard)
    assert repo.import_candles("fixture") == "result"
    assert calls == ["guard", "import"]


def test_checkpoint_is_guarded_and_separate_from_worker_events():
    assert h.CHECKPOINT != "ctrader_worker_run"
    assert h.ERROR != "ctrader_worker_run"
    calls = []
    guard = SimpleNamespace(check=lambda: calls.append("guard"))
    with pytest.raises(AttributeError):
        h.record(None, {}, {}, h.CHECKPOINT, guard)
    assert calls == ["guard"]


def test_checkpoint_normalizes_database_datetime_values_before_json_insert(monkeypatch):
    statements = []
    connection = SimpleNamespace(execute=lambda statement: statements.append(statement))
    engine = SimpleNamespace(begin=lambda: nullcontext(connection))
    monkeypatch.setattr(h, "verify_identity", lambda *args: None)
    config = {"job_id": "fixture", "identity": []}
    h.record(engine, config, {"verified": {"earliest": h.utc("2021-10-22T00:00:00Z")}},
             h.CHECKPOINT, SimpleNamespace(check=lambda: None))
    payload = statements[0].compile().params["payload"]
    assert isinstance(payload["verified"]["earliest"], str)
    assert json.loads(json.dumps(payload))["job_id"] == "fixture"


def test_supervisor_launches_correct_module_and_does_not_restart_safety_stop(tmp_path, monkeypatch):
    launches = []

    def child(command, **kwargs):
        launches.append(command)
        return SimpleNamespace(poll=lambda: 2, returncode=2)

    monkeypatch.setattr(h.subprocess, "Popen", child)
    assert h.supervise(tmp_path) == 2
    assert len(launches) == 1
    assert launches[0][2] == "trading.ctrader.history_backfill"
    assert not (tmp_path / "supervisor.json").exists()


def test_credential_failure_does_not_print_secret_subprocess_content(monkeypatch, capsys, tmp_path):
    def failing(*a, **kw):
        raise RuntimeError("SECRET-DO-NOT-PRINT")

    monkeypatch.setattr(h.subprocess, "run", failing)
    assert h.main(["init", "--state-dir", str(tmp_path)]) == 1
    assert "SECRET-DO-NOT-PRINT" not in capsys.readouterr().err
