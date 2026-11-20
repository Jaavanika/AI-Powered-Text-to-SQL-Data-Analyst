import json
import sqlite3
from contextlib import closing

import pytest

from app.config import get_settings
from app.services import registry
from tests.helpers import sql_reply

CLEAR = {"ambiguous": False, "question": None, "options": [], "reason": ""}
BY_CATEGORY = ("SELECT p.category, SUM(o.quantity * p.price) AS revenue FROM orders o "
               "JOIN products p ON p.product_id = o.product_id GROUP BY p.category ORDER BY revenue DESC")


def summary_reply(text):
    return json.dumps({"summary": text})


def chat(client, message, conversation_id=None, include_summary=True):
    body = {"datasource_id": "sample", "message": message, "include_summary": include_summary}
    if conversation_id:
        body["conversation_id"] = conversation_id
    return client.post("/api/chat/query", json=body)


def payload(**extra):
    return {"columns": ["category", "revenue"],
            "rows": [["Electronics", 600], ["Furniture", 300.5], ["Apparel", 100]], **extra}


# ------------------------------------------------------------- chat + analysis

def test_chat_returns_chart_profile_and_a_grounded_summary(client, fake_llm):
    fake_llm.replies += [CLEAR, sql_reply(BY_CATEGORY)]
    first = chat(client, "revenue by category", include_summary=False).json()
    rows = first["result"]["rows"]
    top = rows[0]
    fake_llm.replies += [CLEAR, sql_reply(BY_CATEGORY),
                         summary_reply(f"{top[0]} had the highest revenue at {top[1]:.2f}.")]
    body = chat(client, "revenue by category").json()
    assert body["status"] == "ok"
    analysis = body["analysis"]
    assert analysis["chart"]["type"] == "bar" and analysis["chart"]["x"] == "category"
    assert analysis["profile"]["row_count"] == len(rows) and analysis["profile"]["facts"]
    assert analysis["summary_source"] == "llm" and top[0] in analysis["summary"]
    assert len(fake_llm.calls) == 5                          # (check + generate) twice, then one summary


def test_include_summary_false_returns_the_chart_without_calling_the_llm_again(client, fake_llm):
    fake_llm.replies += [CLEAR, sql_reply(BY_CATEGORY)]
    body = chat(client, "revenue by category", include_summary=False).json()
    assert body["analysis"]["chart"]["type"] == "bar"
    assert body["analysis"]["summary"] is None and body["analysis"]["summary_source"] is None
    assert len(fake_llm.calls) == 2


def test_the_summary_is_stored_on_the_turn(client, fake_llm):
    fake_llm.replies += [CLEAR, sql_reply("SELECT COUNT(*) AS n FROM customers"),
                         summary_reply("There are 40 customers.")]
    cid = chat(client, "how many customers").json()["conversation_id"]
    turn = client.get(f"/api/chat/conversations/{cid}").json()["turns"][0]
    assert turn["summary"] == "There are 40 customers."


def test_an_invented_number_never_reaches_the_user(client, fake_llm):
    fake_llm.replies += [CLEAR, sql_reply("SELECT COUNT(*) AS n FROM customers"),
                         summary_reply("There are 4000 customers."), summary_reply("There are 9 million customers.")]
    analysis = chat(client, "how many customers").json()["analysis"]
    assert analysis["summary_source"] == "template"
    assert "4000" not in analysis["summary"] and "40" in analysis["summary"]


def test_single_value_answers_become_kpi_cards(client, fake_llm):
    fake_llm.replies += [CLEAR, sql_reply("SELECT COUNT(*) AS n FROM customers"), summary_reply("There are 40 customers.")]
    chart = chat(client, "how many customers").json()["analysis"]["chart"]
    assert chart["type"] == "kpi" and chart["data"] == [{"label": "n", "value": 40}]


def test_clarifications_and_failures_carry_no_analysis(client, fake_llm):
    fake_llm.replies += [{"ambiguous": True, "question": "Which metric?", "options": ["a", "b"], "reason": ""}]
    assert chat(client, "best product").json()["analysis"] is None
    fake_llm.replies += [CLEAR] + [sql_reply("SELECT nope FROM orders")] * 3
    body = chat(client, "something impossible").json()
    assert body["status"] == "failed" and body["analysis"] is None


def test_a_crash_in_the_analysis_does_not_lose_the_answer(client, fake_llm, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("profiling exploded")

    monkeypatch.setattr("app.services.analyst_workflow.profile_result", boom)
    fake_llm.replies += [CLEAR, sql_reply("SELECT COUNT(*) AS n FROM customers")]
    body = chat(client, "how many customers", include_summary=False).json()
    assert body["status"] == "ok" and body["result"]["rows"] == [[40]] and body["analysis"] is None


def test_truncated_results_are_flagged_in_the_profile(client, fake_llm, monkeypatch):
    monkeypatch.setenv("QUERY_MAX_ROWS", "5")
    get_settings.cache_clear()
    fake_llm.replies += [CLEAR, sql_reply("SELECT order_id, quantity FROM orders")]
    profile = chat(client, "all orders", include_summary=False).json()["analysis"]["profile"]
    assert profile["truncated"] is True and "cut off" in profile["facts"][0]


# --------------------------------------------------------- /api/analytics/chart

def test_chart_endpoint_profiles_and_picks_a_chart(client):
    r = client.post("/api/analytics/chart", json=payload(question="Revenue by category"))
    assert r.status_code == 200
    body = r.json()
    assert body["chart"]["type"] == "bar" and body["chart"]["title"] == "Revenue by category"
    assert body["profile"]["columns"][1]["kind"] == "numeric" and body["summary"] is None


def test_chart_endpoint_lets_the_user_switch_chart_type(client):
    assert client.post("/api/analytics/chart", json=payload(chart_type="pie")).json()["chart"]["type"] == "pie"
    assert client.post("/api/analytics/chart", json=payload(chart_type="table")).json()["chart"]["type"] == "table"
    fallback = client.post("/api/analytics/chart", json=payload(chart_type="scatter")).json()["chart"]
    assert fallback["type"] == "bar" and "scatter chart does not fit" in fallback["notes"][0]
    assert client.post("/api/analytics/chart", json=payload(chart_type="radar")).status_code == 422


def test_chart_endpoint_handles_empty_results(client):
    body = client.post("/api/analytics/chart", json={"columns": ["a"], "rows": []}).json()
    assert body["chart"] is None and body["profile"]["row_count"] == 0


@pytest.mark.parametrize("bad", [
    {"columns": [], "rows": []},
    {"columns": ["a", "b"], "rows": [[1]]},                       # ragged
    {"columns": ["a"], "rows": [[{"nested": 1}]]},                # not a plain cell value
    {"columns": ["a"], "rows": [[[1, 2]]]},
    {"columns": ["c%d" % i for i in range(51)], "rows": []},      # too many columns
    {"rows": [[1]]},
])
def test_chart_endpoint_rejects_malformed_payloads(client, bad):
    assert client.post("/api/analytics/chart", json=bad).status_code == 422


def test_chart_endpoint_enforces_the_row_limit(client, monkeypatch):
    monkeypatch.setenv("QUERY_MAX_ROWS", "3")
    get_settings.cache_clear()
    assert client.post("/api/analytics/chart", json={"columns": ["a"], "rows": [[1]] * 3}).status_code == 200
    assert client.post("/api/analytics/chart", json={"columns": ["a"], "rows": [[1]] * 4}).status_code == 422


def test_chart_endpoint_requires_a_workspace(client):
    assert client.post("/api/analytics/chart", json=payload(), headers={"X-Workspace-Id": "x"}).status_code == 400


# ------------------------------------------------------- /api/analytics/summary

def test_summary_endpoint(client, fake_llm):
    fake_llm.replies += [summary_reply("Electronics led with 600 in revenue.")]
    r = client.post("/api/analytics/summary", json=payload(question="Revenue by category"))
    assert r.status_code == 200
    assert r.json() == {"summary": "Electronics led with 600 in revenue.", "source": "llm"}


def test_summary_endpoint_falls_back_to_the_template(client, fake_llm):
    fake_llm.replies += [summary_reply("Revenue doubled to 5000."), summary_reply("Revenue hit 9999.")]
    body = client.post("/api/analytics/summary", json=payload()).json()
    assert body["source"] == "template" and "5000" not in body["summary"]


def test_summary_endpoint_can_attach_the_summary_to_a_turn(client, fake_llm):
    fake_llm.replies += [CLEAR, sql_reply("SELECT COUNT(*) AS n FROM customers")]
    first = chat(client, "how many customers", include_summary=False).json()
    assert client.get(f"/api/chat/conversations/{first['conversation_id']}").json()["turns"][0]["summary"] is None

    fake_llm.replies += [summary_reply("There are 40 customers.")]
    r = client.post("/api/analytics/summary", json={
        "columns": ["n"], "rows": [[40]], "question": "how many customers", "turn_id": first["turn_id"]})
    assert r.status_code == 200
    turn = client.get(f"/api/chat/conversations/{first['conversation_id']}").json()["turns"][0]
    assert turn["summary"] == "There are 40 customers."


def test_summary_endpoint_does_not_touch_other_workspaces_turns(client, other_client, fake_llm):
    fake_llm.replies += [CLEAR, sql_reply("SELECT COUNT(*) AS n FROM customers")]
    first = chat(client, "how many customers", include_summary=False).json()
    r = other_client.post("/api/analytics/summary", json={
        "columns": ["n"], "rows": [[40]], "turn_id": first["turn_id"]})
    assert r.status_code == 404 and fake_llm.replies == []            # refused before any LLM call
    assert client.post("/api/analytics/summary", json={
        "columns": ["n"], "rows": [[40]], "turn_id": "nope"}).status_code == 404


def test_summary_endpoint_without_a_configured_llm_is_503(client):
    assert client.post("/api/analytics/summary", json=payload()).status_code == 503


# ------------------------------------------------------------------- migration

def test_an_existing_registry_without_the_summary_column_is_upgraded(tmp_path, monkeypatch):
    data = tmp_path / "old"
    data.mkdir()
    with closing(sqlite3.connect(data / "registry.sqlite")) as conn, conn:
        conn.execute("CREATE TABLE turns (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, seq INTEGER NOT NULL,"
                     " question TEXT NOT NULL, status TEXT NOT NULL, sql TEXT, explanation TEXT NOT NULL DEFAULT '',"
                     " tables_json TEXT NOT NULL DEFAULT '[]', clarification TEXT, options_json TEXT NOT NULL"
                     " DEFAULT '[]', columns_json TEXT NOT NULL DEFAULT '[]', row_count INTEGER, error TEXT,"
                     " created_at TEXT NOT NULL)")
        conn.execute("INSERT INTO turns (id, conversation_id, seq, question, status, created_at)"
                     " VALUES ('t1', 'c1', 1, 'old question', 'ok', '2026-01-01T00:00:00')")
    monkeypatch.setenv("DATA_DIR", str(data))
    get_settings.cache_clear()
    registry.init_registry()
    registry.init_registry()                                          # running it again is harmless
    with closing(registry.connect()) as conn:
        assert "summary" in {r[1] for r in conn.execute("PRAGMA table_info(turns)")}
        assert conn.execute("SELECT question, summary FROM turns").fetchone()[:] == ("old question", None)
    get_settings.cache_clear()
