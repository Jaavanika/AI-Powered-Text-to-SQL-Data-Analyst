from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder

from app.api.deps import resolve_source
from app.core.workspace import get_workspace_id
from app.schemas import DataSource, TablePreview
from app.services import blocked_service, connectors, database_service, registry
from app.services.database_service import DatabaseError

router = APIRouter(prefix="/api/datasources", tags=["datasources"])


def _get_or_404(datasource_id: str, workspace_id: str) -> DataSource:
    datasource = registry.get_datasource(datasource_id, workspace_id)
    if datasource is None:
        raise HTTPException(status_code=404, detail="Data source not found.")
    return datasource


@router.get("", response_model=list[DataSource])
def list_datasources(workspace_id: str = Depends(get_workspace_id)):
    """Everything the user can ask about: the shared sample, their uploads and their connected databases."""
    return registry.list_datasources(workspace_id)


@router.get("/{datasource_id}", response_model=DataSource)
def get_datasource(datasource_id: str, workspace_id: str = Depends(get_workspace_id)):
    """One data source with its tables and columns."""
    return _get_or_404(datasource_id, workspace_id)


@router.get("/{datasource_id}/tables/{table}/preview", response_model=TablePreview)
def preview_table(
    datasource_id: str,
    table: str,
    limit: int = Query(20, ge=1, le=100),
    workspace_id: str = Depends(get_workspace_id),
):
    """The first rows of a table or view, leaving out blocked columns. A blocked table cannot be previewed."""
    datasource, connector = resolve_source(datasource_id, workspace_id)
    # Only tables listed in the registry are queried; the name is never taken on trust.
    info = next((t for t in datasource.tables if t.name == table), None)
    if info is None:
        raise HTTPException(status_code=404, detail="Table not found.")
    # A preview reads the table directly, so it has to honour what is blocked as well.
    blocked = blocked_service.effective(workspace_id, datasource_id)
    if blocked_service.is_table_blocked(info.name, blocked):
        raise HTTPException(status_code=403, detail="This table is blocked, so it cannot be previewed.")
    hidden = {c.name for c in info.columns if (info.name.lower(), c.name.lower()) in blocked.columns}
    return TablePreview(
        table=info.name,
        row_count=info.row_count,
        columns=[c for c in info.columns if c.name not in hidden],
        rows=[{k: v for k, v in row.items() if k not in hidden}
              for row in jsonable_encoder(connector.preview(info.name, limit))],
    )


@router.post("/{datasource_id}/refresh", response_model=DataSource)
def refresh_datasource(datasource_id: str, workspace_id: str = Depends(get_workspace_id)):
    """Re-read the tables of an external database. Uploads and the sample do not change, so they are returned as is."""
    datasource, connector = resolve_source(datasource_id, workspace_id)
    try:
        return database_service.refresh(datasource, connector, workspace_id)
    except DatabaseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.delete("/{datasource_id}", status_code=204)
def delete_datasource(datasource_id: str, workspace_id: str = Depends(get_workspace_id)):
    """Delete an upload or disconnect a database, with its conversations, definitions and blocked columns. The sample cannot be deleted."""
    datasource = _get_or_404(datasource_id, workspace_id)
    if datasource.kind == "sample":
        raise HTTPException(status_code=403, detail="The sample database cannot be deleted.")
    registry.delete_datasource(datasource_id, workspace_id)
    connectors.forget(datasource_id)
