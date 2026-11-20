"""Per-data-source blocked columns: stored per workspace, enforced everywhere, hidden from the model."""

import pytest

from app.config import get_settings
from tests.helpers import sql_reply, upload_csvs

CLEAR = {"ambiguous": False, "question": None, "options": [], "reason": ""}


def blocked(client, ds="sample"):
    r = client.get(f"/api/datasources/{ds}/blocked-columns")
    assert r.status_code == 200, r.text
    return r.json()


def block(client, columns, ds="sample"):
    return client.put(f"/api/datasources/{ds}/blocked-columns", json={"columns": columns})


def sql_query(client, question="how many customers", ds="sample"):
    return client.post("/api/sql/query", json={"datasource_id": ds, "question": question})


def execute(client, sql, ds="sample"):
    return client.post("/api/sql/execute", json={"datasource_id": ds, "sql": sql})


# ------------------------------------------------------------------- the API

def test_nothing_is_blocked_at_first(client):
    assert blocked(client) == {"columns": [], "locked": []}


def test_entries_are_saved_normalised_and_listed(client):
    r = block(client, [" Customers.Email ", "payments.*", "customers.email", "ORDERS.status"])
    assert r.status_code == 200 and r.json()["columns"] == ["customers.email", "orders.status", "payments.*"]
    assert blocked(client)["columns"] == ["customers.email", "orders.status", "payments.*"]


def test_blank_entries_are_ignored(client):
    assert block(client, ["", "  ", "customers.email"]).json()["columns"] == ["customers.email"]


def test_saving_replaces_the_list_and_an_empty_list_clears_it(client):
    block(client, ["customers.email", "orders.status"])
    assert block(client, ["orders.status"]).json()["columns"] == ["orders.status"]
    assert block(client, []).json()["columns"] == [] and blocked(client)["columns"] == []


@pytest.mark.parametrize("entry,fragment", [
    ("nosuchtable.email", "nosuchtable"),
    ("customers.nosuchcolumn", "nosuchcolumn"),
    ("customers", "table.column"),
    ("a.b.c", "table.column"),
    ("customers.", "table.column"),
])
def test_bad_entries_are_refused_with_a_reason_and_nothing_changes(client, entry, fragment):
    block(client, ["orders.status"])
    r = block(client, ["customers.email", entry])
    assert r.status_code == 422 and fragment in r.json()["detail"]
    assert blocked(client)["columns"] == ["orders.status"]


def test_too_many_entries_are_refused(client):
    assert block(client, [f"customers.c{i}" for i in range(201)]).status_code == 422


def test_an_unknown_data_source_is_a_404(client):
    assert client.get("/api/datasources/nope/blocked-columns").status_code == 404
    assert block(client, [], ds="nope").status_code == 404


def test_the_administrators_setting_is_shown_as_locked_and_cannot_be_removed(client, monkeypatch):
    monkeypatch.setenv("BLOCKED_COLUMNS", "customers.email, Payments.*")
    get_settings.cache_clear()
    assert blocked(client)["locked"] == ["customers.email", "payments.*"]
    block(client, ["orders.status"])
    assert blocked(client) == {"columns": ["orders.status"], "locked": ["customers.email", "payments.*"]}
    assert execute(client, "SELECT email FROM customers").status_code == 422       # still enforced after an empty save
    block(client, [])
    assert execute(client, "SELECT email FROM customers").status_code == 422


# ----------------------------------------------------------------- isolation

def test_blocking_in_one_workspace_does_not_affect_another(client, other_client):
    block(client, ["customers.email"])
    assert blocked(other_client)["columns"] == []
    assert execute(client, "SELECT email FROM customers LIMIT 1").status_code == 422
    assert execute(other_client, "SELECT email FROM customers LIMIT 1").status_code == 200


def test_blocking_applies_to_one_data_source_only(client):
    ds = upload_csvs(client, {"people": "name,email\nA,a@x.com\nB,b@x.com\n"})
    block(client, ["people.email"], ds=ds["id"])
    assert blocked(client)["columns"] == []
    assert execute(client, "SELECT email FROM people", ds=ds["id"]).status_code == 422
    assert execute(client, "SELECT name FROM people", ds=ds["id"]).status_code == 200


def test_deleting_a_data_source_removes_its_blocked_columns(client):
    from contextlib import closing
    from app.services import registry
    ds = upload_csvs(client, {"people": "name,email\nA,a@x.com\n"})
    block(client, ["people.email"], ds=ds["id"])
    assert client.delete(f"/api/datasources/{ds['id']}").status_code == 204
    with closing(registry.connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM blocked_columns").fetchone()[0] == 0


# --------------------------------------------------------------- enforcement

@pytest.mark.parametrize("sql", [
    "SELECT email FROM customers",
    "SELECT * FROM customers",
    "SELECT customer_name FROM customers WHERE email LIKE '%a%'",
    "WITH c AS (SELECT * FROM customers) SELECT * FROM c",
])
def test_a_blocked_column_cannot_be_read_any_way(client, sql):
    block(client, ["customers.email"])
    r = execute(client, sql)
    assert r.status_code == 422 and "not permitted" in str(r.json()["detail"])
    assert client.post("/api/sql/validate", json={"datasource_id": "sample", "sql": sql}).json()["valid"] is False


def test_other_columns_still_work(client):
    block(client, ["customers.email"])
    assert execute(client, "SELECT customer_name FROM customers LIMIT 2").status_code == 200


def test_a_blocked_table_cannot_be_read(client):
    block(client, ["payments.*"])
    assert execute(client, "SELECT COUNT(*) FROM payments").status_code == 422
    assert execute(client, "SELECT COUNT(*) FROM orders").status_code == 200


def test_unblocking_restores_access(client):
    block(client, ["customers.email"])
    block(client, [])
    assert execute(client, "SELECT email FROM customers LIMIT 1").status_code == 200


def test_the_model_asking_for_a_blocked_column_is_corrected_not_obeyed(client, fake_llm):
    block(client, ["customers.email"])
    fake_llm.replies += [sql_reply("SELECT email FROM customers"), sql_reply("SELECT customer_name FROM customers LIMIT 2")]
    body = sql_query(client).json()
    assert body["status"] == "ok" and body["result"]["columns"] == ["customer_name"]
    assert len(body["attempts"]) == 1


# ------------------------------------------------- hidden from what the model sees

def prompt(fake_llm):
    return fake_llm.calls[-1][1]["content"]


def test_blocked_columns_and_tables_are_not_in_the_prompt(client, fake_llm):
    block(client, ["customers.email", "payments.*"])
    fake_llm.replies.append(sql_reply("SELECT COUNT(*) AS n FROM customers"))
    sql_query(client, "how many customers pay by email")
    text = prompt(fake_llm)
    assert "customer_name" in text and "Table customers" in text          # the rest of the schema is still there
    assert "email TEXT" not in text and "Table payments" not in text


def test_without_blocking_the_same_prompt_does_show_them(client, fake_llm):
    fake_llm.replies.append(sql_reply("SELECT COUNT(*) AS n FROM customers"))
    sql_query(client, "how many customers pay by email")
    assert "email" in prompt(fake_llm) and "Table payments" in prompt(fake_llm)


HINT = "orders.status = 'cancelled'"          # the line a mentioned data value becomes in the prompt


def test_values_of_a_blocked_column_are_not_offered_as_hints(client, other_client, fake_llm):
    """Hints are cached per data source; one workspace's blocking must not leak into, or strip, another's."""
    fake_llm.replies += [sql_reply("SELECT 1 AS n")] * 3
    sql_query(other_client, "orders that were cancelled")                 # fills the cache with every column
    assert HINT in prompt(fake_llm)
    block(client, ["orders.status"])
    sql_query(client, "orders that were cancelled")
    assert HINT not in prompt(fake_llm) and "status TEXT" not in prompt(fake_llm)
    sql_query(other_client, "orders that were cancelled")                 # the other workspace is unaffected
    assert HINT in prompt(fake_llm)


def test_the_hint_cache_is_not_filled_from_a_restricted_view_first(client, other_client, fake_llm):
    fake_llm.replies += [sql_reply("SELECT 1 AS n")] * 2
    block(client, ["orders.status"])
    sql_query(client, "cancelled orders")                                 # the restricted workspace goes first
    sql_query(other_client, "cancelled orders")
    assert HINT in prompt(fake_llm)


def test_chat_questions_use_the_restricted_schema_too(client, fake_llm):
    block(client, ["customers.email"])
    fake_llm.replies += [CLEAR, sql_reply("SELECT COUNT(*) AS n FROM customers")]
    client.post("/api/chat/query", json={"datasource_id": "sample", "message": "how many customers", "include_summary": False})
    assert all("email TEXT" not in m["content"] for call in fake_llm.calls for m in call)


def test_a_definition_cannot_use_a_blocked_column(client):
    block(client, ["orders.status"])
    r = client.post("/api/context/sample", json={
        "kind": "metric", "name": "Done", "description": "completed orders",
        "expression": "SUM(CASE WHEN orders.status = 'completed' THEN 1 ELSE 0 END)"})
    assert r.status_code == 422


# ------------------------------------------------------------------- preview

def test_the_table_preview_leaves_out_blocked_columns(client):
    block(client, ["customers.email"])
    body = client.get("/api/datasources/sample/tables/customers/preview").json()
    assert "email" not in [c["name"] for c in body["columns"]]
    assert body["rows"] and all("email" not in row for row in body["rows"])
    assert "customer_name" in body["rows"][0]


def test_a_blocked_table_cannot_be_previewed(client):
    block(client, ["payments.*"])
    r = client.get("/api/datasources/sample/tables/payments/preview")
    assert r.status_code == 403 and "blocked" in r.json()["detail"]


def test_the_schema_endpoint_still_lists_everything_so_blocking_can_be_managed(client):
    block(client, ["customers.email"])
    columns = [c["name"] for t in client.get("/api/datasources/sample/schema").json()["tables"]
               if t["name"] == "customers" for c in t["columns"]]
    assert "email" in columns
