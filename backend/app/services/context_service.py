"""Business context: the definitions and data-dictionary notes that tell the model what terms mean.

A definition is guidance, not code. It is shown to the model as data, and the SQL the model
writes from it still has to pass the same validation and run read-only. A metric's optional SQL
expression is checked when it is saved (it must parse, use only allowed functions and refer to
real columns), so a typo is caught then and not when someone asks a question.

Retrieval is lexical on purpose: a question picks up the definitions whose name or aliases appear
in it (or in the conversation so far). Vector search would add a model and an index for a list that
is capped at 200 short entries; it can be added if lists ever get large.
"""

import csv
import io
import json
import re
import uuid
from contextlib import closing
from datetime import datetime, timezone

import sqlglot
from pydantic import ValidationError
from sqlglot import exp
from sqlglot.errors import SqlglotError

from app.schemas import (
    ContextEntry,
    ContextEntryIn,
    ContextImportPreview,
    ContextImportResult,
    LinkedSchema,
    SchemaInfo,
)
from app.services import registry
from app.services.schema_linking import tokens
from app.services.sql_allowlist import rules_for

MAX_ENTRIES = 200
MAX_IMPORT_BYTES = 200_000
MAX_SELECTED = 8                  # definitions put in one prompt
MAX_SELECTED_CHARS = 2400
MAX_NOTE_CHARS = 200
MAX_WARNINGS = 8

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_COLUMN_SYNONYMS = {
    "name": "name", "term": "name", "metric": "name", "title": "name",
    "description": "description", "definition": "description", "meaning": "description", "desc": "description",
    "kind": "kind", "type": "kind", "aliases": "aliases", "alias": "aliases", "synonyms": "aliases",
    "expression": "expression", "sql": "expression", "formula": "expression",
}


class ContextError(Exception):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def clean(text: object, limit: int) -> str:
    """One line of text: control characters (including newlines) become spaces. Definitions end up in
    LLM prompts, so a stored definition must not be able to start a new line of instructions."""
    return " ".join(_CONTROL.sub(" ", str(text)).split())[:limit]


# ---------------------------------------------------------------------- storage

def _entry(row) -> ContextEntry:
    return ContextEntry(
        id=row["id"], kind=row["kind"], name=row["name"],
        aliases=[a for a in row["aliases"].split("\n") if a], description=row["description"],
        expression=row["expression"], created_at=row["created_at"], updated_at=row["updated_at"])


def list_entries(workspace_id: str, datasource_id: str) -> list[ContextEntry]:
    with closing(registry.connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM context_entries WHERE workspace_id = ? AND datasource_id = ? "
            "ORDER BY kind, lower(name)", (workspace_id, datasource_id)).fetchall()
    return [_entry(r) for r in rows]


def get_entry(workspace_id: str, datasource_id: str, entry_id: str) -> ContextEntry | None:
    with closing(registry.connect()) as conn:
        row = conn.execute(
            "SELECT * FROM context_entries WHERE id = ? AND workspace_id = ? AND datasource_id = ?",
            (entry_id, workspace_id, datasource_id)).fetchone()
    return _entry(row) if row else None


def _find_same(conn, workspace_id, datasource_id, kind, name, exclude_id=None):
    row = conn.execute(
        "SELECT id FROM context_entries WHERE workspace_id = ? AND datasource_id = ? AND kind = ? "
        "AND lower(name) = lower(?)", (workspace_id, datasource_id, kind, name)).fetchone()
    return row["id"] if row and row["id"] != exclude_id else None


def create_entry(workspace_id: str, datasource_id: str, entry: ContextEntryIn) -> ContextEntry:
    with closing(registry.connect()) as conn, conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM context_entries WHERE workspace_id = ? AND datasource_id = ?",
            (workspace_id, datasource_id)).fetchone()[0]
        if count >= MAX_ENTRIES:
            raise ContextError(f"A data source can have at most {MAX_ENTRIES} definitions.", 409)
        if _find_same(conn, workspace_id, datasource_id, entry.kind, entry.name):
            raise ContextError(f"There is already a {entry.kind} named '{entry.name}'.", 409)
        entry_id, now = uuid.uuid4().hex, _now()
        conn.execute(
            "INSERT INTO context_entries (id, workspace_id, datasource_id, kind, name, aliases, description, "
            "expression, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry_id, workspace_id, datasource_id, entry.kind, entry.name, "\n".join(entry.aliases),
             entry.description, entry.expression, now, now))
    return get_entry(workspace_id, datasource_id, entry_id)  # type: ignore[return-value]


def update_entry(workspace_id: str, datasource_id: str, entry_id: str, entry: ContextEntryIn) -> ContextEntry:
    with closing(registry.connect()) as conn, conn:
        if not conn.execute("SELECT 1 FROM context_entries WHERE id = ? AND workspace_id = ? AND datasource_id = ?",
                            (entry_id, workspace_id, datasource_id)).fetchone():
            raise ContextError("Definition not found.", 404)
        if _find_same(conn, workspace_id, datasource_id, entry.kind, entry.name, exclude_id=entry_id):
            raise ContextError(f"There is already a {entry.kind} named '{entry.name}'.", 409)
        conn.execute(
            "UPDATE context_entries SET kind = ?, name = ?, aliases = ?, description = ?, expression = ?, "
            "updated_at = ? WHERE id = ?",
            (entry.kind, entry.name, "\n".join(entry.aliases), entry.description, entry.expression, _now(), entry_id))
    return get_entry(workspace_id, datasource_id, entry_id)  # type: ignore[return-value]


def delete_entry(workspace_id: str, datasource_id: str, entry_id: str) -> bool:
    with closing(registry.connect()) as conn, conn:
        cur = conn.execute("DELETE FROM context_entries WHERE id = ? AND workspace_id = ? AND datasource_id = ?",
                           (entry_id, workspace_id, datasource_id))
    return cur.rowcount > 0


# ------------------------------------------------------------------ validation

def _check_expression(expression: str, schema: SchemaInfo) -> str | None:
    """Why `expression` cannot be used as a metric formula, or None if it can."""
    if ";" in expression:
        return "An expression is a single formula, without semicolons."
    rules = rules_for(schema.dialect)
    if rules is None:
        return f"Expressions are not supported for the '{schema.dialect}' dialect."
    try:
        tree = sqlglot.parse_one(expression, read=schema.dialect)
    except SqlglotError as exc:
        return f"The expression is not valid SQL: {str(exc).splitlines()[0][:120]}"
    if tree is None or isinstance(tree, (exp.Query, exp.Command, exp.DDL, exp.DML)) or tree.find(exp.Select):
        return "The expression must be a formula over columns, not a query. Subqueries are not supported."

    problems: list[str] = []
    for node in tree.walk():
        if isinstance(node, exp.Anonymous):
            if node.name.lower() not in rules.anonymous:
                problems.append(f"The function '{node.name}' is not allowed.")
        elif isinstance(node, exp.Func):
            if node.key not in rules.function_keys and node.key not in rules.structural:
                problems.append(f"The function '{node.sql_name().lower()}' is not allowed.")
        elif node.key not in rules.structural:
            problems.append(f"The construct '{node.key}' is not allowed.")
    if problems:
        return problems[0]

    columns_by_table = {t.name.lower(): {c.name.lower() for c in t.columns} for t in schema.tables}
    for column in tree.find_all(exp.Column):
        table, name = column.table.lower(), column.name.lower()
        if table:
            if table not in columns_by_table:
                return f"The table '{column.table}' does not exist."
            if name not in columns_by_table[table]:
                return f"The column '{column.table}.{column.name}' does not exist."
        elif not any(name in cols for cols in columns_by_table.values()):
            return f"The column '{column.name}' does not exist in any table."
    return None


def normalise(entry: ContextEntryIn, schema: SchemaInfo) -> ContextEntryIn:
    """Clean a definition and check it against the schema. Raises ContextError if it cannot be used."""
    name = clean(entry.name, 80)
    description = clean(entry.description, 600)
    if not name or not description:
        raise ContextError("A definition needs a name and a description.")
    aliases: list[str] = []
    for alias in entry.aliases:
        a = clean(alias, 40)
        if a and a.lower() != name.lower() and a.lower() not in {x.lower() for x in aliases}:
            aliases.append(a)
    expression = clean(entry.expression, 400) if entry.expression else None

    if entry.kind == "table":
        match = next((t for t in schema.tables if t.name.lower() == name.lower()), None)
        if match is None:
            raise ContextError(f"There is no table named '{name}'. Tables: {', '.join(t.name for t in schema.tables)}.")
        name, aliases, expression = match.name, [], None
    elif entry.kind == "column":
        table_name, _, column_name = name.partition(".")
        table = next((t for t in schema.tables if t.name.lower() == table_name.strip().lower()), None)
        column = next((c for c in table.columns if c.name.lower() == column_name.strip().lower()), None) if table else None
        if table is None or column is None:
            raise ContextError(f"There is no column '{name}'. Write it as table.column.")
        name, aliases, expression = f"{table.name}.{column.name}", [], None
    elif entry.kind == "term":
        expression = None
    elif expression:
        problem = _check_expression(expression, schema)
        if problem:
            raise ContextError(problem)
    return ContextEntryIn(kind=entry.kind, name=name, aliases=aliases, description=description, expression=expression)


# -------------------------------------------------------------------- import

def _decode(content: bytes) -> str:
    if len(content) > MAX_IMPORT_BYTES:
        raise ContextError(f"The file is larger than {MAX_IMPORT_BYTES // 1000} KB.", 413)
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ContextError("Could not read the file. Save it as UTF-8 and try again.")


def _infer_kind(raw: dict) -> str:
    if raw.get("kind"):
        return str(raw["kind"]).strip().lower()
    if raw.get("expression"):
        return "metric"
    return "column" if "." in str(raw.get("name", "")) else "term"


def _split_aliases(value) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    return [a for a in re.split(r"[;,|]", str(value or "")) if a.strip()]


def _from_csv(text_: str) -> list[dict]:
    try:
        dialect = csv.Sniffer().sniff(text_[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text_), dialect))
    if not rows:
        return []
    header = [_COLUMN_SYNONYMS.get(h.strip().lower(), h.strip().lower()) for h in rows[0]]
    if "name" not in header or "description" not in header:
        raise ContextError("The first row must name the columns, including a name (or term) and a description (or definition).")
    return [dict(zip(header, row)) for row in rows[1:] if any(cell.strip() for cell in row)]


def _from_json(text_: str) -> list[dict]:
    try:
        data = json.loads(text_)
    except json.JSONDecodeError as exc:
        raise ContextError(f"The file is not valid JSON ({exc.msg}).") from exc
    if isinstance(data, dict):
        data = data.get("entries") or data.get("definitions") or data.get("terms")
    if not isinstance(data, list):
        raise ContextError("The JSON must be a list of definitions, or an object with an 'entries' list.")
    return [{_COLUMN_SYNONYMS.get(str(k).lower(), str(k).lower()): v for k, v in item.items()}
            for item in data if isinstance(item, dict)]


_ALSO = re.compile(r"\s*\((?:also|aka|a\.k\.a\.|synonyms?)\s*:?\s*([^)]*)\)", re.I)
_SEPARATOR = re.compile(r"\s+[—–-]\s+|:\s+")


def _from_text(text_: str, warnings: list[str]) -> list[dict]:
    out, skipped = [], 0
    for number, line in enumerate(text_.splitlines(), start=1):
        stripped = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line).strip().replace("**", "").replace("`", "")
        if not stripped or stripped.startswith("#"):
            continue
        # Aliases first: "Revenue (also: sales): money in" has a colon inside the brackets as well.
        aliases: list[str] = []
        also = _ALSO.search(stripped)
        if also:
            aliases = _split_aliases(also.group(1))
            stripped = _ALSO.sub("", stripped, count=1)
        parts = _SEPARATOR.split(stripped, maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            skipped += 1
            if skipped <= MAX_WARNINGS:
                warnings.append(f"Line {number} was skipped: write each definition as 'Name: what it means'.")
            continue
        head, description = parts
        out.append({"name": head.strip(), "description": description.strip(), "aliases": aliases})
    if skipped > MAX_WARNINGS:
        warnings.append(f"{skipped - MAX_WARNINGS} more lines were skipped.")
    return out


def parse_import(filename: str, content: bytes) -> ContextImportPreview:
    """Read a data dictionary / glossary file (.csv, .json, .md or .txt) into definitions."""
    text_ = _decode(content)
    extension = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    warnings: list[str] = []
    if extension == "csv":
        raw = _from_csv(text_)
    elif extension == "json":
        raw = _from_json(text_)
    elif extension in ("md", "markdown", "txt"):
        raw = _from_text(text_, warnings)
    else:
        raise ContextError("Upload a .csv, .json, .md or .txt file.", 415)

    entries: list[ContextEntryIn] = []
    for number, item in enumerate(raw, start=1):
        if len(entries) >= MAX_ENTRIES:
            warnings.append(f"Only the first {MAX_ENTRIES} definitions were read.")
            break
        try:
            entries.append(ContextEntryIn(
                kind=_infer_kind(item),  # type: ignore[arg-type]
                name=clean(item.get("name", ""), 400), aliases=[clean(a, 400) for a in _split_aliases(item.get("aliases"))][:5],
                description=clean(item.get("description", ""), 5000),
                expression=clean(item["expression"], 5000) if item.get("expression") else None))
        except ValidationError as exc:
            if len(warnings) < MAX_WARNINGS:
                first = exc.errors()[0]
                warnings.append(f"Entry {number} was skipped: {'.'.join(str(p) for p in first['loc'])} {first['msg'].lower()}.")
    if not entries:
        raise ContextError("No definitions were found in the file.")
    return ContextImportPreview(entries=entries, warnings=warnings)


def import_entries(workspace_id: str, datasource_id: str, entries: list[ContextEntryIn],
                   schema: SchemaInfo, mode: str) -> ContextImportResult:
    created = updated = skipped = 0
    errors: list[str] = []
    for entry in entries:
        try:
            clean_entry = normalise(entry, schema)
        except ContextError as exc:
            errors.append(f"{entry.name}: {exc.message}")
            continue
        with closing(registry.connect()) as conn:
            existing = _find_same(conn, workspace_id, datasource_id, clean_entry.kind, clean_entry.name)
        try:
            if existing and mode == "replace":
                update_entry(workspace_id, datasource_id, existing, clean_entry)
                updated += 1
            elif existing:
                skipped += 1
            else:
                create_entry(workspace_id, datasource_id, clean_entry)
                created += 1
        except ContextError as exc:
            errors.append(f"{entry.name}: {exc.message}")
    return ContextImportResult(created=created, updated=updated, skipped=skipped, errors=errors[:MAX_WARNINGS])


# ------------------------------------------------------------------ examples

SAMPLE_EXAMPLES = [
    ContextEntryIn(kind="metric", name="Revenue", aliases=["sales", "turnover"],
                   description="Money earned from orders: quantity multiplied by the product price. "
                               "Cancelled and returned orders do not count.",
                   expression="SUM(CASE WHEN orders.status = 'completed' THEN orders.quantity * products.price ELSE 0 END)"),
    ContextEntryIn(kind="metric", name="Profit", aliases=["margin"],
                   description="Revenue minus the product cost, for completed orders only.",
                   expression="SUM(CASE WHEN orders.status = 'completed' THEN orders.quantity * (products.price - products.cost) ELSE 0 END)"),
    ContextEntryIn(kind="term", name="Best-performing product", aliases=["best product", "top product"],
                   description="The product with the highest revenue, as defined for Revenue."),
    ContextEntryIn(kind="term", name="Active customer",
                   description="A customer with at least one completed order in the period being asked about."),
    ContextEntryIn(kind="column", name="orders.status",
                   description="One of completed, cancelled or returned. Only completed orders count as sales."),
]


def add_examples(workspace_id: str, datasource_id: str, schema: SchemaInfo) -> ContextImportResult:
    return import_entries(workspace_id, datasource_id, SAMPLE_EXAMPLES, schema, "skip")


# ----------------------------------------------------------------- selection

class ContextSelection:
    """The definitions and notes that apply to one question."""

    def __init__(self, definitions=None, table_notes=None, column_notes=None):
        self.definitions: list[ContextEntry] = definitions or []
        self.table_notes: dict[str, str] = table_notes or {}
        self.column_notes: dict[str, str] = column_notes or {}

    @property
    def names(self) -> list[str]:
        return [d.name for d in self.definitions]

    def __bool__(self) -> bool:
        return bool(self.definitions or self.table_notes or self.column_notes)


def _words(text_: str) -> str:
    return " " + " ".join(re.findall(r"[a-z0-9]+", text_.lower())) + " "


def _matches(entry: ContextEntry, text_tokens: set[str], text_words: str) -> int:
    """How strongly the question refers to this definition (0 = not at all): the words matched."""
    best = 0
    for phrase in [entry.name, *entry.aliases]:
        phrase_tokens = tokens(phrase)
        phrase_words = _words(phrase)
        if phrase_words.strip() and phrase_words in text_words:
            best = max(best, len(phrase_words.split()))
        elif phrase_tokens and phrase_tokens <= text_tokens:
            best = max(best, len(phrase_tokens))
    return best


def select(entries: list[ContextEntry], text_: str, linked: LinkedSchema | None = None) -> ContextSelection:
    """Pick the metric and term definitions that the text refers to, and the notes for the tables and
    columns being used. Longer, more specific matches come first, and the total size is capped."""
    if not entries:
        return ContextSelection()
    text_tokens, text_words = tokens(text_), _words(text_)

    scored = [(score, e) for e in entries if e.kind in ("metric", "term")
              and (score := _matches(e, text_tokens, text_words)) > 0]
    scored.sort(key=lambda pair: (-pair[0], pair[1].name.lower()))
    chosen, used = [], 0

    def take(entry: ContextEntry) -> bool:
        nonlocal used
        size = len(entry.description) + len(entry.expression or "") + len(entry.name) + 20
        if len(chosen) >= MAX_SELECTED or used + size > MAX_SELECTED_CHARS:
            return False
        chosen.append(entry)
        used += size
        return True

    for _, entry in scored:
        if not take(entry):
            break
    # A definition can build on another ("the product with the highest Revenue"). Bring those along, so the
    # model uses the real formula instead of improvising one. Only a definition's own name counts as a mention.
    pool = [e for e in entries if e.kind in ("metric", "term")]
    for entry in chosen:                                  # `chosen` grows while this loop runs, so chains resolve
        words = _words(entry.description)
        for other in pool:
            if other not in chosen and _words(other.name).strip() and _words(other.name) in words:
                take(other)

    table_notes: dict[str, str] = {}
    column_notes: dict[str, str] = {}
    if linked is not None:
        shown = {t.name.lower(): {c.name.lower() for c in t.columns} for t in linked.tables}
        for e in entries:
            if e.kind == "table" and e.name.lower() in shown:
                table_notes[e.name.lower()] = e.description[:MAX_NOTE_CHARS]
            elif e.kind == "column":
                table_name, _, column_name = e.name.lower().partition(".")
                if column_name in shown.get(table_name, ()):
                    column_notes[e.name.lower()] = e.description[:MAX_NOTE_CHARS]
    return ContextSelection(chosen, table_notes, column_notes)
