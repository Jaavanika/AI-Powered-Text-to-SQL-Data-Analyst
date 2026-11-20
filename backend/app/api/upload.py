from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.config import get_settings
from app.core.workspace import get_workspace_id
from app.schemas import DataSource, ImportRequest, UploadPreview
from app.services import file_import_service as importer
from app.services.file_import_service import FileImportError

router = APIRouter(prefix="/api/upload", tags=["upload"])


@router.post("/preview", response_model=UploadPreview)
async def preview_upload(
    file: UploadFile = File(...),
    workspace_id: str = Depends(get_workspace_id),
):
    """Stage a CSV/Excel file and return detected sheets, columns, types and a data preview."""
    limit = get_settings().max_upload_mb * 1024 * 1024
    content = await file.read(limit + 1)
    try:
        upload_id, _ = importer.stage_upload(workspace_id, file.filename or "", content)
        return importer.build_preview(workspace_id, upload_id)
    except FileImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/import", response_model=DataSource, status_code=201)
def import_upload(req: ImportRequest, workspace_id: str = Depends(get_workspace_id)):
    """Import selected sheets of a previously previewed upload into a data source."""
    try:
        return importer.import_upload(workspace_id, req)
    except FileImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
