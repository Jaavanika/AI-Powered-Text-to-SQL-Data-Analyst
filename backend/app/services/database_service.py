"""Connecting an external PostgreSQL database: check it, then register it.

Policy, in the order it is applied:
  1. the host must be on the administrator's allowlist;
  2. the server must be reachable with the given credentials (errors are translated, never echoed raw);
  3. superuser accounts are refused, because they can switch off the read-only protections;
  4. the chosen schema must exist;
  5. anything risky about the account (write access, powerful roles, unencrypted traffic) is reported
     as a warning, so the person connecting can switch to a safer account.
The password is encrypted before it is stored and is never returned to any client.
"""

from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.core import secrets
from app.core.hosts import HostNotAllowed, check_host_allowed
from app.schemas import ConnectionReport, ConnectRequest, ConnectResponse, DataSource
from app.services import registry
from app.services.connectors import Connector
from app.services.postgres_connector import (
    PgParams, create_pg_engine, dispose_engine, friendly_error, inspect_server,
)

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


class DatabaseError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _params(req: ConnectRequest) -> PgParams:
    return PgParams(
        host=req.host.strip(), port=req.port, database=req.database.strip(), username=req.username.strip(),
        password=req.password.get_secret_value(), schema=req.schema_name.strip(), sslmode=req.sslmode)


def _warnings(facts, params: PgParams) -> list[str]:
    warnings = list(facts.role_warnings)
    if facts.writable_tables:
        shown = ", ".join(facts.writable_tables[:3]) + ("…" if len(facts.writable_tables) > 3 else "")
        warnings.insert(0, (
            f"This account can modify data in {len(facts.writable_tables)} table(s) ({shown}). The assistant only "
            "runs read-only queries, but a dedicated read-only account is strongly recommended."))
    if params.sslmode in ("disable", "allow", "prefer") and params.host.lower() not in _LOOPBACK:
        warnings.append("Traffic to this server may not be encrypted. Use SSL mode 'require' or stronger.")
    if not facts.tables:
        warnings.append(f"The schema '{params.schema}' has no tables this account can see.")
    return warnings


def _report(facts, params: PgParams) -> ConnectionReport:
    settings = get_settings()
    if facts.is_superuser and not settings.allow_superuser_connections:
        raise DatabaseError(
            "This is a superuser account, which can switch off the read-only protections. "
            "Connect with a dedicated read-only role instead.", 403)
    if not facts.schema_exists:
        raise DatabaseError(f"The schema '{params.schema}' does not exist in database '{params.database}'.", 404)
    return ConnectionReport(
        server_version=facts.version, database=params.database, schema_name=params.schema,
        username=facts.user, tables=facts.tables, warnings=_warnings(facts, params))


def test_connection(req: ConnectRequest) -> ConnectionReport:
    """Check that the database can be reached and used, without saving anything."""
    try:
        check_host_allowed(req.host, get_settings().allowed_db_hosts)
    except HostNotAllowed as exc:
        raise DatabaseError(exc.message, 403) from exc
    params = _params(req)
    try:
        engine = create_pg_engine(params, pooled=False)
    except HostNotAllowed as exc:                 # the name resolved somewhere that may not be used, or nowhere
        raise DatabaseError(exc.message, exc.status_code) from exc
    try:
        facts = inspect_server(engine, params.schema)
    except SQLAlchemyError as exc:
        raise DatabaseError(friendly_error(exc, params)) from exc
    finally:
        engine.dispose()
    return _report(facts, params)


def _check_room(workspace_id: str) -> None:
    cap = get_settings().max_connections_per_workspace
    have = registry.count_connections(workspace_id)
    if cap > 0 and have >= cap:
        raise DatabaseError(
            f"You already have {have} connected database{'s' if have != 1 else ''}, the limit of {cap}. "
            "Disconnect one first.", 409)


def connect(workspace_id: str, req: ConnectRequest) -> ConnectResponse:
    """Test the connection and, if it is acceptable, save it for this workspace."""
    _check_room(workspace_id)                    # before reaching out to the server at all
    report = test_connection(req)
    password = req.password.get_secret_value()
    stored = {
        "host": req.host.strip(), "port": req.port, "database": req.database.strip(),
        "username": req.username.strip(), "schema_name": req.schema_name.strip(), "sslmode": req.sslmode,
        "password_enc": secrets.encrypt(password) if password else "",
    }
    name = (req.name or f"{req.database.strip()} on {req.host.strip()}").strip()[:100]
    datasource = registry.create_postgres_datasource(workspace_id, name, stored, report.tables)
    return ConnectResponse(datasource=datasource, warnings=report.warnings)


def refresh(datasource: DataSource, connector: Connector, workspace_id: str) -> DataSource:
    """Re-read the tables of an external database (they change over time) and update the registry."""
    if datasource.kind != "postgres":
        return datasource
    try:
        with connector.engine() as engine:
            facts = inspect_server(engine, connector.schema_name or "public")
    except SQLAlchemyError as exc:
        dispose_engine(datasource.id)         # the pooled connections may be stale; start fresh next time
        raise DatabaseError(friendly_error(exc, connector.params), 502) from exc   # type: ignore[attr-defined]
    if not facts.schema_exists:
        raise DatabaseError(f"The schema '{connector.schema_name}' no longer exists.", 404)
    registry.update_tables(datasource.id, facts.tables)
    return registry.get_datasource(datasource.id, workspace_id) or datasource
