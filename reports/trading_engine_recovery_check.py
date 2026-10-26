"""Read-only checks for the explicitly approved, single-service recovery."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

from sqlalchemy import text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
IMAGE = "sha256:551957a8f8a949b51d1a4dc1fb251c784b5e8a7fd2372782306a29fcd897fdaa"
TAG = "ai-trading-system-trading-engine:recovery-0005-20261022"
ENGINE = "ai-trading-system-trading-engine-1"
COMMAND = ["uvicorn", "trading.api:app", "--host", "0.0.0.0", "--port", "8000"]
COMPOSE = ["compose", "-p", "ai-trading-system", "-f", "compose.yaml", "-f",
           "reports/trading-engine-readonly-recovery.compose.yaml"]


def docker(*args, stdin=None):
    result = subprocess.run(["docker", *args], input=stdin, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("Docker check failed; potentially sensitive diagnostics suppressed")
    return result.stdout


def inspect(name):
    return json.loads(docker("inspect", name))[0]


def check_environment(env):
    assert env["PGOPTIONS"] == "-c default_transaction_read_only=on"
    assert env["LIVE_EXECUTION_ENABLED"].lower() == "false"
    assert env["TRADING_MODE"] == "DEMO"
    url = make_url(env["DATABASE_URL"])
    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port, url.database) == ("db", 5432, "trading")
    assert not url.query, "Connection query options could override read-only settings"
    return url


def check_database(env, expected_revision=True):
    """Use the app's own connection factory; let PGOPTIONS enforce read-only."""
    url = check_environment(env)
    os.environ["DATABASE_URL"] = url.set(host="127.0.0.1").render_as_string(hide_password=False)
    for key in ("PGOPTIONS", "LIVE_EXECUTION_ENABLED", "TRADING_MODE"):
        os.environ[key] = env[key]
    from trading.config import get_settings
    from trading.db import get_engine

    get_settings.cache_clear()
    get_engine.cache_clear()
    settings = get_settings()
    assert settings.live_execution_enabled is False
    assert settings.trading_mode == "DEMO"
    engine = get_engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT current_database() AS database, "
                                    "current_setting('default_transaction_read_only') AS default_ro, "
                                    "current_setting('transaction_read_only') AS transaction_ro"))
            values = dict(row.mappings().one())
            assert values == {"database": "trading", "default_ro": "on", "transaction_ro": "on"}
            if expected_revision:
                revisions = list(conn.execute(text("SELECT version_num FROM public.alembic_version")).scalars())
                assert revisions == ["0005_historical_source_jobs"]
            conn.rollback()
        print(json.dumps({"database_read_only_check": values, "revision": "0005_historical_source_jobs"}))
    finally:
        engine.dispose()


def preflight():
    config = json.loads(docker(*COMPOSE, "config", "--format", "json"))
    svc = config["services"]["trading-engine"]
    assert svc["image"] == TAG
    assert svc["command"] == COMMAND
    assert svc.get("entrypoint") == []
    assert svc["pull_policy"] == "never"
    check_environment(svc["environment"])
    old_env = dict(value.split("=", 1) for value in inspect(ENGINE)["Config"]["Env"])
    assert make_url(old_env["DATABASE_URL"]) == make_url(svc["environment"]["DATABASE_URL"])
    assert json.loads(docker("image", "inspect", IMAGE))[0]["Id"] == IMAGE
    # Offline Python only: no environment file, network, DB, API, or migration execution.
    code = """import hashlib,json
from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory
s=ScriptDirectory.from_config(Config('/app/alembic.ini'))
paths=list(Path('/app/alembic/versions').glob('*.py'))
paths += [Path('/app/src/trading')/p for p in ('api.py','db.py','config.py','__init__.py')]
print(json.dumps({'heads':s.get_heads(),'hashes':{str(p.relative_to('/app')):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}))
"""
    result = json.loads(docker("run", "--rm", "--pull", "never", "--network", "none",
                              "--read-only", "--no-healthcheck", "--entrypoint", "python",
                              IMAGE, "-c", code))
    assert result["heads"] == ["0005_historical_source_jobs"]
    assert len([p for p in result["hashes"] if p.startswith("alembic/")]) == 5
    for path, digest in result["hashes"].items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
    check_database(svc["environment"])
    print("PREFLIGHT_PASS: pinned image, migrations, application code, Uvicorn-only command, DEMO/LIVE-disabled settings and effective read-only transactions verified")


def check_container(running):
    c = inspect(ENGINE)
    assert c["Image"] == IMAGE
    assert c["Config"]["Cmd"] == COMMAND
    assert not c["Config"].get("Entrypoint")
    assert c["Path"] == "uvicorn"
    env = dict(value.split("=", 1) for value in c["Config"]["Env"])
    check_environment(env)
    check_database(env)
    state = c["State"]["Status"]
    assert state == ("running" if running else "created")
    print(json.dumps({"container": ENGINE, "image": c["Image"], "status": state,
                      "command": c["Config"]["Cmd"], "restart_count": c["RestartCount"],
                      "health": c["State"].get("Health", {}).get("Status")}))
    if running:
        # This reads the actual container connection factory, not just the host analogue.
        code = """import json
from sqlalchemy import text
from trading.config import get_settings
from trading.db import get_engine
s=get_settings()
assert not s.live_execution_enabled and s.trading_mode=='DEMO'
with get_engine().connect() as c:
 r=dict(c.execute(text("SELECT current_setting('default_transaction_read_only') AS default_ro,current_setting('transaction_read_only') AS transaction_ro")).mappings().one())
 assert r=={'default_ro':'on','transaction_ro':'on'}
 c.rollback()
 print(json.dumps(r))
"""
        print("CONTAINER_DATABASE_READ_ONLY", docker("exec", ENGINE, "python", "-c", code).strip())
        code = """import json
from urllib.request import urlopen
for endpoint in ('live','ready'):
 with urlopen('http://127.0.0.1:8000/health/'+endpoint,timeout=10) as r:
  assert r.status==200
  print(json.dumps({'endpoint':endpoint,'http_status':r.status,'response':json.load(r)}))
"""
        print("CONTAINER_HTTP_HEALTH", docker("exec", ENGINE, "python", "-c", code).strip())
        opener = build_opener(ProxyHandler({}))
        for endpoint in ("live", "ready"):
            try:
                with opener.open(f"http://127.0.0.1:8000/health/{endpoint}", timeout=10) as response:
                    print("HOST_HTTP_HEALTH", endpoint, response.status)
            except HTTPError as error:
                print("HOST_HTTP_ROUTING_LIMITATION", endpoint, error.code)
        raw = subprocess.run(["docker", "logs", "--tail", "100", ENGINE], capture_output=True, text=True)
        assert raw.returncode == 0
        # Print only recognized, secret-free log categories; never raw diagnostics.
        lines = (raw.stdout + raw.stderr).splitlines()
        categories = {
            "startup_complete": sum("Application startup complete" in line for line in lines),
            "error_lines": sum(any(term in line.lower() for term in ("error", "traceback", "exception")) for line in lines),
            "alembic_lines": sum("alembic" in line.lower() for line in lines),
            "unknown_revision_lines": sum("can't locate revision" in line.lower() for line in lines),
        }
        print("SANITIZED_LOG_SUMMARY", json.dumps(categories))
        assert categories["error_lines"] == categories["alembic_lines"] == categories["unknown_revision_lines"] == 0
        baseline = json.loads((ROOT / "reports/trading-engine-recovery-baseline.json").read_text())
        for name, previous in baseline.items():
            if name == ENGINE:
                continue
            current = inspect(name)
            assert current["Id"] == previous["id"]
            assert current["State"]["StartedAt"] == previous["started_at"]
            assert current["RestartCount"] == previous["restart_count"]
            print("UNCHANGED_CONTAINER", name, current["State"]["Status"],
                  current["State"].get("Health", {}).get("Status"))


if __name__ == "__main__":
    try:
        action = sys.argv[1]
        if action == "preflight":
            preflight()
        elif action in ("stopped", "running"):
            check_container(action == "running")
        else:
            raise ValueError("Unknown action")
    except Exception as error:
        print("CHECK_FAILED:", type(error).__name__, "details suppressed to protect credentials")
        sys.exit(1)
