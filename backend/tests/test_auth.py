"""Accounts: registering, logging in, sessions, and keeping each person's data to themselves."""

import re
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.core import limits, passwords
from app.services import registry
from tests.helpers import sql_reply, upload_csvs

PASSWORD = "a-long-password"
CLEAR = {"ambiguous": False, "question": None, "options": [], "reason": ""}


def register(c, username="alice", password=PASSWORD):
    return c.post("/api/auth/register", json={"username": username, "password": password})


def login(c, username="alice", password=PASSWORD):
    return c.post("/api/auth/login", json={"username": username, "password": password})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def person(accounts, username="alice", password=PASSWORD):
    """A client that is logged in as a newly registered user."""
    r = register(accounts, username, password)
    assert r.status_code == 201, r.text
    return TestClient(accounts.app, headers=bearer(r.json()["token"]))


def setting(monkeypatch, name, value):
    monkeypatch.setenv(name, str(value))
    get_settings.cache_clear()


def rows(sql, *params):
    with closing(registry.connect()) as conn:
        return conn.execute(sql, params).fetchall()


# --------------------------------------------------------------- configuration

def test_the_login_screen_can_ask_how_the_server_is_set_up(accounts, monkeypatch):
    assert accounts.get("/api/auth/config").json() == {"mode": "accounts", "registration_open": True}
    setting(monkeypatch, "ALLOW_REGISTRATION", "false")
    assert accounts.get("/api/auth/config").json() == {"mode": "accounts", "registration_open": False}


def test_without_accounts_the_server_says_so_and_has_no_login(client):
    assert client.get("/api/auth/config").json() == {"mode": "none", "registration_open": False}
    assert register(client).status_code == 404 and login(client).status_code == 404


def test_the_health_check_needs_no_login(accounts):
    assert accounts.get("/health").status_code == 200


# ------------------------------------------------------------------ registering

def test_registering_gives_a_session_that_works(accounts):
    r = register(accounts)
    assert r.status_code == 201
    body = r.json()
    assert body["user"]["username"] == "alice" and body["user"]["id"] and len(body["token"]) >= 40
    assert "password" not in str(body).lower().replace("password_", "")
    h = bearer(body["token"])
    assert accounts.get("/api/auth/me", headers=h).json() == body["user"]
    assert accounts.get("/api/datasources", headers=h).status_code == 200


@pytest.mark.parametrize("username,fragment", [
    ("ab", "3 to 40"), ("x" * 41, "3 to 40"), ("has space", "letters, digits"), ("semi;colon", "letters, digits"),
    ("émile", "letters, digits"), ("", "3 to 40"),
])
def test_usernames_are_checked(accounts, username, fragment):
    r = register(accounts, username)
    assert r.status_code == 422 and fragment in str(r.json()["detail"])


@pytest.mark.parametrize("password,fragment", [
    ("short", "at least 10"), ("x" * 129, "at most 128"), ("alice", "at least 10"), ("alice-alice", "username"),
])
def test_passwords_are_checked(accounts, password, fragment):
    r = register(accounts, "alice", password)
    assert r.status_code == 422 and fragment in str(r.json()["detail"])


def test_a_rejected_password_is_never_echoed_back(accounts):
    r = register(accounts, "alice", "tiny")
    assert "tiny" not in r.text


def test_usernames_are_unique_ignoring_case(accounts):
    assert register(accounts, "Alice").status_code == 201
    r = register(accounts, "aLICE")
    assert r.status_code == 409 and "already taken" in r.json()["detail"]


def test_registration_can_be_closed(accounts, monkeypatch):
    person(accounts, "alice")
    setting(monkeypatch, "ALLOW_REGISTRATION", "false")
    r = register(accounts, "bob")
    assert r.status_code == 403 and "closed" in r.json()["detail"]
    assert login(accounts, "alice").status_code == 200              # existing accounts still work


def test_registering_too_often_from_one_address_is_slowed(accounts, monkeypatch):
    setting(monkeypatch, "REGISTER_PER_HOUR_PER_IP", 3)
    assert [register(accounts, f"user{i}").status_code for i in range(3)] == [201, 201, 201]
    r = register(accounts, "user9")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


# --------------------------------------------------------------------- logging in

def test_logging_in_gives_a_new_session(accounts):
    first = register(accounts).json()["token"]
    second = login(accounts).json()["token"]
    assert second != first
    assert accounts.get("/api/auth/me", headers=bearer(first)).status_code == 200       # both are valid
    assert accounts.get("/api/auth/me", headers=bearer(second)).status_code == 200


def test_the_username_is_not_case_sensitive_at_login(accounts):
    register(accounts, "Alice")
    assert login(accounts, "ALICE").status_code == 200


def test_a_wrong_password_and_an_unknown_user_look_the_same(accounts):
    register(accounts)
    wrong, unknown = login(accounts, "alice", "not-the-password"), login(accounts, "nobody", PASSWORD)
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Incorrect username or password."}


def test_an_unknown_user_still_costs_a_hash_so_timing_does_not_reveal_who_exists(accounts, monkeypatch):
    calls = []
    real = passwords.verify_password
    monkeypatch.setattr(passwords, "verify_password", lambda pw, stored: calls.append(stored) or real(pw, stored))
    login(accounts, "nobody", PASSWORD)
    assert calls == [passwords.DUMMY_HASH]


def test_repeated_failures_lock_the_account_even_for_the_right_password(accounts, monkeypatch):
    setting(monkeypatch, "LOGIN_MAX_FAILURES", 3)
    register(accounts)
    assert [login(accounts, "alice", "wrong-password").status_code for _ in range(3)] == [401, 401, 401]
    r = login(accounts, "alice", PASSWORD)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0 and "Try again" in r.json()["detail"]
    assert login(accounts, "someone-else", PASSWORD).status_code == 401           # other usernames are not locked


def test_a_success_before_the_limit_resets_the_count(accounts, monkeypatch):
    setting(monkeypatch, "LOGIN_MAX_FAILURES", 3)
    register(accounts)
    for _ in range(2):
        login(accounts, "alice", "wrong-password")
    assert login(accounts, "alice", PASSWORD).status_code == 200
    for _ in range(2):
        login(accounts, "alice", "wrong-password")
    assert login(accounts, "alice", PASSWORD).status_code == 200


def test_one_address_cannot_try_many_accounts(accounts, monkeypatch):
    setting(monkeypatch, "LOGIN_MAX_FAILURES_PER_IP", 4)
    for i in range(4):
        login(accounts, f"user{i}", "wrong-password")
    r = login(accounts, "user99", "wrong-password")
    assert r.status_code == 429


def test_a_locked_username_stays_locked_whatever_the_case(accounts, monkeypatch):
    setting(monkeypatch, "LOGIN_MAX_FAILURES", 2)
    register(accounts, "Alice")
    login(accounts, "ALICE", "wrong-password"), login(accounts, "alice", "wrong-password")
    assert login(accounts, "Alice", PASSWORD).status_code == 429


# --------------------------------------------------------------------- sessions

def test_logging_out_ends_only_that_session(accounts):
    register(accounts)
    one, two = login(accounts).json()["token"], login(accounts).json()["token"]
    assert accounts.post("/api/auth/logout", headers=bearer(one)).status_code == 204
    assert accounts.get("/api/auth/me", headers=bearer(one)).status_code == 401
    assert accounts.get("/api/auth/me", headers=bearer(two)).status_code == 200


def test_a_session_expires(accounts):
    token = register(accounts).json()["token"]
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with closing(registry.connect()) as conn, conn:
        conn.execute("UPDATE sessions SET expires_at = ?", (past,))
    r = accounts.get("/api/auth/me", headers=bearer(token))
    assert r.status_code == 401 and "expired" in r.json()["detail"].lower()


def test_using_a_session_keeps_it_alive(accounts, monkeypatch):
    setting(monkeypatch, "SESSION_DAYS", 7)
    token = register(accounts).json()["token"]
    stale = datetime.now(timezone.utc) - timedelta(hours=2)
    soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    with closing(registry.connect()) as conn, conn:
        conn.execute("UPDATE sessions SET last_seen = ?, expires_at = ?", (stale.isoformat(), soon))
    assert accounts.get("/api/auth/me", headers=bearer(token)).status_code == 200
    expires = datetime.fromisoformat(rows("SELECT expires_at FROM sessions")[0]["expires_at"])
    assert expires > datetime.now(timezone.utc) + timedelta(days=6)


def test_expired_sessions_are_removed(accounts):
    register(accounts)
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with closing(registry.connect()) as conn, conn:
        conn.execute("UPDATE sessions SET expires_at = ?", (past,))
    login(accounts)                                              # a login tidies up old sessions
    assert len(rows("SELECT * FROM sessions")) == 1


def test_only_a_hash_of_the_token_is_stored(accounts):
    token = register(accounts).json()["token"]
    stored = rows("SELECT token_hash FROM sessions")[0]["token_hash"]
    assert token not in stored and re.fullmatch(r"[0-9a-f]{64}", stored)


def test_only_a_salted_hash_of_the_password_is_stored(accounts):
    register(accounts)
    stored = rows("SELECT password_hash FROM users")[0]["password_hash"]
    assert PASSWORD not in stored and stored.startswith("scrypt$")


# -------------------------------------------------------- who is calling

@pytest.mark.parametrize("headers", [
    {}, {"Authorization": "Bearer"}, {"Authorization": "Bearer "}, {"Authorization": "Bearer not-a-real-token"},
    {"Authorization": "Basic YWxpY2U6cGFzcw=="}, {"Authorization": "bearer"}, {"Authorization": "x" * 5000},
])
def test_a_missing_or_bad_token_is_refused(accounts, headers):
    r = accounts.get("/api/datasources", headers=headers)
    assert r.status_code == 401 and r.headers["WWW-Authenticate"] == "Bearer"


def test_the_workspace_header_is_ignored_when_accounts_are_on(accounts):
    """It names a workspace without proving anything, so it must not open one up."""
    assert accounts.get("/api/datasources", headers={"X-Workspace-Id": "workspace-one"}).status_code == 401


def test_a_valid_token_wins_over_a_workspace_header_naming_someone_else(accounts):
    alice, bob = person(accounts, "alice"), person(accounts, "bob")
    upload_csvs(alice, {"people": "name\nA\n"})
    seen = bob.get("/api/datasources", headers={"X-Workspace-Id": "workspace-of-alice"}).json()
    assert [d["id"] for d in seen] == ["sample"]


def test_the_bearer_scheme_is_case_insensitive(accounts):
    token = register(accounts).json()["token"]
    assert accounts.get("/api/auth/me", headers={"Authorization": f"bEaReR {token}"}).status_code == 200


def test_every_api_route_needs_a_login(accounts):
    """A safety net for the future: a new endpoint that forgets authentication fails this test."""
    open_routes = {"/api/auth/register", "/api/auth/login", "/api/auth/config"}
    checked = 0
    for path, operations in accounts.app.openapi()["paths"].items():          # the public list of every endpoint
        if not path.startswith("/api/") or path in open_routes:
            continue
        for method in operations:
            url = re.sub(r"\{[^}]+\}", "x", path)
            r = accounts.request(method.upper(), url)
            assert r.status_code == 401, f"{method.upper()} {path} answered {r.status_code} without a login"
            checked += 1
    assert checked > 40


# ----------------------------------------------------------------- isolation

def test_one_persons_data_is_invisible_to_another(accounts, fake_llm):
    alice, bob = person(accounts, "alice"), person(accounts, "bob")
    ds = upload_csvs(alice, {"people": "name,email\nA,a@x.com\n"})
    fake_llm.replies += [CLEAR, sql_reply("SELECT COUNT(*) AS n FROM people")]
    alice.post("/api/chat/query", json={"datasource_id": ds["id"], "message": "how many people", "include_summary": False})
    alice.post(f"/api/context/{ds['id']}", json={"kind": "term", "name": "VIP", "description": "a big customer"})
    alice.put(f"/api/datasources/{ds['id']}/blocked-columns", json={"columns": ["people.email"]})

    assert [d["id"] for d in bob.get("/api/datasources").json()] == ["sample"]
    for path in (f"/api/datasources/{ds['id']}/schema", f"/api/context/{ds['id']}",
                 f"/api/datasources/{ds['id']}/blocked-columns", f"/api/datasources/{ds['id']}/tables/people/preview"):
        assert bob.get(path).status_code == 404, path
    assert bob.get("/api/history", params={"datasource_id": ds["id"]}).json()["items"] == []
    assert bob.get("/api/chat/conversations").json() == []
    assert bob.post("/api/sql/execute", json={"datasource_id": ds["id"], "sql": "SELECT * FROM people"}).status_code == 404
    assert bob.delete(f"/api/datasources/{ds['id']}").status_code == 404
    assert alice.get(f"/api/datasources/{ds['id']}/schema").status_code == 200           # and still hers


def test_a_conversation_id_from_someone_else_is_not_found(accounts, fake_llm):
    alice, bob = person(accounts, "alice"), person(accounts, "bob")
    fake_llm.replies += [CLEAR, sql_reply("SELECT 1 AS n")]
    cid = alice.post("/api/chat/query", json={"datasource_id": "sample", "message": "hello there", "include_summary": False}).json()["conversation_id"]
    assert bob.get(f"/api/chat/conversations/{cid}").status_code == 404
    assert bob.post("/api/chat/query", json={"datasource_id": "sample", "message": "again", "conversation_id": cid}).status_code == 404
    assert bob.delete(f"/api/chat/conversations/{cid}").status_code == 404


def test_the_shared_sample_is_available_to_everyone_without_sharing_what_is_done_with_it(accounts):
    alice, bob = person(accounts, "alice"), person(accounts, "bob")
    alice.put("/api/datasources/sample/blocked-columns", json={"columns": ["customers.email"]})
    assert bob.get("/api/datasources/sample/blocked-columns").json()["columns"] == []
    assert bob.post("/api/sql/execute", json={"datasource_id": "sample", "sql": "SELECT email FROM customers LIMIT 1"}).status_code == 200


# ----------------------------------------------------------- changing a password

def change(c, current=PASSWORD, new="a-brand-new-password"):
    return c.post("/api/auth/password", json={"current_password": current, "new_password": new})


def test_changing_the_password_needs_the_current_one(accounts):
    alice = person(accounts)
    r = change(alice, current="not-my-password")
    assert r.status_code == 400 and "current password" in r.json()["detail"].lower()
    assert login(accounts, "alice", PASSWORD).status_code == 200                       # nothing changed


def test_the_new_password_replaces_the_old_and_other_sessions_end(accounts):
    alice = person(accounts)
    other = login(accounts).json()["token"]                                            # a second device
    assert change(alice).status_code == 204
    assert login(accounts, "alice", PASSWORD).status_code == 401
    assert login(accounts, "alice", "a-brand-new-password").status_code == 200
    assert alice.get("/api/auth/me").status_code == 200                                # this session carries on
    assert accounts.get("/api/auth/me", headers=bearer(other)).status_code == 401      # the other one is signed out


def test_the_new_password_is_checked_like_any_other(accounts):
    alice = person(accounts)
    assert change(alice, new="short").status_code == 422
    assert change(alice, new="alice-alice-alice").status_code == 422


def test_guessing_the_current_password_is_throttled_too(accounts, monkeypatch):
    setting(monkeypatch, "LOGIN_MAX_FAILURES", 3)
    alice = person(accounts)
    for _ in range(3):
        change(alice, current="wrong-password-guess")
    assert change(alice).status_code == 429


def test_limits_are_forgotten_between_tests(accounts):
    assert limits.failures("user").locked_for("alice") == 0
