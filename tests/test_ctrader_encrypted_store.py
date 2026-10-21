import os
import stat

import pytest
from cryptography.fernet import Fernet

from trading.ctrader import encrypted_store
from trading.ctrader.encrypted_store import (
    EncryptedFileBackend,
    EncryptedStoreError,
    init_key_file,
)
from trading.ctrader.oauth import CTraderSecretStore, OAuthError, OAuthManager, OAuthTokens

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
SECRET = "super-secret-value"


@pytest.fixture
def paths(tmp_path):
    key_file = tmp_path / "key"
    init_key_file(key_file)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    if os.name == "posix":
        data_dir.chmod(0o700)
    return data_dir / "credentials.enc", key_file


def env_for(paths):
    return {
        "CTRADER_CREDENTIAL_BACKEND": "encrypted-file",
        "CTRADER_CREDENTIAL_FILE": str(paths[0]),
        "CTRADER_CREDENTIAL_KEY_FILE": str(paths[1]),
    }


def test_values_are_encrypted_and_persist_across_instances(paths):
    EncryptedFileBackend(*paths).set_password("svc", "client-secret", SECRET)
    assert SECRET.encode() not in paths[0].read_bytes()
    assert EncryptedFileBackend(*paths).get_password("svc", "client-secret") == SECRET
    assert EncryptedFileBackend(*paths).get_password("svc", "other") is None


def test_refresh_token_rotation_persists_across_restart(paths):
    clock = [100]
    store = CTraderSecretStore(EncryptedFileBackend(*paths))
    manager = OAuthManager(store, now=lambda: clock[0])
    manager.configure("id", "secret")
    store.set(CTraderSecretStore.TOKEN_KEY, OAuthTokens("old", "refresh-1", 99).to_json())
    manager._post_form = lambda *_: {
        "accessToken": "new",
        "refreshToken": "refresh-2",
        "expiresIn": 3600,
    }
    assert manager.access_token() == "new"

    restarted = OAuthManager(CTraderSecretStore(EncryptedFileBackend(*paths)), now=lambda: 200)
    assert restarted.credentials() == ("id", "secret")
    assert restarted.access_token() == "new"
    stored = OAuthTokens.from_json(restarted.store.get(CTraderSecretStore.TOKEN_KEY))
    assert stored.refresh_token == "refresh-2"


def test_wrong_key_fails_closed_without_leaking(tmp_path, paths):
    EncryptedFileBackend(*paths).set_password("svc", "k", SECRET)
    other_key = tmp_path / "other-key"
    init_key_file(other_key)
    backend = EncryptedFileBackend(paths[0], other_key)
    with pytest.raises(EncryptedStoreError) as caught:
        backend.get_password("svc", "k")
    assert SECRET not in str(caught.value)
    with pytest.raises(EncryptedStoreError):
        backend.set_password("svc", "k", "x")


def test_corrupted_or_tampered_data_fails_closed(paths):
    backend = EncryptedFileBackend(*paths)
    backend.set_password("svc", "k", SECRET)
    raw = bytearray(paths[0].read_bytes())
    raw[len(raw) // 2] ^= 0x01
    paths[0].write_bytes(bytes(raw))
    with pytest.raises(EncryptedStoreError):
        backend.get_password("svc", "k")
    paths[0].write_bytes(b"not-fernet")
    with pytest.raises(EncryptedStoreError):
        backend.get_password("svc", "k")


def test_valid_ciphertext_with_wrong_shape_is_rejected(paths):
    fernet = Fernet(paths[1].read_bytes())
    paths[0].write_bytes(fernet.encrypt(b'["list"]'))
    if os.name == "posix":
        paths[0].chmod(0o600)
    with pytest.raises(EncryptedStoreError):
        EncryptedFileBackend(*paths).get_password("svc", "k")


def test_missing_and_invalid_key_files(tmp_path, paths):
    with pytest.raises(EncryptedStoreError):
        EncryptedFileBackend(paths[0], tmp_path / "missing")
    bad = tmp_path / "bad-key"
    bad.write_text("not a key")
    with pytest.raises(EncryptedStoreError) as caught:
        EncryptedFileBackend(paths[0], bad)
    assert "not a key" not in str(caught.value)


def test_init_key_file_refuses_to_overwrite(paths):
    with pytest.raises(EncryptedStoreError):
        init_key_file(paths[1])


def test_missing_data_directory_fails(tmp_path, paths):
    with pytest.raises(EncryptedStoreError):
        EncryptedFileBackend(tmp_path / "nope" / "credentials.enc", paths[1])


def test_symlinked_data_file_is_rejected(tmp_path, paths):
    target = tmp_path / "target"
    target.write_bytes(b"x")
    try:
        paths[0].symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(EncryptedStoreError):
        EncryptedFileBackend(*paths).get_password("svc", "k")


@posix_only
def test_files_are_created_with_strict_permissions(paths):
    EncryptedFileBackend(*paths).set_password("svc", "k", SECRET)
    assert stat.S_IMODE(paths[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(paths[1].stat().st_mode) == 0o600
    assert not list(paths[0].parent.glob("*.tmp"))


@posix_only
def test_unsafe_data_file_permissions_are_rejected(paths):
    backend = EncryptedFileBackend(*paths)
    backend.set_password("svc", "k", SECRET)
    paths[0].chmod(0o644)
    with pytest.raises(EncryptedStoreError, match="permissions"):
        backend.get_password("svc", "k")
    with pytest.raises(EncryptedStoreError, match="permissions"):
        backend.set_password("svc", "k", "x")


@posix_only
def test_key_file_writable_by_others_is_rejected_but_read_only_mount_is_allowed(paths):
    paths[1].chmod(0o444)
    EncryptedFileBackend(*paths)
    paths[1].chmod(0o666)
    with pytest.raises(EncryptedStoreError):
        EncryptedFileBackend(*paths)


@posix_only
def test_group_writable_directory_is_rejected(paths):
    paths[0].parent.chmod(0o770)
    with pytest.raises(EncryptedStoreError):
        EncryptedFileBackend(*paths)


def test_failed_write_leaves_existing_data_intact(paths, monkeypatch):
    backend = EncryptedFileBackend(*paths)
    backend.set_password("svc", "k", "first")

    def boom(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(encrypted_store.os, "replace", boom)
    with pytest.raises(EncryptedStoreError):
        backend.set_password("svc", "k", "second")
    monkeypatch.undo()
    assert EncryptedFileBackend(*paths).get_password("svc", "k") == "first"
    assert not list(paths[0].parent.glob("*.tmp"))


def test_default_selection_is_keyring_and_selection_is_explicit(paths):
    assert encrypted_store.backend_from_environment({}) is None
    assert encrypted_store.backend_from_environment({"CTRADER_CREDENTIAL_BACKEND": ""}) is None
    assert isinstance(
        encrypted_store.backend_from_environment(env_for(paths)), EncryptedFileBackend
    )
    with pytest.raises(EncryptedStoreError):
        encrypted_store.backend_from_environment({"CTRADER_CREDENTIAL_BACKEND": "plaintext"})
    incomplete = {"CTRADER_CREDENTIAL_BACKEND": "encrypted-file"}
    with pytest.raises(EncryptedStoreError):
        encrypted_store.backend_from_environment(incomplete)


def test_secret_store_uses_encrypted_backend_from_environment(paths, monkeypatch):
    for name, value in env_for(paths).items():
        monkeypatch.setenv(name, value)
    CTraderSecretStore().set(CTraderSecretStore.CLIENT_SECRET_KEY, SECRET)
    assert CTraderSecretStore().get(CTraderSecretStore.CLIENT_SECRET_KEY) == SECRET


def test_secret_store_fails_closed_on_bad_configuration(paths, monkeypatch):
    monkeypatch.setenv("CTRADER_CREDENTIAL_BACKEND", "encrypted-file")
    monkeypatch.delenv("CTRADER_CREDENTIAL_FILE", raising=False)
    monkeypatch.delenv("CTRADER_CREDENTIAL_KEY_FILE", raising=False)
    with pytest.raises(OAuthError):
        CTraderSecretStore()


def test_secret_store_wrapper_reports_storage_errors_as_oauth_errors(paths):
    store = CTraderSecretStore(EncryptedFileBackend(*paths))
    paths[0].write_bytes(b"garbage")
    if os.name == "posix":
        paths[0].chmod(0o600)
    with pytest.raises(OAuthError) as caught:
        store.get(CTraderSecretStore.TOKEN_KEY)
    assert "garbage" not in str(caught.value)
