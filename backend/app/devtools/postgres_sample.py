"""Development helpers: a throwaway PostgreSQL server preloaded with the sample business data.

Used by the PostgreSQL tests, by scripts/dev_postgres.py (so you can try the PostgreSQL connection
without installing a database) and by browser checks. Needs `pixeltable-pgserver`
(requirements-dev.txt) for the embedded server; loading data into any server needs only psycopg.
"""

import sqlite3
from pathlib import Path
from urllib.parse import urlparse

import psycopg

from app.services.sample_data import _seed

SCHEMA = "shop"

DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
CREATE TABLE {SCHEMA}.regions (
    region_id   integer PRIMARY KEY,
    region_name text NOT NULL
);
CREATE TABLE {SCHEMA}.customers (
    customer_id   integer PRIMARY KEY,
    customer_name text NOT NULL,
    email         text,
    city          text,
    region_id     integer REFERENCES {SCHEMA}.regions (region_id),
    signup_date   date
);
CREATE TABLE {SCHEMA}.products (
    product_id   integer PRIMARY KEY,
    product_name text NOT NULL,
    category     text NOT NULL,
    price        numeric(10, 2) NOT NULL,
    cost         numeric(10, 2) NOT NULL
);
CREATE TABLE {SCHEMA}.orders (
    order_id    integer PRIMARY KEY,
    customer_id integer NOT NULL REFERENCES {SCHEMA}.customers (customer_id),
    product_id  integer NOT NULL REFERENCES {SCHEMA}.products (product_id),
    quantity    integer NOT NULL,
    order_date  date NOT NULL,
    status      text NOT NULL
);
CREATE TABLE {SCHEMA}.payments (
    payment_id     integer PRIMARY KEY,
    order_id       integer NOT NULL REFERENCES {SCHEMA}.orders (order_id),
    payment_method text NOT NULL,
    amount         numeric(10, 2) NOT NULL,
    payment_date   date NOT NULL
);
"""

TABLE_ORDER = ["regions", "customers", "products", "orders", "payments"]


def start_embedded_server(directory: Path):
    """Start (or reuse) an embedded PostgreSQL server whose data lives in `directory`."""
    import pixeltable_pgserver

    directory.mkdir(parents=True, exist_ok=True)
    return pixeltable_pgserver.get_server(directory, cleanup_mode="stop")


def split_uri(uri: str) -> dict:
    """Host, port, database and user of a postgresql:// URI."""
    parsed = urlparse(uri)
    return {"host": parsed.hostname, "port": parsed.port or 5432,
            "database": (parsed.path or "/postgres").lstrip("/") or "postgres",
            "username": parsed.username or "postgres", "password": parsed.password or ""}


def _sample_rows() -> dict[str, list[tuple]]:
    memory = sqlite3.connect(":memory:")
    try:
        _seed(memory)
        return {t: memory.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in TABLE_ORDER}
    finally:
        memory.close()


def load_sample(admin_uri: str, schema_present_ok: bool = True) -> None:
    """Create the `shop` schema with the sample tables (typed, with foreign keys) and load the data."""
    with psycopg.connect(admin_uri, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (SCHEMA,)).fetchone()
        if exists:
            if schema_present_ok:
                return
            conn.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
        conn.execute(DDL)
        rows = _sample_rows()
        for table in TABLE_ORDER:
            width = len(rows[table][0])
            with conn.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO {SCHEMA}.{table} VALUES ({', '.join(['%s'] * width)})", rows[table])
        conn.execute("ANALYZE")


def create_role(admin_uri: str, role: str, *, password: str = "", can_write: bool = False, owner: bool = False) -> None:
    """A login role: read-only by default; `can_write` adds INSERT/UPDATE/DELETE; `owner` makes it own the tables."""
    with psycopg.connect(admin_uri, autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
            conn.execute(f"REASSIGN OWNED BY {role} TO CURRENT_USER")     # keep the tables if it owned them
            conn.execute(f"DROP OWNED BY {role}")                         # then remove its privileges
            conn.execute(f"DROP ROLE {role}")
        conn.execute(f"CREATE ROLE {role} LOGIN" + (f" PASSWORD '{password}'" if password else ""))
        conn.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {role}")
        conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO {role}")
        if can_write:
            conn.execute(f"GRANT INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {SCHEMA} TO {role}")
        if owner:
            for table in TABLE_ORDER:
                conn.execute(f"ALTER TABLE {SCHEMA}.{table} OWNER TO {role}")
