"""Opt-in Docker policy tests using a disposable image and synthetic children only.

Build Dockerfile.ctrader-worker as ai-trading-ctrader-worker:supervision-test, then set
RUN_CTRADER_SUPERVISION_DOCKER_TESTS=1. No daemon restart or real worker collection is performed.
"""

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

IMAGE = "ai-trading-ctrader-worker:supervision-test"
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_CTRADER_SUPERVISION_DOCKER_TESTS") != "1",
    reason="opt-in isolated Docker supervision tests",
)

# The child only reads a synthetic gate and waits for termination. It has no trading imports.
FAKE_WORKER = """
import signal, sys, time
from pathlib import Path
from threading import Event
stop = Event()
def terminate(*_):
    print('SIMULATED_TERM', flush=True)
    stop.set()
signal.signal(signal.SIGTERM, terminate)
if not Path('/test-control/recovered').exists():
    print('SIMULATED_TRANSIENT', flush=True)
    sys.exit(1)
print('SIMULATED_READY', flush=True)
stop.wait()
time.sleep(0.1)
print('SIMULATED_FINISHED', flush=True)
"""
BOOTSTRAP = (
    "import os, sys\nfrom pathlib import Path\n"
    "from trading.ctrader import worker_supervisor as s\n"
    "Path('/tmp/test-supervisor.pid').write_text(str(os.getpid()))\n"
    f"s.WORKER_COMMAND = (sys.executable, '-c', {FAKE_WORKER!r})\n"
    "raise SystemExit(s.main([]))\n"
)


@pytest.fixture
def docker():
    executable = shutil.which("docker")
    if executable is None and os.name == "nt":
        executable = str(
            Path(os.environ["LOCALAPPDATA"]) / "Programs/DockerDesktop/resources/bin/docker.exe"
        )
    if not executable or not Path(executable).is_file():
        pytest.fail("Docker CLI is required for opt-in tests")

    def run(*arguments, allowed=(0,)):
        result = subprocess.run(
            [executable, *arguments], capture_output=True, text=True, timeout=30, check=False
        )
        assert result.returncode in allowed, result.stderr
        output = result.stdout + result.stderr if arguments[0] == "logs" else result.stdout
        return output.strip()

    run("image", "inspect", IMAGE, "--format", "{{.Id}}")  # Never pull or build implicitly.
    return run


@pytest.fixture
def container(docker, tmp_path):
    names = []

    def create(*, fake=False, recovered=False):
        if recovered:
            (tmp_path / "recovered").touch()
        name = "ctrader-supervision-test-" + uuid.uuid4().hex[:12]
        names.append(name)
        args = [
            "run",
            "--detach",
            "--name",
            name,
            "--pull",
            "never",
            "--network",
            "none",
            "--restart",
            "unless-stopped",
            "--read-only",
            "--init",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "64",
            "--memory",
            "256m",
            "--cpus",
            "1",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--tmpfs",
            "/var/lib/ctrader:rw,noexec,nosuid,nodev,size=4m,mode=0700,uid=10001,gid=10001",
            "--health-interval",
            "1s",
            "--health-start-period",
            "0s",
            "--health-timeout",
            "2s",
            "--health-retries",
            "1",
        ]
        if fake:
            args += [
                "--mount",
                f"type=bind,source={tmp_path},target=/test-control,readonly",
                "--entrypoint",
                "python",
                IMAGE,
                "-c",
                BOOTSTRAP,
            ]
        else:
            args += [IMAGE]
        docker(*args)
        assert docker("inspect", name, "--format", "{{.HostConfig.NetworkMode}}") == "none"
        assert docker("inspect", name, "--format", "{{.Config.User}}") == "10001:10001"
        return name

    yield create, tmp_path
    for name in names:
        # Scoped to containers created by this fixture; remove their restart policy first.
        docker("update", "--restart", "no", name)
        docker("rm", "--force", "--volumes", name)


def eventually(condition, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.2)
    pytest.fail("Isolated container did not reach the expected state")


def healthy(docker, name):
    return docker("inspect", name, "--format", "{{.State.Health.Status}}") == "healthy"


def test_configuration_exit_two_is_quarantined_until_explicit_restart(docker, container):
    create, _ = container
    name = create()
    eventually(
        lambda: docker("inspect", name, "--format", "{{.State.Health.Status}}") == "unhealthy"
    )
    eventually(lambda: "automatic retries disabled" in docker("logs", name))
    time.sleep(12)  # Observe beyond Docker's documented 10s startup window.
    assert docker("inspect", name, "--format", "{{.RestartCount}}") == "0"
    assert docker("inspect", name, "--format", "{{.State.Status}}") == "running"
    assert docker("logs", name).count("cTrader worker: configuration error:") == 1
    docker("restart", "--time", "5", name)
    eventually(lambda: docker("logs", name).count("cTrader worker: configuration error:") == 2)
    eventually(
        lambda: docker("inspect", name, "--format", "{{.State.Health.Status}}") == "unhealthy"
    )


def test_docker_retries_transient_child_failure_and_recovers(docker, container):
    create, gate = container
    name = create(fake=True)
    eventually(lambda: int(docker("inspect", name, "--format", "{{.RestartCount}}")) >= 1)
    (gate / "recovered").touch()
    eventually(lambda: "SIMULATED_READY" in docker("logs", name) and healthy(docker, name))
    restarts = docker("inspect", name, "--format", "{{.RestartCount}}")
    time.sleep(2)
    assert docker("inspect", name, "--format", "{{.RestartCount}}") == restarts
    assert "SIMULATED_TRANSIENT" in docker("logs", name)


def test_stop_finishes_child_without_restart_and_explicit_start_recovers(docker, container):
    create, _ = container
    name = create(fake=True, recovered=True)
    eventually(lambda: "SIMULATED_READY" in docker("logs", name) and healthy(docker, name))
    docker("stop", "--time", "10", name)
    assert docker("inspect", name, "--format", "{{.State.ExitCode}}") == "0"
    assert "SIMULATED_TERM" in docker("logs", name)
    assert "SIMULATED_FINISHED" in docker("logs", name)
    time.sleep(1)
    assert docker("inspect", name, "--format", "{{.State.Status}}") == "exited"
    assert docker("inspect", name, "--format", "{{.RestartCount}}") == "0"
    docker("start", name)
    eventually(lambda: docker("logs", name).count("SIMULATED_READY") == 2 and healthy(docker, name))


def test_docker_recovers_after_supervisor_crash(docker, container):
    create, _ = container
    name = create(fake=True, recovered=True)
    eventually(lambda: "SIMULATED_READY" in docker("logs", name) and healthy(docker, name))
    docker(
        "exec",
        name,
        "python",
        "-c",
        "import os; from pathlib import Path; "
        "os.kill(int(Path('/tmp/test-supervisor.pid').read_text()), 9)",
        allowed=(0, 137),  # The daemon may terminate the exec process with its container.
    )
    eventually(lambda: int(docker("inspect", name, "--format", "{{.RestartCount}}")) >= 1)
    eventually(lambda: docker("logs", name).count("SIMULATED_READY") >= 2 and healthy(docker, name))


def test_windows_host_secrets_stage_privately_and_survive_restaging(docker, tmp_path):
    for name in ("ctrader_credential_key", "ctrader_worker_database_url"):
        (tmp_path / name).write_text("synthetic-secret")
    code = """
import os, stat
from pathlib import Path
from trading.ctrader.worker_secrets import stage_secrets, SECRET_FILES, SecretStagingError
assert os.getuid() == 10001
for _ in range(2):
    stage_secrets(os.environ)
    for name in SECRET_FILES:
        path = Path('/run/secrets') / name
        assert path.read_text() == 'synthetic-secret'
        assert stat.S_IMODE(path.stat().st_mode) == 0o400
        assert Path('/run/host-secrets', name).read_text() == 'synthetic-secret'
directory = Path('/run/secrets')
link = directory / 'directory-link'
link.symlink_to(directory)
for target in (link, directory):
    if target == directory:
        directory.chmod(0o755)
    try:
        stage_secrets(os.environ, target)
    except SecretStagingError:
        pass
    else:
        raise AssertionError('Unsafe secret directory accepted')
directory.chmod(0o700)
source = directory / 'source'
source.mkdir()
(source / 'ctrader_credential_key').symlink_to(directory / 'ctrader_credential_key')
try:
    stage_secrets({'CTRADER_SECRET_SOURCE_DIR': str(source)})
except SecretStagingError:
    pass
else:
    raise AssertionError('Symlinked host secret accepted')
try:
    Path('/app/write-probe').write_text('forbidden')
except OSError:
    pass
else:
    raise AssertionError('Root filesystem is writable')
print('PRIVATE_STAGING_OK')
"""
    output = docker(
        "run",
        "--rm",
        "--network",
        "none",
        "--restart",
        "no",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--mount",
        f"type=bind,source={tmp_path},target=/run/host-secrets,readonly",
        "--tmpfs",
        "/run/secrets:rw,noexec,nosuid,nodev,size=64k,mode=0700,uid=10001,gid=10001",
        "--tmpfs",
        "/var/lib/ctrader:rw,size=1m,mode=0700,uid=10001,gid=10001",
        "--env",
        "CTRADER_SECRET_SOURCE_DIR=/run/host-secrets",
        "--entrypoint",
        "python",
        IMAGE,
        "-c",
        code,
    )
    assert output == "PRIVATE_STAGING_OK"
