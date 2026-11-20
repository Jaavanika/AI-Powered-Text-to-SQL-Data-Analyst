import sqlite3
from contextlib import closing

from app.services import registry


def _query(sql):
    path = registry.sample_db_path()
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(sql).fetchall()


def test_sample_is_registered_and_listed_first(client):
    sources = client.get("/api/datasources").json()
    assert sources[0]["id"] == "sample"
    assert sources[0]["read_only"] is True
    tables = {t["name"]: t for t in sources[0]["tables"]}
    assert set(tables) == {"regions", "customers", "products", "orders", "payments"}
    assert tables["orders"]["row_count"] == 400
    types = {c["name"]: c["type"] for c in tables["orders"]["columns"]}
    assert types["order_date"] == "date" and types["quantity"] == "integer"


def test_sample_data_is_deterministic_and_consistent(client):
    assert _query("PRAGMA foreign_key_check") == []
    # some products and customers are never referenced, for "never ordered" questions
    assert _query("SELECT COUNT(*) FROM products WHERE product_id NOT IN (SELECT product_id FROM orders)")[0][0] == 2
    assert _query("SELECT COUNT(*) FROM customers WHERE customer_id NOT IN (SELECT customer_id FROM orders)")[0][0] >= 3
    # data spans both years and several statuses
    assert {r[0] for r in _query("SELECT DISTINCT substr(order_date, 1, 4) FROM orders")} == {"2024", "2025"}
    assert {r[0] for r in _query("SELECT DISTINCT status FROM orders")} == {"completed", "cancelled", "returned"}
    # edge cases: NULLs exist
    assert _query("SELECT COUNT(*) FROM customers WHERE city IS NULL")[0][0] >= 1


def test_sample_cannot_be_deleted(client):
    assert client.delete("/api/datasources/sample").status_code == 403


def test_sample_table_preview(client):
    r = client.get("/api/datasources/sample/tables/customers/preview", params={"limit": 3})
    assert r.status_code == 200
    assert len(r.json()["rows"]) == 3


def test_startup_is_idempotent(client):
    # entering the lifespan again must not recreate or duplicate anything
    from fastapi.testclient import TestClient
    with TestClient(client.app, headers={"X-Workspace-Id": "workspace-one"}) as again:
        assert len(again.get("/api/datasources").json()) == 1
