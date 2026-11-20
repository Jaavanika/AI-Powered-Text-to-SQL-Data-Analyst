"""Encryption of stored database passwords.

Passwords for external databases are kept in the registry so the backend can reconnect, but never
in plain text. They are encrypted with a Fernet key from CREDENTIALS_KEY or, if that is not set,
a key generated once and stored beside the registry. Anyone who can read both files can decrypt
them, so in a shared deployment set CREDENTIALS_KEY from a secrets manager instead.
"""

import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class CredentialError(Exception):
    """Stored credentials cannot be read (wrong or missing key)."""


@lru_cache
def _fernet_for(key: str) -> Fernet:
    return Fernet(key.encode())


def _key() -> str:
    configured = get_settings().credentials_key.strip()
    if configured:
        return configured
    path = get_settings().data_dir.resolve() / "credentials.key"
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key().decode()
    # 'x' so two processes starting together cannot overwrite each other's key
    try:
        with open(path, "x") as handle:
            handle.write(key)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass  # not supported on every platform
    except FileExistsError:
        key = path.read_text().strip()
    return key


def encrypt(plain: str) -> str:
    return _fernet_for(_key()).encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet_for(_key()).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise CredentialError(
            "The stored database credentials cannot be decrypted (the encryption key changed). "
            "Reconnect the database.") from exc
