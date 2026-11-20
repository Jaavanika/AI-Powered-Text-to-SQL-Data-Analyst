import sqlite3
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool


def readonly_engine(db_path: Path) -> Engine:
    """SQLAlchemy engine on a SQLite file opened read-only at the driver level."""
    uri = f"{db_path.as_uri()}?mode=ro"
    return create_engine(
        "sqlite://",
        creator=lambda: sqlite3.connect(uri, uri=True),
        poolclass=NullPool,
    )


def quote(engine: Engine, identifier: str) -> str:
    return engine.dialect.identifier_preparer.quote(identifier)
