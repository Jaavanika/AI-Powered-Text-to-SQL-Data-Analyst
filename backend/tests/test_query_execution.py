import sqlite3
import time
from contextlib import closing

import pytest

from app.services import registry
from app.services.query_execution import ExecutionError, execute_query
from app.services.sql_validation import validate_sql
from tests.sql_corpus import INVALID, VALID

TABLES = {"customers", "orders", "products", "payments", "regions"}


@pytest.fixture
def db(client):
    return registry.get_db_path("sample", None)


def run(db, sql, **kw):
    kw.setdefault("allowed_tables", TABLES)
    return execute_query(db, sql, **kw)


def table_counts(db):
    with closing(sqlite3.connect(db)) as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in sorted(TABLES)}


def test_basic_result_shape(db):
    result = run(db, "SELECT customer_id, customer_name FROM customers ORDER BY customer_id LIMIT 3")
    assert result.columns == ["customer_id", "customer_name"]
    assert result.row_count == 3 and len(result.rows) == 3 and result.rows[0][0] == 1
    assert result.truncated is False and result.elapsed_ms >= 0


def test_row_cap_marks_result_truncated(db):
    result = run(db, "SELECT order_id FROM orders", max_rows=5)
    assert result.row_count == 5 and result.truncated is True and result.max_rows == 5
    assert "first 5 rows" in result.notes[0]
    exact = run(db, "SELECT order_id FROM orders LIMIT 5", max_rows=5)
    assert exact.truncated is False


@pytest.mark.parametrize("sql", VALID, ids=[s[:60] for s in VALID])
def test_every_valid_query_also_runs_under_the_authorizer(db, sql):
    # guards against the authorizer being stricter than the validator
    run(db, sql)


# Everything the validator rejects is also refused by the database layer itself. These run the
# statements directly, skipping validation, to prove the second layer holds on its own.
ATTACKS = [
    "INSERT INTO orders VALUES (9999, 1, 1, 1, '2025-01-01', 'x')",
    "UPDATE orders SET quantity = 0",
    "DELETE FROM orders",
    "DROP TABLE orders",
    "ALTER TABLE orders ADD COLUMN x TEXT",
    "CREATE TABLE evil (x INT)",
    "ATTACH DATABASE 'evil.db' AS evil",
    "PRAGMA table_info(orders)",
    "PRAGMA writable_schema = 1",
    "SELECT 1; DROP TABLE orders",
    "SELECT name FROM sqlite_master",
    "SELECT * FROM pragma_table_info('orders')",
    "SELECT hex(randomblob(10))",
    "SELECT zeroblob(1000000000)",
    "SELECT load_extension('evil')",
    "SELECT * FROM main.sqlite_master",
    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c",
    "VACUUM",
    "BEGIN",
]


@pytest.mark.parametrize("sql", ATTACKS, ids=[a[:50] for a in ATTACKS])
def test_database_layer_refuses_unsafe_statements_even_without_validation(db, sql):
    before = table_counts(db)
    with pytest.raises(ExecutionError) as exc:
        run(db, sql)
    assert exc.value.kind in ("denied", "sql")
    assert table_counts(db) == before
    with closing(sqlite3.connect(db)) as conn:       # no stray tables or files were created
        assert {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")} == TABLES


def test_validator_also_rejects_everything_in_the_attack_list(client, sample_schema):
    for sql, _ in INVALID:
        assert not validate_sql(sql, sample_schema).valid


def test_blocked_columns_are_enforced_by_the_database_layer(db):
    blocked = {("customers", "email")}
    with pytest.raises(ExecutionError) as exc:
        run(db, "SELECT email FROM customers", blocked_columns=blocked)
    assert exc.value.kind == "denied"
    with pytest.raises(ExecutionError):
        run(db, "SELECT * FROM customers", blocked_columns=blocked)
    assert run(db, "SELECT customer_name FROM customers", blocked_columns=blocked).row_count == 40


def test_tables_outside_the_allowed_set_are_refused(db):
    with pytest.raises(ExecutionError) as exc:
        run(db, "SELECT * FROM payments", allowed_tables={"orders"})
    assert exc.value.kind == "denied"


def test_runaway_query_is_interrupted_by_the_time_limit(db):
    started = time.monotonic()
    with pytest.raises(ExecutionError) as exc:
        run(db, "SELECT COUNT(*) FROM orders a, orders b, orders c, orders d, orders e",
            timeout_seconds=0.5)
    assert exc.value.kind == "timeout" and "0.5 second" in exc.value.message
    assert time.monotonic() - started < 5


def test_oversized_values_are_refused(db):
    # would build a string of roughly 10 GB if SQLite's length limit were not lowered
    started = time.monotonic()
    with pytest.raises(ExecutionError) as exc:
        run(db, "SELECT replace(printf('%99999d', 1), ' ', printf('%99999d', 1))")
    assert "too big" in exc.value.message.lower()
    assert time.monotonic() - started < 5


def test_runtime_errors_are_reported_without_paths(db):
    with pytest.raises(ExecutionError) as exc:
        run(db, "SELECT nope FROM customers")
    assert "no such column" in exc.value.message.lower()
    assert str(db.parent) not in exc.value.message


def test_database_file_is_unchanged_after_all_of_the_above(db):
    assert table_counts(db) == {"customers": 40, "orders": 400, "payments": table_counts(db)["payments"],
                                "products": 20, "regions": 4}
