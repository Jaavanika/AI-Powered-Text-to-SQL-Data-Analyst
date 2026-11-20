"""Safe, read-only execution of validated SQL against a SQLite data source.

Defence in depth: even if validation had a gap, the connection itself
  - is opened read-only (`mode=ro`),
  - has an authorizer that only permits SELECT, reads of known tables (minus blocked
    columns) and a fixed set of functions,
  - is interrupted when it exceeds the time limit,
  - returns at most `max_rows` rows, and caps the size of any single value.
"""

import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from pydantic import BaseModel, Field

MAX_VALUE_LENGTH = 5_000_000  # SQLITE_LIMIT_LENGTH: stops e.g. zeroblob/replace from allocating gigabytes
PROGRESS_INTERVAL = 10_000    # SQLite VM instructions between time-limit checks

# Names as SQLite reports them to the authorizer (operators such as LIKE are functions too).
RUNTIME_FUNCTIONS = {
    "like", "glob", "regexp", "count", "sum", "avg", "min", "max", "total", "abs", "round",
    "coalesce", "ifnull", "nullif", "iif", "length", "lower", "upper", "substr", "substring",
    "trim", "ltrim", "rtrim", "replace", "instr", "printf", "format", "concat", "concat_ws",
    "strftime", "date", "time", "datetime", "julianday", "unixepoch", "group_concat",
    "string_agg", "row_number", "rank", "dense_rank", "percent_rank", "cume_dist", "ntile",
    "lag", "lead", "first_value", "last_value", "nth_value", "sign", "ceil", "ceiling", "floor",
    "sqrt", "power", "pow", "ln", "log", "log10", "exp", "mod", "trunc", "hex", "char", "unicode",
    "typeof", "likely", "unlikely", "sin", "cos", "tan", "pi", "radians", "degrees", "cast",
    "timediff",
}


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list]
    row_count: int
    truncated: bool = False
    max_rows: int
    elapsed_ms: int
    notes: list[str] = Field(default_factory=list)


class ExecutionError(Exception):
    def __init__(self, message: str, kind: str = "sql"):
        super().__init__(message)
        self.message = message
        self.kind = kind  # "sql" | "timeout" | "denied"


def _authorizer(allowed_tables: set[str], blocked_columns: set[tuple[str, str]]):
    def authorize(action, arg1, arg2, dbname, source):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            table, column = (arg1 or "").lower(), (arg2 or "").lower()
            if dbname not in (None, "main", "temp") or table not in allowed_tables:
                return sqlite3.SQLITE_DENY
            if (table, column) in blocked_columns:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_OK if (arg2 or "").lower() in RUNTIME_FUNCTIONS else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY  # PRAGMA, ATTACH, INSERT, UPDATE, DDL, RECURSIVE, TRANSACTION, ...

    return authorize


def _clean_error(message: str) -> str:
    message = re.sub(r"[A-Za-z]:[\\/][^\s'\"]+|/[\w.\-/]+\.(?:sqlite|db)\b", "<path>", message)
    return message[:300]


def _json_value(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<binary {len(bytes(value))} bytes>"
    return value


def execute_query(
    db_path: Path,
    sql: str,
    *,
    allowed_tables: set[str],
    blocked_columns: set[tuple[str, str]] | None = None,
    timeout_seconds: float = 15,
    max_rows: int = 1000,
) -> QueryResult:
    started = time.monotonic()
    deadline = started + timeout_seconds
    try:
        conn = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise ExecutionError(_clean_error(f"Could not open the database: {exc}")) from exc
    with closing(conn):
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_LENGTH)
        conn.set_authorizer(_authorizer({t.lower() for t in allowed_tables}, blocked_columns or set()))
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, PROGRESS_INTERVAL)
        try:
            cursor = conn.execute(sql)
            columns = [d[0] for d in cursor.description or []]
            fetched = cursor.fetchmany(max_rows + 1)
        except sqlite3.DatabaseError as exc:
            text = str(exc)
            if "interrupted" in text.lower():
                raise ExecutionError(
                    f"The query exceeded the {timeout_seconds:g} second time limit.", "timeout") from exc
            if "not authorized" in text.lower() or "prohibited" in text.lower():
                raise ExecutionError("The database refused this query: it is not a permitted read.", "denied") from exc
            raise ExecutionError(_clean_error(text)) from exc
        except sqlite3.Warning as exc:  # e.g. "You can only execute one statement at a time"
            raise ExecutionError(_clean_error(str(exc)), "denied") from exc

    truncated = len(fetched) > max_rows
    rows = [[_json_value(v) for v in row] for row in fetched[:max_rows]]
    return QueryResult(
        columns=columns, rows=rows, row_count=len(rows), truncated=truncated, max_rows=max_rows,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        notes=[f"Only the first {max_rows} rows are shown."] if truncated else [],
    )
