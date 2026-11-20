"""Registry of data sources, stored in a small SQLite file under DATA_DIR.

Each data source maps to one SQLite database file. File paths are always derived
from the data source id, never from user input.
"""

import json
import shutil
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.schemas import ConnectionInfo, DataSource, TableInfo

SAMPLE_ID = "sample"


def data_dir() -> Path:
    path = get_settings().data_dir.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def workspace_dir(workspace_id: str) -> Path:
    path = data_dir() / "workspaces" / workspace_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def staging_dir(workspace_id: str) -> Path:
    path = workspace_dir(workspace_id) / "staging"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sample_db_path() -> Path:
    path = data_dir() / "sample"
    path.mkdir(parents=True, exist_ok=True)
    return path / "sample.sqlite"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(data_dir() / "registry.sqlite")
    conn.row_factory = sqlite3.Row
    return conn


def connect() -> sqlite3.Connection:
    """Connection to the registry file, for other services that store their data in it."""
    return _connect()


def init_registry() -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS datasources (
                id TEXT PRIMARY KEY,
                workspace_id TEXT,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                db_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                tables_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS relationships (
                id TEXT PRIMARY KEY,
                datasource_id TEXT NOT NULL,
                from_table TEXT NOT NULL,
                from_column TEXT NOT NULL,
                to_table TEXT NOT NULL,
                to_column TEXT NOT NULL,
                cardinality TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                datasource_id TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS turns (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                question TEXT NOT NULL,
                status TEXT NOT NULL,
                sql TEXT,
                explanation TEXT NOT NULL DEFAULT '',
                tables_json TEXT NOT NULL DEFAULT '[]',
                clarification TEXT,
                options_json TEXT NOT NULL DEFAULT '[]',
                columns_json TEXT NOT NULL DEFAULT '[]',
                row_count INTEGER,
                error TEXT,
                summary TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        # Registries created by earlier versions lack newer columns; CREATE IF NOT EXISTS leaves them as they are.
        turn_columns = {row[1] for row in conn.execute("PRAGMA table_info(turns)")}
        if "summary" not in turn_columns:
            conn.execute("ALTER TABLE turns ADD COLUMN summary TEXT")
        source_columns = {row[1] for row in conn.execute("PRAGMA table_info(datasources)")}
        if "connection_json" not in source_columns:
            conn.execute("ALTER TABLE datasources ADD COLUMN connection_json TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS turns_by_conversation ON turns (conversation_id, seq)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS turn_results (
                turn_id TEXT PRIMARY KEY,
                columns_json TEXT NOT NULL,
                rows_json TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                truncated INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS context_entries (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                datasource_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                aliases TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL,
                expression TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS context_by_source ON context_entries (workspace_id, datasource_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                username_key TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS sessions_by_user ON sessions (user_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blocked_columns (
                workspace_id TEXT NOT NULL,
                datasource_id TEXT NOT NULL,
                entry TEXT NOT NULL,
                PRIMARY KEY (workspace_id, datasource_id, entry)
            )
            """
        )


def _connection_info(row: sqlite3.Row) -> ConnectionInfo | None:
    raw = row["connection_json"]
    if not raw:
        return None
    stored = json.loads(raw)
    return ConnectionInfo(**{k: v for k, v in stored.items() if k in ConnectionInfo.model_fields})


def _row_to_model(row: sqlite3.Row) -> DataSource:
    external = row["kind"] == "postgres"
    return DataSource(
        id=row["id"],
        name=row["name"],
        kind=row["kind"],
        dialect="postgres" if external else "sqlite",
        # The sample and external databases are never written to by this application.
        read_only=row["kind"] in ("sample", "postgres"),
        created_at=row["created_at"],
        tables=[TableInfo(**t) for t in json.loads(row["tables_json"])],
        connection=_connection_info(row),
    )


def register_sample(tables: list[TableInfo]) -> DataSource:
    payload = json.dumps([t.model_dump() for t in tables])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(_connect()) as conn, conn:
        conn.execute(
            """
            INSERT INTO datasources (id, workspace_id, name, kind, db_path, created_at, tables_json)
            VALUES (?, NULL, 'Sample business database', 'sample', ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET db_path = excluded.db_path, tables_json = excluded.tables_json
            """,
            (SAMPLE_ID, str(sample_db_path()), now, payload),
        )
    return get_datasource(SAMPLE_ID, workspace_id=None)  # type: ignore[return-value]


def new_upload_datasource_path(workspace_id: str, datasource_id: str) -> Path:
    return workspace_dir(workspace_id) / f"ds_{datasource_id}.sqlite"


def create_upload_datasource(workspace_id: str, name: str, tables: list[TableInfo]) -> DataSource:
    datasource_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = new_upload_datasource_path(workspace_id, datasource_id)
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO datasources (id, workspace_id, name, kind, db_path, created_at, tables_json)"
            " VALUES (?, ?, ?, 'upload', ?, ?, ?)",
            (datasource_id, workspace_id, name, str(path), now,
             json.dumps([t.model_dump() for t in tables])),
        )
    return get_datasource(datasource_id, workspace_id)  # type: ignore[return-value]


def create_postgres_datasource(workspace_id: str, name: str, connection: dict, tables: list[TableInfo]) -> DataSource:
    """Register an external PostgreSQL database. `connection` carries the already encrypted password."""
    datasource_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO datasources (id, workspace_id, name, kind, db_path, created_at, tables_json, connection_json)"
            " VALUES (?, ?, ?, 'postgres', '', ?, ?, ?)",
            (datasource_id, workspace_id, name, now,
             json.dumps([t.model_dump() for t in tables]), json.dumps(connection)),
        )
    return get_datasource(datasource_id, workspace_id)  # type: ignore[return-value]


def count_connections(workspace_id: str) -> int:
    """How many external databases (not uploads or the shared sample) this workspace has connected."""
    with closing(_connect()) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM datasources WHERE workspace_id = ? AND kind NOT IN ('upload', 'sample')",
            (workspace_id,)).fetchone()[0]


def get_stored_connection(datasource_id: str, workspace_id: str | None) -> dict | None:
    """The stored connection settings, including the encrypted password. Never sent to clients."""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT connection_json FROM datasources WHERE id = ? AND kind = 'postgres'"
            " AND (workspace_id IS NULL OR workspace_id = ?)",
            (datasource_id, workspace_id),
        ).fetchone()
    return json.loads(row["connection_json"]) if row and row["connection_json"] else None


def update_tables(datasource_id: str, tables: list[TableInfo]) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "UPDATE datasources SET tables_json = ? WHERE id = ?",
            (json.dumps([t.model_dump() for t in tables]), datasource_id),
        )


def get_datasource(datasource_id: str, workspace_id: str | None) -> DataSource | None:
    """Return the data source if it is the shared sample or belongs to the workspace."""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT * FROM datasources WHERE id = ? AND (workspace_id IS NULL OR workspace_id = ?)",
            (datasource_id, workspace_id),
        ).fetchone()
    return _row_to_model(row) if row else None


def get_db_path(datasource_id: str, workspace_id: str | None) -> Path | None:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT db_path FROM datasources WHERE id = ? AND (workspace_id IS NULL OR workspace_id = ?)",
            (datasource_id, workspace_id),
        ).fetchone()
    return Path(row["db_path"]) if row else None


def list_datasources(workspace_id: str) -> list[DataSource]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM datasources WHERE workspace_id IS NULL OR workspace_id = ?"
            " ORDER BY (workspace_id IS NULL) DESC, created_at",
            (workspace_id,),
        ).fetchall()
    return [_row_to_model(r) for r in rows]


def delete_datasource(datasource_id: str, workspace_id: str) -> bool:
    """Delete a workspace-owned data source. The shared sample cannot be deleted."""
    path = None
    with closing(_connect()) as conn, conn:
        row = conn.execute(
            "SELECT db_path, kind FROM datasources WHERE id = ? AND workspace_id = ?",
            (datasource_id, workspace_id),
        ).fetchone()
        if not row:
            return False
        # External databases have no file of ours to remove; deleting the row removes the credentials.
        path = Path(row["db_path"]) if row["kind"] == "upload" and row["db_path"] else None
        conn.execute("DELETE FROM datasources WHERE id = ?", (datasource_id,))
        conn.execute("DELETE FROM relationships WHERE datasource_id = ?", (datasource_id,))
        conn.execute(
            "DELETE FROM turn_results WHERE turn_id IN (SELECT t.id FROM turns t JOIN conversations c "
            "ON c.id = t.conversation_id WHERE c.datasource_id = ?)", (datasource_id,))
        conn.execute(
            "DELETE FROM turns WHERE conversation_id IN "
            "(SELECT id FROM conversations WHERE datasource_id = ?)", (datasource_id,))
        conn.execute("DELETE FROM conversations WHERE datasource_id = ?", (datasource_id,))
        conn.execute("DELETE FROM context_entries WHERE datasource_id = ?", (datasource_id,))
        conn.execute("DELETE FROM blocked_columns WHERE datasource_id = ?", (datasource_id,))
    if path is not None:
        path.unlink(missing_ok=True)
    return True


# ---------------------------------------------------- user-confirmed relationships

def add_user_relationship(
    datasource_id: str, from_table: str, from_column: str,
    to_table: str, to_column: str, cardinality: str,
) -> str:
    relationship_id = f"user:{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO relationships VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (relationship_id, datasource_id, from_table, from_column, to_table, to_column,
             cardinality, now),
        )
    return relationship_id


def list_user_relationships(datasource_id: str) -> list[sqlite3.Row]:
    with closing(_connect()) as conn:
        return conn.execute(
            "SELECT * FROM relationships WHERE datasource_id = ? ORDER BY created_at, id",
            (datasource_id,),
        ).fetchall()


def delete_user_relationship(datasource_id: str, relationship_id: str) -> bool:
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "DELETE FROM relationships WHERE datasource_id = ? AND id = ?",
            (datasource_id, relationship_id),
        )
    return cur.rowcount > 0


def remove_staging(workspace_id: str, upload_id: str) -> None:
    shutil.rmtree(staging_dir(workspace_id) / upload_id, ignore_errors=True)
