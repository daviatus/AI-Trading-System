"""Keep Docker restart recovery while quarantining permanent worker configuration failures."""

import contextlib
import os
import signal
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from threading import Event

STATE_FILE = Path("/tmp/ctrader-worker-supervisor.status")
WORKER_COMMAND = (sys.executable, "-m", "trading.ctrader.worker_entrypoint")
SHUTDOWN_SECONDS = 80
POLL_SECONDS = 0.2


def _write_state(path: Path, state: str) -> None:
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(state, encoding="ascii")
        temporary.replace(path)
    except OSError:
        # A failed transition must not leave an old "running" health marker behind.
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
        raise
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


def healthcheck(path: Path = STATE_FILE) -> int:
    """Report supervision state only; never probe credentials, a broker or a database."""
    try:
        state = path.read_text(encoding="ascii")
    except (OSError, ValueError):
        state = "unavailable"
    if state == "running":
        return 0
    if state == "configuration-error":
        print(
            "cTrader supervisor: worker exit 2; correct configuration and restart", file=sys.stderr
        )
    else:
        print("cTrader supervisor: worker is not running", file=sys.stderr)
    return 1


def _signal_child(child: subprocess.Popen, signum: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        if os.name == "posix":
            os.killpg(child.pid, signum)
        elif signum == signal.SIGTERM:
            child.terminate()
        else:
            child.kill()


def _stop_child(child: subprocess.Popen, timeout: float) -> None:
    if child.poll() is not None:
        return
    _signal_child(child, signal.SIGTERM)
    try:
        child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print("cTrader supervisor: shutdown grace exceeded; killing worker", file=sys.stderr)
        _signal_child(child, signal.SIGKILL if os.name == "posix" else signal.SIGTERM)
        # On Windows terminate() also forcibly ends the process.
        child.wait()


def supervise(
    command: Sequence[str] = WORKER_COMMAND,
    *,
    stop: Event | None = None,
    state_file: Path = STATE_FILE,
    popen: Callable = subprocess.Popen,
    shutdown_seconds: float = SHUTDOWN_SECONDS,
) -> int:
    """Run one child; Docker retries exits except 2, which waits for operator intervention."""
    stop = Event() if stop is None else stop
    _write_state(state_file, "starting")
    if stop.is_set():
        _write_state(state_file, "stopping")
        return 0
    # Inherit stdout/stderr directly; never capture or re-render worker credentials/errors.
    child = popen(list(command), start_new_session=os.name == "posix")
    try:
        _write_state(state_file, "running")
        while not stop.is_set():
            code = child.poll()
            if code is not None:
                if code == 2:
                    try:
                        _write_state(state_file, "configuration-error")
                    except OSError:
                        # Metadata failure must never turn exit 2 into a restart loop.
                        print("cTrader supervisor: health status unavailable", file=sys.stderr)
                    print(
                        "cTrader supervisor: worker exit 2; automatic retries disabled; "
                        "correct configuration and restart",
                        file=sys.stderr,
                        flush=True,
                    )
                    stop.wait()
                    with contextlib.suppress(OSError):
                        _write_state(state_file, "stopping")
                    return 0
                _write_state(state_file, "exited")
                # Popen uses negative return codes for signal deaths on POSIX.
                return code if code >= 0 else 128 - code
            stop.wait(POLL_SECONDS)
        _write_state(state_file, "stopping")
        return 0
    finally:
        _stop_child(child, shutdown_seconds)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["--healthcheck"]:
        return healthcheck()
    stop = Event()
    previous = {}
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, lambda *_: stop.set())
        # The worker rejects extra arguments as configuration errors; quarantine those too.
        return supervise((*WORKER_COMMAND, *arguments), stop=stop)
    except OSError:
        print("cTrader supervisor: unable to supervise worker", file=sys.stderr)
        return 1
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
