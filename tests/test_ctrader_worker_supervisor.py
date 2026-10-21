import signal
import subprocess
from threading import Event

import pytest

from trading.ctrader import worker_supervisor as supervisor


class FakeChild:
    def __init__(self, code, *, timeout=False):
        self.code = code
        self.timeout = timeout
        self.waits = []

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.timeout and timeout is not None:
            raise subprocess.TimeoutExpired("fake worker", timeout)
        self.code = 0
        return self.code


class ControlledStop(Event):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.waits = []

    def wait(self, timeout=None):
        self.waits.append(timeout)
        self.callback()
        self.set()
        return True


def test_permanent_failure_waits_without_respawning_and_reports_unhealthy(tmp_path, capsys):
    state = tmp_path / "state"
    launches = []

    def blocked():
        assert state.read_text() == "configuration-error"
        assert supervisor.healthcheck(state) == 1
        assert launches == [1]

    stop = ControlledStop(blocked)

    def spawn(*_args, **_kwargs):
        launches.append(1)
        return FakeChild(2)

    assert supervisor.supervise(stop=stop, state_file=state, popen=spawn) == 0
    assert launches == [1]
    assert stop.waits == [None]
    assert state.read_text() == "stopping"
    assert "automatic retries disabled" in capsys.readouterr().err


def test_permanent_failure_is_not_retried_when_health_metadata_write_fails(tmp_path, monkeypatch):
    writes = []

    def write_state(path, state):
        writes.append(state)
        if state in {"configuration-error", "stopping"}:
            raise OSError("synthetic disk failure")

    stop = ControlledStop(lambda: None)
    monkeypatch.setattr(supervisor, "_write_state", write_state)
    assert supervisor.supervise(
        stop=stop, state_file=tmp_path / "state", popen=lambda *a, **k: FakeChild(2)
    ) == 0
    assert stop.waits == [None]
    assert writes == ["starting", "running", "configuration-error", "stopping"]


def test_failed_state_transition_removes_stale_running_marker(tmp_path, monkeypatch):
    path = tmp_path / "state"
    path.write_text("running")

    def fail(*_args):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(type(path), "replace", fail)
    with pytest.raises(OSError):
        supervisor._write_state(path, "configuration-error")
    assert not path.exists()
    assert supervisor.healthcheck(path) == 1


@pytest.mark.parametrize("code, expected", [(1, 1), (0, 0), (137, 137), (-9, 137), (-15, 143)])
def test_other_exits_are_passed_to_docker_without_internal_retry(tmp_path, code, expected):
    launches = []

    def spawn(command, **kwargs):
        launches.append((command, kwargs))
        return FakeChild(code)

    state = tmp_path / "state"
    assert supervisor.supervise(state_file=state, popen=spawn) == expected
    assert len(launches) == 1
    assert launches[0][0] == list(supervisor.WORKER_COMMAND)
    assert state.read_text() == "exited"


@pytest.mark.parametrize("timeout", [False, True])
def test_stop_forwards_term_waits_and_reaps_child(tmp_path, monkeypatch, timeout):
    child = FakeChild(None, timeout=timeout)
    signals = []
    state = tmp_path / "state"

    def running():
        assert supervisor.healthcheck(state) == 0

    stop = ControlledStop(running)
    monkeypatch.setattr(supervisor, "_signal_child", lambda process, sig: signals.append(sig))
    assert (
        supervisor.supervise(
            stop=stop, state_file=state, popen=lambda *a, **k: child, shutdown_seconds=0.1
        )
        == 0
    )
    assert signals[0] == signal.SIGTERM
    assert child.waits == ([0.1, None] if timeout else [0.1])
    assert len(signals) == (2 if timeout else 1)
    assert child.poll() == 0


def test_stop_before_spawn_never_launches_worker(tmp_path):
    stop = Event()
    stop.set()

    def forbidden(*_args, **_kwargs):
        pytest.fail("Worker launched after stop")

    assert supervisor.supervise(stop=stop, state_file=tmp_path / "state", popen=forbidden) == 0


def test_state_write_failure_after_spawn_still_reaps_worker(tmp_path, monkeypatch):
    child = FakeChild(None)
    writes = []

    def write_state(*_args):
        writes.append(1)
        if len(writes) == 2:
            raise OSError("fake state failure")

    monkeypatch.setattr(supervisor, "_write_state", write_state)
    monkeypatch.setattr(supervisor, "_signal_child", lambda *_: None)
    with pytest.raises(OSError):
        supervisor.supervise(state_file=tmp_path / "state", popen=lambda *a, **k: child)
    assert child.waits == [supervisor.SHUTDOWN_SECONDS]


@pytest.mark.parametrize("state", [None, "starting", "configuration-error", "exited", "garbage"])
def test_healthcheck_fails_closed_without_worker(tmp_path, state):
    path = tmp_path / "state"
    if state is not None:
        path.write_text(state)
    assert supervisor.healthcheck(path) == 1


def test_main_restores_signal_handlers_and_sanitizes_os_errors(monkeypatch, capsys):
    handlers = {}
    changes = []

    def install(signum, handler):
        changes.append((signum, handler))
        old = handlers.get(signum, signal.SIG_DFL)
        handlers[signum] = handler
        return old

    def fail(*_args, **_kwargs):
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        raise OSError("SYNTHETIC-SECRET-MUST-NOT-LEAK")

    monkeypatch.setattr(supervisor.signal, "signal", install)
    monkeypatch.setattr(supervisor, "supervise", fail)
    assert supervisor.main([]) == 1
    assert handlers == {signal.SIGTERM: signal.SIG_DFL, signal.SIGINT: signal.SIG_DFL}
    assert len(changes) == 4
    assert "SYNTHETIC-SECRET" not in capsys.readouterr().err


def test_main_passes_extra_args_to_workers_configuration_validation(monkeypatch):
    captured = []

    def run(command, *, stop):
        captured.append(command)
        assert not stop.is_set()
        return 0

    monkeypatch.setattr(supervisor, "supervise", run)
    assert supervisor.main(["--unexpected"]) == 0
    assert captured == [(*supervisor.WORKER_COMMAND, "--unexpected")]
