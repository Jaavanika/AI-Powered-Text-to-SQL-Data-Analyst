"""Columns and tables that questions may never read.

Two lists apply together: the administrator's BLOCKED_COLUMNS setting (always on, shown as "locked"),
and a list each workspace keeps per data source. Blocking works in two ways: the validator and the
database layer refuse any query that touches a blocked column, and the schema shown to the model,
the value hints and the table preview leave blocked columns out, so they are not even suggested.
"""

import re
from contextlib import closing
from typing import NamedTuple

from app.config import get_settings
from app.schemas import ManyToMany, Relationship, SchemaInfo, SchemaTable
from app.services import registry
from app.services.sql_validation import parse_blocked

MAX_ENTRIES = 200
_ENTRY = re.compile(r"^([^.\s]+)\.([^.\s]+)$")


class BlockedError(Exception):
    status_code = 422

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class Blocked(NamedTuple):
    tables: frozenset[str]
    columns: frozenset[tuple[str, str]]

    def __bool__(self) -> bool:
        return bool(self.tables or self.columns)


def _clean(entries) -> list[str]:
    return sorted({e.strip().lower() for e in entries if e and e.strip()})


def global_entries() -> list[str]:
    return _clean(get_settings().blocked_columns.split(","))


def list_entries(workspace_id: str, datasource_id: str) -> list[str]:
    with closing(registry.connect()) as conn:
        rows = conn.execute(
            "SELECT entry FROM blocked_columns WHERE workspace_id = ? AND datasource_id = ?",
            (workspace_id, datasource_id)).fetchall()
    return _clean(r["entry"] for r in rows)


def set_entries(workspace_id: str, datasource_id: str, raw: list[str], schema: SchemaInfo) -> list[str]:
    """Replace this workspace's list for a data source. Every entry must name a real table and column."""
    entries = _clean(raw)
    if len(entries) > MAX_ENTRIES:
        raise BlockedError(f"At most {MAX_ENTRIES} entries can be blocked.")
    columns = {t.name.lower(): {c.name.lower() for c in t.columns} for t in schema.tables}
    for entry in entries:
        match = _ENTRY.match(entry)
        if not match:
            raise BlockedError(f"'{entry}' is not valid. Write table.column, or table.* for a whole table.")
        table, column = match.groups()
        if table not in columns:
            raise BlockedError(f"The table '{table}' does not exist in this data source.")
        if column != "*" and column not in columns[table]:
            raise BlockedError(f"The column '{table}.{column}' does not exist in this data source.")
    with closing(registry.connect()) as conn, conn:
        conn.execute("DELETE FROM blocked_columns WHERE workspace_id = ? AND datasource_id = ?",
                     (workspace_id, datasource_id))
        conn.executemany("INSERT INTO blocked_columns (workspace_id, datasource_id, entry) VALUES (?, ?, ?)",
                         [(workspace_id, datasource_id, e) for e in entries])
    return entries


def effective(workspace_id: str, datasource_id: str) -> Blocked:
    """Everything blocked for this workspace on this data source: the administrator's list plus its own."""
    spec = ",".join([*global_entries(), *list_entries(workspace_id, datasource_id)])
    tables, columns = parse_blocked(spec)
    return Blocked(frozenset(tables), frozenset(columns))


def visible(schema: SchemaInfo, blocked: Blocked) -> SchemaInfo:
    """The schema without blocked tables and columns, or the same schema if nothing is blocked.

    A relationship through a removed column is dropped with it: the join could not be written anyway.
    """
    if not blocked:
        return schema
    tables: list[SchemaTable] = []
    for table in schema.tables:
        if table.name.lower() in blocked.tables:
            continue
        columns = [c for c in table.columns if (table.name.lower(), c.name.lower()) not in blocked.columns]
        if not columns:
            continue
        kept = {c.name for c in columns}
        tables.append(table.model_copy(update={
            "columns": columns, "primary_key": [c for c in table.primary_key if c in kept]}))

    shown = {t.name: {c.name for c in t.columns} for t in tables}

    def usable(rel: Relationship) -> bool:
        return (set(rel.from_columns) <= shown.get(rel.from_table, set())
                and set(rel.to_columns) <= shown.get(rel.to_table, set()))

    many: list[ManyToMany] = [m for m in schema.many_to_many
                              if {m.table_a, m.table_b, m.via} <= shown.keys()]
    return schema.model_copy(update={
        "tables": tables, "relationships": [r for r in schema.relationships if usable(r)], "many_to_many": many})


def is_table_blocked(name: str, blocked: Blocked) -> bool:
    return name.lower() in blocked.tables
