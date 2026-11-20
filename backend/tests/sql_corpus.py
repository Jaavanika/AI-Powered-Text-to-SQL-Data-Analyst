"""Queries shared by the validator and executor tests (sample database schema)."""

VALID = [
    "SELECT * FROM customers",
    "SELECT COUNT(*) AS n FROM customers AS c",
    "SELECT strftime('%Y-%m', o.order_date) AS ym, COUNT(*) AS n FROM orders AS o "
    "WHERE strftime('%Y', o.order_date) = '2024' GROUP BY ym ORDER BY ym",
    "WITH r AS (SELECT p.product_id, SUM(o.quantity * p.price) AS rev FROM orders o "
    "JOIN products p ON o.product_id = p.product_id GROUP BY p.product_id) "
    "SELECT product_id, rev FROM r ORDER BY rev DESC LIMIT 3",
    "SELECT customer_name FROM customers UNION SELECT product_name FROM products",
    "SELECT customer_name FROM customers WHERE customer_id NOT IN (SELECT customer_id FROM orders)",
    "SELECT c.customer_name FROM customers c LEFT JOIN orders o ON c.customer_id = o.customer_id "
    "WHERE o.order_id IS NULL",
    "SELECT order_id, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date) AS rn FROM orders",
    "SELECT CASE WHEN quantity > 2 THEN 'big' ELSE 'small' END AS size, COUNT(*) FROM orders GROUP BY 1",
    "SELECT CAST(quantity AS REAL) / 2 FROM orders",
    "SELECT ROUND(AVG(price), 2) AS avg_price, MIN(price), MAX(price) FROM products",
    "SELECT category, COUNT(DISTINCT product_id) FROM products GROUP BY category HAVING COUNT(*) > 1",
    "SELECT UPPER(customer_name) || ' (' || city || ')' FROM customers WHERE email LIKE '%example.com'",
    "SELECT COALESCE(city, 'unknown'), COUNT(*) FROM customers GROUP BY 1 ORDER BY 2 DESC",
    "SELECT product_name FROM products WHERE price BETWEEN 10 AND 100 AND category IN ('Apparel', 'Furniture')",
    "SELECT o.order_id FROM orders o WHERE EXISTS (SELECT 1 FROM payments p WHERE p.order_id = o.order_id)",
    "SELECT date(order_date), julianday('2025-01-01') - julianday(order_date) FROM orders LIMIT 5",
    "SELECT SUM(quantity) OVER (ORDER BY order_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) FROM orders",
    "SELECT group_concat(product_name, ', ') FROM products",
    "SELECT 1",
    "SELECT COUNT(*) FROM CUSTOMERS",                      # identifiers are case-insensitive
    "SELECT customer_name FROM customers;",                # trailing semicolon
    "SELECT customer_name /* the name */ FROM customers -- all of them\n",
    "WITH orders AS (SELECT 1 AS x) SELECT x FROM orders",  # a CTE may shadow a table name
]

# (sql, substring expected in the error message)
INVALID = [
    ("INSERT INTO orders VALUES (1, 1, 1, 1, '2025-01-01', 'x')", "Only SELECT"),
    ("UPDATE orders SET quantity = 0", "Only SELECT"),
    ("DELETE FROM orders", "Only SELECT"),
    ("DROP TABLE orders", "Only SELECT"),
    ("ALTER TABLE orders ADD COLUMN x TEXT", "Only SELECT"),
    ("CREATE TABLE t (x INT)", "Only SELECT"),
    ("TRUNCATE TABLE orders", "Only SELECT"),
    ("ATTACH DATABASE 'other.db' AS other", "Only SELECT"),
    ("PRAGMA table_info(orders)", "Only SELECT"),
    ("SELECT 1; DROP TABLE orders", "Exactly one"),
    ("SELECT 1; SELECT 2", "Exactly one"),
    ("SELECT * FROM ghosts", "Unknown table 'ghosts'"),
    ("SELECT name FROM sqlite_master", "Unknown table 'sqlite_master'"),
    ("SELECT * FROM main.orders", "Qualified table names"),
    ("SELECT * FROM other.orders", "Qualified table names"),
    ("SELECT * FROM pragma_table_info('orders')", "not allowed"),
    ("SELECT nope FROM orders", "could not be resolved"),
    ("SELECT o.nope FROM orders o", "Unknown column"),
    ("SELECT customer_id FROM customers c JOIN orders o ON c.customer_id = o.customer_id", "could not be resolved"),
    ("SELECT hex(randomblob(1000000000))", "'randomblob' is not allowed"),
    ("SELECT zeroblob(1000000000)", "'zeroblob' is not allowed"),
    ("SELECT load_extension('evil')", "'load_extension' is not allowed"),
    ("SELECT readfile('/etc/passwd')", "'readfile' is not allowed"),
    ("SELECT sqlite_version()", "not allowed"),   # SQLGlot reports it as 'current_version'
    ("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT x FROM c", "Recursive"),
    ("SELECT * INTO newtab FROM orders", "not allowed"),
    ("SELECT * FROM orders WHERE order_id = ?", "not allowed"),
    ("SELEC * FROM orders", "syntax error"),
    ("SELECT FROM", "syntax error"),
    ("", "empty"),
    ("   ;  ", "empty"),
]
