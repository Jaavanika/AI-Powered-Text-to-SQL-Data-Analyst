from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import resolve_source
from app.core.workspace import get_workspace_id
from app.schemas import BlockedColumns, BlockedColumnsUpdate
from app.services import blocked_service, schema_service
from app.services.blocked_service import BlockedError

router = APIRouter(prefix="/api/datasources/{datasource_id}/blocked-columns", tags=["blocked columns"])


def _current(workspace_id: str, datasource_id: str) -> BlockedColumns:
    return BlockedColumns(columns=blocked_service.list_entries(workspace_id, datasource_id),
                          locked=blocked_service.global_entries())


@router.get("", response_model=BlockedColumns)
def get_blocked(datasource_id: str, workspace_id: str = Depends(get_workspace_id)):
    """The columns and tables blocked for this user on a data source, and those the administrator blocked for everyone."""
    resolve_source(datasource_id, workspace_id)
    return _current(workspace_id, datasource_id)


@router.put("", response_model=BlockedColumns)
def set_blocked(datasource_id: str, body: BlockedColumnsUpdate, workspace_id: str = Depends(get_workspace_id)):
    """Replace the columns (`table.column`) and tables (`table.*`) that questions may never read."""
    datasource, connector = resolve_source(datasource_id, workspace_id)
    try:
        blocked_service.set_entries(
            workspace_id, datasource_id, body.columns, schema_service.build_schema(datasource, connector))
    except BlockedError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return _current(workspace_id, datasource_id)
