from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import get_settings
from app.core.workspace import get_workspace_id
from app.schemas import HistoryPage, RetentionInfo
from app.services import conversation_service
from app.services.query_execution import QueryResult

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("", response_model=HistoryPage)
def list_history(
    datasource_id: str,
    q: str | None = Query(None, max_length=200),
    status: Literal["ok", "clarification", "failed"] | None = None,
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    workspace_id: str = Depends(get_workspace_id),
):
    """Questions asked about a data source, newest first. `q` searches the question, SQL and summary."""
    items, total = conversation_service.search_history(workspace_id, datasource_id, q, status, limit, offset)
    settings = get_settings()
    return HistoryPage(items=items, total=total, retention=RetentionInfo(
        retain_rows=settings.history_retain_rows, retention_days=settings.history_retention_days))


@router.delete("", status_code=204)
def clear_history(datasource_id: str, workspace_id: str = Depends(get_workspace_id)):
    """Delete every conversation about a data source, including any rows that were kept."""
    conversation_service.delete_history(workspace_id, datasource_id)


@router.delete("/turns/{turn_id}", status_code=204)
def delete_turn(turn_id: str, workspace_id: str = Depends(get_workspace_id)):
    """Delete one question and any result kept for it."""
    if not conversation_service.delete_turn(turn_id, workspace_id):
        raise HTTPException(status_code=404, detail="Question not found.")


@router.get("/turns/{turn_id}/result", response_model=QueryResult)
def kept_result(turn_id: str, workspace_id: str = Depends(get_workspace_id)):
    """The rows kept for an answer. Only exists when the administrator allows keeping results."""
    result = conversation_service.get_result(turn_id, workspace_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No result was kept for this answer.")
    return result
