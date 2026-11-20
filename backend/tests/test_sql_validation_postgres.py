import pytest

from app.services.sql_allowlist import rules_for
from app.services.sql_validation import validate_sql
from tests.pg_schema import pg_schema

SCHEMA = pg_schema()


def check(sql, **kw):
    return validate_sql(sql, SCHEMA, "postgres", **kw)


VALID = [
    "SELECT date_trunc('month', o.order_date) AS m, COUNT(*) AS n FROM orders o GROUP BY 1 ORDER BY 1",
    "SELECT EXTRACT(YEAR FROM order_date) AS y, SUM(quantity) FROM orders GROUP BY y",
    "SELECT quantity::numeric / 2 AS half, order_date::date FROM orders",
    "SELECT * FROM customers WHERE customer_name ILIKE '%an%'",
    "SELECT string_agg(customer_name, ', ' ORDER BY customer_name) FROM customers",
    "SELECT COUNT(*) FILTER (WHERE quantity > 2) AS big FROM orders",
    "SELECT DISTINCT ON (customer_id) customer_id, order_date FROM orders ORDER BY customer_id, order_date DESC",
    "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY price) FROM products",
    "SELECT to_char(order_date, 'YYYY-MM') AS ym FROM orders",
    "SELECT * FROM orders WHERE order_date > CURRENT_DATE - 30",
    "SELECT * FROM orders WHERE order_date > now() - interval '30 days'",
    "SELECT ROUND(AVG(price)::numeric, 2) FROM products",
    "SELECT SUM(quantity) OVER (PARTITION BY customer_id ORDER BY order_date) FROM orders",
    "SELECT * FROM customers WHERE customer_id = ANY(ARRAY[1, 2, 3])",
    "WITH big AS (SELECT * FROM orders WHERE quantity > 3) SELECT COUNT(*) FROM big",
    "SELECT COALESCE(city, 'unknown'), COUNT(*) FROM customers GROUP BY 1 ORDER BY 2 DESC",
    "SELECT customer_name FROM customers WHERE customer_id NOT IN (SELECT customer_id FROM orders)",
    "SELECT c.customer_name, SUM(p.amount) FROM customers c JOIN orders o ON o.customer_id = c.customer_id "
    "JOIN payments p ON p.order_id = o.order_id GROUP BY 1",
    "SELECT product_name FROM products WHERE price BETWEEN 10 AND 100 AND category IN ('Apparel', 'Furniture')",
    "SELECT customer_name FROM customers UNION SELECT product_name FROM products",
    'SELECT "Order Total" FROM "Weird Table"',
    'SELECT w."Order Total", w.id FROM "Weird Table" AS w WHERE w."Order Total" > 5',
    'SELECT "Amount" FROM "Invoices"',
    "SELECT 1",
    "SELECT COUNT(*) FROM customers;",
]

# (sql, text expected in an error message)
INVALID = [
    ("SELECT pg_sleep(10)", "'pg_sleep' is not allowed"),
    ("SELECT pg_read_file('/etc/passwd')", "'pg_read_file' is not allowed"),
    ("SELECT set_config('transaction_read_only', 'off', true)", "'set_config' is not allowed"),
    ("SELECT current_setting('server_version')", "'current_setting' is not allowed"),
    ("SELECT version()", "not allowed"),
    ("SELECT * FROM generate_series(1, 1000000000)", "not allowed"),
    ("SELECT generate_series(1, 1000000000)", "not allowed"),
    ("SELECT lo_import('/etc/passwd')", "'lo_import' is not allowed"),
    ("SELECT * FROM dblink('host=x', 'select 1') AS t(a int)", "not allowed"),
    ("SELECT nextval('s')", "'nextval' is not allowed"),
    ("SELECT txid_current()", "'txid_current' is not allowed"),
    ("SELECT pg_advisory_lock(1)", "'pg_advisory_lock' is not allowed"),
    ("SELECT pg_terminate_backend(1)", "'pg_terminate_backend' is not allowed"),
    ("SELECT pg_ls_dir('.')", "'pg_ls_dir' is not allowed"),
    ("SELECT current_user", "not allowed"),
    ("SELECT * FROM orders FOR UPDATE", "not allowed"),
    ("SELECT * FROM customers c, LATERAL (SELECT * FROM orders o WHERE o.customer_id = c.customer_id) x", "not allowed"),
    ("SELECT unnest(ARRAY[1, 2])", "not allowed"),
    ("SELECT * FROM information_schema.tables", "Qualified table names"),
    ("SELECT * FROM pg_catalog.pg_user", "Qualified table names"),
    ("SELECT * FROM shop.orders", "Qualified table names"),
    ("SELECT * FROM pg_user", "Unknown table"),
    ("SELECT * FROM pg_shadow", "Unknown table"),
    ("COPY orders TO '/tmp/x'", "Only SELECT"),
    ("INSERT INTO orders VALUES (1) RETURNING *", "Only SELECT"),
    ("UPDATE orders SET quantity = 0", "Only SELECT"),
    ("DELETE FROM orders", "Only SELECT"),
    ("DROP TABLE orders", "Only SELECT"),
    ("TRUNCATE orders", "Only SELECT"),
    ("CREATE TABLE t (x int)", "Only SELECT"),
    ("ALTER TABLE orders ADD COLUMN x int", "Only SELECT"),
    ("SET search_path = public", "Only SELECT"),
    ("SHOW all", "Only SELECT"),
    ("SELECT * INTO newt FROM orders", "not allowed"),
    ("WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d", "not allowed"),
    ("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT x FROM c", "Recursive"),
    ("SELECT 1; DROP TABLE orders", "Exactly one"),
    ("SELECT * FROM orders WHERE order_id = $1", "not allowed"),
    ("SELECT nope FROM orders", "could not be resolved"),
    ("SELECT * FROM ghosts", "Unknown table 'ghosts'"),
]


@pytest.mark.parametrize("sql", VALID, ids=[s[:60] for s in VALID])
def test_realistic_postgresql_queries_pass(sql):
    result = check(sql)
    assert result.valid, result.errors


@pytest.mark.parametrize("sql,expected", INVALID, ids=[s[0][:60] for s in INVALID])
def test_postgresql_specific_attacks_are_rejected(sql, expected):
    result = check(sql)
    assert not result.valid, f"accepted: {sql}"
    assert any(expected.lower() in e.lower() for e in result.errors), result.errors


# ------------------------------------------------------- identifier quoting

def test_a_mixed_case_name_must_be_quoted_exactly_as_in_the_schema():
    for sql in ("SELECT amount FROM invoices", "SELECT Amount FROM Invoices", 'SELECT amount FROM "Invoices"',
                'SELECT "Amount" FROM invoices', 'SELECT duedate FROM "Invoices"'):
        assert not check(sql).valid, sql
    # tables and columns each get an actionable message
    assert 'must be written in double quotes' in check("SELECT amount FROM invoices").errors[0]
    assert '"Invoices"' in check("SELECT amount FROM invoices").errors[0]
    assert '"DueDate"' in check('SELECT duedate FROM "Invoices"').errors[0]
    assert check('SELECT "DueDate", "Amount" FROM "Invoices"').valid


def test_an_unquoted_name_that_also_exists_in_plain_form_is_not_assumed_wrong():
    # payments.amount is a plain name, so `amount` unquoted may well mean it
    assert check("SELECT p.amount FROM payments p").valid


def test_quoting_is_required_for_names_with_spaces_and_the_error_is_actionable():
    result = check('SELECT order_total FROM "Weird Table"')
    assert not result.valid and "could not be resolved" in result.errors[0]
    assert "Order Total" in result.errors[0]                      # the columns available are listed


def test_plain_lower_case_names_need_no_quotes_and_may_be_quoted_anyway():
    assert check('SELECT "customer_name" FROM "customers"').valid
    assert check("SELECT customer_name FROM customers").valid


# ------------------------------------------------------------------ rules

def test_the_derived_rules_contain_nothing_dangerous():
    rules = rules_for("postgres")
    allowed = rules.function_keys | rules.structural
    for dangerous in ("lock", "into", "lateral", "copy", "command", "insert", "update", "delete", "drop", "create",
                      "alter", "set", "pragma", "values", "tablesample", "unnest", "explodinggenerateseries",
                      "currentversion", "placeholder", "parameter", "currentuser", "sessionuser"):
        assert dangerous not in allowed, dangerous
    assert not {"pg_sleep", "pg_read_file", "set_config", "dblink", "lo_import", "nextval"} & rules.anonymous


def test_unknown_dialects_are_refused_rather_than_guessed():
    result = validate_sql("SELECT 1", SCHEMA, "oracle")
    assert not result.valid and "not supported" in result.errors[0]


def test_blocked_columns_work_for_postgres_too():
    for sql in ("SELECT email FROM customers", "SELECT * FROM customers", "SELECT c.email FROM customers c"):
        result = check(sql, blocked_columns={("customers", "email")})
        assert not result.valid and "email" in result.errors[0], sql
    assert check("SELECT customer_name FROM customers", blocked_columns={("customers", "email")}).valid


def test_the_sqlite_rules_are_unchanged_by_the_new_dialect():
    assert not validate_sql("SELECT date_trunc('month', order_date) FROM orders", SCHEMA, "sqlite").valid
    assert not validate_sql("SELECT strftime('%Y', order_date) FROM orders", SCHEMA, "postgres").valid
