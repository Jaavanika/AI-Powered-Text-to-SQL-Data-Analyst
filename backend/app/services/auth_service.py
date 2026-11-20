"""Accounts and login sessions.

Passwords are stored only as salted scrypt hashes. A session is a random token given to the browser; only
its SHA-256 hash is kept, so a copy of the database cannot be used to log in. Sessions expire after
SESSION_DAYS without use.
"""

import hashlib
import re
import secrets
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from app.config import get_settings
from app.core import limits, passwords
from app.services import registry

USERNAME = re.compile(r"^[A-Za-z0-9._-]{3,40}$")
MIN_PASSWORD, MAX_PASSWORD = 10, 128
REFRESH_AFTER = timedelta(hours=1)        # how often using a session pushes its expiry out
MAX_TOKEN_LENGTH = 200


class AuthError(Exception):
    def __init__(self, message: str, status_code: int = 400, retry_after: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.retry_after = retry_after


class User(NamedTuple):
    id: str
    username: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="microseconds")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------- validation

def check_username(username: str) -> str:
    username = username.strip()
    if not USERNAME.match(username):
        raise AuthError("Usernames are 3 to 40 characters: letters, digits, '.', '_' or '-'.", 422)
    return username


def check_password(password: str, username: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"Passwords need at least {MIN_PASSWORD} characters.", 422)
    if len(password) > MAX_PASSWORD:
        raise AuthError(f"Passwords can be at most {MAX_PASSWORD} characters.", 422)
    if username.lower() in password.lower():
        raise AuthError("Passwords cannot contain your username.", 422)


# ------------------------------------------------------------------- throttles

def _locked(username_key: str, address: str) -> int:
    wait = max(limits.failures("user").locked_for(username_key), limits.failures("ip").locked_for(address))
    return int(wait) + 1 if wait else 0


def _refuse_if_locked(username_key: str, address: str) -> None:
    wait = _locked(username_key, address)
    if wait:
        minutes = max(1, round(wait / 60))
        raise AuthError(f"Too many failed attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''}.",
                        429, retry_after=wait)


def _failed(username_key: str, address: str) -> None:
    limits.failures("user").fail(username_key)
    limits.failures("ip").fail(address)


# -------------------------------------------------------------------- sessions

def _purge_expired(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_iso(_now()),))


def _create_session(conn: sqlite3.Connection, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, last_seen, expires_at) VALUES (?, ?, ?, ?, ?)",
        (_token_hash(token), user_id, _iso(now), _iso(now),
         _iso(now + timedelta(days=get_settings().session_days))))
    return token


def user_for_token(token: str) -> User:
    """The user a session token belongs to. Using a session also keeps it alive."""
    if not token or len(token) > MAX_TOKEN_LENGTH:
        raise AuthError("Log in to continue.", 401)
    now = _now()
    with closing(registry.connect()) as conn, conn:
        row = conn.execute(
            "SELECT s.token_hash, s.last_seen, s.expires_at, u.id, u.username FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", (_token_hash(token),)).fetchone()
        if row is None:
            raise AuthError("Log in to continue.", 401)
        if datetime.fromisoformat(row["expires_at"]) < now:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (row["token_hash"],))
            raise AuthError("Your session has expired. Log in again.", 401)
        if now - datetime.fromisoformat(row["last_seen"]) > REFRESH_AFTER:
            conn.execute("UPDATE sessions SET last_seen = ?, expires_at = ? WHERE token_hash = ?",
                         (_iso(now), _iso(now + timedelta(days=get_settings().session_days)), row["token_hash"]))
    return User(row["id"], row["username"])


def logout(token: str) -> None:
    with closing(registry.connect()) as conn, conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


# -------------------------------------------------------------------- accounts

def register(username: str, password: str, address: str) -> tuple[User, str]:
    settings = get_settings()
    if not settings.allow_registration:
        raise AuthError("Registration is closed on this server. Ask your administrator for an account.", 403)
    wait = limits.registrations.hit(address, settings.register_per_hour_per_ip)
    if wait is not None:
        raise AuthError("Too many accounts were created from this address. Try again later.", 429, int(wait) + 1)
    username = check_username(username)
    check_password(password, username)
    user_id = uuid.uuid4().hex
    try:
        with closing(registry.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO users (id, username, username_key, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, username, username.lower(), passwords.hash_password(password), _iso(_now())))
            _purge_expired(conn)
            token = _create_session(conn, user_id)
    except sqlite3.IntegrityError as exc:
        raise AuthError("That username is already taken.", 409) from exc
    return User(user_id, username), token


def _check_credentials(username: str, password: str, address: str) -> sqlite3.Row:
    key = username.strip().lower()
    _refuse_if_locked(key, address)
    with closing(registry.connect()) as conn:
        row = conn.execute("SELECT * FROM users WHERE username_key = ?", (key,)).fetchone()
    # An unknown user is checked against a dummy hash, so it takes as long as a wrong password.
    ok = passwords.verify_password(password, row["password_hash"] if row else passwords.DUMMY_HASH)
    if row is None or not ok:
        _failed(key, address)
        raise AuthError("Incorrect username or password.", 401)
    limits.failures("user").succeed(key)
    return row


def login(username: str, password: str, address: str) -> tuple[User, str]:
    row = _check_credentials(username, password, address)
    with closing(registry.connect()) as conn, conn:
        _purge_expired(conn)
        token = _create_session(conn, row["id"])
    return User(row["id"], row["username"]), token


def change_password(user: User, current: str, new: str, address: str, keep_token: str) -> None:
    key = user.username.lower()
    _refuse_if_locked(key, address)
    with closing(registry.connect()) as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user.id,)).fetchone()
    if row is None or not passwords.verify_password(current, row["password_hash"]):
        _failed(key, address)
        raise AuthError("The current password is incorrect.", 400)
    limits.failures("user").succeed(key)
    check_password(new, user.username)
    with closing(registry.connect()) as conn, conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (passwords.hash_password(new), user.id))
        # Anyone else who was logged in with the old password is signed out.
        conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user.id, _token_hash(keep_token)))
