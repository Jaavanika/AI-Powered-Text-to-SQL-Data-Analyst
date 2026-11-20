"""Schema linking: pick the tables, columns and values relevant to a question.

Matching is lexical (names, original headers, a few synonyms) plus matching against the
distinct values of low-cardinality text columns. Tables that are matched are connected
using known relationships. Small schemas are sent whole; if nothing matches, the whole
schema is used instead of guessing.
"""

import re
import time
from collections.abc import Sequence

from sqlalchemy import text

from app.core.db import quote
from app.schemas import (
    JoinPlan,
    LinkedSchema,
    LinkedTable,
    SchemaColumn,
    SchemaInfo,
    SchemaTable,
    ValueHint,
)
from app.services.connectors import Connector
from app.services.relationship_service import find_join_plan
from app.services.schema_service import singular

LARGE_TABLE_ROWS = 200_000          # PostgreSQL tables above this are sampled for value hints
VALUE_INDEX_BUDGET_SECONDS = 8      # the value pass gives up after this long
FULL_CONTEXT_MAX_TABLES = 6   # schemas this small are sent whole
MAX_LINKED_TABLES = 6         # matched tables kept for large schemas
FALLBACK_MAX_TABLES = 20      # tables sent when nothing matched in a large schema
WIDE_TABLE_COLUMNS = 30       # tables wider than this get a trimmed column list
WIDE_TABLE_EXTRA_COLUMNS = 15
MAX_DISTINCT_VALUES = 50      # text columns with more distinct values are not value-indexed
MAX_VALUE_COLUMNS = 200
MAX_HINTS = 10
MAX_HINTS_PER_COLUMN = 3

STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "for", "to", "by", "per", "and", "or", "is", "are", "was",
    "were", "be", "me", "my", "show", "list", "give", "get", "find", "what", "which", "who",
    "how", "many", "much", "all", "each", "every", "with", "from", "that", "this", "those",
    "these", "than", "then", "has", "have", "had", "do", "does", "did", "top", "most", "least",
    "highest", "lowest", "total", "number", "count", "average", "avg", "sum", "between", "over",
    "under", "only", "also", "their", "its", "it", "at", "as", "we", "our", "us", "i", "can",
    "please", "compare", "now", "just",
}
# Column-name parts too common to be evidence on their own.
GENERIC_TOKENS = {"id", "name", "date", "type", "code", "number", "no", "value", "status"}

# Everyday business words mapped to the schema vocabulary they usually refer to.
SYNONYMS = {
    "revenue": ["sale", "amount", "price", "payment", "order"],
    "sale": ["order", "amount", "price", "payment"],
    "spent": ["amount", "price", "payment", "order"],
    "spend": ["amount", "price", "payment", "order"],
    "income": ["amount", "price", "payment"],
    "profit": ["cost", "price"],
    "buyer": ["customer"],
    "client": ["customer"],
    "purchase": ["order"],
    "item": ["product"],
    "merchandise": ["product"],
    "area": ["region"],
    "location": ["city", "region"],
}

_cache: dict[tuple, dict[tuple[str, str], list[str]]] = {}


def tokens(value: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", value.lower())
    return {singular(w) for w in words if len(w) > 1 and not w.isdigit() and w not in STOPWORDS}


def _question_tokens(question: str) -> set[str]:
    base = tokens(question)
    extra: set[str] = set()
    for tok in base:
        extra.update(SYNONYMS.get(tok, []))
    return base | extra


def _normalised(value: str) -> str:
    """Lowercase, singular word tokens joined by spaces, padded for whole-word search."""
    words = [singular(w) for w in re.findall(r"[a-z0-9]+", value.lower())]
    return " " + " ".join(words) + " "


# ------------------------------------------------------------ value index

def distinct_values(datasource_id: str, connector: Connector, schema: SchemaInfo) -> dict[tuple[str, str], list[str]]:
    """Distinct values of low-cardinality text columns, cached until the data may have changed.

    Big PostgreSQL tables are sampled rather than scanned, and the whole pass stops at a time
    budget, so a large database never makes a question wait on this. Hints are only hints: a
    value missed here is simply not offered to the model.
    """
    # Part of the key is the columns being scanned, so a workspace that blocks a column neither fills the cache
    # for others with a smaller view nor is served hints of a column it cannot read.
    shape = hash(tuple((t.name, tuple(c.name for c in t.columns)) for t in schema.tables))
    key = (datasource_id, connector.cache_key(), shape)
    if key in _cache:
        return _cache[key]
    if len(_cache) > 64:
        _cache.clear()

    values: dict[tuple[str, str], list[str]] = {}
    started = time.monotonic()
    with connector.engine() as engine, engine.connect() as conn:
        scanned = 0
        for table in schema.tables:
            sample = connector.dialect == "postgres" and table.row_count > LARGE_TABLE_ROWS
            for col in table.columns:
                if col.type != "text" or col.is_primary_key or scanned >= MAX_VALUE_COLUMNS:
                    continue
                if time.monotonic() - started > VALUE_INDEX_BUDGET_SECONDS:
                    break
                scanned += 1
                t, c = quote(engine, table.name), quote(engine, col.name)
                source = f"{t} TABLESAMPLE SYSTEM (1)" if sample else t
                try:
                    rows = conn.execute(text(
                        f"SELECT DISTINCT {c} FROM {source} WHERE {c} IS NOT NULL LIMIT {MAX_DISTINCT_VALUES + 1}"
                    )).scalars().all()
                except Exception:
                    conn.rollback()        # e.g. a statement timeout on a very large table: skip the column
                    continue
                if 0 < len(rows) <= MAX_DISTINCT_VALUES:
                    values[(table.name, col.name)] = [str(v)[:60] for v in rows]
    _cache[key] = values
    return values


def _value_hints(question: str, values: dict[tuple[str, str], list[str]]) -> list[ValueHint]:
    q = _normalised(question)
    hints: list[ValueHint] = []
    for (table, column), column_values in values.items():
        found = 0
        for value in column_values:
            norm = _normalised(value)
            if len(norm.strip()) >= 3 and norm in q:
                hints.append(ValueHint(table=table, column=column, value=value))
                found += 1
                if found >= MAX_HINTS_PER_COLUMN:
                    break
    return hints[:MAX_HINTS]


# --------------------------------------------------------------- linking

def _key_columns(schema: SchemaInfo) -> dict[str, set[str]]:
    keys: dict[str, set[str]] = {}
    for rel in schema.relationships:
        keys.setdefault(rel.from_table, set()).update(rel.from_columns)
        keys.setdefault(rel.to_table, set()).update(rel.to_columns)
    return keys


def _column_matches(col: SchemaColumn, q_tokens: set[str]) -> bool:
    col_tokens = tokens(col.name) | tokens(col.original_name)
    specific = col_tokens - GENERIC_TOKENS
    if specific:
        return bool(specific & q_tokens)
    return bool(col_tokens & q_tokens)  # e.g. a column literally called "date" or "name"


def link_schema(
    schema: SchemaInfo,
    question: str,
    values: dict[tuple[str, str], list[str]] | None = None,
    context: str = "",
    pinned: Sequence[str] = (),
) -> LinkedSchema:
    """Select the schema slice for `question`.

    For follow-ups, `context` (earlier questions) is matched as well, and `pinned` tables (those
    used by the previous answer) are always kept, because "now only the top five" names nothing.
    """
    text_to_match = f"{question} {context}".strip()
    q_tokens = _question_tokens(text_to_match)
    hints = _value_hints(text_to_match, values or {})
    hinted_tables = {h.table for h in hints}
    keys = _key_columns(schema)

    scores: dict[str, float] = {}
    matched_columns: dict[str, set[str]] = {}
    for table in schema.tables:
        score = 0.0
        name_tokens = tokens(table.name) | tokens(table.original_name)
        score += 3 * len((name_tokens - GENERIC_TOKENS) & q_tokens)
        cols: set[str] = set()
        for col in table.columns:
            # Key columns are shared between tables, so matching them would drag in neighbours.
            if col.is_primary_key or col.name in keys.get(table.name, set()):
                continue
            if _column_matches(col, q_tokens):
                cols.add(col.name)
                score += 2
        if table.name in hinted_tables:
            score += 3
            cols |= {h.column for h in hints if h.table == table.name}
        if score > 0:
            scores[table.name] = score
            matched_columns[table.name] = cols

    ranked = sorted(scores, key=lambda t: (-scores[t], t))[:MAX_LINKED_TABLES]
    by_name = {t.name: t for t in schema.tables}
    for name in pinned:
        if name in by_name and name not in ranked:
            ranked.append(name)
            scores.setdefault(name, 1.0)

    join_plan: JoinPlan | None = None
    if len(ranked) >= 2:
        join_plan = find_join_plan(schema, ranked)

    fallback = not ranked
    if len(schema.tables) <= FULL_CONTEXT_MAX_TABLES:
        included = [t.name for t in schema.tables]
    elif fallback:
        by_size = sorted(schema.tables, key=lambda t: (-t.row_count, t.name))
        included = [t.name for t in by_size[:FALLBACK_MAX_TABLES]]
    else:
        included = list(ranked)
        if join_plan:
            included += [t for t in join_plan.tables if t not in included]

    included_set = set(included)
    linked_tables = [
        _linked_table(by_name[name], matched_columns.get(name, set()), keys.get(name, set()),
                      scores.get(name, 0.0), name in ranked)
        for name in sorted(included_set, key=lambda n: (-scores.get(n, 0.0), n))
    ]
    return LinkedSchema(
        tables=linked_tables,
        relationships=[r for r in schema.relationships
                       if r.from_table in included_set and r.to_table in included_set],
        join_plan=join_plan,
        value_hints=[h for h in hints if h.table in included_set],
        fallback=fallback,
    )


def _linked_table(table: SchemaTable, matched: set[str], key_cols: set[str], score: float,
                  is_matched: bool) -> LinkedTable:
    columns = table.columns
    if len(columns) > WIDE_TABLE_COLUMNS:
        keep = matched | key_cols | set(table.primary_key)
        extras = [c.name for c in columns if c.name not in keep][:WIDE_TABLE_EXTRA_COLUMNS]
        keep |= set(extras)
        columns = [c for c in columns if c.name in keep]
    return LinkedTable(
        name=table.name, original_name=table.original_name, row_count=table.row_count,
        columns=columns, primary_key=table.primary_key, is_view=table.is_view, matched=is_matched, score=score,
    )
