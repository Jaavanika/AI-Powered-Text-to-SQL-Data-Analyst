"""Connecting and using an external PostgreSQL database through the API, against a real server."""

import json
from types import SimpleNamespace

import psycopg
import pytest

from app.config import get_settings
from app.services import database_service, postgres_connector, registry
from app.services.postgres_connector import PgParams
from tests.helpers import sql_reply

CLEAR = {"ambiguous": False, "question": None, "options": [], "reason": ""}
PASSWORD = "analyst-pw"


def body(pg, **overrides):
    return {"host": pg["host"], "port": pg["port"], "database": pg["database"], "username": "analyst",
            "password": PASSWORD, "schema_name": pg["schema"], "sslmode": "disable", **overrides}


def setting(monkeypatch, name, value):
    monkeypatch.setenv(name, str(value))
    get_settings.cache_clear()


def count_orders(pg):
    with psycopg.connect(pg["admin_uri"]) as c:
        return c.execute("SELECT COUNT(*) FROM shop.orders").fetchone()[0]


# -------------------------------------------------------------- test & connect

def test_testing_a_connection_reports_what_the_account_can_see_and_saves_nothing(client, pg):
    r = client.post("/api/database/test", json=body(pg))
    assert r.status_code == 200
    report = r.json()
    assert report["server_version"].startswith("16") and report["username"] == "analyst"
    assert report["database"] == pg["database"] and report["schema_name"] == "shop"
    assert {t["name"]: t["row_count"] for t in report["tables"]} == {
        "customers": 40, "orders": 400, "payments": 354, "products": 20, "regions": 4}
    assert report["warnings"] == []
    assert [d["id"] for d in client.get("/api/datasources").json()] == ["sample"]       # nothing was registered


def test_connecting_registers_a_read_only_data_source_without_the_password(pg_connect, client):
    r = pg_connect(name="Shop warehouse")
    assert r.status_code == 201
    ds = r.json()["datasource"]
    assert ds["name"] == "Shop warehouse" and ds["kind"] == "postgres" and ds["dialect"] == "postgres"
    assert ds["read_only"] is True and len(ds["tables"]) == 5
    assert ds["connection"] == {"host": "127.0.0.1", "port": ds["connection"]["port"], "database": "postgres",
                                "username": "analyst", "schema_name": "shop", "sslmode": "disable"}
    assert PASSWORD not in r.text
    for path in (f"/api/datasources/{ds['id']}", "/api/datasources"):
        assert PASSWORD not in client.get(path).text


def test_a_default_name_is_made_from_the_database_and_host(pg_connect):
    assert pg_connect().json()["datasource"]["name"] == "postgres on 127.0.0.1"


def test_the_password_is_stored_encrypted(pg_connect, client):
    ds = pg_connect(password="Zx9!very-secret-pw").json()["datasource"]
    raw = (registry.data_dir() / "registry.sqlite").read_bytes()
    assert b"very-secret-pw" not in raw
    stored = registry.get_stored_connection(ds["id"], "workspace-one")
    assert stored["password_enc"] and "very-secret" not in stored["password_enc"]
    assert (registry.data_dir() / "credentials.key").exists()


def test_a_connection_without_a_password_is_allowed(pg_connect):
    assert pg_connect(password="").status_code == 201


# ------------------------------------------------------------------- policy

def test_a_superuser_account_is_refused(client, pg):
    r = client.post("/api/database/connect", json=body(pg, username=pg["superuser"], password=""))
    assert r.status_code == 403 and "superuser" in r.json()["detail"]
    assert [d["id"] for d in client.get("/api/datasources").json()] == ["sample"]


def test_a_superuser_can_be_allowed_by_the_administrator(client, pg, monkeypatch):
    setting(monkeypatch, "ALLOW_SUPERUSER_CONNECTIONS", "true")
    assert client.post("/api/database/connect", json=body(pg, username=pg["superuser"], password="")).status_code == 201


def test_an_account_that_can_write_connects_with_a_warning(client, pg):
    r = client.post("/api/database/connect", json=body(pg, username="shop_owner", password="owner-pw"))
    assert r.status_code == 201
    warnings = r.json()["warnings"]
    assert len(warnings) == 1 and "can modify data in 5 table(s)" in warnings[0]
    assert "read-only account is strongly recommended" in warnings[0]
    assert "owner-pw" not in r.text


def test_a_host_that_is_not_allowed_is_refused_before_any_connection_is_made(client, pg):
    for path in ("/api/database/test", "/api/database/connect"):
        r = client.post(path, json=body(pg, host="db.example.com"))
        assert r.status_code == 403 and "ALLOWED_DB_HOSTS" in r.json()["detail"]
    assert client.post("/api/database/test", json=body(pg, host="169.254.169.254")).status_code == 403


def test_the_allowlist_is_configurable(client, pg, monkeypatch):
    setting(monkeypatch, "ALLOWED_DB_HOSTS", "10.0.0.0/8")
    assert client.post("/api/database/test", json=body(pg)).status_code == 403            # loopback no longer listed
    setting(monkeypatch, "ALLOWED_DB_HOSTS", "127.0.0.1")
    assert client.post("/api/database/test", json=body(pg)).status_code == 200


def test_unencrypted_traffic_to_a_remote_host_is_warned_about():
    facts = SimpleNamespace(role_warnings=[], writable_tables=[], tables=[object()])
    remote = PgParams(host="db.corp.example", port=5432, database="d", username="u", sslmode="prefer")
    assert any("may not be encrypted" in w for w in database_service._warnings(facts, remote))
    assert not any("encrypted" in w for w in database_service._warnings(facts, PgParams(**{**remote.__dict__, "sslmode": "require"})))
    assert not any("encrypted" in w for w in database_service._warnings(facts, PgParams(**{**remote.__dict__, "host": "localhost"})))


# ---------------------------------------------------------------- failures

def test_failures_are_explained_in_plain_words(client, pg, monkeypatch):
    setting(monkeypatch, "DB_CONNECT_TIMEOUT_SECONDS", 2)
    cases = [
        (dict(port=1), 400, ("Could not connect to 127.0.0.1:1", "timed out")),
        (dict(database="no_such_db"), 400, ("does not exist",)),
        (dict(username="no_such_role"), 400, ("refused the connection",)),
        (dict(schema_name="nope"), 404, ("schema 'nope' does not exist",)),
    ]
    for override, status, expected in cases:
        r = client.post("/api/database/connect", json=body(pg, **override))
        assert r.status_code == status, (override, r.text)
        assert any(e in r.json()["detail"] for e in expected), r.json()
        assert PASSWORD not in r.text
    assert [d["id"] for d in client.get("/api/datasources").json()] == ["sample"]


def test_malformed_requests_never_echo_the_password_back(client, pg):
    for bad in ({"port": 70000}, {"host": ""}, {"sslmode": "carrier-pigeon"}, {"database": None}):
        r = client.post("/api/database/connect", json=body(pg, **bad))
        assert r.status_code == 422 and PASSWORD not in r.text, bad
        assert all("input" not in e for e in r.json()["detail"])
    incomplete = client.post("/api/database/connect", json={"host": "127.0.0.1", "password": PASSWORD})
    assert incomplete.status_code == 422 and PASSWORD not in incomplete.text


# -------------------------------------------------------- schema and preview

def test_the_schema_has_typed_columns_declared_keys_and_relationships(client, pg_source):
    schema = client.get(f"/api/datasources/{pg_source['id']}/schema").json()
    assert schema["dialect"] == "postgres"
    tables = {t["name"]: t for t in schema["tables"]}
    assert tables["orders"]["primary_key"] == ["order_id"] and tables["orders"]["primary_key_inferred"] is False
    assert {c["name"]: c["type"] for c in tables["orders"]["columns"]}["order_date"] == "date"
    assert {c["name"]: c["db_type"] for c in tables["products"]["columns"]}["price"].startswith("NUMERIC")
    assert {r["id"] for r in schema["relationships"]} == {
        "fk:customers.region_id->regions", "fk:orders.customer_id->customers",
        "fk:orders.product_id->products", "fk:payments.order_id->orders"}
    assert all(r["source"] == "declared" and r["cardinality"] == "many_to_one" for r in schema["relationships"])


def test_joins_follow_the_declared_keys(client, pg_source):
    plan = client.post(f"/api/datasources/{pg_source['id']}/join-path", json={"tables": ["customers", "products"]}).json()
    assert plan["status"] == "ok" and plan["tables"] == ["customers", "orders", "products"]


def test_table_preview_returns_json_friendly_rows(client, pg_source):
    r = client.get(f"/api/datasources/{pg_source['id']}/tables/products/preview", params={"limit": 3})
    assert r.status_code == 200 and len(r.json()["rows"]) == 3
    assert isinstance(r.json()["rows"][0]["price"], (int, float))
    assert client.get(f"/api/datasources/{pg_source['id']}/tables/nope/preview").status_code == 404


def test_relationships_can_be_suggested_and_confirmed_when_the_database_declares_none(client, pg):
    with psycopg.connect(pg["admin_uri"], autocommit=True) as admin:
        admin.execute("DROP SCHEMA IF EXISTS loose CASCADE")
        admin.execute("CREATE SCHEMA loose")
        admin.execute("CREATE TABLE loose.customers (customer_id int, name text)")
        admin.execute("CREATE TABLE loose.orders (order_id int, customer_id int, total numeric)")
        admin.execute("INSERT INTO loose.customers VALUES (1, 'A'), (2, 'B'), (3, 'C')")
        admin.execute("INSERT INTO loose.orders VALUES (10, 1, 5), (11, 1, 6), (12, 2, 7)")
        admin.execute("GRANT USAGE ON SCHEMA loose TO analyst")
        admin.execute("GRANT SELECT ON ALL TABLES IN SCHEMA loose TO analyst")
        try:
            ds = client.post("/api/database/connect", json=body(pg, schema_name="loose")).json()["datasource"]
            base = f"/api/datasources/{ds['id']}"
            suggestions = client.get(f"{base}/relationships/suggestions").json()
            assert [(s["from_table"], s["from_column"], s["to_table"], s["to_column"]) for s in suggestions] == [
                ("orders", "customer_id", "customers", "customer_id")]
            assert client.post(f"{base}/join-path", json={"tables": ["customers", "orders"]}).json()["status"] == "disconnected"
            r = client.post(f"{base}/relationships", json={"from_table": "orders", "from_column": "customer_id",
                                                           "to_table": "customers", "to_column": "customer_id"})
            assert r.status_code == 201 and r.json()["source"] == "user"
            assert client.post(f"{base}/join-path", json={"tables": ["customers", "orders"]}).json()["status"] == "ok"
        finally:
            admin.execute("DROP SCHEMA loose CASCADE")


def test_refreshing_picks_up_new_tables_and_current_row_counts(client, pg, pg_source):
    base = f"/api/datasources/{pg_source['id']}"
    assert len(client.get(base).json()["tables"]) == 5
    with psycopg.connect(pg["admin_uri"], autocommit=True) as admin:
        admin.execute("CREATE TABLE shop.extra (x int)")
        admin.execute("INSERT INTO shop.extra SELECT generate_series(1, 7)")
        admin.execute("GRANT SELECT ON shop.extra TO analyst")
        try:
            refreshed = client.post(f"{base}/refresh").json()
            assert {t["name"]: t["row_count"] for t in refreshed["tables"]}["extra"] == 7
            assert len(client.get(base).json()["tables"]) == 6                     # saved, not just returned
            assert "extra" in {t["name"] for t in client.get(f"{base}/schema").json()["tables"]}
        finally:
            admin.execute("DROP TABLE shop.extra")
    assert len(client.post(f"{base}/refresh").json()["tables"]) == 5


def test_refreshing_the_sample_or_an_upload_changes_nothing(client):
    r = client.post("/api/datasources/sample/refresh")
    assert r.status_code == 200 and r.json()["id"] == "sample"


# -------------------------------------------------------------------- queries

def execute(client, ds, sql):
    return client.post("/api/sql/execute", json={"datasource_id": ds["id"], "sql": sql})


def test_validated_postgresql_runs_and_returns_typed_json(client, pg_source):
    r = execute(client, pg_source, "SELECT date_trunc('month', order_date)::date AS m, COUNT(*) AS n, "
                                   "SUM(quantity) AS qty FROM orders GROUP BY 1 ORDER BY 1 LIMIT 3")
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["columns"] == ["m", "n", "qty"] and result["row_count"] == 3
    assert result["rows"][0][0] == "2024-01-01" and isinstance(result["rows"][0][1], int)


def test_postgresql_features_the_model_will_use_work_end_to_end(client, pg_source):
    for sql in (
        "SELECT EXTRACT(YEAR FROM order_date) AS y, COUNT(*) FROM orders GROUP BY 1 ORDER BY 1",
        "SELECT customer_name FROM customers WHERE customer_name ILIKE '%an%' LIMIT 3",
        "SELECT string_agg(DISTINCT category, ', ' ORDER BY category) FROM products",
        "SELECT COUNT(*) FILTER (WHERE quantity > 2) AS big FROM orders",
        "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY price) FROM products",
        "SELECT to_char(order_date, 'YYYY-MM') AS ym, SUM(quantity) FROM orders GROUP BY 1 ORDER BY 1 LIMIT 2",
        "SELECT c.customer_name, SUM(p.amount) AS paid FROM customers c JOIN orders o ON o.customer_id = c.customer_id "
        "JOIN payments p ON p.order_id = o.order_id GROUP BY 1 ORDER BY 2 DESC LIMIT 3",
        "WITH big AS (SELECT * FROM orders WHERE quantity > 3) SELECT COUNT(*) FROM big",
    ):
        r = execute(client, pg_source, sql)
        assert r.status_code == 200, (sql, r.text)


def test_unsafe_postgresql_is_refused_and_the_data_is_untouched(client, pg, pg_source):
    before = count_orders(pg)
    for sql in ("DROP TABLE orders", "DELETE FROM orders", "SELECT pg_sleep(30)", "SELECT pg_read_file('/etc/passwd')",
                "SELECT set_config('transaction_read_only', 'off', true)", "SELECT * FROM information_schema.tables",
                "SELECT * FROM pg_user", "SELECT 1; DELETE FROM orders", "COPY orders TO '/tmp/x'",
                "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d", "SELECT * FROM orders FOR UPDATE"):
        r = execute(client, pg_source, sql)
        assert r.status_code == 422, (sql, r.status_code)
    assert count_orders(pg) == before


def test_a_writing_account_still_cannot_write_through_the_api(client, pg):
    owner = client.post("/api/database/connect", json=body(pg, username="shop_owner", password="owner-pw")).json()["datasource"]
    before = count_orders(pg)
    for sql in ("DELETE FROM orders", "UPDATE products SET price = 0", "TRUNCATE orders"):
        assert execute(client, owner, sql).status_code == 422
    assert count_orders(pg) == before


def test_a_runaway_query_is_stopped_by_the_server(client, pg_source, monkeypatch):
    setting(monkeypatch, "QUERY_TIMEOUT_SECONDS", 1)
    r = execute(client, pg_source, "SELECT COUNT(*) FROM orders a, orders b, orders c, orders d, orders e")
    assert r.status_code == 408


def test_the_row_cap_applies(client, pg_source, monkeypatch):
    setting(monkeypatch, "QUERY_MAX_ROWS", 5)
    result = execute(client, pg_source, "SELECT order_id FROM orders").json()["result"]
    assert result["row_count"] == 5 and result["truncated"] is True


def test_runtime_errors_are_reported_as_400(client, pg_source):
    r = execute(client, pg_source, "SELECT 1 / 0")
    assert r.status_code == 400 and "division by zero" in r.json()["detail"]


# ------------------------------------------------------------------ the chat

def chat(client, ds, message, **kw):
    return client.post("/api/chat/query", json={"datasource_id": ds["id"], "message": message,
                                                "include_summary": False, **kw})


def test_a_question_is_answered_from_postgresql_end_to_end(client, pg_source, fake_llm):
    sql = ("SELECT p.category, SUM(o.quantity) AS units FROM orders o JOIN products p ON p.product_id = o.product_id "
           "GROUP BY p.category ORDER BY units DESC")
    fake_llm.replies += [CLEAR, sql_reply(sql, "Units sold per category.")]
    r = chat(client, pg_source, "Total quantity of electronics sold, by category")
    assert r.status_code == 200
    body_ = r.json()
    assert body_["status"] == "ok" and body_["dialect"] == "postgres"
    assert body_["result"]["columns"] == ["category", "units"] and body_["result"]["row_count"] == 4
    assert body_["analysis"]["chart"]["type"] == "bar"

    prompt = fake_llm.calls[1][1]["content"]                         # the generation prompt
    system = fake_llm.calls[1][0]["content"]
    assert "PostgreSQL" in system and "date_trunc" in system and "double quotes" in system
    assert "Table orders (~400 rows, primary key: order_id)" in prompt
    assert "order_date date" in prompt.lower() and "stored as ISO text" not in prompt
    assert "products.category = 'Electronics'" in prompt             # a value hint read from PostgreSQL


def test_a_follow_up_works_on_postgresql_and_is_revalidated(client, pg, pg_source, fake_llm):
    before = count_orders(pg)
    fake_llm.replies += [CLEAR, sql_reply("SELECT COUNT(*) AS n FROM customers")]
    cid = chat(client, pg_source, "How many customers?").json()["conversation_id"]
    fake_llm.replies += [CLEAR, sql_reply("DELETE FROM orders"), sql_reply("SELECT COUNT(*) AS n FROM orders")]
    body_ = chat(client, pg_source, "and orders?", conversation_id=cid).json()
    assert body_["status"] == "ok" and body_["result"]["rows"] == [[400]]
    assert "Only SELECT" in body_["attempts"][0]["error"] and count_orders(pg) == before


def test_a_model_that_forgets_to_quote_a_name_is_corrected(client, pg, fake_llm):
    with psycopg.connect(pg["admin_uri"], autocommit=True) as admin:
        admin.execute("DROP SCHEMA IF EXISTS quoted CASCADE")
        admin.execute("CREATE SCHEMA quoted")
        admin.execute('CREATE TABLE quoted."Sales Data" ("Order Total" numeric, region text)')
        admin.execute("""INSERT INTO quoted."Sales Data" VALUES (10, 'N'), (20, 'S')""")
        admin.execute("GRANT USAGE ON SCHEMA quoted TO analyst")
        admin.execute("GRANT SELECT ON ALL TABLES IN SCHEMA quoted TO analyst")
        try:
            ds = client.post("/api/database/connect", json=body(pg, schema_name="quoted")).json()["datasource"]
            fake_llm.replies += [CLEAR, sql_reply('SELECT SUM(order_total) FROM "Sales Data"'),
                                 sql_reply('SELECT SUM("Order Total") AS total FROM "Sales Data"')]
            r = chat(client, ds, "total of all orders").json()
            assert r["status"] == "ok" and r["result"]["rows"] == [[30]]
            assert len(r["attempts"]) == 1
            assert '"Sales Data"' in fake_llm.calls[1][1]["content"]       # names with spaces are shown quoted
        finally:
            admin.execute("DROP SCHEMA quoted CASCADE")


# --------------------------------------------------- lifecycle and isolation

def test_deleting_removes_the_connection_credentials_and_conversations(client, pg_connect, fake_llm):
    ds = pg_connect().json()["datasource"]
    fake_llm.replies += [CLEAR, sql_reply("SELECT COUNT(*) AS n FROM customers")]
    chat(client, ds, "How many customers?")
    assert ds["id"] in postgres_connector._engines
    assert client.delete(f"/api/datasources/{ds['id']}").status_code == 204
    assert ds["id"] not in postgres_connector._engines                                # the pool was closed
    assert registry.get_stored_connection(ds["id"], "workspace-one") is None
    assert client.get(f"/api/datasources/{ds['id']}").status_code == 404
    assert client.get("/api/chat/conversations").json() == []


def test_other_workspaces_cannot_see_or_use_a_connection(client, other_client, pg_source):
    base = f"/api/datasources/{pg_source['id']}"
    assert other_client.get(base).status_code == 404
    assert other_client.get(f"{base}/schema").status_code == 404
    assert other_client.post(f"{base}/refresh").status_code == 404
    assert other_client.delete(base).status_code == 404
    assert execute(other_client, pg_source, "SELECT 1").status_code == 404
    assert [d["id"] for d in other_client.get("/api/datasources").json()] == ["sample"]


def test_uploads_cannot_be_added_to_an_external_database(client, pg_source):
    preview = client.post("/api/upload/preview", files={"file": ("t.csv", b"a\n1\n", "text/csv")}).json()
    r = client.post("/api/upload/import", json={"upload_id": preview["upload_id"], "datasource_id": pg_source["id"]})
    assert r.status_code == 403


def test_an_unreachable_database_is_reported_as_unavailable(client, pg_source, fake_llm, monkeypatch):
    setting(monkeypatch, "DB_CONNECT_TIMEOUT_SECONDS", 2)
    stored = registry.get_stored_connection(pg_source["id"], "workspace-one")
    stored["port"] = 1                                                              # the server "goes away"
    with registry.connect() as conn:
        conn.execute("UPDATE datasources SET connection_json = ? WHERE id = ?", (json.dumps(stored), pg_source["id"]))
    postgres_connector.dispose_engine(pg_source["id"])
    assert execute(client, pg_source, "SELECT 1").status_code == 503
    r = client.get(f"/api/datasources/{pg_source['id']}/schema")
    assert r.status_code == 503 and "analyst" not in r.text and "127.0.0.1" not in r.text     # nothing internal leaks
    fake_llm.replies += [CLEAR, sql_reply("SELECT 1")]
    assert chat(client, pg_source, "anything").status_code == 503
    assert client.post(f"/api/datasources/{pg_source['id']}/refresh").status_code in (502, 503)


def test_a_changed_encryption_key_asks_the_user_to_reconnect(client, pg_source, monkeypatch):
    from cryptography.fernet import Fernet
    setting(monkeypatch, "CREDENTIALS_KEY", Fernet.generate_key().decode())
    r = client.get(f"/api/datasources/{pg_source['id']}/schema")
    assert r.status_code == 409 and "Reconnect" in r.json()["detail"]
