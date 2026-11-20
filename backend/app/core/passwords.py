"""Password hashing with scrypt from the standard library.

A hash is stored as `scrypt$N$r$p$salt$digest`, so the cost can be raised later and old hashes still verify.
The defaults (N=2^15, r=8, p=1: 32 MiB, about 0.3 s) trade a little of the OWASP-listed parallelism for a login
that stays quick; logins are also throttled, so the cost to an attacker still dominates.
"""

import base64
import binascii
import hashlib
import hmac
import secrets

DEFAULT_PARAMS = (2 ** 15, 8, 1)       # N, r, p
DIGEST_BYTES = 32
SALT_BYTES = 16
MAX_MEMORY = 256 * 1024 * 1024
# What a stored hash may ask for. A tampered row must not be able to make a login cost minutes or gigabytes.
MAX_N, MAX_R, MAX_P = 2 ** 17, 16, 16


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=DIGEST_BYTES, maxmem=MAX_MEMORY)


def hash_password(password: str) -> str:
    n, r, p = DEFAULT_PARAMS
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _scrypt(password, salt, n, r, p)
    return "$".join(["scrypt", str(n), str(r), str(p),
                     base64.b64encode(salt).decode("ascii"), base64.b64encode(digest).decode("ascii")])


def verify_password(password: str, stored: str) -> bool:
    """True if `password` matches `stored`. A damaged or unrecognised hash is a failed login, never an error."""
    try:
        algorithm, n_text, r_text, p_text, salt_text, digest_text = stored.split("$")
        n, r, p = int(n_text), int(r_text), int(p_text)
        salt = base64.b64decode(salt_text, validate=True)
        expected = base64.b64decode(digest_text, validate=True)
    except (ValueError, binascii.Error, AttributeError):
        return False
    if algorithm != "scrypt" or not (2 <= n <= MAX_N and n & (n - 1) == 0 and 1 <= r <= MAX_R and 1 <= p <= MAX_P):
        return False
    if not salt or len(expected) != DIGEST_BYTES:
        return False
    try:
        actual = _scrypt(password, salt, n, r, p)
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(actual, expected)


# Checked against when a username does not exist, so a wrong username and a wrong password take the same time.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
