"""Schema extraction: tables, columns, keys and relationships for a data source."""

import re
from itertools import combinations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from app.core.db import quote
from app.schemas import (
    DataSource,
    LogicalType,
    ManyToMany,
    Relationship,
    SchemaColumn,
    SchemaInfo,
    SchemaTable,
)
from app.services import registry
from app.services.connectors import Connector

# A link table has at least two parent tables and few columns besides its keys.
JUNCTION_MAX_EXTRA_COLUMNS = 2


def singular(word: str) -> str:
    """Crude English singularisation, good enough for matching table names to id columns."""
    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    if word.endswith(("sses", "xes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) > 1:
        return word[:-1]
    return word


_ARRAY_OR_RANGE = re.compile(r"\[\]|\bARRAY\b|RANGE")


def logical_type_from_db(db_type: str) -> LogicalType:
    """Map a database column type (SQLite or PostgreSQL) to the simple types the app reasons about."""
    t = db_type.upper()
    if _ARRAY_OR_RANGE.search(t) or "INTERVAL" in t:
        return "text"                        # not a plain number or date, even though it contains INT/DATE
    if "BOOL" in t:
        return "boolean"
    if "TIMESTAMP" in t or "DATETIME" in t:
        return "datetime"
    if re.search(r"\bDATE\b", t):
        return "date"
    if any(k in t for k in ("REAL", "FLOA", "DOUB", "NUMERIC", "DECIMAL", "MONEY")):
        return "float"
    if "INT" in t or "SERIAL" in t:
        return "integer"
    return "text"


def is_unique_key(engine: Engine, conn: Connection, table: str, column: str) -> bool:
    """True when the column has no NULLs and no duplicate values (and at least one row)."""
    t, c = quote(engine, table), quote(engine, column)
    total, non_null, distinct = conn.execute(
        text(f"SELECT COUNT(*), COUNT({c}), COUNT(DISTINCT {c}) FROM {t}")
    ).one()
    return total > 0 and total == non_null == distinct


def _inferred_key_names(table: str) -> list[str]:
    return ["id", f"{singular(table)}_id", f"{table}_id"]


def build_schema(datasource: DataSource, connector: Connector) -> SchemaInfo:
    with connector.engine() as engine, engine.connect() as conn:
        return _build(engine, conn, datasource, connector.schema_name, connector.dialect)


def _build(engine: Engine, conn: Connection, datasource: DataSource, schema: str | None,
           dialect: str) -> SchemaInfo:
    insp = inspect(conn)
    registered = {t.name: t for t in datasource.tables}
    tables: list[SchemaTable] = []
    relationships: list[Relationship] = []
    existing_columns: dict[str, set[str]] = {}

    views = {t.name for t in datasource.tables if t.is_view}        # found when the database was connected
    for name in sorted(set(insp.get_table_names(schema=schema)) | views):
        info = registered.get(name)
        is_view = name in views
        meta = {c.name: c for c in info.columns} if info else {}
        pk = list(insp.get_pk_constraint(name, schema=schema).get("constrained_columns") or [])

        columns = []
        for col in insp.get_columns(name, schema=schema):
            db_type = str(col["type"])
            reg = meta.get(col["name"])
            columns.append(SchemaColumn(
                name=col["name"],
                original_name=reg.original_name if reg else col["name"],
                type=reg.type if reg else logical_type_from_db(db_type),
                db_type=db_type,
                nullable=bool(col.get("nullable", True)),
                is_primary_key=col["name"] in pk,
            ))
        existing_columns[name] = {c.name for c in columns}

        inferred = False
        if not pk and not is_view:
            for candidate in _inferred_key_names(name):
                if candidate in existing_columns[name] and is_unique_key(engine, conn, name, candidate):
                    pk, inferred = [candidate], True
                    for c in columns:
                        c.is_primary_key = c.name == candidate
                    break

        row_count = info.row_count if info else conn.execute(
            text(f"SELECT COUNT(*) FROM {quote(engine, name)}")).scalar_one()          # a view is never counted
        tables.append(SchemaTable(
            name=name,
            original_name=info.original_name if info else name,
            row_count=row_count,
            columns=columns,
            primary_key=pk,
            primary_key_inferred=inferred,
            is_view=is_view,
        ))
        if is_view:
            continue                      # a view has no keys or constraints of its own to read

        unique_sets = [set(u["column_names"]) for u in insp.get_unique_constraints(name, schema=schema)]
        unique_sets += [set(i["column_names"]) for i in insp.get_indexes(name, schema=schema) if i.get("unique")]
        if pk and not inferred:
            unique_sets.append(set(pk))
        for fk in insp.get_foreign_keys(name, schema=schema):
            if not fk.get("referred_table") or not fk["constrained_columns"]:
                continue
            if fk.get("referred_schema") not in (None, schema):
                continue          # a key into another database schema, which this data source does not expose
            cols = list(fk["constrained_columns"])
            relationships.append(Relationship(
                id=f"fk:{name}.{','.join(cols)}->{fk['referred_table']}",
                from_table=name,
                from_columns=cols,
                to_table=fk["referred_table"],
                to_columns=list(fk["referred_columns"]),
                source="declared",
                cardinality="one_to_one" if set(cols) in unique_sets else "many_to_one",
            ))

    # User-confirmed relationships; ignore any whose tables/columns no longer exist.
    for row in registry.list_user_relationships(datasource.id):
        if (row["from_column"] in existing_columns.get(row["from_table"], set())
                and row["to_column"] in existing_columns.get(row["to_table"], set())):
            relationships.append(Relationship(
                id=row["id"],
                from_table=row["from_table"], from_columns=[row["from_column"]],
                to_table=row["to_table"], to_columns=[row["to_column"]],
                source="user", cardinality=row["cardinality"],
            ))

    many_to_many = _flag_junctions(tables, relationships)
    return SchemaInfo(
        datasource_id=datasource.id,
        dialect=dialect,
        tables=tables,
        relationships=relationships,
        many_to_many=many_to_many,
    )


def _flag_junctions(tables: list[SchemaTable], relationships: list[Relationship]) -> list[ManyToMany]:
    """Mark link tables and list the many-to-many pairs they connect."""
    result: list[ManyToMany] = []
    for table in tables:
        outgoing = [r for r in relationships if r.from_table == table.name and r.to_table != table.name]
        parents = sorted({r.to_table for r in outgoing})
        if len(parents) < 2:
            continue
        key_columns = {c for r in outgoing for c in r.from_columns} | set(table.primary_key)
        extra = [c for c in table.columns if c.name not in key_columns]
        if len(extra) > JUNCTION_MAX_EXTRA_COLUMNS:
            continue
        table.is_junction = True
        result.extend(ManyToMany(table_a=a, table_b=b, via=table.name)
                      for a, b in combinations(parents, 2))
    return result
