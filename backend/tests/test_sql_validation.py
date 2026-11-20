import pytest

from app.services.sql_validation import MAX_SQL_LENGTH, parse_blocked, validate_sql
from tests.sql_corpus import INVALID, VALID


@pytest.mark.parametrize("sql", VALID, ids=[s[:60] for s in VALID])
def test_valid_queries_pass(sample_schema, sql):
    result = validate_sql(sql, sample_schema)
    assert result.valid, result.errors
    assert result.errors == []


@pytest.mark.parametrize("sql,expected", INVALID, ids=[s[0][:60] or "<empty>" for s in INVALID])
def test_unsafe_or_invalid_queries_are_rejected(sample_schema, sql, expected):
    result = validate_sql(sql, sample_schema)
    assert not result.valid
    assert any(expected.lower() in e.lower() for e in result.errors), result.errors


def test_validated_sql_is_cleaned_and_tables_reported(sample_schema):
    result = validate_sql("  SELECT c.customer_name FROM customers c JOIN orders o "
                          "ON o.customer_id = c.customer_id ;  ", sample_schema)
    assert result.valid
    assert result.sql.endswith("c.customer_id") and ";" not in result.sql
    assert result.tables == ["customers", "orders"]


def test_unknown_column_error_lists_available_columns(sample_schema):
    result = validate_sql("SELECT o.revenue FROM orders o", sample_schema)
    assert not result.valid
    assert "order_id" in result.errors[0] and "quantity" in result.errors[0]


def test_unknown_table_error_lists_available_tables(sample_schema):
    result = validate_sql("SELECT * FROM sales", sample_schema)
    assert "customers" in result.errors[0] and "orders" in result.errors[0]


def test_unknown_table_inside_subquery_is_caught(sample_schema):
    result = validate_sql("SELECT * FROM customers WHERE customer_id IN (SELECT id FROM secrets)", sample_schema)
    assert not result.valid and "secrets" in result.errors[0]


def test_length_limit(sample_schema):
    result = validate_sql("SELECT " + ", ".join(["1"] * (MAX_SQL_LENGTH // 2)), sample_schema)
    assert not result.valid and "longer than" in result.errors[0]


def test_a_cte_cannot_be_used_to_smuggle_in_an_unknown_table(sample_schema):
    # the inner reference is a real table lookup, not the CTE being defined
    for sql in ("WITH ghosts AS (SELECT * FROM ghosts) SELECT * FROM ghosts",
                "WITH x AS (SELECT * FROM ghosts) SELECT * FROM x",
                "WITH a AS (SELECT 1 AS n), ghosts AS (SELECT * FROM ghosts) SELECT * FROM a, ghosts"):
        result = validate_sql(sql, sample_schema)
        assert not result.valid and "Unknown table 'ghosts'" in result.errors[0], sql


def test_a_cte_cannot_be_used_to_hide_a_blocked_table(sample_schema):
    sql = "WITH payments AS (SELECT * FROM payments) SELECT * FROM payments"
    result = validate_sql(sql, sample_schema, blocked_tables={"payments"})
    assert not result.valid and "payments" in result.errors[0]


def test_legitimate_cte_names_and_references_still_work(sample_schema):
    sql = ("WITH big AS (SELECT * FROM orders WHERE quantity > 3), "
           "named AS (SELECT b.order_id, c.customer_name FROM big b JOIN customers c ON c.customer_id = b.customer_id) "
           "SELECT * FROM named")
    result = validate_sql(sql, sample_schema)
    assert result.valid and result.tables == ["orders", "customers"]


def test_parse_blocked():
    tables, columns = parse_blocked(" Customers.Email , payments.* ,secrets, ")
    assert tables == {"payments", "secrets"}
    assert columns == {("customers", "email")}


@pytest.mark.parametrize("sql", [
    "SELECT email FROM customers",
    "SELECT c.email FROM customers c",
    "SELECT * FROM customers",                                   # star expands to the blocked column
    "SELECT t.* FROM customers t",
    "WITH x AS (SELECT email AS e FROM customers) SELECT e FROM x",
    "SELECT customer_name FROM customers WHERE email LIKE '%a%'",  # blocked column used only in WHERE
])
def test_blocked_columns_cannot_be_read_any_way(sample_schema, sql):
    result = validate_sql(sql, sample_schema, blocked_columns={("customers", "email")})
    assert not result.valid and "email" in result.errors[0]


def test_blocked_column_does_not_affect_other_columns(sample_schema):
    assert validate_sql("SELECT customer_name FROM customers", sample_schema,
                        blocked_columns={("customers", "email")}).valid


def test_blocked_tables(sample_schema):
    for sql in ("SELECT * FROM payments", "SELECT COUNT(*) FROM orders o JOIN payments p ON p.order_id = o.order_id"):
        result = validate_sql(sql, sample_schema, blocked_tables={"payments"})
        assert not result.valid and "payments" in result.errors[0]
    assert validate_sql("SELECT COUNT(*) FROM orders", sample_schema, blocked_tables={"payments"}).valid
