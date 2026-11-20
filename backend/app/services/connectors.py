"""Connectors: one interface over the kinds of database a data source can be.

Everything above this layer (schema extraction, relationships, linking, validation, execution)
works with a `Connector` and never with a file path or a connection string. SQLite files (the
sample and uploads) and external PostgreSQL servers implement it.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ContextManager, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.db import quote, readonly_engine
from app.schemas import DataSource
from app.services import registry
from app.services.query_execution import QueryResult, execute_query


class Connector(Protocol):
    dialect: str                 # the SQLGlot dialect name: "sqlite" | "postgres"
    schema_name: str | None      # the database schema in use (PostgreSQL), None for SQLite

    def engine(self) -> ContextManager[Engine]:
        """A SQLAlchemy engine for read-only introspection and light queries."""

    def cache_key(self) -> tuple:
        """Changes when the data may have changed, so caches built from it can be refreshed."""

    def execute(
        self, sql: str, *, allowed_tables: set[str], blocked_columns: set[tuple[str, str]],
        timeout_seconds: float, max_rows: int,
    ) -> QueryResult:
        """Run an already validated SELECT read-only."""

    def preview(self, table: str, limit: int) -> list[dict[str, Any]]:
        """The first rows of a table."""


class SqliteConnector:
    dialect = "sqlite"
    schema_name = None

    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def engine(self) -> Iterator[Engine]:
        engine = readonly_engine(self.path)
        try:
            yield engine
        finally:
            engine.dispose()

    def cache_key(self) -> tuple:
        stat = self.path.stat()
        return ("sqlite", str(self.path), stat.st_mtime_ns, stat.st_size)

    def execute(self, sql, *, allowed_tables, blocked_columns, timeout_seconds, max_rows) -> QueryResult:
        return execute_query(
            self.path, sql, allowed_tables=allowed_tables, blocked_columns=blocked_columns,
            timeout_seconds=timeout_seconds, max_rows=max_rows)

    def preview(self, table: str, limit: int) -> list[dict[str, Any]]:
        with self.engine() as engine, engine.connect() as conn:
            result = conn.execute(text(f"SELECT * FROM {quote(engine, table)} LIMIT :n"), {"n": limit})
            return [dict(row) for row in result.mappings()]


def resolve(datasource_id: str, workspace_id: str | None) -> tuple[DataSource, Connector] | None:
    """The data source and a connector for it, if it is the shared sample or belongs to the workspace."""
    datasource = registry.get_datasource(datasource_id, workspace_id)
    if datasource is None:
        return None
    if datasource.kind == "postgres":
        from app.services.postgres_connector import connector_for  # imported lazily: needs the driver
        return datasource, connector_for(datasource_id, workspace_id)
    path = registry.get_db_path(datasource_id, workspace_id)
    return (datasource, SqliteConnector(path)) if path else None


def forget(datasource_id: str) -> None:
    """Drop anything cached for a data source that is being deleted or reconnected."""
    from app.services import postgres_connector
    postgres_connector.dispose_engine(datasource_id)
