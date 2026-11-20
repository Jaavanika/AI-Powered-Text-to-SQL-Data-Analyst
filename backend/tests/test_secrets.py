import pytest
from cryptography.fernet import Fernet

from app.config import get_settings
from app.core import secrets
from app.core.secrets import CredentialError


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CREDENTIALS_KEY", raising=False)
    get_settings.cache_clear()
    yield tmp_path / "data"
    get_settings.cache_clear()


def test_round_trip_and_the_ciphertext_hides_the_password(data_dir):
    token = secrets.encrypt("s3cret-Pa$$word")
    assert "s3cret" not in token and token != "s3cret-Pa$$word"
    assert secrets.decrypt(token) == "s3cret-Pa$$word"


def test_the_same_password_encrypts_differently_each_time(data_dir):
    assert secrets.encrypt("pw") != secrets.encrypt("pw")


def test_a_key_is_generated_once_and_reused(data_dir):
    first = secrets.encrypt("pw")
    key_file = data_dir / "credentials.key"
    assert key_file.exists()
    contents = key_file.read_text()
    secrets._fernet_for.cache_clear()
    assert secrets.decrypt(first) == "pw"                     # still decryptable after a "restart"
    assert key_file.read_text() == contents                   # the key was not regenerated


def test_a_configured_key_is_used_instead_of_the_file(data_dir, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIALS_KEY", key)
    get_settings.cache_clear()
    token = secrets.encrypt("pw")
    assert Fernet(key.encode()).decrypt(token.encode()) == b"pw"
    assert not (data_dir / "credentials.key").exists()


def test_a_changed_key_gives_a_clear_error_not_a_crash(data_dir, monkeypatch):
    token = secrets.encrypt("pw")
    monkeypatch.setenv("CREDENTIALS_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    with pytest.raises(CredentialError, match="Reconnect"):
        secrets.decrypt(token)


def test_garbage_ciphertext_is_a_credential_error(data_dir):
    with pytest.raises(CredentialError):
        secrets.decrypt("not-a-token")
