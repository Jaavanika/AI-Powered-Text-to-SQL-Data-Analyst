import sqlite3
from contextlib import closing

import pytest

from app.config import get_settings
from app.services import registry
from app.services.llm_service import LLMError
from tests.helpers import sql_reply, upload_csvs


def ask(client, question="How many customers are there?", datasource_id="sample"):
    return client.post("/api/sql/query", json={"datasource_id": datasource_id, "question": question})


def sample_counts():
    with closing(sqlite3.connect(registry.sample_db_path())) as conn:
        return [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("customers", "orders", "products", "payments", "regions")]


def setting(monkeypatch, name, value):
    monkeypatch.setenv(name, str(value))
    get_settings.cache_clear()


GOOD = "SELECT COUNT(*) AS n FROM customers"


def test_valid_sql_runs_on_the_first_attempt(client, fake_llm):
    fake_llm.replies.append(sql_reply(GOOD, "Counts customers."))
    body = ask(client).json()
    assert body["status"] == "ok" and body["error"] is None
    assert body["result"]["columns"] == ["n"] and body["result"]["rows"] == [[40]]
    assert body["validation"]["valid"] is True and body["validation"]["tables"] == ["customers"]
    assert body["attempts"] == [] and len(fake_llm.calls) == 1


def test_invalid_sql_is_corrected_using_the_validator_feedback(client, fake_llm):
    fake_llm.replies += [sql_reply("SELECT o.revenue FROM orders o"), sql_reply(GOOD)]
    body = ask(client).json()
    assert body["status"] == "ok" and body["result"]["rows"] == [[40]]
    assert len(body["attempts"]) == 1 and "Unknown column" in body["attempts"][0]["error"]
    feedback = fake_llm.calls[1][-1]["content"]
    assert "failed validation" in feedback and "Columns available" in feedback and "quantity" in feedback
    assert fake_llm.calls[1][-2]["role"] == "assistant"      # the rejected answer is shown back


@pytest.mark.parametrize("unsafe", [
    "DROP TABLE orders",
    "DELETE FROM customers",
    "SELECT 1; DROP TABLE orders",
    "UPDATE products SET price = 0",
    "SELECT * FROM sqlite_master",
    "SELECT load_extension('x')",
])
def test_unsafe_sql_from_the_model_is_never_executed(client, fake_llm, unsafe):
    before = sample_counts()
    fake_llm.replies += [sql_reply(unsafe), sql_reply(GOOD)]
    body = ask(client).json()
    assert body["status"] == "ok"                          # recovered with safe SQL on the retry
    assert body["attempts"][0]["sql"] == unsafe
    assert sample_counts() == before


def test_model_that_keeps_proposing_unsafe_sql_is_stopped_after_the_attempt_limit(client, fake_llm):
    before = sample_counts()
    fake_llm.replies += [sql_reply("DROP TABLE orders")] * 5
    body = ask(client).json()
    assert body["status"] == "failed" and body["result"] is None
    assert len(fake_llm.calls) == 3 and len(body["attempts"]) == 3        # sql_max_attempts defaults to 3
    assert "3 attempt" in body["error"] and "Only SELECT" in body["error"]
    assert sample_counts() == before


def test_attempt_limit_is_configurable(client, fake_llm, monkeypatch):
    setting(monkeypatch, "SQL_MAX_ATTEMPTS", 1)
    fake_llm.replies += [sql_reply("SELECT nope FROM orders")]
    body = ask(client).json()
    assert body["status"] == "failed" and len(fake_llm.calls) == 1


def test_unusable_replies_count_as_attempts_and_can_recover(client, fake_llm):
    fake_llm.replies += ["this is not json", sql_reply(GOOD)]
    body = ask(client).json()
    assert body["status"] == "ok" and len(body["attempts"]) == 1
    assert body["attempts"][0]["sql"] is None and "not a valid JSON" in body["attempts"][0]["error"]


def test_runtime_error_is_fed_back_for_correction(client, fake_llm):
    # passes validation but overflows when run
    fake_llm.replies += [sql_reply("SELECT abs(-9223372036854775808)"), sql_reply(GOOD)]
    body = ask(client).json()
    assert body["status"] == "ok"
    assert "overflow" in body["attempts"][0]["error"].lower()
    assert "running the SQL failed" in fake_llm.calls[1][-1]["content"]


def test_timeout_is_reported_to_the_model_with_advice(client, fake_llm, monkeypatch):
    setting(monkeypatch, "QUERY_TIMEOUT_SECONDS", 1)
    slow = "SELECT COUNT(*) FROM orders a, orders b, orders c, orders d, orders e"
    fake_llm.replies += [sql_reply(slow), sql_reply(GOOD)]
    body = ask(client).json()
    assert body["status"] == "ok" and "time limit" in body["attempts"][0]["error"]
    assert "cheaper" in fake_llm.calls[1][-1]["content"]


def test_clarification_is_returned_without_running_anything(client, fake_llm):
    fake_llm.replies.append({"sql": None, "explanation": "", "tables_used": [], "assumptions": [],
                             "clarification": "Best by revenue or quantity?"})
    body = ask(client, "best product").json()
    assert body["status"] == "clarification" and "revenue" in body["clarification"]
    assert body["sql"] is None and body["result"] is None and body["attempts"] == []


def test_blocked_columns_cannot_be_read_even_if_the_model_asks(client, fake_llm, monkeypatch):
    setting(monkeypatch, "BLOCKED_COLUMNS", "customers.email")
    fake_llm.replies += [sql_reply("SELECT email FROM customers"), sql_reply("SELECT * FROM customers"),
                         sql_reply("SELECT customer_name FROM customers LIMIT 2")]
    body = ask(client).json()
    assert body["status"] == "ok" and len(body["attempts"]) == 2
    assert all("not permitted" in a["error"] for a in body["attempts"])
    assert body["result"]["columns"] == ["customer_name"]


def test_row_cap_is_reported(client, fake_llm, monkeypatch):
    setting(monkeypatch, "QUERY_MAX_ROWS", 5)
    fake_llm.replies.append(sql_reply("SELECT order_id FROM orders"))
    body = ask(client).json()
    assert body["result"]["row_count"] == 5 and body["result"]["truncated"] is True


def test_llm_outage_during_correction_returns_a_clean_error(client, fake_llm):
    fake_llm.replies += [sql_reply("SELECT nope FROM orders"), LLMError("The LLM request timed out.", 504)]
    r = ask(client)
    assert r.status_code == 504 and r.json()["detail"] == "The LLM request timed out."


def test_query_on_uploaded_data(client, fake_llm):
    ds = upload_csvs(client, {"sales": "region,amount\nN,10\nN,5\nS,7\n"})
    fake_llm.replies.append(sql_reply("SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY region"))
    body = ask(client, "sales by region", ds["id"]).json()
    assert body["status"] == "ok" and body["result"]["rows"] == [["N", 15], ["S", 7]]


def test_query_errors_for_unknown_or_foreign_sources(client, other_client, fake_llm):
    assert ask(client, datasource_id="nope").status_code == 404
    ds = upload_csvs(client, {"t": "a\n1\n"})
    assert ask(other_client, datasource_id=ds["id"]).status_code == 404


# --------------------------------------------------------- /validate and /execute

def post(client, path, sql, datasource_id="sample"):
    return client.post(f"/api/sql/{path}", json={"datasource_id": datasource_id, "sql": sql})


def test_validate_endpoint(client):
    ok = post(client, "validate", "SELECT COUNT(*) FROM customers;").json()
    assert ok["valid"] is True and ok["sql"] == "SELECT COUNT(*) FROM customers"
    bad = post(client, "validate", "DROP TABLE customers").json()
    assert bad["valid"] is False and "Only SELECT" in bad["errors"][0]
    assert post(client, "validate", "SELECT 1", "nope").status_code == 404


def test_execute_endpoint_runs_valid_sql(client):
    r = post(client, "execute", "SELECT customer_id FROM customers ORDER BY 1 LIMIT 2")
    assert r.status_code == 200
    assert r.json()["result"]["rows"] == [[1], [2]] and r.json()["validation"]["valid"] is True


def test_execute_endpoint_refuses_invalid_sql_and_leaves_data_alone(client):
    before = sample_counts()
    for sql in ("DROP TABLE orders", "DELETE FROM orders", "SELECT * FROM sqlite_master",
                "SELECT 1; DELETE FROM orders", "INSERT INTO orders VALUES (1,1,1,1,'x','y')"):
        r = post(client, "execute", sql)
        assert r.status_code == 422, sql
        assert r.json()["detail"]["errors"]
    assert sample_counts() == before


def test_execute_endpoint_error_codes(client, monkeypatch):
    assert post(client, "execute", "SELECT abs(-9223372036854775808)").status_code == 400
    setting(monkeypatch, "QUERY_TIMEOUT_SECONDS", 1)
    slow = "SELECT COUNT(*) FROM orders a, orders b, orders c, orders d, orders e"
    assert post(client, "execute", slow).status_code == 408
    assert post(client, "execute", "").status_code == 422             # empty is rejected by the request model
    assert post(client, "execute", "SELECT 1", "nope").status_code == 404


def test_execute_endpoint_respects_workspaces(client, other_client):
    ds = upload_csvs(client, {"t": "a\n1\n"})
    assert post(client, "execute", "SELECT a FROM t", ds["id"]).json()["result"]["rows"] == [[1]]
    assert post(other_client, "execute", "SELECT a FROM t", ds["id"]).status_code == 404
