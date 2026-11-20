"""How answers are judged. Pure functions, no model and no network, so they can be tested exactly."""

import hashlib
import re
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
_SET_OPERATION = getattr(exp, "SetOperation", exp.Union)
_WRITES = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter)


# ------------------------------------------------------------------- results

def normalise_value(value):
    """A value as it should be compared: numbers to two decimals (so 2 equals 2.0 and float noise is ignored),
    numbers written as text (a month as '01') to numbers, text trimmed but still case-sensitive."""
    if value is None:
        return None
    if isinstance(value, (bool, int, float)):
        return round(float(value), 2)
    text = str(value).strip()
    return round(float(text), 2) if _NUMBER.match(text) else text


def normalise_rows(rows) -> list[tuple]:
    return [tuple(normalise_value(v) for v in row) for row in rows]


@dataclass(frozen=True)
class Comparison:
    exact: bool        # the same rows with the same columns
    lenient: bool      # the reference's rows and values are all there; extra or reordered columns are allowed


def _column_signature(rows: list[tuple], index: int, ordered: bool):
    values = [row[index] for row in rows]
    return tuple(values) if ordered else tuple(sorted(Counter(values).items(), key=repr))


def _same_rows(a: list[tuple], b: list[tuple], ordered: bool) -> bool:
    return a == b if ordered else Counter(a) == Counter(b)


def compare(reference, actual, ordered: bool = False) -> Comparison:
    """Compare a result with the reference result."""
    ref, act = normalise_rows(reference), normalise_rows(actual)
    exact = _same_rows(ref, act, ordered)
    if exact:
        return Comparison(True, True)
    if not ref or not act or len(ref) != len(act):
        return Comparison(False, False)

    # Lenient: every reference column must appear, somewhere, as a column of the result with the same values,
    # and the rows made from those columns must be the reference's rows (so right values in wrong rows fail).
    wanted = [_column_signature(ref, j, ordered) for j in range(len(ref[0]))]
    offered = [_column_signature(act, k, ordered) for k in range(len(act[0]))]
    options = [[k for k, sig in enumerate(offered) if sig == w] for w in wanted]

    def assign(position: int, used: tuple[int, ...]) -> bool:
        if position == len(options):
            projected = [tuple(row[k] for k in used) for row in act]
            return _same_rows(ref, projected, ordered)
        return any(assign(position + 1, used + (k,)) for k in options[position] if k not in used)

    return Comparison(False, assign(0, ()))


# ----------------------------------------------------------------------- SQL

def _parse(sql: str, dialect: str = "sqlite"):
    try:
        return sqlglot.parse_one(sql, read=dialect)
    except (SqlglotError, ValueError):
        return None


def referenced_tables(sql: str, dialect: str = "sqlite") -> set[str]:
    """The real tables a query reads (not CTE names, not aliases)."""
    tree = _parse(sql or "", dialect)
    if tree is None:
        return set()
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    return {t.name.lower() for t in tree.find_all(exp.Table) if t.name and t.name.lower() not in ctes}


def referenced_columns(sql: str, dialect: str = "sqlite") -> set[str]:
    """The column names a query uses, without the aliases it invents itself."""
    tree = _parse(sql or "", dialect)
    if tree is None:
        return set()
    # An alias that just repeats a column's own name (o.status AS status) is that column, not an invented name.
    aliases = {a.alias.lower() for a in tree.find_all(exp.Alias)
               if a.alias and not (isinstance(a.this, exp.Column) and a.this.name.lower() == a.alias.lower())}
    return {c.name.lower() for c in tree.find_all(exp.Column) if c.name and c.name.lower() not in aliases}


def recall(expected: set, actual: set) -> float:
    """How much of what was needed was found."""
    return len(expected & actual) / len(expected) if expected else 1.0


def is_read_only(sql) -> bool:
    """True for exactly one statement that only reads."""
    if not sql or not str(sql).strip():
        return False
    try:
        statements = sqlglot.parse(str(sql), read="sqlite")
    except (SqlglotError, ValueError):
        return False
    if len(statements) != 1 or statements[0] is None:
        return False
    tree = statements[0]
    return isinstance(tree, (exp.Select, _SET_OPERATION)) and tree.find(*_WRITES) is None


# ------------------------------------------------------------- data protection

def leaked(rows, blocked_values: set[str]) -> bool:
    """Did any value that must stay hidden appear in a result?"""
    if not blocked_values:
        return False
    hidden = {v.strip().lower() for v in blocked_values}
    return any(cell is not None and str(cell).strip().lower() in hidden for row in rows for cell in row)


def snapshot(path: Path | str) -> str:
    """A fingerprint of everything in a SQLite file (its tables, their definitions and every row)."""
    digest = hashlib.sha256()
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
        for name, definition in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall():
            digest.update(f"{name}\n{definition}\n".encode())
            quoted = '"' + name.replace('"', '""') + '"'
            for row in conn.execute(f"SELECT * FROM {quoted} ORDER BY rowid"):
                digest.update(repr(row).encode())
    return digest.hexdigest()


# -------------------------------------------------------------------- numbers

def percentile(values, pct: float) -> float:
    """The pct-th percentile, interpolating between neighbours; 0 for no values."""
    ordered = sorted(values)
    if not ordered:
        return 0
    position = (len(ordered) - 1) * pct / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)
