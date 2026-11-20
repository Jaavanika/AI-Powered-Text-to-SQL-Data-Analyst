"""Structural validation of generated SQL.

The SQL is parsed (never pattern-matched) and must pass four checks before it can run:
  1. exactly one statement, and it is a SELECT (optionally with CTEs / set operations);
  2. every construct and function is on an explicit allowlist;
  3. every table and column exists in the accessible schema (and is not blocked);
  4. basic size limits.

This is the first of two layers. The executor opens the database read-only (SQLite also installs
an authorizer; PostgreSQL uses a read-only transaction with timeouts), so the database itself still
refuses anything unsafe if this layer has a gap. What counts as allowed depends on the dialect:
see sql_allowlist.py.
"""

import re

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError, SqlglotError, TokenError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope
from sqlglot.schema import MappingSchema

from app.schemas import SchemaInfo
from app.services.sql_allowlist import rules_for

MAX_SQL_LENGTH = 10_000
MAX_TABLE_REFERENCES = 15


_SENSITIVE_PREFIX = "sqlite_"
_PLAIN_PG_NAME = re.compile(r"[a-z_][a-z0-9_$]*")      # PostgreSQL names that need no quoting


class ValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    # The statement that was checked (trailing semicolons/whitespace removed). This exact text is executed.
    sql: str = ""
    tables: list[str] = Field(default_factory=list)


def parse_blocked(spec: str) -> tuple[set[str], set[tuple[str, str]]]:
    """Parse 'customers.email, payments.*, secrets' into (blocked tables, blocked columns)."""
    tables: set[str] = set()
    columns: set[tuple[str, str]] = set()
    for item in (s.strip().lower() for s in spec.split(",")):
        if not item:
            continue
        table, _, column = item.partition(".")
        if column in ("", "*"):
            tables.add(table)
        else:
            columns.add((table, column))
    return tables, columns


def _fail(sql: str, errors: list[str], tables: list[str] | None = None) -> ValidationResult:
    return ValidationResult(valid=False, errors=list(dict.fromkeys(errors)), sql=sql, tables=tables or [])


def _columns_hint(schema: SchemaInfo, table_names: list[str]) -> str:
    by_name = {t.name: t for t in schema.tables}
    parts = [f"{n}({', '.join(c.name for c in by_name[n].columns)})" for n in table_names if n in by_name]
    return " Columns available: " + "; ".join(parts) + "." if parts else ""


def validate_sql(
    sql: str,
    schema: SchemaInfo,
    dialect: str = "sqlite",
    blocked_tables: set[str] | None = None,
    blocked_columns: set[tuple[str, str]] | None = None,
) -> ValidationResult:
    blocked_tables = blocked_tables or set()
    blocked_columns = blocked_columns or set()
    cleaned = sql.strip().rstrip(";").strip()
    if not cleaned:
        return _fail(cleaned, ["The SQL is empty."])
    if len(cleaned) > MAX_SQL_LENGTH:
        return _fail(cleaned, [f"The SQL is longer than {MAX_SQL_LENGTH} characters."])
    rules = rules_for(dialect)
    if rules is None:
        return _fail(cleaned, [f"The SQL dialect '{dialect}' is not supported."])

    # 1. exactly one SELECT statement
    try:
        statements = [s for s in sqlglot.parse(cleaned, read=dialect) if s is not None]
    except (ParseError, TokenError) as exc:
        return _fail(cleaned, [f"SQL syntax error: {_first_line(exc)}"])
    if len(statements) != 1:
        return _fail(cleaned, ["Exactly one SQL statement is allowed."])
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        return _fail(cleaned, [f"Only SELECT statements are allowed, not {tree.key.upper()}."])

    errors: list[str] = []

    # 2. construct and function allowlist
    for node in tree.walk():
        if isinstance(node, exp.Anonymous):
            if node.name.lower() not in rules.anonymous:
                errors.append(f"The function '{node.name}' is not allowed.")
        elif isinstance(node, exp.Func):
            if node.key not in rules.function_keys and node.key not in rules.structural:
                errors.append(f"The function or construct '{node.sql_name().lower()}' is not allowed.")
        elif node.key not in rules.structural:
            errors.append(f"The SQL construct '{node.key}' is not allowed.")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            errors.append("Recursive queries (WITH RECURSIVE) are not allowed.")
    if errors:
        return _fail(cleaned, errors)

    # 3a. tables
    known = {t.name.lower(): t.name for t in schema.tables}
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    # Scope analysis tells real table lookups apart from references to a CTE, so a CTE that
    # reuses a table's name (WITH x AS (SELECT * FROM x) ...) cannot hide the real lookup.
    try:
        physical = {id(src) for scope in traverse_scope(tree)
                    for src in scope.sources.values() if isinstance(src, exp.Table)}
    except SqlglotError as exc:
        return _fail(cleaned, [f"Could not analyse the query: {_first_line(exc)}"])
    tables_used: list[str] = []
    table_nodes = list(tree.find_all(exp.Table))
    if len(table_nodes) > MAX_TABLE_REFERENCES:
        return _fail(cleaned, [f"More than {MAX_TABLE_REFERENCES} table references are not allowed."])
    for table in table_nodes:
        if not isinstance(table.this, exp.Identifier):
            errors.append("Table-valued functions are not allowed in FROM.")
            continue
        if table.args.get("db") or table.args.get("catalog"):
            errors.append(f"Qualified table names are not allowed: use '{table.name}' without a schema prefix.")
            continue
        name = table.name.lower()
        if name in cte_names and id(table) not in physical:
            continue  # a reference to a CTE defined in this query
        if name.startswith(_SENSITIVE_PREFIX) or name not in known:
            errors.append(f"Unknown table '{table.name}'. Available tables: {', '.join(sorted(known.values()))}.")
            continue
        if name in blocked_tables:
            errors.append(f"Access to table '{table.name}' is not permitted.")
            continue
        if known[name] not in tables_used:
            tables_used.append(known[name])
    if errors:
        return _fail(cleaned, errors, tables_used)

    # PostgreSQL folds unquoted names to lower case, so a table or column called "Order Total" only
    # works when written in double quotes. SQLGlot's name matching would accept it unquoted, but the
    # database would not, so say so here and let the model correct it.
    if dialect == "postgres":
        needs_quotes: dict[str, str] = {}
        plain: set[str] = set()
        for t in schema.tables:
            for name in [t.name] + [c.name for c in t.columns]:
                if _PLAIN_PG_NAME.fullmatch(name):
                    plain.add(name)
                else:
                    needs_quotes[name.lower()] = name
        for ident in tree.find_all(exp.Identifier):
            real = needs_quotes.get(ident.name.lower())
            # If a plain name with the same spelling also exists, the unquoted use may be that one.
            if real and not ident.args.get("quoted") and ident.name.lower() not in plain:
                errors.append(f'The name {real} must be written in double quotes, exactly as in the schema: "{real}".')
    if errors:
        return _fail(cleaned, errors, tables_used)

    # 3b. columns, resolved with proper scoping (aliases, CTEs, subqueries, SELECT *)
    # SQLite names are case-insensitive, so SQLGlot may normalise them. PostgreSQL names are exact
    # (unquoted ones are folded to lower case in the query, quoted ones are kept), so the schema is
    # given as it is and SQLGlot applies the same folding to the query.
    mapping = MappingSchema(
        {t.name: {c.name: (c.db_type or "TEXT") for c in t.columns} for t in schema.tables},
        dialect=dialect, normalize=(dialect != "postgres"),
    )
    try:
        qualified = qualify(
            tree.copy(), schema=mapping, dialect=dialect, validate_qualify_columns=True,
            quote_identifiers=False, identify=False)
    except OptimizeError as exc:
        return _fail(cleaned, [f"{_first_line(exc)}.{_columns_hint(schema, tables_used)}"], tables_used)
    except SqlglotError as exc:
        return _fail(cleaned, [f"Could not analyse the query: {_first_line(exc)}"], tables_used)

    if blocked_columns:
        for scope in traverse_scope(qualified):
            for column in scope.columns:
                source = scope.sources.get(column.table)
                if isinstance(source, exp.Table) and (source.name.lower(), column.name.lower()) in blocked_columns:
                    errors.append(f"Access to column '{source.name}.{column.name}' is not permitted.")
    if errors:
        return _fail(cleaned, errors, tables_used)

    return ValidationResult(valid=True, sql=cleaned, tables=tables_used)


def _first_line(exc: Exception) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", str(exc))  # SQLGlot colours error positions
    return re.sub(r"\s+", " ", text.splitlines()[0] if text else type(exc).__name__)[:300]
