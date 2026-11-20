from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import resolve_source
from app.core.workspace import get_llm_workspace_id, get_workspace_id
from app.schemas import GenerateRequest, GenerateResponse
from app.services import (
    blocked_service,
    context_service,
    query_pipeline,
    schema_linking,
    schema_service,
    sql_generation,
)
from app.services.llm_service import LLMClient, LLMError, get_llm
from app.services.query_execution import ExecutionError, QueryResult
from app.services.query_pipeline import QueryResponse
from app.services.sql_validation import MAX_SQL_LENGTH, ValidationResult, validate_sql

router = APIRouter(prefix="/api/sql", tags=["sql"])

_EXECUTION_STATUS = {"timeout": 408, "denied": 403, "sql": 400, "unavailable": 503}


class SqlRequest(BaseModel):
    datasource_id: str
    sql: str = Field(min_length=1, max_length=MAX_SQL_LENGTH * 2)


class ExecuteResponse(BaseModel):
    validation: ValidationResult
    result: QueryResult


def llm_dependency() -> LLMClient:
    try:
        return get_llm()
    except LLMError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


_resolve = resolve_source


@router.post("/generate", response_model=GenerateResponse)
def generate(
    req: GenerateRequest,
    workspace_id: str = Depends(get_llm_workspace_id),
    llm: LLMClient = Depends(llm_dependency),
):
    """Turn a question into a proposed SQL query. The SQL is not validated or executed here."""
    datasource, connector = _resolve(req.datasource_id, workspace_id)
    schema = schema_service.build_schema(datasource, connector)
    shown = blocked_service.visible(schema, blocked_service.effective(workspace_id, datasource.id))
    values = schema_linking.distinct_values(datasource.id, connector, shown)
    linked = schema_linking.link_schema(shown, req.question, values)
    definitions = context_service.select(context_service.list_entries(workspace_id, datasource.id), req.question, linked)
    try:
        generated = sql_generation.generate_sql(llm, req.question, linked, schema.dialect, definitions)
    except LLMError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return GenerateResponse(
        **generated.model_dump(), question=req.question, dialect=schema.dialect, linked=linked)


@router.post("/query", response_model=QueryResponse)
def query(
    req: GenerateRequest,
    workspace_id: str = Depends(get_llm_workspace_id),
    llm: LLMClient = Depends(llm_dependency),
):
    """Question -> SQL -> validation (with bounded correction) -> read-only execution."""
    datasource, connector = _resolve(req.datasource_id, workspace_id)
    schema = schema_service.build_schema(datasource, connector)
    blocked = blocked_service.effective(workspace_id, datasource.id)
    shown = blocked_service.visible(schema, blocked)
    values = schema_linking.distinct_values(datasource.id, connector, shown)
    linked = schema_linking.link_schema(shown, req.question, values)
    definitions = context_service.select(context_service.list_entries(workspace_id, datasource.id), req.question, linked)
    try:
        return query_pipeline.run_question(
            llm, req.question, datasource, connector, schema, linked, context=definitions, blocked=blocked)
    except LLMError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/validate", response_model=ValidationResult)
def validate(req: SqlRequest, workspace_id: str = Depends(get_workspace_id)):
    """Check a SQL statement against the safety rules and the data source's schema."""
    datasource, connector = _resolve(req.datasource_id, workspace_id)
    schema = schema_service.build_schema(datasource, connector)
    blocked = blocked_service.effective(workspace_id, datasource.id)
    return validate_sql(req.sql, schema, schema.dialect, set(blocked.tables), set(blocked.columns))


@router.post("/execute", response_model=ExecuteResponse)
def execute(req: SqlRequest, workspace_id: str = Depends(get_workspace_id)):
    """Run a read-only query. The SQL is always validated first; invalid SQL is never executed."""
    datasource, connector = _resolve(req.datasource_id, workspace_id)
    schema = schema_service.build_schema(datasource, connector)
    try:
        validation, result = query_pipeline.check_and_execute(
            schema, connector, req.sql, blocked_service.effective(workspace_id, datasource.id))
    except ExecutionError as exc:
        raise HTTPException(status_code=_EXECUTION_STATUS.get(exc.kind, 400), detail=exc.message) from exc
    if not validation.valid or result is None:
        raise HTTPException(
            status_code=422, detail={"message": "The SQL failed validation.", "errors": validation.errors})
    return ExecuteResponse(validation=validation, result=result)
