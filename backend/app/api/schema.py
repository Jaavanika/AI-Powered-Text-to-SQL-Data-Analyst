from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import resolve_source
from app.core.workspace import get_workspace_id
from app.schemas import (
    DataSource,
    JoinPathRequest,
    JoinPlan,
    Relationship,
    RelationshipCreate,
    RelationshipSuggestion,
    SchemaInfo,
)
from app.services import registry, relationship_service, schema_service
from app.services.relationship_service import RelationshipError

router = APIRouter(prefix="/api/datasources/{datasource_id}", tags=["schema"])


_resolve = resolve_source


@router.get("/schema", response_model=SchemaInfo)
def get_schema(datasource_id: str, workspace_id: str = Depends(get_workspace_id)):
    """Tables, columns, keys and the relationships usable for joins."""
    datasource, connector = _resolve(datasource_id, workspace_id)
    return schema_service.build_schema(datasource, connector)


@router.get("/relationships", response_model=list[Relationship])
def list_relationships(datasource_id: str, workspace_id: str = Depends(get_workspace_id)):
    """The relationships used for joins: declared in the database or confirmed by the user."""
    datasource, connector = _resolve(datasource_id, workspace_id)
    return schema_service.build_schema(datasource, connector).relationships


@router.get("/relationships/suggestions", response_model=list[RelationshipSuggestion])
def suggest_relationships(datasource_id: str, workspace_id: str = Depends(get_workspace_id)):
    """Likely relationships found by comparing column names and values. Not used until confirmed."""
    datasource, connector = _resolve(datasource_id, workspace_id)
    return relationship_service.suggest_relationships(datasource, connector)


@router.post("/relationships", response_model=Relationship, status_code=201)
def confirm_relationship(
    datasource_id: str,
    body: RelationshipCreate,
    workspace_id: str = Depends(get_workspace_id),
):
    """Confirm a suggested relationship so questions may join through it."""
    datasource, connector = _resolve(datasource_id, workspace_id)
    try:
        return relationship_service.add_user_relationship(datasource, connector, body)
    except RelationshipError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.delete("/relationships/{relationship_id}", status_code=204)
def delete_relationship(
    datasource_id: str,
    relationship_id: str,
    workspace_id: str = Depends(get_workspace_id),
):
    """Remove a relationship the user confirmed. Relationships declared in the database cannot be removed."""
    datasource, _ = _resolve(datasource_id, workspace_id)
    if datasource.kind == "sample":
        raise HTTPException(status_code=403, detail="The sample database cannot be modified.")
    if not relationship_id.startswith("user:"):
        raise HTTPException(status_code=403, detail="Only user-confirmed relationships can be removed.")
    if not registry.delete_user_relationship(datasource_id, relationship_id):
        raise HTTPException(status_code=404, detail="Relationship not found.")


@router.post("/join-path", response_model=JoinPlan)
def join_path(
    datasource_id: str,
    body: JoinPathRequest,
    workspace_id: str = Depends(get_workspace_id),
):
    """How to join the given tables using declared or confirmed relationships."""
    datasource, connector = _resolve(datasource_id, workspace_id)
    schema = schema_service.build_schema(datasource, connector)
    try:
        return relationship_service.find_join_plan(schema, body.tables, body.max_hops)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
