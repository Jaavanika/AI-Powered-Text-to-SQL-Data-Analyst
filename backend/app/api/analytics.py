"""Chart and summary endpoints that work on a result the client already has.

They never touch a data source: the caller sends the columns and rows it received from a query.
That lets the UI switch chart types instantly, and fetch the (slower) summary separately.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.api.sql import llm_dependency
from app.config import get_settings
from app.core.workspace import get_llm_workspace_id, get_workspace_id
from app.schemas import ChartType, ResultAnalysis
from app.services import conversation_service
from app.services.llm_service import LLMClient
from app.services.result_analysis import profile_result
from app.services.result_summary import summarize
from app.services.visualization import select_chart

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

MAX_COLUMNS = 50
HARD_MAX_ROWS = 5000

Cell = str | int | float | bool | None


class ResultPayload(BaseModel):
    columns: list[str] = Field(min_length=1, max_length=MAX_COLUMNS)
    rows: list[list[Cell]] = Field(max_length=HARD_MAX_ROWS)
    truncated: bool = False
    question: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _shape(self):
        limit = max(get_settings().query_max_rows, 1)
        if len(self.rows) > limit:
            raise ValueError(f"At most {limit} rows are accepted.")
        width = len(self.columns)
        if any(len(row) != width for row in self.rows):
            raise ValueError("Every row must have one value per column.")
        return self


class ChartRequest(ResultPayload):
    chart_type: ChartType | None = None


class SummaryRequest(ResultPayload):
    # When given, the summary is also stored on that conversation turn.
    turn_id: str | None = None


class SummaryResponse(BaseModel):
    summary: str
    source: Literal["llm", "template"]


@router.post("/chart", response_model=ResultAnalysis)
def chart(req: ChartRequest, workspace_id: str = Depends(get_workspace_id)):
    """Profile a result and choose a chart for it. `chart_type` overrides the automatic choice when feasible."""
    profile = profile_result(req.columns, req.rows, req.truncated)
    return ResultAnalysis(
        profile=profile, chart=select_chart(profile, req.columns, req.rows, req.question, req.chart_type))


@router.post("/summary", response_model=SummaryResponse)
def summary(
    req: SummaryRequest,
    workspace_id: str = Depends(get_llm_workspace_id),
    llm: LLMClient = Depends(llm_dependency),
):
    """A natural-language summary of a result, checked so that it quotes no figure the data lacks."""
    if req.turn_id and not conversation_service.turn_belongs_to(req.turn_id, workspace_id):
        raise HTTPException(status_code=404, detail="Turn not found.")
    profile = profile_result(req.columns, req.rows, req.truncated)
    result = summarize(llm, req.question or "", req.columns, req.rows, profile)
    if req.turn_id:
        conversation_service.set_summary(req.turn_id, workspace_id, result.text)
    return SummaryResponse(summary=result.text, source=result.source)
