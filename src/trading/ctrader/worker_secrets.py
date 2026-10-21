"""Stage read-only host secrets into a private container tmpfs without changing originals."""

import contextlib
import os
import stat
import tempfile
from collections.abc import MutableMapping
from pathlib import Path

SOURCE_ENV = "CTRADER_SECRET_SOURCE_DIR"
TARGET_DIRECTORY = Path("/run/secrets")
SECRET_FILES = {
    "ctrader_credential_key": "CTRADER_CREDENTIAL_KEY_FILE",
    "ctrader_worker_database_url": "DATABASE_URL_FILE",
}


class SecretStagingError(Exception):
    """A fixed, sanitized secret-staging diagnostic."""


def stage_secrets(env: MutableMapping[str, str], target: Path = TARGET_DIRECTORY) -> None:
    """Opt in with SOURCE_ENV; Compose supplies a UID-owned 0700 tmpfs target.

    Atomic replacement permits explicit restarts when tmpfs contents survive. No key
    generation, decryption or credential-store writes take place here.
    """
    source = env.get(SOURCE_ENV, "").strip()
    if not source:
        return
    temporary = None
    try:
        info = target.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError
        if os.name == "posix" and (
            info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ValueError
        for name in SECRET_FILES:
            # Host bind modes are unreliable on Windows; the mount itself is read-only.
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            with os.fdopen(os.open(Path(source) / name, flags), "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                    raise ValueError
                value = handle.read(4097)
            if not value.strip() or len(value) > 4096:
                raise ValueError
            fd, temporary = tempfile.mkstemp(dir=target, prefix=".staging-")
            with os.fdopen(fd, "wb") as handle:
                handle.write(value)
                handle.flush()
                if os.name == "posix":
                    os.fchmod(handle.fileno(), 0o400)
            os.replace(temporary, target / name)
            temporary = None
        for name, variable in SECRET_FILES.items():
            env[variable] = str(target / name)
    except (OSError, ValueError):
        raise SecretStagingError(
            "Unable to stage worker secrets in the private directory"
        ) from None
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                Path(temporary).unlink()
