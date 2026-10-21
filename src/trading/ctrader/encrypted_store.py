"""Opt-in Fernet-encrypted file backend for cTrader credentials (Linux Docker)."""

import contextlib
import importlib
import json
import os
import stat
import tempfile
import threading
from collections.abc import Iterator, Mapping
from pathlib import Path

BACKEND_ENV = "CTRADER_CREDENTIAL_BACKEND"
FILE_ENV = "CTRADER_CREDENTIAL_FILE"
KEY_FILE_ENV = "CTRADER_CREDENTIAL_KEY_FILE"
BACKEND_KEYRING = "keyring"
BACKEND_ENCRYPTED_FILE = "encrypted-file"
MAX_FILE_BYTES = 1024 * 1024
_GROUP_OTHER_BITS = stat.S_IRWXG | stat.S_IRWXO
_GROUP_OTHER_WRITE = stat.S_IWGRP | stat.S_IWOTH
_POSIX = os.name == "posix"
_PROCESS_LOCK = threading.Lock()


class EncryptedStoreError(Exception):
    """A credential storage failure that never contains sensitive content."""


def _fernet_module():
    try:
        return importlib.import_module("cryptography.fernet")
    except ImportError as error:
        raise EncryptedStoreError(
            'Install the cTrader extra with: pip install -e ".[ctrader]"'
        ) from error


def _check_regular_file(path: Path, label: str) -> os.stat_result:
    try:
        info = os.lstat(path)
    except FileNotFoundError as error:
        raise EncryptedStoreError(f"The {label} does not exist") from error
    except OSError as error:
        raise EncryptedStoreError(f"Unable to inspect the {label}") from error
    if not stat.S_ISREG(info.st_mode):
        raise EncryptedStoreError(f"The {label} must be a regular file")
    return info


def init_key_file(path: str | Path) -> None:
    """Create a new random key file (mode 0600) without ever printing the key."""
    fernet = _fernet_module()
    target = Path(path)
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(fernet.Fernet.generate_key())
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as error:
        raise EncryptedStoreError("The credential key file already exists") from error
    except OSError as error:
        raise EncryptedStoreError("Unable to create the credential key file") from error


def _load_key(path: Path):
    fernet = _fernet_module()
    info = _check_regular_file(path, "credential key file")
    # Docker secrets are mounted read-only and world-readable (0444); only writability is unsafe.
    if _POSIX and info.st_mode & _GROUP_OTHER_WRITE:
        raise EncryptedStoreError("The credential key file must not be group/other writable")
    try:
        if info.st_size > 1024:
            raise ValueError
        key = path.read_bytes().strip()
        return fernet.Fernet(key)
    except Exception as error:
        raise EncryptedStoreError("The credential key file is invalid") from error


class EncryptedFileBackend:
    """Keyring-compatible get_password/set_password backed by one encrypted file."""

    def __init__(self, data_file: str | Path, key_file: str | Path) -> None:
        self._data_file = Path(data_file)
        self._key_file = Path(key_file)
        self._fernet = _load_key(self._key_file)
        self._invalid_token = _fernet_module().InvalidToken
        self._validate_directory()

    def get_password(self, service: str, key: str) -> str | None:
        with self._locked():
            return self._read().get(self._name(service, key))

    def set_password(self, service: str, key: str, value: str) -> None:
        if not isinstance(value, str):
            raise EncryptedStoreError("Credential values must be strings")
        with self._locked():
            data = self._read()
            data[self._name(service, key)] = value
            self._write(data)

    @staticmethod
    def _name(service: str, key: str) -> str:
        return f"{service}/{key}"

    def _validate_directory(self) -> None:
        directory = self._data_file.parent
        try:
            info = os.stat(directory)
        except OSError as error:
            raise EncryptedStoreError("The credential directory is unavailable") from error
        if not stat.S_ISDIR(info.st_mode):
            raise EncryptedStoreError("The credential directory is unavailable")
        if _POSIX and info.st_mode & _GROUP_OTHER_WRITE:
            raise EncryptedStoreError("The credential directory must not be group/other writable")

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        """Serialise read-modify-write across threads and (on POSIX) processes."""
        with _PROCESS_LOCK:
            if not _POSIX:
                yield
                return
            fcntl = importlib.import_module("fcntl")
            lock_path = self._data_file.with_name(self._data_file.name + ".lock")
            try:
                fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            except OSError as error:
                raise EncryptedStoreError("Unable to lock the credential store") from error
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)

    def _read(self) -> dict[str, str]:
        if not os.path.lexists(self._data_file):
            return {}
        info = _check_regular_file(self._data_file, "credential file")
        if _POSIX and info.st_mode & _GROUP_OTHER_BITS:
            raise EncryptedStoreError("The credential file permissions are unsafe")
        if info.st_size > MAX_FILE_BYTES:
            raise EncryptedStoreError("The credential file is invalid")
        try:
            token = self._data_file.read_bytes()
        except OSError as error:
            raise EncryptedStoreError("Unable to read the credential file") from error
        try:
            payload = json.loads(self._fernet.decrypt(token))
        except self._invalid_token as error:
            raise EncryptedStoreError(
                "The credential file cannot be decrypted (wrong key or corrupted data)"
            ) from error
        except ValueError as error:
            raise EncryptedStoreError("The credential file is invalid") from error
        return self._validate_payload(payload)

    @staticmethod
    def _validate_payload(payload: object) -> dict[str, str]:
        if not isinstance(payload, Mapping) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in payload.items()
        ):
            raise EncryptedStoreError("The credential file is invalid")
        return dict(payload)

    def _write(self, data: dict[str, str]) -> None:
        token = self._fernet.encrypt(json.dumps(data, sort_keys=True).encode("utf-8"))
        temp_name: str | None = None
        try:
            fd, temp_name = tempfile.mkstemp(
                dir=self._data_file.parent, prefix=self._data_file.name + ".", suffix=".tmp"
            )
            with os.fdopen(fd, "wb") as handle:
                if _POSIX:
                    os.fchmod(handle.fileno(), 0o600)
                handle.write(token)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self._data_file)
            temp_name = None
            self._fsync_directory()
        except OSError as error:
            raise EncryptedStoreError("Unable to write the credential file") from error
        finally:
            if temp_name is not None:
                with contextlib.suppress(OSError):
                    os.unlink(temp_name)

    def _fsync_directory(self) -> None:
        if not _POSIX:
            return
        fd = os.open(self._data_file.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def backend_from_environment(environ: Mapping[str, str] | None = None):
    """Return an encrypted backend when explicitly configured, else None (use keyring)."""
    env = os.environ if environ is None else environ
    selected = env.get(BACKEND_ENV, BACKEND_KEYRING).strip().lower() or BACKEND_KEYRING
    if selected == BACKEND_KEYRING:
        return None
    if selected != BACKEND_ENCRYPTED_FILE:
        raise EncryptedStoreError(
            f"{BACKEND_ENV} must be '{BACKEND_KEYRING}' or '{BACKEND_ENCRYPTED_FILE}'"
        )
    data_file = env.get(FILE_ENV, "").strip()
    key_file = env.get(KEY_FILE_ENV, "").strip()
    if not data_file or not key_file:
        raise EncryptedStoreError(f"{FILE_ENV} and {KEY_FILE_ENV} are required")
    return EncryptedFileBackend(data_file, key_file)
