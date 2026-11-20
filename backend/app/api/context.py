from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.deps import resolve_source
from app.core.workspace import get_workspace_id
from app.schemas import (
    ContextEntry,
    ContextEntryIn,
    ContextImportPreview,
    ContextImportRequest,
    ContextImportResult,
)
from app.services import blocked_service, context_service, schema_service
from app.services.context_service import MAX_IMPORT_BYTES, ContextError

router = APIRouter(prefix="/api/context/{datasource_id}", tags=["context"])


def _fail(exc: ContextError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


def _schema(datasource_id: str, workspace_id: str):
    datasource, connector = resolve_source(datasource_id, workspace_id)
    # Definitions may only use what this workspace may read, so a formula cannot lean on a blocked column.
    schema = schema_service.build_schema(datasource, connector)
    return datasource, blocked_service.visible(schema, blocked_service.effective(workspace_id, datasource_id))


@router.get("", response_model=list[ContextEntry])
def list_entries(datasource_id: str, workspace_id: str = Depends(get_workspace_id)):
    """The business definitions and notes for a data source."""
    resolve_source(datasource_id, workspace_id)
    return context_service.list_entries(workspace_id, datasource_id)


@router.post("", response_model=ContextEntry, status_code=201)
def create_entry(datasource_id: str, body: ContextEntryIn, workspace_id: str = Depends(get_workspace_id)):
    """Add a definition. It is checked against the schema, so a typo in a table, column or formula is caught now."""
    _, schema = _schema(datasource_id, workspace_id)
    try:
        return context_service.create_entry(workspace_id, datasource_id, context_service.normalise(body, schema))
    except ContextError as exc:
        raise _fail(exc) from exc


@router.put("/{entry_id}", response_model=ContextEntry)
def update_entry(datasource_id: str, entry_id: str, body: ContextEntryIn,
                 workspace_id: str = Depends(get_workspace_id)):
    """Change a definition. It is checked against the schema again."""
    _, schema = _schema(datasource_id, workspace_id)
    try:
        return context_service.update_entry(
            workspace_id, datasource_id, entry_id, context_service.normalise(body, schema))
    except ContextError as exc:
        raise _fail(exc) from exc


@router.delete("/{entry_id}", status_code=204)
def delete_entry(datasource_id: str, entry_id: str, workspace_id: str = Depends(get_workspace_id)):
    """Delete a definition."""
    resolve_source(datasource_id, workspace_id)
    if not context_service.delete_entry(workspace_id, datasource_id, entry_id):
        raise HTTPException(status_code=404, detail="Definition not found.")


@router.post("/import/preview", response_model=ContextImportPreview)
async def preview_import(datasource_id: str, file: UploadFile = File(...),
                         workspace_id: str = Depends(get_workspace_id)):
    """Read a glossary or data dictionary (.csv, .json, .md, .txt) and show what would be imported. Saves nothing."""
    resolve_source(datasource_id, workspace_id)
    content = await file.read(MAX_IMPORT_BYTES + 1)
    try:
        return context_service.parse_import(file.filename or "", content)
    except ContextError as exc:
        raise _fail(exc) from exc


@router.post("/import", response_model=ContextImportResult)
def import_entries(datasource_id: str, body: ContextImportRequest, workspace_id: str = Depends(get_workspace_id)):
    """Save previewed definitions. Each one is checked against the schema; problems are reported per entry."""
    _, schema = _schema(datasource_id, workspace_id)
    return context_service.import_entries(workspace_id, datasource_id, body.entries, schema, body.mode)


@router.post("/examples", response_model=ContextImportResult)
def add_examples(datasource_id: str, workspace_id: str = Depends(get_workspace_id)):
    """Add example definitions (revenue, profit, ...) to the sample database, to see how definitions work."""
    datasource, schema = _schema(datasource_id, workspace_id)
    if datasource.kind != "sample":
        raise HTTPException(status_code=400, detail="Examples are only available for the sample database.")
    return context_service.add_examples(workspace_id, datasource_id, schema)
