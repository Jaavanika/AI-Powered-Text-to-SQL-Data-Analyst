"""Generate -> validate -> execute, with a bounded correction loop.

Only SQL that passed validation is ever executed. When a check fails, a sanitised description of
the problem goes back to the LLM, and the corrected SQL is validated again from the start.
The total number of LLM attempts is capped; after that the failure is reported instead.
"""

import json
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field

from app.config import get_settings
from app.schemas import DataSource, LinkedSchema, SchemaInfo, Turn
from app.services.blocked_service import Blocked
from app.services.connectors import Connector
from app.services.llm_service import LLMClient
from app.services.query_execution import ExecutionError, QueryResult
from app.services.sql_generation import build_messages, parse_model_output
from app.services.sql_validation import ValidationResult, parse_blocked, validate_sql


# Model calls that run beside the caller's own work (see start_generation). Small: it only needs to cover the
# questions being answered at once.
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="llm")


def start_generation(
    llm: LLMClient, question: str, linked: LinkedSchema, dialect: str,
    history: Sequence[Turn] = (), context=None,
) -> Future:
    """Begin the first model call for a question now, to be handed to run_question as `first_reply`."""
    messages = build_messages(question, linked, dialect, history, context)
    return _pool.submit(llm.complete, messages, json_output=True)


def abandon(future: Future) -> None:
    """Give up on a started call. It may already be running, so its outcome is simply never looked at."""
    if not future.cancel():
        future.add_done_callback(lambda f: f.exception())       # reading it keeps a failure from being reported as unhandled


class Attempt(BaseModel):
    sql: str | None = None
    error: str


class QueryResponse(BaseModel):
    status: Literal["ok", "clarification", "failed"]
    question: str
    dialect: str
    linked: LinkedSchema
    sql: str | None = None
    explanation: str = ""
    tables_used: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    clarification: str | None = None
    # Names of the user's business definitions that were given to the model for this question.
    definitions_used: list[str] = Field(default_factory=list)
    # Suggested answers to the clarification question, when there are any.
    options: list[str] = Field(default_factory=list)
    validation: ValidationResult | None = None
    result: QueryResult | None = None
    attempts: list[Attempt] = Field(default_factory=list)
    error: str | None = None


def check_and_execute(
    schema: SchemaInfo, connector: Connector, sql: str, blocked: Blocked | None = None,
) -> tuple[ValidationResult, QueryResult | None]:
    """Validate `sql`; if it passes, run it read-only. Never executes unvalidated SQL.

    `blocked` is what this workspace may not read; without it only the administrator's setting applies.
    """
    settings = get_settings()
    if blocked is None:
        blocked_tables, blocked_columns = parse_blocked(settings.blocked_columns)
    else:
        blocked_tables, blocked_columns = set(blocked.tables), set(blocked.columns)
    validation = validate_sql(sql, schema, schema.dialect, blocked_tables, blocked_columns)
    if not validation.valid:
        return validation, None
    allowed = {t.name for t in schema.tables} - blocked_tables
    result = connector.execute(
        validation.sql,
        allowed_tables=allowed, blocked_columns=blocked_columns,
        timeout_seconds=settings.query_timeout_seconds, max_rows=settings.query_max_rows,
    )
    return validation, result


def _feedback(raw: str, problem: str) -> list[dict]:
    return [
        {"role": "assistant", "content": raw[:4000]},
        {"role": "user", "content": (
            f"That answer cannot be used: {problem}\n"
            "Fix it using only the tables and columns in the schema, and reply again with ONLY the "
            "JSON object described above.")},
    ]


def run_question(
    llm: LLMClient,
    question: str,
    datasource: DataSource,
    connector: Connector,
    schema: SchemaInfo,
    linked: LinkedSchema,
    history: Sequence[Turn] = (),
    context=None,
    blocked: Blocked | None = None,
    first_reply: Future | None = None,
) -> QueryResponse:
    max_attempts = max(1, get_settings().sql_max_attempts)
    messages = build_messages(question, linked, schema.dialect, history, context)
    attempts: list[Attempt] = []
    base = dict(question=question, dialect=schema.dialect, linked=linked,
                definitions_used=context.names if context else [])
    last_sql: str | None = None
    last_validation: ValidationResult | None = None

    for attempt in range(max_attempts):
        # The first reply may already be on its way (started while the question was being checked).
        raw = first_reply.result() if attempt == 0 and first_reply is not None else llm.complete(messages, json_output=True)
        try:
            generated = parse_model_output(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            problem = f"the reply was not a valid JSON answer ({str(exc)[:120]})"
            attempts.append(Attempt(error=problem))
            messages = messages + _feedback(raw, problem)
            continue

        if generated.clarification:
            return QueryResponse(
                status="clarification", clarification=generated.clarification,
                explanation=generated.explanation, tables_used=generated.tables_used,
                assumptions=generated.assumptions, attempts=attempts, **base)

        sql = generated.sql or ""
        last_sql = sql
        try:
            validation, result = check_and_execute(schema, connector, sql, blocked)
        except ExecutionError as exc:
            if exc.kind == "unavailable":
                raise            # an outage is not something the model can fix by rewriting the SQL
            problem = exc.message
            if exc.kind == "timeout":
                problem += " Rewrite it to be cheaper: avoid cross joins and add filters."
            attempts.append(Attempt(sql=sql, error=problem))
            messages = messages + _feedback(raw, f"running the SQL failed: {problem}")
            continue

        last_validation = validation
        if not validation.valid:
            problem = " ".join(validation.errors)
            attempts.append(Attempt(sql=sql, error=problem))
            messages = messages + _feedback(raw, f"the SQL failed validation: {problem}")
            continue

        return QueryResponse(
            status="ok", sql=validation.sql, explanation=generated.explanation,
            tables_used=validation.tables or generated.tables_used,
            assumptions=generated.assumptions, validation=validation, result=result,
            attempts=attempts, **base)

    last_error = attempts[-1].error if attempts else "unknown error"
    return QueryResponse(
        status="failed", sql=last_sql, validation=last_validation, attempts=attempts,
        error=f"No valid query was produced after {max_attempts} attempt(s). Last problem: {last_error}",
        **base)
