"""Per-user request limits, with a stricter one for the endpoints that call the model."""

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from tests.helpers import sql_reply

CLEAR = {"ambiguous": False, "question": None, "options": [], "reason": ""}
SUMMARY = {"columns": ["n"], "rows": [[1]], "truncated": False, "question": "how many"}


def setting(monkeypatch, name, value):
    monkeypatch.setenv(name, str(value))
    get_settings.cache_clear()


def chat(c, message="how many customers"):
    return c.post("/api/chat/query", json={"datasource_id": "sample", "message": message, "include_summary": False})


# ------------------------------------------------------------------ every request

def test_requests_beyond_the_limit_are_refused_with_a_wait(client, monkeypatch):
    setting(monkeypatch, "RATE_LIMIT_PER_MINUTE", 5)
    assert [client.get("/api/datasources").status_code for _ in range(5)] == [200] * 5
    r = client.get("/api/datasources")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert "too fast" in r.json()["detail"]


def test_each_user_has_their_own_budget(client, other_client, monkeypatch):
    setting(monkeypatch, "RATE_LIMIT_PER_MINUTE", 2)
    client.get("/api/datasources"), client.get("/api/datasources")
    assert client.get("/api/datasources").status_code == 429
    assert other_client.get("/api/datasources").status_code == 200


def test_the_limit_is_per_user_not_per_address_with_accounts(accounts, monkeypatch):
    from tests.test_auth import person

    setting(monkeypatch, "RATE_LIMIT_PER_MINUTE", 3)
    alice, bob = person(accounts, "alice"), person(accounts, "bob")          # the same test client address
    for _ in range(3):
        alice.get("/api/datasources")
    assert alice.get("/api/datasources").status_code == 429
    assert bob.get("/api/datasources").status_code == 200


def test_a_limit_of_zero_switches_the_limit_off(client, monkeypatch):
    setting(monkeypatch, "RATE_LIMIT_PER_MINUTE", 0)
    assert all(client.get("/api/datasources").status_code == 200 for _ in range(40))


def test_the_health_check_is_never_limited(client, monkeypatch):
    setting(monkeypatch, "RATE_LIMIT_PER_MINUTE", 1)
    assert all(client.get("/health").status_code == 200 for _ in range(10))


def test_requests_without_a_login_are_refused_before_they_count_against_anyone(accounts, monkeypatch):
    setting(monkeypatch, "RATE_LIMIT_PER_MINUTE", 1)
    assert [accounts.get("/api/datasources").status_code for _ in range(5)] == [401] * 5


# ---------------------------------------------------------------------- the model

def test_questions_beyond_the_llm_limit_are_refused_before_the_model_is_called(client, fake_llm, monkeypatch):
    setting(monkeypatch, "RATE_LIMIT_LLM_PER_MINUTE", 2)
    for _ in range(2):
        fake_llm.replies += [CLEAR, sql_reply("SELECT COUNT(*) AS n FROM customers")]
        assert chat(client).status_code == 200
    calls = len(fake_llm.calls)
    r = chat(client)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert len(fake_llm.calls) == calls                                       # no model call, no cost
    assert client.get("/api/chat/conversations").json().__len__() == 2        # and nothing was stored


def test_the_four_model_endpoints_share_one_budget(client, fake_llm, monkeypatch):
    setting(monkeypatch, "RATE_LIMIT_LLM_PER_MINUTE", 3)
    fake_llm.replies += [CLEAR, sql_reply("SELECT 1 AS n")]
    assert chat(client).status_code == 200
    fake_llm.replies += [sql_reply("SELECT 1 AS n")]
    assert client.post("/api/sql/generate", json={"datasource_id": "sample", "question": "one"}).status_code == 200
    fake_llm.replies += [sql_reply("SELECT 1 AS n")]
    assert client.post("/api/sql/query", json={"datasource_id": "sample", "question": "two"}).status_code == 200
    assert client.post("/api/analytics/summary", json=SUMMARY).status_code == 429


def test_other_endpoints_are_not_held_back_by_the_llm_limit(client, fake_llm, monkeypatch):
    setting(monkeypatch, "RATE_LIMIT_LLM_PER_MINUTE", 1)
    fake_llm.replies += [CLEAR, sql_reply("SELECT 1 AS n")]
    chat(client)
    assert chat(client).status_code == 429
    for path in ("/api/datasources", "/api/chat/conversations", "/api/history?datasource_id=sample"):
        assert client.get(path).status_code == 200, path
    assert client.post("/api/sql/execute", json={"datasource_id": "sample", "sql": "SELECT 1"}).status_code == 200


def test_the_llm_limit_is_per_user(client, other_client, fake_llm, monkeypatch):
    setting(monkeypatch, "RATE_LIMIT_LLM_PER_MINUTE", 1)
    fake_llm.replies += [CLEAR, sql_reply("SELECT 1 AS n")] * 2
    assert chat(client).status_code == 200
    assert chat(client).status_code == 429
    assert chat(other_client).status_code == 200


def test_a_limit_of_zero_switches_the_llm_limit_off(client, fake_llm, monkeypatch):
    setting(monkeypatch, "RATE_LIMIT_LLM_PER_MINUTE", 0)
    fake_llm.replies += [CLEAR, sql_reply("SELECT 1 AS n")] * 5
    assert all(chat(client).status_code == 200 for _ in range(5))


def test_a_question_also_counts_against_the_general_limit_once(client, fake_llm, monkeypatch):
    setting(monkeypatch, "RATE_LIMIT_PER_MINUTE", 2)
    fake_llm.replies += [CLEAR, sql_reply("SELECT 1 AS n")]
    assert chat(client).status_code == 200                                    # one request, not two
    assert client.get("/api/datasources").status_code == 200
    assert client.get("/api/datasources").status_code == 429


def test_the_defaults_leave_room_for_normal_use(client):
    s = get_settings()
    assert s.rate_limit_llm_per_minute >= 10 and s.rate_limit_per_minute >= 120


# ------------------------------------------------------------ probing database hosts

NOT_ALLOWED = {"host": "db.example.com", "port": 5432, "database": "shop", "username": "analyst", "password": "pw"}


def probe(c, body=NOT_ALLOWED):
    return c.post("/api/database/test", json=body)


def test_testing_a_connection_has_its_own_tighter_limit(client, monkeypatch):
    """Each test is a way to ask 'is anything listening there?', so it gets far fewer tries than ordinary requests."""
    setting(monkeypatch, "RATE_LIMIT_DB_PROBE_PER_MINUTE", 3)
    assert [probe(client).status_code for _ in range(3)] == [403, 403, 403]             # refused hosts still count
    r = probe(client)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1 and "too fast" in r.json()["detail"]


def test_connecting_and_testing_share_the_same_budget(client, monkeypatch):
    setting(monkeypatch, "RATE_LIMIT_DB_PROBE_PER_MINUTE", 2)
    probe(client)
    assert client.post("/api/database/connect", json=NOT_ALLOWED).status_code == 403
    assert probe(client).status_code == 429


def test_the_probe_limit_leaves_everything_else_alone(client, monkeypatch):
    setting(monkeypatch, "RATE_LIMIT_DB_PROBE_PER_MINUTE", 1)
    probe(client)
    assert probe(client).status_code == 429
    assert client.get("/api/datasources").status_code == 200
    assert client.post("/api/sql/execute", json={"datasource_id": "sample", "sql": "SELECT 1"}).status_code == 200


def test_the_probe_limit_is_per_user(client, other_client, monkeypatch):
    setting(monkeypatch, "RATE_LIMIT_DB_PROBE_PER_MINUTE", 1)
    probe(client)
    assert probe(client).status_code == 429
    assert probe(other_client).status_code == 403


def test_a_probe_limit_of_zero_switches_it_off(client, monkeypatch):
    setting(monkeypatch, "RATE_LIMIT_DB_PROBE_PER_MINUTE", 0)
    assert all(probe(client).status_code == 403 for _ in range(15))


def test_a_refused_probe_still_counts_against_the_general_limit_once(client, monkeypatch):
    setting(monkeypatch, "RATE_LIMIT_PER_MINUTE", 2)
    assert probe(client).status_code == 403
    assert client.get("/api/datasources").status_code == 200
    assert client.get("/api/datasources").status_code == 429


def test_the_default_probe_limit_is_tight_but_usable():
    s = get_settings()
    assert 5 <= s.rate_limit_db_probe_per_minute <= 30
