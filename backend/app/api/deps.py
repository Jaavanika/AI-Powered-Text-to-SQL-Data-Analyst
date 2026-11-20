from fastapi import HTTPException

from app.schemas import DataSource
from app.services import connectors
from app.services.connectors import Connector


def resolve_source(datasource_id: str, workspace_id: str) -> tuple[DataSource, Connector]:
    """The data source and a connector for it, or a 404 if it is not visible to this workspace."""
    found = connectors.resolve(datasource_id, workspace_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Data source not found.")
    return found
