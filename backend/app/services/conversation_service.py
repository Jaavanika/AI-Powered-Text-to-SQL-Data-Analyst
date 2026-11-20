"""Conversation storage: the turns that give follow-up questions their context, and the query history.

Always kept: questions, the SQL that answered them, result column names, row counts and summaries.
Result rows are sensitive, so they are kept only when HISTORY_RETAIN_ROWS is above 0 (and then only
that many rows per answer). Everything older than HISTORY_RETENTION_DAYS is removed.
"""

import json
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.schemas import ConversationSummary, HistoryItem, Turn
from app.services import registry
from app.services.query_execution import QueryResult
from app.services.query_pipeline import QueryResponse

HISTORY_TURNS = 6
TITLE_LENGTH = 80
MAX_STORED_RESULT_BYTES = 2_000_000


def _now() -> str:
    # Microseconds, so conversations active within the same second still sort correctly.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


_HAS_RESULT = "EXISTS (SELECT 1 FROM turn_results r WHERE r.turn_id = t.id) AS has_result"
_TURN_SELECT = f"SELECT t.*, {_HAS_RESULT} FROM turns t"


def _turn_fields(row) -> dict:
    return dict(
        id=row["id"], seq=row["seq"], question=row["question"], status=row["status"],
        sql=row["sql"], explanation=row["explanation"], tables_used=json.loads(row["tables_json"]),
        clarification=row["clarification"], options=json.loads(row["options_json"]),
        columns=json.loads(row["columns_json"]), row_count=row["row_count"], error=row["error"],
        summary=row["summary"], has_result=bool(row["has_result"]), created_at=row["created_at"],
    )


def _turn(row) -> Turn:
    return Turn(**_turn_fields(row))


def _summary(row) -> ConversationSummary:
    return ConversationSummary(
        id=row["id"], datasource_id=row["datasource_id"], title=row["title"],
        created_at=row["created_at"], updated_at=row["updated_at"], turn_count=row["turn_count"])


_SUMMARY_SQL = (
    "SELECT c.*, (SELECT COUNT(*) FROM turns t WHERE t.conversation_id = c.id) AS turn_count "
    "FROM conversations c")


def create_conversation(workspace_id: str, datasource_id: str, title: str) -> str:
    conversation_id = uuid.uuid4().hex
    now = _now()
    with closing(registry.connect()) as conn, conn:
        conn.execute(
            "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, workspace_id, datasource_id, " ".join(title.split())[:TITLE_LENGTH], now, now))
    return conversation_id


def get_conversation(conversation_id: str, workspace_id: str) -> ConversationSummary | None:
    with closing(registry.connect()) as conn:
        row = conn.execute(
            f"{_SUMMARY_SQL} WHERE c.id = ? AND c.workspace_id = ?", (conversation_id, workspace_id)
        ).fetchone()
    return _summary(row) if row else None


def list_conversations(workspace_id: str, datasource_id: str | None = None) -> list[ConversationSummary]:
    query, params = f"{_SUMMARY_SQL} WHERE c.workspace_id = ?", [workspace_id]
    if datasource_id:
        query += " AND c.datasource_id = ?"
        params.append(datasource_id)
    with closing(registry.connect()) as conn:
        rows = conn.execute(query + " ORDER BY c.updated_at DESC, c.rowid DESC", params).fetchall()
    return [_summary(r) for r in rows]


def list_turns(conversation_id: str) -> list[Turn]:
    with closing(registry.connect()) as conn:
        rows = conn.execute(f"{_TURN_SELECT} WHERE t.conversation_id = ? ORDER BY t.seq", (conversation_id,)).fetchall()
    return [_turn(r) for r in rows]


def recent_history(conversation_id: str, limit: int = HISTORY_TURNS) -> list[Turn]:
    """The most recent answered or clarified turns, oldest first. Failed turns carry no context."""
    usable = [t for t in list_turns(conversation_id) if t.status != "failed"]
    return usable[-limit:]


def _store_result(conn, turn_id: str, result: QueryResult, now: str) -> None:
    """Keep the first HISTORY_RETAIN_ROWS rows of an answer, if the administrator allows it."""
    limit = get_settings().history_retain_rows
    if limit <= 0 or not result.rows:
        return
    rows = result.rows[:limit]
    payload = json.dumps(rows)
    while len(payload) > MAX_STORED_RESULT_BYTES and len(rows) > 1:      # a few huge cells must not bloat the file
        rows = rows[: len(rows) // 2]
        payload = json.dumps(rows)
    conn.execute(
        "INSERT OR REPLACE INTO turn_results (turn_id, columns_json, rows_json, row_count, truncated, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (turn_id, json.dumps(result.columns), payload, len(rows),
         int(result.truncated or len(rows) < result.row_count), now))


def add_turn(conversation_id: str, question: str, response: QueryResponse,
             summary: str | None = None) -> Turn:
    now = _now()
    result = response.result
    with closing(registry.connect()) as conn, conn:
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM turns WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()[0]
        turn_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO turns (id, conversation_id, seq, question, status, sql, explanation, tables_json,"
            " clarification, options_json, columns_json, row_count, error, summary, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (turn_id, conversation_id, seq, question, response.status, response.sql,
             response.explanation, json.dumps(response.tables_used), response.clarification,
             json.dumps(response.options), json.dumps(result.columns if result else []),
             result.row_count if result else None, response.error, summary, now))
        if result is not None and response.status == "ok":
            _store_result(conn, turn_id, result, now)
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
        row = conn.execute(f"{_TURN_SELECT} WHERE t.id = ?", (turn_id,)).fetchone()
    purge_expired()
    return _turn(row)


def get_result(turn_id: str, workspace_id: str) -> QueryResult | None:
    """The rows kept for a turn, if the turn is this workspace's and rows were kept."""
    with closing(registry.connect()) as conn:
        row = conn.execute(
            "SELECT r.* FROM turn_results r JOIN turns t ON t.id = r.turn_id JOIN conversations c "
            "ON c.id = t.conversation_id WHERE r.turn_id = ? AND c.workspace_id = ?",
            (turn_id, workspace_id)).fetchone()
    if row is None:
        return None
    rows = json.loads(row["rows_json"])
    truncated = bool(row["truncated"])
    return QueryResult(
        columns=json.loads(row["columns_json"]), rows=rows, row_count=len(rows), truncated=truncated,
        max_rows=len(rows), elapsed_ms=0,
        notes=[f"Saved result: the first {len(rows)} rows of the answer were kept."] if truncated else [])


def turn_belongs_to(turn_id: str, workspace_id: str) -> bool:
    with closing(registry.connect()) as conn:
        row = conn.execute(
            "SELECT 1 FROM turns WHERE id = ? AND conversation_id IN "
            "(SELECT id FROM conversations WHERE workspace_id = ?)", (turn_id, workspace_id)).fetchone()
    return row is not None


def set_summary(turn_id: str, workspace_id: str, summary: str) -> bool:
    """Attach a summary to a turn, if the turn belongs to this workspace."""
    with closing(registry.connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE turns SET summary = ? WHERE id = ? AND conversation_id IN "
            "(SELECT id FROM conversations WHERE workspace_id = ?)",
            (summary, turn_id, workspace_id))
    return cur.rowcount > 0


# ---------------------------------------------------------------- history

def _like(text: str) -> str:
    """A LIKE pattern that matches `text` literally (so a % or _ typed by the user is not a wildcard)."""
    return "%" + text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search_history(workspace_id: str, datasource_id: str, query: str | None = None, status: str | None = None,
                   limit: int = 30, offset: int = 0) -> tuple[list[HistoryItem], int]:
    """Turns of this workspace's conversations about one data source, newest first, with a total count."""
    where = "c.workspace_id = ? AND c.datasource_id = ?"
    params: list = [workspace_id, datasource_id]
    if status:
        where += " AND t.status = ?"
        params.append(status)
    if query and query.strip():
        pattern = _like(query.strip())
        where += (" AND (lower(t.question) LIKE ? ESCAPE '\\' OR lower(COALESCE(t.sql, '')) LIKE ? ESCAPE '\\'"
                  " OR lower(COALESCE(t.summary, '')) LIKE ? ESCAPE '\\')")
        params += [pattern, pattern, pattern]
    join = "FROM turns t JOIN conversations c ON c.id = t.conversation_id"
    with closing(registry.connect()) as conn:
        total = conn.execute(f"SELECT COUNT(*) {join} WHERE {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT t.*, {_HAS_RESULT}, c.id AS conversation_id, c.title AS conversation_title {join} "
            f"WHERE {where} ORDER BY t.created_at DESC, t.rowid DESC LIMIT ? OFFSET ?",
            [*params, limit, offset]).fetchall()
    items = [HistoryItem(**_turn_fields(r), conversation_id=r["conversation_id"],
                         conversation_title=r["conversation_title"]) for r in rows]
    return items, total


def _delete_turns(conn, where: str, params: list) -> None:
    conn.execute(f"DELETE FROM turn_results WHERE turn_id IN (SELECT t.id FROM turns t WHERE {where})", params)
    conn.execute(f"DELETE FROM turns WHERE id IN (SELECT t.id FROM turns t WHERE {where})", params)


def _delete_empty_conversations(conn) -> None:
    conn.execute("DELETE FROM conversations WHERE id NOT IN (SELECT DISTINCT conversation_id FROM turns)")


def delete_turn(turn_id: str, workspace_id: str) -> bool:
    with closing(registry.connect()) as conn, conn:
        if not conn.execute(
                "SELECT 1 FROM turns WHERE id = ? AND conversation_id IN "
                "(SELECT id FROM conversations WHERE workspace_id = ?)", (turn_id, workspace_id)).fetchone():
            return False
        _delete_turns(conn, "t.id = ?", [turn_id])
        _delete_empty_conversations(conn)
    return True


def delete_history(workspace_id: str, datasource_id: str) -> int:
    """Remove every conversation this workspace has about a data source. Returns how many turns went."""
    with closing(registry.connect()) as conn, conn:
        where = ("t.conversation_id IN (SELECT id FROM conversations WHERE workspace_id = ? AND datasource_id = ?)")
        count = conn.execute(f"SELECT COUNT(*) FROM turns t WHERE {where}", [workspace_id, datasource_id]).fetchone()[0]
        _delete_turns(conn, where, [workspace_id, datasource_id])
        conn.execute("DELETE FROM conversations WHERE workspace_id = ? AND datasource_id = ?",
                     (workspace_id, datasource_id))
    return count


def delete_conversation(conversation_id: str, workspace_id: str) -> bool:
    with closing(registry.connect()) as conn, conn:
        owned = conn.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND workspace_id = ?", (conversation_id, workspace_id)
        ).fetchone()
        if not owned:
            return False
        _delete_turns(conn, "t.conversation_id = ?", [conversation_id])
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    return True


def purge_expired(days: int | None = None) -> int:
    """Delete turns (and their kept rows) older than the retention period. Returns how many were removed."""
    days = get_settings().history_retention_days if days is None else days
    if days <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="microseconds")
    with closing(registry.connect()) as conn, conn:
        count = conn.execute("SELECT COUNT(*) FROM turns WHERE created_at < ?", (cutoff,)).fetchone()[0]
        if count:
            _delete_turns(conn, "t.created_at < ?", [cutoff])
            _delete_empty_conversations(conn)
    return count
