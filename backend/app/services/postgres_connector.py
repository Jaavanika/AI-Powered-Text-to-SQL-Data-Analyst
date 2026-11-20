"""PostgreSQL connector: reads only, by construction.

Every transaction on every connection this module opens starts with `SET TRANSACTION READ ONLY`,
a statement timeout, a lock timeout and a search_path limited to the chosen schema. These are set
per transaction (not as connection options), so they also work behind connection poolers.
The validator is the first line of defence; this is the second. A dedicated read-only database
role, which the connection check recommends and checks for, is the third.
"""

import datetime as dt
import decimal
import json
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.core import secrets
from app.core.db import quote
from app.core.hosts import ensure_listed, pin_addresses
from app.schemas import ColumnInfo, TableInfo
from app.services import registry
from app.services.query_execution import ExecutionError, QueryResult
from app.services.schema_service import logical_type_from_db

CACHE_SECONDS = 300            # how long value hints stay valid before they are rebuilt
LOCK_TIMEOUT_MS = 2000
IDLE_IN_TRANSACTION_MS = 60_000
EXACT_COUNT_FALLBACK_SECONDS = 3
POWERFUL_ROLES = ("pg_write_all_data", "pg_execute_server_program", "pg_read_server_files",
                  "pg_write_server_files", "pg_signal_backend")


@dataclass(frozen=True)
class PgParams:
    host: str
    port: int
    database: str
    username: str
    password: str = field(default="", repr=False)
    schema: str = "public"
    sslmode: str = "prefer"


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


# --------------------------------------------------------------------- engines

def create_pg_engine(params: PgParams, *, pooled: bool = True) -> Engine:
    """An engine whose every transaction is read-only, time-limited and confined to one schema."""
    settings = get_settings()
    # The address is chosen here, from the same lookup the allowlist check used, and handed to the driver as
    # `hostaddr`; `host` stays the name so TLS certificates are still checked against it. One entry per
    # address lets libpq fall back to the next one, as it would have with a name that has several.
    addresses = pin_addresses(params.host, settings.allowed_db_hosts)
    url = URL.create("postgresql+psycopg", username=params.username, password=params.password or None,
                     port=params.port, database=params.database)
    options: dict[str, Any] = {
        "connect_args": {
            "host": ",".join([params.host.strip("[]")] * len(addresses)),
            "hostaddr": ",".join(addresses),
            "connect_timeout": settings.db_connect_timeout_seconds,
            "sslmode": params.sslmode,
            "application_name": "text-to-sql-analyst",
        },
        "pool_pre_ping": True,
    }
    if pooled:
        options.update(pool_size=3, max_overflow=2, pool_recycle=300)
    else:
        options["poolclass"] = NullPool
    engine = create_engine(url, **options)
    schema_sql = quote_ident(params.schema)

    @event.listens_for(engine, "begin")
    def _read_only(conn):            # runs at the start of every transaction
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        # The limits are applied with a real query (set_config, local to this transaction). PostgreSQL
        # only locks a transaction's access mode once its first query has run, and plain SET statements
        # do not count: without this, later SQL could still say SET TRANSACTION READ WRITE.
        conn.exec_driver_sql(
            "SELECT set_config('statement_timeout', %s, true), set_config('lock_timeout', %s, true), "
            "set_config('idle_in_transaction_session_timeout', %s, true), set_config('search_path', %s, true), "
            "set_config('TimeZone', 'UTC', true)",       # so date_trunc() and casts do not depend on the server's zone
            (str(int(get_settings().query_timeout_seconds * 1000)), str(LOCK_TIMEOUT_MS),
             str(IDLE_IN_TRANSACTION_MS), schema_sql))

    return engine


_engines: dict[str, Engine] = {}
_lock = threading.Lock()


def _engine_for(datasource_id: str, params: PgParams) -> Engine:
    # A saved connection is checked again each time it is used: the allowlist may have been tightened.
    ensure_listed(params.host, get_settings().allowed_db_hosts)
    with _lock:
        engine = _engines.get(datasource_id)
        if engine is None:
            engine = _engines[datasource_id] = create_pg_engine(params, pooled=True)
        return engine


def dispose_engine(datasource_id: str) -> None:
    with _lock:
        engine = _engines.pop(datasource_id, None)
    if engine is not None:
        engine.dispose()


# ---------------------------------------------------------------------- errors

def _orig(exc: BaseException):
    return getattr(exc, "orig", exc)


def sqlstate(exc: BaseException) -> str | None:
    orig = _orig(exc)
    return getattr(orig, "sqlstate", None) or getattr(getattr(orig, "diag", None), "sqlstate", None)


def primary_message(exc: BaseException) -> str:
    orig = _orig(exc)
    message = getattr(getattr(orig, "diag", None), "message_primary", None)
    return message or (str(orig).strip().splitlines() or [type(orig).__name__])[0]


def scrub(message: str, params: PgParams | None = None) -> str:
    """Remove the password (and anything shaped like a connection URI) from an error message."""
    if params and params.password:
        message = message.replace(params.password, "***")
    message = re.sub(r"postgres(?:ql)?(?:\+\w+)?://\S+", "<connection>", message)
    return message[:300]


def friendly_error(exc: BaseException, params: PgParams) -> str:
    """A message a user can act on, without driver internals or credentials."""
    state = sqlstate(exc)
    raw = str(_orig(exc)).lower()
    where = f"{params.host}:{params.port}"
    # Errors raised while connecting carry no SQLSTATE in the driver, so the server's wording counts too.
    if state == "28P01" or "password authentication failed" in raw:
        return f"Authentication failed for user '{params.username}'. Check the username and password."
    if state == "28000" or "no pg_hba.conf entry" in raw or re.search(r'role ".*" does not exist', raw):
        return (f"The server refused the connection for user '{params.username}': the role may not exist, "
                "or no access rule allows this connection (or it requires SSL).")
    if state == "3D000" or re.search(r'database ".*" does not exist', raw):
        return f"The database '{params.database}' does not exist on that server."
    if state in ("57P03", "53300"):
        return "The database is starting up or has too many connections. Try again shortly."
    if "timeout expired" in raw or "timed out" in raw:
        return f"Connecting to {where} timed out. Check the host, port and firewall."
    if any(s in raw for s in ("could not translate host name", "name or service not known",
                              "getaddrinfo failed", "nodename nor servname", "no such host")):
        return f"The host name '{params.host}' could not be found."
    if any(s in raw for s in ("connection refused", "actively refused", "could not connect")):
        return f"Could not connect to {where}. Check the host and port, and that the server accepts connections."
    if "ssl" in raw:
        return "The SSL settings do not match the server (it may not support SSL, or may require it). Change the SSL mode."
    return "Could not connect to the database: " + scrub(primary_message(exc), params)


# ------------------------------------------------------------------- connector

def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() and abs(value) < 2 ** 53 else float(value)
    if isinstance(value, dt.datetime):
        # In UTC, and a midnight value as a plain date: date_trunc() on a date returns a timestamp at
        # midnight, which should read as 2024-01-01 (the form the rest of the app recognises as a date).
        if value.tzinfo is not None:
            value = value.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return value.date().isoformat() if value.time() == dt.time(0) else value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<binary {len(bytes(value))} bytes>"
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, default=str)
    return str(value)


class PostgresConnector:
    dialect = "postgres"

    def __init__(self, datasource_id: str, params: PgParams):
        self.datasource_id = datasource_id
        self.params = params
        self.schema_name = params.schema

    @contextmanager
    def engine(self) -> Iterator[Engine]:
        yield _engine_for(self.datasource_id, self.params)      # pooled and shared: not disposed per use

    def cache_key(self) -> tuple:
        return ("postgres", self.datasource_id, int(time.time() // CACHE_SECONDS))

    def preview(self, table: str, limit: int) -> list[dict[str, Any]]:
        with self.engine() as engine, engine.connect() as conn:
            result = conn.execute(text(f"SELECT * FROM {quote(engine, table)} LIMIT :n"), {"n": limit})
            return [{k: _json_value(v) for k, v in row.items()} for row in result.mappings()]

    def execute(self, sql, *, allowed_tables, blocked_columns, timeout_seconds, max_rows) -> QueryResult:
        # allowed_tables / blocked_columns are enforced by the validator; PostgreSQL itself offers no
        # per-statement authorizer, so recommend (and check for) a role granted only what it should read.
        started = time.monotonic()
        try:
            with self.engine() as engine, engine.connect() as conn:
                conn.exec_driver_sql(f"SET LOCAL statement_timeout = {max(1, int(timeout_seconds * 1000))}")
                # no_parameters: the SQL is sent as written, so a '%' in LIKE '%x%' is not a placeholder
                streaming = conn.execution_options(stream_results=True, no_parameters=True)
                result = streaming.exec_driver_sql(sql)
                columns = list(result.keys())
                fetched = result.fetchmany(max_rows + 1)
                result.close()
        except DBAPIError as exc:
            raise self._execution_error(exc, timeout_seconds) from exc
        except SQLAlchemyError as exc:
            raise ExecutionError(
                "The database connection failed: " + scrub(str(exc).splitlines()[0], self.params),
                "unavailable") from exc

        truncated = len(fetched) > max_rows
        rows = [[_json_value(v) for v in row] for row in fetched[:max_rows]]
        return QueryResult(
            columns=columns, rows=rows, row_count=len(rows), truncated=truncated, max_rows=max_rows,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            notes=[f"Only the first {max_rows} rows are shown."] if truncated else [],
        )

    def _execution_error(self, exc: DBAPIError, timeout_seconds: float) -> ExecutionError:
        state = sqlstate(exc)
        if state == "57014":
            return ExecutionError(f"The query exceeded the {timeout_seconds:g} second time limit.", "timeout")
        if state in ("25006", "42501"):
            return ExecutionError("The database refused this query: it is not a permitted read.", "denied")
        if state is None or state.startswith("08") or state in ("57P01", "57P02", "57P03"):
            return ExecutionError("The database could not be reached: " + friendly_error(exc, self.params), "unavailable")
        return ExecutionError(scrub(primary_message(exc), self.params))


def params_from_stored(stored: dict) -> PgParams:
    return PgParams(
        host=stored["host"], port=int(stored["port"]), database=stored["database"],
        username=stored["username"], schema=stored.get("schema_name", "public"),
        sslmode=stored.get("sslmode", "prefer"),
        password=secrets.decrypt(stored["password_enc"]) if stored.get("password_enc") else "",
    )


def connector_for(datasource_id: str, workspace_id: str | None) -> PostgresConnector:
    stored = registry.get_stored_connection(datasource_id, workspace_id)
    if stored is None:
        raise LookupError("No stored connection for this data source.")
    return PostgresConnector(datasource_id, params_from_stored(stored))


# --------------------------------------------------------------- introspection

@dataclass
class ServerFacts:
    version: str
    user: str
    is_superuser: bool
    schema_exists: bool
    tables: list[TableInfo]
    role_warnings: list[str]
    writable_tables: list[str]


def inspect_server(engine: Engine, schema: str) -> ServerFacts:
    """Facts about the server, the account and the chosen schema, gathered with read-only queries."""
    with engine.connect() as conn:
        version = conn.execute(text("SHOW server_version")).scalar_one()
        user, superuser, createrole, createdb, bypassrls = conn.execute(text(
            "SELECT current_user, r.rolsuper, r.rolcreaterole, r.rolcreatedb, r.rolbypassrls "
            "FROM pg_roles r WHERE r.rolname = current_user")).one()
        exists = conn.execute(text("SELECT 1 FROM pg_namespace WHERE nspname = :s"), {"s": schema}).first() is not None

        role_warnings: list[str] = []
        if createrole or createdb or bypassrls:
            powers = [n for n, on in (("create roles", createrole), ("create databases", createdb),
                                      ("bypass row-level security", bypassrls)) if on]
            role_warnings.append(f"This account can {', '.join(powers)}.")
        powerful = conn.execute(text(
            "SELECT r.rolname FROM pg_roles r WHERE r.rolname = ANY(:names) AND pg_has_role(current_user, r.oid, 'MEMBER')"),
            {"names": list(POWERFUL_ROLES)}).scalars().all()
        if powerful:
            role_warnings.append(f"This account belongs to powerful built-in roles: {', '.join(sorted(powerful))}.")

        if not exists:
            return ServerFacts(version, user, bool(superuser), False, [], role_warnings, [])

        insp = inspect(conn)
        names = sorted(insp.get_table_names(schema=schema))
        estimates = dict(conn.execute(text(
            "SELECT c.relname, c.reltuples::bigint FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :s AND c.relkind IN ('r', 'p')"), {"s": schema}).all())
        writable = conn.execute(text(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :s AND c.relkind IN ('r', 'p', 'v') AND ("
            "has_table_privilege(current_user, c.oid, 'INSERT') OR has_table_privilege(current_user, c.oid, 'UPDATE') "
            "OR has_table_privilege(current_user, c.oid, 'DELETE') OR has_table_privilege(current_user, c.oid, 'TRUNCATE')) "
            "ORDER BY c.relname"), {"s": schema}).scalars().all()

        tables = []
        for name in names:
            columns = [ColumnInfo(name=c["name"], original_name=c["name"], type=logical_type_from_db(str(c["type"])))
                       for c in insp.get_columns(name, schema=schema)]
            count = estimates.get(name, -1)
            if count is None or count < 0:               # never analysed: count them (small tables are quick)
                count = _exact_count(conn, name)
            tables.append(TableInfo(name=name, original_name=name, row_count=int(count), columns=columns))

        # Views and materialized views the account may read are offered like tables. They are not counted (that would
        # run the whole query), and one the account cannot select from is left out, so it is never suggested.
        readable_views = conn.execute(text(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :s AND c.relkind IN ('v', 'm') AND has_table_privilege(current_user, c.oid, 'SELECT') "
            "ORDER BY c.relname"), {"s": schema}).scalars().all()
        for name in readable_views:
            columns = [ColumnInfo(name=c["name"], original_name=c["name"], type=logical_type_from_db(str(c["type"])))
                       for c in insp.get_columns(name, schema=schema)]
            tables.append(TableInfo(name=name, original_name=name, row_count=0, columns=columns, is_view=True))
        tables.sort(key=lambda t: t.name)
        return ServerFacts(version, user, bool(superuser), True, tables, role_warnings, list(writable))


def _exact_count(conn, table: str) -> int:
    try:
        conn.exec_driver_sql(f"SET LOCAL statement_timeout = {EXACT_COUNT_FALLBACK_SECONDS * 1000}")
        return int(conn.execute(text(f"SELECT COUNT(*) FROM {quote_ident(table)}")).scalar_one())
    except SQLAlchemyError:
        conn.rollback()
        return 0
