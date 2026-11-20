from fastapi import APIRouter, Depends, HTTPException

from app.core.workspace import get_probe_workspace_id
from app.schemas import ConnectionReport, ConnectRequest, ConnectResponse
from app.services import database_service
from app.services.database_service import DatabaseError

router = APIRouter(prefix="/api/database", tags=["database"])


@router.post("/test", response_model=ConnectionReport)
def test_connection(req: ConnectRequest, workspace_id: str = Depends(get_probe_workspace_id)):
    """Check a PostgreSQL connection and list what the account can see. Nothing is saved."""
    try:
        return database_service.test_connection(req)
    except DatabaseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/connect", response_model=ConnectResponse, status_code=201)
def connect(req: ConnectRequest, workspace_id: str = Depends(get_probe_workspace_id)):
    """Connect a PostgreSQL database. The password is stored encrypted and never returned."""
    try:
        return database_service.connect(workspace_id, req)
    except DatabaseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
