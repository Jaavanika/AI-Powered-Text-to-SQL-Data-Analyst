"""The analyst workflow for one chat message.

    message -> schema linking (using the conversation) -> ambiguity check -> SQL generation
            -> validation (with bounded correction) -> read-only execution

Every message, including follow-ups, goes through the complete pipeline: earlier SQL is only
context for the model and is never re-run without being generated and validated again.
"""

import logging
from collections.abc import Sequence

from app.config import get_settings
from app.schemas import ContextEntry, DataSource, ResultAnalysis, SchemaInfo, Turn
from app.services import blocked_service, context_service, schema_linking
from app.services.ambiguity_service import AmbiguityResult, check_ambiguity, join_ambiguity
from app.services.blocked_service import Blocked
from app.services.connectors import Connector
from app.services.llm_service import LLMClient
from app.services.query_pipeline import QueryResponse, abandon, run_question, start_generation
from app.services.result_analysis import profile_result
from app.services.result_summary import summarize
from app.services.visualization import select_chart

logger = logging.getLogger(__name__)

CONTEXT_QUESTIONS = 2  # earlier questions that also take part in schema linking


class ChatResponse(QueryResponse):
    conversation_id: str
    turn_id: str
    analysis: ResultAnalysis | None = None


def _context(history: Sequence[Turn]) -> tuple[str, list[str]]:
    """Text from recent questions (for linking) and the tables of the last answered question."""
    text = " ".join(t.question for t in history[-CONTEXT_QUESTIONS:])
    pinned = next((t.tables_used for t in reversed(history) if t.status == "ok" and t.tables_used), [])
    return text, list(pinned)


def add_analysis(
    llm: LLMClient, question: str, response: QueryResponse, include_summary: bool = True,
) -> ResultAnalysis | None:
    """Profile, chart and (optionally) summarise a successful result.

    This only enriches an answer that already ran, so a failure here is logged and the answer
    is returned without analysis rather than being lost.
    """
    if response.status != "ok" or response.result is None:
        return None
    result = response.result
    try:
        profile = profile_result(result.columns, result.rows, result.truncated)
        analysis = ResultAnalysis(
            profile=profile, chart=select_chart(profile, result.columns, result.rows, question))
        if include_summary:
            summary = summarize(llm, question, result.columns, result.rows, profile)
            analysis.summary, analysis.summary_source = summary.text, summary.source
        return analysis
    except Exception:
        logger.exception("Result analysis failed")
        return None


def handle_message(
    llm: LLMClient,
    datasource: DataSource,
    connector: Connector,
    schema: SchemaInfo,
    message: str,
    history: Sequence[Turn] = (),
    entries: Sequence[ContextEntry] = (),
    blocked: Blocked | None = None,
) -> QueryResponse:
    # The model only ever sees what this workspace may read. The full schema and `blocked` still go to
    # validation, which refuses anything that touches a blocked column.
    shown = blocked_service.visible(schema, blocked) if blocked else schema
    values = schema_linking.distinct_values(datasource.id, connector, shown)
    context, pinned = _context(history)
    linked = schema_linking.link_schema(shown, message, values, context=context, pinned=pinned)
    # Definitions apply when the question, or the conversation it continues, uses a defined term.
    definitions = context_service.select(list(entries), f"{message} {context}", linked)

    # If the assistant just asked for clarification, this message is the answer: do not ask again.
    awaiting_answer = bool(history) and history[-1].status == "clarification"
    first_reply = None
    if not awaiting_answer:
        settings = get_settings()
        ambiguity: AmbiguityResult | None = join_ambiguity(linked)
        if ambiguity is None and settings.ambiguity_check:
            # The generation does not depend on the check, so start it now and let the two overlap. It is only
            # used if the question turns out to be clear.
            if settings.ambiguity_parallel:
                first_reply = start_generation(llm, message, linked, schema.dialect, history, definitions)
            try:
                ambiguity = check_ambiguity(llm, message, linked, history, definitions)
            except BaseException:
                if first_reply is not None:
                    abandon(first_reply)
                raise
        if ambiguity is not None:
            if first_reply is not None:
                abandon(first_reply)
            return QueryResponse(
                status="clarification", question=message, dialect=schema.dialect, linked=linked,
                clarification=ambiguity.question, options=ambiguity.options,
                explanation=ambiguity.reason, definitions_used=definitions.names)

    return run_question(llm, message, datasource, connector, schema, linked, history, definitions, blocked,
                        first_reply=first_reply)
