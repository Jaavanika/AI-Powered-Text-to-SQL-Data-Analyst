"""Builds the deterministic sample business database used for development and tests."""

import random
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from app.schemas import ColumnInfo, TableInfo
from app.services import registry

SCHEMA_SQL = Path(__file__).resolve().parents[3] / "sample_database" / "sample_schema.sql"

REGIONS = ["North", "South", "East", "West"]

CITIES = {
    "North": ["Chicago", "Minneapolis", "Detroit"],
    "South": ["Houston", "Atlanta", "Miami"],
    "East": ["New York", "Boston", "Philadelphia"],
    "West": ["Seattle", "San Francisco", "Denver"],
}

FIRST = ["Ava", "Liam", "Noah", "Emma", "Olivia", "Mason", "Sophia", "Lucas", "Mia", "Ethan",
         "Amelia", "Logan", "Harper", "James", "Ella", "Henry"]
LAST = ["Smith", "Johnson", "Brown", "Garcia", "Miller", "Davis", "Martinez", "Lopez", "Clark", "Young"]

PRODUCTS = [
    ("Laptop Pro 14", "Electronics", 1299.00, 900.00),
    ("Wireless Mouse", "Electronics", 29.99, 12.00),
    ("Mechanical Keyboard", "Electronics", 89.50, 45.00),
    ("4K Monitor", "Electronics", 349.00, 240.00),
    ("USB-C Hub", "Electronics", 49.00, 22.00),
    ("Standing Desk", "Furniture", 499.00, 310.00),
    ("Ergonomic Chair", "Furniture", 289.00, 170.00),
    ("Bookshelf", "Furniture", 119.00, 70.00),
    ("Desk Lamp", "Furniture", 39.90, 16.00),
    ("Notebook Pack", "Stationery", 12.50, 4.00),
    ("Gel Pen Set", "Stationery", 8.99, 3.00),
    ("Sticky Notes", "Stationery", 5.49, 1.80),
    ("Whiteboard", "Stationery", 64.00, 35.00),
    ("Running Shoes", "Apparel", 94.00, 48.00),
    ("Rain Jacket", "Apparel", 129.00, 65.00),
    ("Wool Socks", "Apparel", 14.00, 5.00),
    ("Yoga Mat", "Apparel", 35.00, 14.00),
    ("Espresso Machine", "Furniture", 249.00, 150.00),
    ("Smart Speaker", "Electronics", 79.00, 40.00),
    ("Backpack", "Apparel", 59.00, 25.00),
]
# The last two products are never ordered, to exercise "never ordered" questions.
NEVER_ORDERED = 2

STATUSES = ["completed"] * 17 + ["cancelled"] * 2 + ["returned"]
PAYMENT_METHODS = ["card", "card", "card", "paypal", "bank_transfer"]


def _seed(conn: sqlite3.Connection) -> None:
    rng = random.Random(42)
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))

    conn.executemany("INSERT INTO regions VALUES (?, ?)", list(enumerate(REGIONS, start=1)))

    customers = []
    for cid in range(1, 41):
        region_id = rng.randint(1, len(REGIONS))
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        city = rng.choice(CITIES[REGIONS[region_id - 1]])
        email = f"customer{cid}@example.com"
        signup = date(2023, 1, 1) + timedelta(days=rng.randint(0, 700))
        customers.append([cid, name, email, city, region_id, signup.isoformat()])
    customers[6][3] = None  # a customer with no city
    customers[11][2] = None  # a customer with no email
    conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?)", customers)

    conn.executemany(
        "INSERT INTO products VALUES (?, ?, ?, ?, ?)",
        [(i, *p) for i, p in enumerate(PRODUCTS, start=1)],
    )

    # The last 3 customers never order, to exercise "customers without orders".
    orderable_customers = 40 - 3
    orderable_products = len(PRODUCTS) - NEVER_ORDERED
    start, end = date(2024, 1, 1), date(2025, 12, 31)
    span = (end - start).days

    orders, payments = [], []
    payment_id = 1
    for oid in range(1, 401):
        day = start + timedelta(days=rng.randint(0, span))
        product_id = rng.randint(1, orderable_products)
        price = PRODUCTS[product_id - 1][2]
        quantity = rng.randint(1, 5)
        status = rng.choice(STATUSES)
        orders.append((oid, rng.randint(1, orderable_customers), product_id, quantity,
                       day.isoformat(), status))
        if status != "cancelled":
            payments.append((payment_id, oid, rng.choice(PAYMENT_METHODS),
                             round(price * quantity, 2),
                             (day + timedelta(days=rng.randint(0, 3))).isoformat()))
            payment_id += 1
    conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)", orders)
    conn.executemany("INSERT INTO payments VALUES (?, ?, ?, ?, ?)", payments)
    conn.commit()


_SQLITE_TYPE_MAP = {
    "INTEGER": "integer",
    "REAL": "float",
    "DATE": "date",
    "DATETIME": "datetime",
    "BOOLEAN": "boolean",
}


def describe_sqlite(path: Path) -> list[TableInfo]:
    """Describe tables in a SQLite file using the declared column types."""
    tables: list[TableInfo] = []
    with closing(sqlite3.connect(path)) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for name in names:
            cols = conn.execute(f'PRAGMA table_info("{name}")').fetchall()
            count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            tables.append(TableInfo(
                name=name,
                original_name=name,
                row_count=count,
                columns=[
                    ColumnInfo(name=c[1], original_name=c[1],
                               type=_SQLITE_TYPE_MAP.get(c[2].upper(), "text"))
                    for c in cols
                ],
            ))
    return tables


def ensure_sample_database() -> None:
    """Create the sample database if missing and make sure it is registered."""
    path = registry.sample_db_path()
    if not path.exists():
        tmp = path.with_suffix(".tmp")
        tmp.unlink(missing_ok=True)
        with closing(sqlite3.connect(tmp)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            _seed(conn)
        tmp.replace(path)
    registry.register_sample(describe_sqlite(path))
