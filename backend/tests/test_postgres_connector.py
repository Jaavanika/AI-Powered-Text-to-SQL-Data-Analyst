"""The PostgreSQL connector against a real server (embedded, or TEST_POSTGRES_URL)."""

import time

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.services import postgres_connector as pgc
from app.services.postgres_connector import (
    PgParams, PostgresConnector, create_pg_engine, friendly_error, inspect_server, sqlstate,
)
from app.services.query_execution import ExecutionError


def params(pg, user="analyst", password="", schema="shop", **kw):
    return PgParams(host=pg["host"], port=pg["port"], database=pg["database"], username=user,
                    password=password, schema=schema, sslmode="disable", **kw)


def connector(pg, user="analyst", **kw):
    return PostgresConnector(f"test-{user}", params(pg, user, **kw))


def run(conn, sql, timeout=5, rows=100):
    return conn.execute(sql, allowed_tables=set(), blocked_columns=set(), timeout_seconds=timeout, max_rows=rows)


def count_orders(pg):
    with psycopg.connect(pg["admin_uri"]) as c:
        return c.execute("SELECT COUNT(*) FROM shop.orders").fetchone()[0]


@pytest.fixture(autouse=True)
def _dispose_engines():
    yield
    for key in list(pgc._engines):
        pgc.dispose_engine(key)


# ------------------------------------------------------------- introspection

def test_inspecting_the_sample_as_a_read_only_role(pg):
    engine = create_pg_engine(params(pg), pooled=False)
    try:
        facts = inspect_server(engine, "shop")
    finally:
        engine.dispose()
    assert facts.version.startswith("16") and facts.user == "analyst"
    assert facts.is_superuser is False and facts.schema_exists is True
    assert {t.name: t.row_count for t in facts.tables} == {
        "customers": 40, "orders": 400, "payments": 354, "products": 20, "regions": 4}
    orders = {c.name: c.type for c in next(t for t in facts.tables if t.name == "orders").columns}
    assert orders == {"order_id": "integer", "customer_id": "integer", "product_id": "integer",
                      "quantity": "integer", "order_date": "date", "status": "text"}
    assert {c.name: c.type for c in next(t for t in facts.tables if t.name == "products").columns}["price"] == "float"
    assert facts.writable_tables == [] and facts.role_warnings == []


def test_an_account_that_can_write_is_reported(pg):
    engine = create_pg_engine(params(pg, "shop_owner"), pooled=False)
    try:
        facts = inspect_server(engine, "shop")
    finally:
        engine.dispose()
    assert facts.writable_tables == ["customers", "orders", "payments", "products", "regions"]


def test_a_missing_schema_is_reported_not_an_error(pg):
    engine = create_pg_engine(params(pg, schema="nope"), pooled=False)
    try:
        facts = inspect_server(engine, "nope")
    finally:
        engine.dispose()
    assert facts.schema_exists is False and facts.tables == []


def test_the_superuser_is_recognised(pg):
    engine = create_pg_engine(params(pg, pg["superuser"]), pooled=False)
    try:
        assert inspect_server(engine, "shop").is_superuser is True
    finally:
        engine.dispose()


# ----------------------------------------------------------------- execution

def test_results_have_columns_rows_and_json_friendly_values(pg):
    result = run(connector(pg), "SELECT customer_id, customer_name, signup_date, region_id FROM customers ORDER BY 1 LIMIT 2")
    assert result.columns == ["customer_id", "customer_name", "signup_date", "region_id"]
    assert result.row_count == 2 and result.rows[0][0] == 1
    assert isinstance(result.rows[0][2], str) and len(result.rows[0][2]) == 10         # a date, as ISO text


def test_numeric_values_come_back_as_numbers_not_strings(pg):
    row = run(connector(pg), "SELECT price, price * 3 AS triple, 12.50::numeric AS exact FROM products ORDER BY 1 LIMIT 1").rows[0]
    assert all(isinstance(v, (int, float)) for v in row)
    assert row[2] == 12.5


def test_other_types_are_converted_safely(pg):
    result = run(connector(pg), "SELECT NULL AS n, TRUE AS b, ARRAY[1,2] AS a, '{\"k\": 1}'::jsonb AS j, "
                                "'550e8400-e29b-41d4-a716-446655440000'::uuid AS u, '\\x010203'::bytea AS raw, "
                                "now() AS ts, interval '2 days' AS span")
    n, b, a, j, u, raw, ts, span = result.rows[0]
    assert n is None and b is True
    assert a == "[1, 2]" and '"k"' in j and u.startswith("550e8400")
    assert raw == "<binary 3 bytes>" and span == "2 days, 0:00:00"
    assert len(ts) == 19 and ts[10] == " "                          # 'YYYY-MM-DD HH:MM:SS', in UTC, no offset


def test_timestamps_are_utc_and_midnights_read_as_plain_dates(pg):
    conn = connector(pg)
    # date_trunc on a date yields a timestamptz at midnight; it must not carry the server's zone offset
    assert run(conn, "SELECT date_trunc('month', order_date) FROM orders ORDER BY 1 LIMIT 1").rows == [["2024-01-01"]]
    assert run(conn, "SELECT current_setting('TimeZone')").rows == [["UTC"]]
    # month boundaries are the same whatever zone the server runs in
    assert run(conn, "SELECT date_trunc('month', TIMESTAMPTZ '2024-03-31 23:30:00+00')").rows == [["2024-03-01"]]
    assert run(conn, "SELECT TIMESTAMPTZ '2024-03-31 23:30:00+05:30'").rows == [["2024-03-31 18:00:00"]]
    assert run(conn, "SELECT TIMESTAMP '2024-03-31 10:15:30.123456'").rows == [["2024-03-31 10:15:30"]]
    assert run(conn, "SELECT DATE '2024-03-31', TIME '10:15:30'").rows == [["2024-03-31", "10:15:30"]]


def test_a_percent_sign_in_the_sql_is_not_a_placeholder(pg):
    result = run(connector(pg), "SELECT COUNT(*) FROM customers WHERE customer_name LIKE '%a%' AND email NOT LIKE '100%'")
    assert result.rows[0][0] > 0


def test_the_row_cap_marks_truncation(pg):
    result = run(connector(pg), "SELECT order_id FROM orders", rows=5)
    assert result.row_count == 5 and result.truncated is True and "first 5 rows" in result.notes[0]
    assert run(connector(pg), "SELECT order_id FROM orders LIMIT 5", rows=5).truncated is False


def test_unqualified_names_resolve_in_the_chosen_schema_only(pg):
    conn = connector(pg)
    assert run(conn, "SELECT current_schema()").rows == [["shop"]]
    assert run(conn, "SELECT current_schemas(false)").rows == [["[\"shop\"]"]] or run(conn, "SELECT current_schemas(false)").rows[0][0] in ('["shop"]', "['shop']")
    assert run(conn, "SELECT COUNT(*) FROM orders").rows == [[400]]


def test_a_slow_query_is_cancelled_by_the_server_at_the_time_limit(pg):
    started = time.monotonic()
    with pytest.raises(ExecutionError) as exc:
        run(connector(pg), "SELECT pg_sleep(10)", timeout=1)
    assert exc.value.kind == "timeout" and "1 second" in exc.value.message
    assert time.monotonic() - started < 6


def test_a_heavy_join_is_cancelled_too(pg):
    with pytest.raises(ExecutionError) as exc:
        run(connector(pg), "SELECT COUNT(*) FROM orders a, orders b, orders c, orders d, orders e", timeout=1)
    assert exc.value.kind == "timeout"


def test_sql_errors_are_reported_without_driver_noise(pg):
    for sql, fragment in (("SELECT * FROM nope", 'relation "nope" does not exist'), ("SELEC 1", "syntax error"),
                          ("SELECT nope FROM customers", 'column "nope" does not exist')):
        with pytest.raises(ExecutionError) as exc:
            run(connector(pg), sql)
        assert exc.value.kind == "sql" and fragment in exc.value.message
        assert "LINE 1" not in exc.value.message and "psycopg" not in exc.value.message


def test_reading_a_table_the_role_has_no_access_to_is_denied(pg):
    with psycopg.connect(pg["admin_uri"], autocommit=True) as admin:
        admin.execute("CREATE TABLE shop.secret_notes (note text)")
        try:
            with pytest.raises(ExecutionError) as exc:
                run(connector(pg), "SELECT * FROM secret_notes")
            assert exc.value.kind == "denied"
        finally:
            admin.execute("DROP TABLE shop.secret_notes")


# ------------------------------------------- the read-only guarantee, itself

WRITES = [
    "DELETE FROM orders",
    "UPDATE products SET price = 0",
    "INSERT INTO regions VALUES (98, 'x')",
    "TRUNCATE orders",
    "CREATE TABLE evil (x int)",
    "DROP TABLE payments",
]


@pytest.mark.parametrize("sql", WRITES)
def test_every_transaction_is_read_only_even_for_an_account_that_may_write(pg, sql):
    """Straight through the engine, with no validator and no streaming cursor in the way."""
    before = count_orders(pg)
    engine = create_pg_engine(params(pg, "shop_owner"), pooled=False)
    try:
        with engine.connect() as conn:
            with pytest.raises(DBAPIError) as exc:
                conn.exec_driver_sql(sql)
            assert sqlstate(exc.value) == "25006"            # "cannot execute ... in a read-only transaction"
    finally:
        engine.dispose()
    assert count_orders(pg) == before


def test_even_a_permitted_sequence_cannot_be_advanced(pg):
    """nextval() changes state without touching a table, so it needs its own check."""
    with psycopg.connect(pg["admin_uri"], autocommit=True) as admin:
        admin.execute("CREATE SEQUENCE shop.counter")
        admin.execute("GRANT USAGE, UPDATE ON SEQUENCE shop.counter TO shop_owner")
        try:
            engine = create_pg_engine(params(pg, "shop_owner"), pooled=False)
            try:
                with engine.connect() as conn:
                    with pytest.raises(DBAPIError) as exc:
                        conn.exec_driver_sql("SELECT nextval('counter')")
                    assert sqlstate(exc.value) == "25006"
            finally:
                engine.dispose()
            assert admin.execute("SELECT last_value, is_called FROM shop.counter").fetchone() == (1, False)
        finally:
            admin.execute("DROP SEQUENCE shop.counter")


def test_the_read_only_flag_cannot_be_switched_off_from_inside(pg):
    engine = create_pg_engine(params(pg, "shop_owner"), pooled=False)
    before = count_orders(pg)
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SHOW transaction_read_only").scalar() == "on"
            with pytest.raises(DBAPIError):
                conn.exec_driver_sql("SELECT set_config('transaction_read_only', 'off', true)")
        with engine.connect() as conn:
            with pytest.raises(DBAPIError):
                conn.exec_driver_sql("SET TRANSACTION READ WRITE")
        with engine.connect() as conn:
            conn.exec_driver_sql("SET LOCAL default_transaction_read_only = off")       # affects only later transactions
            with pytest.raises(DBAPIError) as exc:
                conn.exec_driver_sql("DELETE FROM orders")
            assert sqlstate(exc.value) == "25006"
    finally:
        engine.dispose()
    assert count_orders(pg) == before


@pytest.mark.parametrize("sql", WRITES[:4] + ["SELECT 1; DELETE FROM orders",
                                               "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d"])
def test_the_connector_refuses_writes_for_a_writing_account(pg, sql):
    before = count_orders(pg)
    with pytest.raises(ExecutionError):
        run(connector(pg, "shop_owner"), sql)
    assert count_orders(pg) == before


def test_a_fresh_connection_from_the_pool_is_read_only_too(pg):
    conn = connector(pg, "shop_owner")
    for _ in range(4):                       # several checkouts, as a busy server would make
        assert run(conn, "SELECT current_setting('transaction_read_only')").rows == [["on"]]
    assert run(conn, "SELECT current_setting('lock_timeout')").rows == [["2s"]]


# --------------------------------------------------------- errors, secrets

def test_connection_failures_become_actionable_messages(pg, monkeypatch):
    monkeypatch.setenv("DB_CONNECT_TIMEOUT_SECONDS", "2")
    get_settings.cache_clear()
    cases = [
        # a closed port is refused on Linux and times out on Windows; either way the host and port are named
        (dict(host="127.0.0.1", port=1), ("Could not connect to 127.0.0.1:1", "Connecting to 127.0.0.1:1 timed out")),
        (dict(database="no_such_db"), ("database 'no_such_db' does not exist",)),
        (dict(username="no_such_role"), ("refused the connection for user 'no_such_role'",)),
    ]
    for override, expected in cases:
        base = params(pg)
        p = PgParams(**{**base.__dict__, **override})
        engine = create_pg_engine(p, pooled=False)
        try:
            with pytest.raises(Exception) as exc:
                inspect_server(engine, "shop")
        finally:
            engine.dispose()
        message = friendly_error(exc.value, p)
        assert any(e in message for e in expected), message
    get_settings.cache_clear()


@pytest.mark.parametrize("error,expected", [
    (psycopg.errors.InvalidPassword("x"), "Authentication failed for user 'analyst'"),
    (psycopg.errors.InvalidCatalogName("x"), "does not exist"),
    (psycopg.errors.CannotConnectNow("x"), "starting up"),
])
def test_known_error_codes_are_translated(pg, error, expected):
    wrapped = DBAPIError("SELECT 1", {}, error)
    assert expected in friendly_error(wrapped, params(pg))


def test_a_password_never_appears_in_a_message_a_repr_or_an_engine_url(pg):
    p = params(pg, password="Sup3r-Secret!")
    assert "Sup3r-Secret" not in repr(p) and "Sup3r-Secret" not in str(create_pg_engine(p, pooled=False).url)
    leaky = RuntimeError("connection to postgresql://analyst:Sup3r-Secret!@host/db failed: Sup3r-Secret!")
    assert "Sup3r-Secret" not in friendly_error(leaky, p)
    assert "Sup3r-Secret" not in pgc.scrub("password Sup3r-Secret! rejected", p)
    assert "postgresql://" not in pgc.scrub("see postgresql+psycopg://u:p@h/db", p)


def test_unusual_passwords_survive_the_connection_url(pg):
    p = params(pg, password="p@ss:w/rd#?%&=+ \"quoted\"")
    engine = create_pg_engine(p, pooled=False)
    try:
        assert inspect_server(engine, "shop").schema_exists     # trust auth ignores it, but the URL must still parse
    finally:
        engine.dispose()


def test_value_hint_cache_expires_so_changed_data_is_noticed(pg, monkeypatch):
    conn = connector(pg)
    first = conn.cache_key()
    assert conn.cache_key() == first
    real_time = time.time
    monkeypatch.setattr(pgc.time, "time", lambda: real_time() + pgc.CACHE_SECONDS + 1)
    assert conn.cache_key() != first


def test_engines_are_pooled_and_disposed(pg):
    conn = connector(pg)
    run(conn, "SELECT 1")
    assert "test-analyst" in pgc._engines
    pgc.dispose_engine("test-analyst")
    assert "test-analyst" not in pgc._engines
    assert run(conn, "SELECT 1").rows == [[1]]          # a new engine is made on demand


def test_preview_rows_are_json_friendly(pg):
    rows = connector(pg).preview("customers", 3)
    assert len(rows) == 3 and rows[0]["customer_id"] == 1 and isinstance(rows[0]["signup_date"], str)
    assert isinstance(connector(pg).preview("products", 1)[0]["price"], (int, float))
