"""PostgreSQL views are listed and can be queried, like tables, and only the ones the account may read."""

import psycopg
import pytest

from app.schemas import LinkedSchema, LinkedTable, SchemaColumn
from app.services import sql_generation

SCHEMA = "shop"


def admin(pg, *statements):
    with psycopg.connect(pg["admin_uri"], autocommit=True) as conn:
        for sql in statements:
            conn.execute(sql)


@pytest.fixture
def views(pg):
    """A plain view and a materialized view the analyst may read, and one it may not. Always removed afterwards."""
    admin(
        pg,
        f"CREATE VIEW {SCHEMA}.completed_orders AS SELECT order_id, customer_id, quantity FROM {SCHEMA}.orders WHERE status = 'completed'",
        f"CREATE MATERIALIZED VIEW {SCHEMA}.orders_per_customer AS SELECT customer_id, COUNT(*) AS orders FROM {SCHEMA}.orders GROUP BY customer_id",
        f"CREATE VIEW {SCHEMA}.private_orders AS SELECT * FROM {SCHEMA}.orders",
        f"GRANT SELECT ON {SCHEMA}.completed_orders, {SCHEMA}.orders_per_customer TO analyst",
    )
    yield
    admin(pg, f"DROP VIEW IF EXISTS {SCHEMA}.completed_orders", f"DROP MATERIALIZED VIEW IF EXISTS {SCHEMA}.orders_per_customer",
          f"DROP VIEW IF EXISTS {SCHEMA}.private_orders")


def tables_of(source):
    return {t["name"]: t for t in source["tables"]}


def test_readable_views_are_listed_with_the_tables_and_marked(views, pg_connect):
    source = pg_connect().json()["datasource"]
    tables = tables_of(source)
    assert {"completed_orders", "orders_per_customer"} <= tables.keys()
    assert tables["completed_orders"]["is_view"] is True and tables["orders_per_customer"]["is_view"] is True
    assert [c["name"] for c in tables["completed_orders"]["columns"]] == ["order_id", "customer_id", "quantity"]
    assert tables["orders"]["is_view"] is False and tables["customers"]["is_view"] is False


def test_a_view_the_account_cannot_read_is_not_offered(views, pg_connect):
    assert "private_orders" not in tables_of(pg_connect().json()["datasource"])


def test_a_view_has_no_row_count_to_pay_for(views, pg_connect):
    """Counting a view runs its whole query, so it is not counted when connecting."""
    assert tables_of(pg_connect().json()["datasource"])["completed_orders"]["row_count"] == 0


def test_without_views_the_tables_are_exactly_as_before(pg_connect):
    tables = tables_of(pg_connect().json()["datasource"])
    assert set(tables) == {"customers", "orders", "payments", "products", "regions"}
    assert not any(t["is_view"] for t in tables.values())


def test_the_schema_lists_a_view_without_keys_or_relationships(views, client, pg_source):
    schema = client.get(f"/api/datasources/{pg_source['id']}/schema").json()
    view = next(t for t in schema["tables"] if t["name"] == "completed_orders")
    assert view["is_view"] is True and view["primary_key"] == [] and view["row_count"] == 0
    assert not any("completed_orders" in (r["from_table"], r["to_table"]) for r in schema["relationships"])
    assert next(t for t in schema["tables"] if t["name"] == "orders")["is_view"] is False


def test_a_view_can_be_queried_read_only(views, client, pg_source):
    r = client.post("/api/sql/execute", json={"datasource_id": pg_source["id"], "sql": "SELECT COUNT(*) AS n FROM completed_orders"})
    assert r.status_code == 200, r.text
    expected = client.post("/api/sql/execute", json={"datasource_id": pg_source["id"],
                                                     "sql": "SELECT COUNT(*) AS n FROM orders WHERE status = 'completed'"}).json()
    assert r.json()["result"]["rows"] == expected["result"]["rows"]
    mv = client.post("/api/sql/execute", json={"datasource_id": pg_source["id"], "sql": "SELECT SUM(orders) AS n FROM orders_per_customer"})
    assert mv.status_code == 200 and mv.json()["result"]["rows"][0][0] == 400


def test_a_view_still_cannot_be_written_through(views, client, pg_source):
    for sql in ("DELETE FROM completed_orders", "UPDATE completed_orders SET quantity = 0"):
        assert client.post("/api/sql/execute", json={"datasource_id": pg_source["id"], "sql": sql}).status_code in (400, 422)


def test_a_view_can_be_previewed(views, client, pg_source):
    r = client.get(f"/api/datasources/{pg_source['id']}/tables/completed_orders/preview?limit=3")
    assert r.status_code == 200 and len(r.json()["rows"]) == 3 and r.json()["columns"][0]["name"] == "order_id"


def test_refreshing_finds_a_view_created_after_connecting(pg, client, pg_source):
    assert "completed_orders" not in tables_of(client.get("/api/datasources").json()[-1])
    admin(pg, f"CREATE VIEW {SCHEMA}.completed_orders AS SELECT order_id FROM {SCHEMA}.orders WHERE status = 'completed'",
          f"GRANT SELECT ON {SCHEMA}.completed_orders TO analyst")
    try:
        refreshed = client.post(f"/api/datasources/{pg_source['id']}/refresh").json()
        assert tables_of(refreshed)["completed_orders"]["is_view"] is True
    finally:
        admin(pg, f"DROP VIEW IF EXISTS {SCHEMA}.completed_orders")


def test_the_model_is_told_a_view_is_a_view_not_an_empty_table():
    col = SchemaColumn(name="order_id", original_name="order_id", type="integer", db_type="integer", nullable=True)
    linked = LinkedSchema(tables=[
        LinkedTable(name="completed_orders", original_name="completed_orders", row_count=0, columns=[col], is_view=True),
        LinkedTable(name="orders", original_name="orders", row_count=400, columns=[col], primary_key=["order_id"])],
        relationships=[])
    text = sql_generation.render_schema(linked, "postgres")
    assert "Table completed_orders (view)" in text and "~0 rows" not in text
    assert "Table orders (~400 rows, primary key: order_id)" in text


def test_sqlite_sources_have_no_views(client):
    assert not any(t["is_view"] for t in client.get("/api/datasources").json()[0]["tables"])
