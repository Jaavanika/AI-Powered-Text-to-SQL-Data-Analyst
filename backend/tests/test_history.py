"""Query history: search, retention, kept results, deletion and isolation between workspaces."""

import json
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.services import conversation_service, registry
from tests.helpers import sql_reply, upload_csvs

CLEAR = {"ambiguous": False, "question": None, "options": [], "reason": ""}
UNSURE = {"ambiguous": True, "question": "Which region?", "options": ["North", "South"], "reason": "unclear"}


def ask(client, fake_llm, question, sql="SELECT COUNT(*) AS n FROM customers", **kw):
    fake_llm.replies += [CLEAR, sql_reply(sql)]
    r = client.post("/api/chat/query", json={"datasource_id": "sample", "message": question,
                                              "include_summary": False, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def history(client, **params):
    r = client.get("/api/history", params={"datasource_id": "sample", **params})
    assert r.status_code == 200, r.text
    return r.json()


def retain(monkeypatch, rows=0, days=90):
    monkeypatch.setenv("HISTORY_RETAIN_ROWS", str(rows))
    monkeypatch.setenv("HISTORY_RETENTION_DAYS", str(days))
    get_settings.cache_clear()


def age(turn_id, days):
    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="microseconds")
    with closing(registry.connect()) as conn, conn:
        conn.execute("UPDATE turns SET created_at = ? WHERE id = ?", (stamp, turn_id))


def count(table):
    with closing(registry.connect()) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ----------------------------------------------------------------- listing

def test_history_is_empty_at_first(client):
    page = history(client)
    assert page["items"] == [] and page["total"] == 0
    assert page["retention"] == {"retain_rows": 0, "retention_days": 90}


def test_questions_come_back_newest_first_with_sql_and_status(client, fake_llm):
    ask(client, fake_llm, "how many customers")
    ask(client, fake_llm, "how many orders", "SELECT COUNT(*) AS n FROM orders")
    page = history(client)
    assert [i["question"] for i in page["items"]] == ["how many orders", "how many customers"]
    newest = page["items"][0]
    assert newest["status"] == "ok" and "FROM orders" in newest["sql"] and newest["row_count"] == 1
    assert newest["conversation_title"] and newest["conversation_id"] and newest["has_result"] is False


def test_follow_ups_in_one_conversation_are_listed_individually(client, fake_llm):
    cid = ask(client, fake_llm, "how many customers")["conversation_id"]
    ask(client, fake_llm, "and orders", "SELECT COUNT(*) AS n FROM orders", conversation_id=cid)
    items = history(client)["items"]
    assert len(items) == 2 and {i["conversation_id"] for i in items} == {cid}


def test_clarifications_and_failures_are_kept_and_filterable(client, fake_llm):
    ask(client, fake_llm, "good question")
    fake_llm.replies += [UNSURE]
    client.post("/api/chat/query", json={"datasource_id": "sample", "message": "sales by region", "include_summary": False})
    fake_llm.replies += [CLEAR, sql_reply("DROP TABLE orders"), sql_reply("DROP TABLE orders"), sql_reply("DROP TABLE orders")]
    client.post("/api/chat/query", json={"datasource_id": "sample", "message": "bad one", "include_summary": False})
    assert history(client)["total"] == 3
    assert [i["question"] for i in history(client, status="ok")["items"]] == ["good question"]
    unsure = history(client, status="clarification")["items"]
    assert [i["question"] for i in unsure] == ["sales by region"] and unsure[0]["options"] == ["North", "South"]
    assert [i["question"] for i in history(client, status="failed")["items"]] == ["bad one"]
    assert client.get("/api/history", params={"datasource_id": "sample", "status": "nope"}).status_code == 422


def test_search_matches_question_sql_and_summary_case_insensitively(client, fake_llm):
    ask(client, fake_llm, "Top Customers please", "SELECT customer_id FROM customers LIMIT 3")
    ask(client, fake_llm, "order count", "SELECT COUNT(*) AS n FROM orders")
    assert [i["question"] for i in history(client, q="TOP cust")["items"]] == ["Top Customers please"]
    assert [i["question"] for i in history(client, q="from orders")["items"]] == ["order count"]          # in the SQL
    turn = history(client)["items"][0]
    conversation_service.set_summary(turn["id"], "workspace-one", "Mentions zebra stripes")
    assert [i["question"] for i in history(client, q="zebra")["items"]] == ["order count"]               # in the summary
    assert history(client, q="nothing like this")["total"] == 0


def test_search_treats_percent_and_underscore_literally(client, fake_llm):
    ask(client, fake_llm, "growth is 50% up")
    ask(client, fake_llm, "plain question")
    assert [i["question"] for i in history(client, q="50%")["items"]] == ["growth is 50% up"]
    assert history(client, q="%")["total"] == 1                       # not a wildcard that matches everything
    assert history(client, q="_")["total"] == 0
    assert history(client, q="   ")["total"] == 2                     # blank search = no search


def test_pagination_reports_the_total_and_does_not_overlap(client, fake_llm):
    for i in range(5):
        ask(client, fake_llm, f"question {i}")
    first, second = history(client, limit=2), history(client, limit=2, offset=2)
    assert first["total"] == second["total"] == 5
    names = [i["question"] for i in first["items"] + second["items"]]
    assert names == ["question 4", "question 3", "question 2", "question 1"]
    assert [i["question"] for i in history(client, limit=2, offset=4)["items"]] == ["question 0"]
    assert history(client, offset=50)["items"] == []
    assert client.get("/api/history", params={"datasource_id": "sample", "limit": 0}).status_code == 422
    assert client.get("/api/history", params={"datasource_id": "sample", "limit": 101}).status_code == 422


def test_history_is_per_data_source(client, fake_llm):
    ask(client, fake_llm, "sample question")
    ds = upload_csvs(client, {"t": "a\n1\n"})
    assert client.get("/api/history", params={"datasource_id": ds["id"]}).json()["total"] == 0


def test_history_is_private_to_the_workspace(client, other_client, fake_llm):
    turn = ask(client, fake_llm, "secret question")
    mine = history(client)["items"][0]
    theirs = other_client.get("/api/history", params={"datasource_id": "sample"}).json()
    assert theirs["total"] == 0 and theirs["items"] == []
    assert other_client.delete(f"/api/history/turns/{mine['id']}").status_code == 404
    assert other_client.get(f"/api/history/turns/{mine['id']}/result").status_code == 404
    other_client.delete("/api/history", params={"datasource_id": "sample"})        # clearing theirs leaves mine
    assert history(client)["total"] == 1 and turn["conversation_id"]


# ------------------------------------------------------------ kept results

def test_result_rows_are_not_kept_by_default(client, fake_llm):
    ask(client, fake_llm, "customer ids", "SELECT customer_id FROM customers LIMIT 5")
    item = history(client)["items"][0]
    assert item["has_result"] is False and count("turn_results") == 0
    assert client.get(f"/api/history/turns/{item['id']}/result").status_code == 404


def test_rows_are_kept_when_the_administrator_allows_it(client, fake_llm, monkeypatch):
    retain(monkeypatch, rows=3)
    ask(client, fake_llm, "customer ids", "SELECT customer_id FROM customers ORDER BY customer_id LIMIT 3")
    item = history(client)["items"][0]
    assert item["has_result"] is True and history(client)["retention"]["retain_rows"] == 3
    kept = client.get(f"/api/history/turns/{item['id']}/result").json()
    assert kept["columns"] == ["customer_id"] and len(kept["rows"]) == 3 and kept["truncated"] is False


def test_only_the_configured_number_of_rows_is_kept_and_says_so(client, fake_llm, monkeypatch):
    retain(monkeypatch, rows=5)
    ask(client, fake_llm, "all orders", "SELECT order_id FROM orders ORDER BY order_id")
    item = history(client)["items"][0]
    assert item["row_count"] > 5
    kept = client.get(f"/api/history/turns/{item['id']}/result").json()
    assert len(kept["rows"]) == 5 and kept["truncated"] is True
    assert any("first 5 rows" in n for n in kept["notes"])


def test_a_few_huge_cells_cannot_bloat_the_stored_result(client, fake_llm, monkeypatch):
    retain(monkeypatch, rows=100)
    monkeypatch.setattr(conversation_service, "MAX_STORED_RESULT_BYTES", 3000)
    ask(client, fake_llm, "wide", f"SELECT order_id, '{'x' * 200}' AS blob FROM orders ORDER BY order_id LIMIT 100")
    item = history(client)["items"][0]
    kept = client.get(f"/api/history/turns/{item['id']}/result").json()
    assert 1 <= len(kept["rows"]) < 100 and kept["truncated"] is True
    assert len(json.dumps(kept["rows"])) <= 3000


def test_failed_and_clarification_turns_keep_nothing(client, fake_llm, monkeypatch):
    retain(monkeypatch, rows=10)
    fake_llm.replies += [UNSURE]
    client.post("/api/chat/query", json={"datasource_id": "sample", "message": "sales by region", "include_summary": False})
    fake_llm.replies += [CLEAR] + [sql_reply("DROP TABLE orders")] * 3
    client.post("/api/chat/query", json={"datasource_id": "sample", "message": "bad", "include_summary": False})
    assert count("turn_results") == 0 and not any(i["has_result"] for i in history(client)["items"])


def test_an_empty_result_stores_no_rows(client, fake_llm, monkeypatch):
    retain(monkeypatch, rows=10)
    ask(client, fake_llm, "nobody", "SELECT customer_id FROM customers WHERE 1 = 0")
    assert history(client)["items"][0]["has_result"] is False


# ---------------------------------------------------------------- deletion

def test_deleting_a_question_removes_it_and_its_kept_rows(client, fake_llm, monkeypatch):
    retain(monkeypatch, rows=3)
    ask(client, fake_llm, "one", "SELECT customer_id FROM customers LIMIT 3")
    ask(client, fake_llm, "two", "SELECT customer_id FROM customers LIMIT 3")
    gone = history(client)["items"][1]
    assert client.delete(f"/api/history/turns/{gone['id']}").status_code == 204
    assert [i["question"] for i in history(client)["items"]] == ["two"] and count("turn_results") == 1
    assert client.delete(f"/api/history/turns/{gone['id']}").status_code == 404


def test_deleting_the_last_question_removes_the_empty_conversation(client, fake_llm):
    ask(client, fake_llm, "only one")
    assert len(client.get("/api/chat/conversations", params={"datasource_id": "sample"}).json()) == 1
    client.delete(f"/api/history/turns/{history(client)['items'][0]['id']}")
    assert client.get("/api/chat/conversations", params={"datasource_id": "sample"}).json() == []


def test_a_conversation_with_other_questions_survives_deleting_one(client, fake_llm):
    cid = ask(client, fake_llm, "first")["conversation_id"]
    ask(client, fake_llm, "second", conversation_id=cid)
    client.delete(f"/api/history/turns/{history(client)['items'][1]['id']}")
    assert [c["id"] for c in client.get("/api/chat/conversations", params={"datasource_id": "sample"}).json()] == [cid]


def test_clearing_history_removes_everything_for_that_source_only(client, fake_llm, monkeypatch):
    retain(monkeypatch, rows=3)
    ask(client, fake_llm, "one", "SELECT customer_id FROM customers LIMIT 3")
    ask(client, fake_llm, "two", "SELECT customer_id FROM customers LIMIT 3")
    ds = upload_csvs(client, {"t": "a\n1\n2\n"})
    fake_llm.replies += [CLEAR, sql_reply("SELECT a FROM t")]
    client.post("/api/chat/query", json={"datasource_id": ds["id"], "message": "upload q", "include_summary": False})
    assert client.delete("/api/history", params={"datasource_id": "sample"}).status_code == 204
    assert history(client)["total"] == 0 and history(client)["items"] == []
    assert client.get("/api/history", params={"datasource_id": ds["id"]}).json()["total"] == 1
    assert count("turn_results") == 1                                   # only the upload's rows remain
    assert client.delete("/api/history", params={"datasource_id": "sample"}).status_code == 204   # idempotent


def test_deleting_a_conversation_removes_its_kept_rows(client, fake_llm, monkeypatch):
    retain(monkeypatch, rows=3)
    cid = ask(client, fake_llm, "one", "SELECT customer_id FROM customers LIMIT 3")["conversation_id"]
    assert count("turn_results") == 1
    assert client.delete(f"/api/chat/conversations/{cid}").status_code in (200, 204)
    assert count("turn_results") == 0 and history(client)["total"] == 0


def test_deleting_a_data_source_removes_its_history_and_rows(client, fake_llm, monkeypatch):
    retain(monkeypatch, rows=3)
    ds = upload_csvs(client, {"t": "a\n1\n2\n"})
    fake_llm.replies += [CLEAR, sql_reply("SELECT a FROM t")]
    client.post("/api/chat/query", json={"datasource_id": ds["id"], "message": "upload q", "include_summary": False})
    assert count("turn_results") == 1
    assert client.delete(f"/api/datasources/{ds['id']}").status_code in (200, 204)
    assert count("turns") == 0 and count("turn_results") == 0 and count("conversations") == 0


# --------------------------------------------------------------- retention

def test_old_questions_are_purged(client, fake_llm, monkeypatch):
    retain(monkeypatch, rows=3, days=30)
    ask(client, fake_llm, "ancient", "SELECT customer_id FROM customers LIMIT 3")
    ask(client, fake_llm, "recent", "SELECT customer_id FROM customers LIMIT 3")
    old = history(client)["items"][1]
    age(old["id"], 31)
    assert conversation_service.purge_expired() == 1
    assert [i["question"] for i in history(client)["items"]] == ["recent"]
    assert count("turn_results") == 1


def test_a_question_inside_the_period_is_kept(client, fake_llm, monkeypatch):
    retain(monkeypatch, days=30)
    ask(client, fake_llm, "month old")
    age(history(client)["items"][0]["id"], 29)
    assert conversation_service.purge_expired() == 0 and history(client)["total"] == 1


def test_the_conversation_goes_with_its_last_expired_question(client, fake_llm, monkeypatch):
    retain(monkeypatch, days=30)
    ask(client, fake_llm, "old")
    age(history(client)["items"][0]["id"], 60)
    conversation_service.purge_expired()
    assert count("conversations") == 0


def test_retention_days_zero_keeps_everything(client, fake_llm, monkeypatch):
    retain(monkeypatch, days=0)
    ask(client, fake_llm, "ancient")
    age(history(client)["items"][0]["id"], 5000)
    assert conversation_service.purge_expired() == 0 and history(client)["total"] == 1
    assert history(client)["retention"]["retention_days"] == 0


def test_expired_questions_are_purged_when_a_new_one_is_added(client, fake_llm, monkeypatch):
    retain(monkeypatch, days=30)
    ask(client, fake_llm, "ancient")
    age(history(client)["items"][0]["id"], 90)
    ask(client, fake_llm, "fresh")
    assert [i["question"] for i in history(client)["items"]] == ["fresh"]


def test_purging_applies_to_every_workspace(client, other_client, fake_llm, monkeypatch):
    retain(monkeypatch, days=30)
    ask(client, fake_llm, "mine")
    fake_llm.replies += [CLEAR, sql_reply("SELECT 1 AS n")]
    other_client.post("/api/chat/query", json={"datasource_id": "sample", "message": "theirs", "include_summary": False})
    with closing(registry.connect()) as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM turns")]
    for turn_id in ids:
        age(turn_id, 45)
    assert conversation_service.purge_expired() == 2 and count("turns") == 0


@pytest.mark.parametrize("path", ["/api/history/turns/nope", "/api/history/turns/nope/result"])
def test_unknown_turns_are_404(client, path):
    assert (client.delete(path) if path.endswith("nope") else client.get(path)).status_code == 404
