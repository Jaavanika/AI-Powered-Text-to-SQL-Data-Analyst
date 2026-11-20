"""Run the evaluation questions through the real pipeline and judge each answer.

Questions go straight to the assistant's workflow (the same code the chat endpoint uses), on a private copy of the
data in a temporary folder, so a run never touches anyone's real data and cannot be slowed by the request limits.
"""

import os
import sqlite3
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from app.config import get_settings
from app.schemas import DataSource, ImportRequest, SchemaInfo, Turn
from app.services import (
    analyst_workflow,
    connectors,
    context_service,
    file_import_service,
    query_pipeline,
    registry,
    sample_data,
    schema_linking,
    schema_service,
)
from app.services.blocked_service import Blocked
from app.services.connectors import Connector
from app.services.sql_validation import parse_blocked
from evaluation import scoring
from evaluation.cases import ANSWER, CLARIFY, DECLINE, SAFE, SAMPLE, UPLOAD, UPLOAD_CSV, Case, by_id, chains

WORKSPACE = "evaluation"
CONTEXT_QUESTIONS = analyst_workflow.CONTEXT_QUESTIONS


class Source(NamedTuple):
    datasource: DataSource
    connector: Connector
    schema: SchemaInfo
    path: Path


class Environment:
    """A private copy of the sample data and an uploaded file, in a temporary data folder."""

    def __init__(self, data_dir: Path):
        self._previous = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = str(data_dir)
        get_settings.cache_clear()
        registry.init_registry()
        sample_data.ensure_sample_database()
        self._sources = {SAMPLE: self._load(SAMPLE)}
        self.sample_path = self._sources[SAMPLE].path
        self.sample_snapshot = scoring.snapshot(self.sample_path)
        self._sources[UPLOAD] = self._import_upload()
        context_service.add_examples(WORKSPACE, SAMPLE, self._sources[SAMPLE].schema)
        self.example_entries = context_service.list_entries(WORKSPACE, SAMPLE)

    def _load(self, datasource_id: str) -> Source:
        found = connectors.resolve(datasource_id, WORKSPACE if datasource_id != SAMPLE else None)
        assert found is not None, datasource_id
        datasource, connector = found
        schema = schema_service.build_schema(datasource, connector)
        workspace = WORKSPACE if datasource_id != SAMPLE else None
        return Source(datasource, connector, schema, Path(registry.get_db_path(datasource_id, workspace)))

    def _import_upload(self) -> Source:
        upload_id, _ = file_import_service.stage_upload(WORKSPACE, "sales.csv", UPLOAD_CSV.encode())
        file_import_service.build_preview(WORKSPACE, upload_id)
        datasource = file_import_service.import_upload(
            WORKSPACE, ImportRequest(upload_id=upload_id, datasource_name="Evaluation sales"))
        return self._load(datasource.id)

    def source(self, dataset: str) -> Source:
        return self._sources[dataset]

    def close(self) -> None:
        if self._previous is None:
            os.environ.pop("DATA_DIR", None)
        else:
            os.environ["DATA_DIR"] = self._previous
        get_settings.cache_clear()


def build_environment(data_dir: Path) -> Environment:
    return Environment(Path(data_dir))


# ----------------------------------------------------------------------- results

@dataclass
class CaseResult:
    id: str
    category: str
    question: str
    expect: str
    status: str                         # ok | clarification | failed | error
    sql: str | None = None
    tables: list[str] = field(default_factory=list)
    attempts: int = 0
    first_try_valid: bool = False
    exact: bool = False
    lenient: bool = False
    join_ok: bool | None = None
    table_recall: float | None = None
    column_recall: float | None = None
    link_ok: bool | None = None
    chart: str | None = None
    chart_ok: bool | None = None
    asked: bool = False
    data_unchanged: bool = True
    leaked: bool = False
    total_ms: float = 0.0
    db_ms: float | None = None
    error: str | None = None
    clarification: str | None = None
    passed: bool = False
    reference_sql: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------------ pieces

def _linking_context(case: Case) -> tuple[str, list[str]]:
    """The conversation a follow-up belongs to: what linking is told, as in the chat workflow."""
    known = by_id()
    earlier: list[Case] = []
    parent = known.get(case.follows) if case.follows else None
    while parent is not None:
        earlier.insert(0, parent)
        parent = known.get(parent.follows) if parent.follows else None
    text = " ".join(c.question for c in earlier[-CONTEXT_QUESTIONS:])
    pinned = sorted(scoring.referenced_tables(earlier[-1].reference_sql)) if earlier and earlier[-1].reference_sql else []
    return text, pinned


def link_ok(env: Environment, case: Case) -> bool:
    """Does schema linking, before any model is involved, hand the model every table the right answer needs?"""
    source = env.source(case.dataset)
    values = schema_linking.distinct_values(source.datasource.id, source.connector, source.schema)
    context, pinned = _linking_context(case)
    linked = schema_linking.link_schema(source.schema, case.question, values, context=context, pinned=pinned)
    return scoring.referenced_tables(case.reference_sql) <= {t.name.lower() for t in linked.tables}


def _blocked(case: Case) -> Blocked | None:
    if not case.blocked:
        return None
    tables, columns = parse_blocked(",".join(case.blocked))
    return Blocked(frozenset(tables), frozenset(columns))


def _blocked_values(env: Environment, case: Case) -> set[str]:
    """What the blocked columns actually contain, read directly, to see whether any of it reaches an answer."""
    if not case.blocked:
        return set()
    values: set[str] = set()
    with closing(sqlite3.connect(f"file:{env.source(case.dataset).path}?mode=ro", uri=True)) as conn:
        for entry in case.blocked:
            table, _, column = entry.partition(".")
            columns = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')] if column in ("", "*") else [column]
            for name in columns:
                values |= {str(v) for (v,) in conn.execute(f'SELECT "{name}" FROM "{table}"') if v is not None}
    return values


def _accepted_answers(env: Environment, case: Case) -> list[list]:
    """The rows of the reference answer, and of any other answer the case accepts as equally right."""
    source = env.source(case.dataset)
    answers = []
    for sql in (case.reference_sql, *case.alternatives):
        validation, result = query_pipeline.check_and_execute(source.schema, source.connector, sql)
        assert validation.valid and result is not None, f"{case.id}: a reference SQL is not usable: {validation.errors}"
        answers.append(result.rows)
    return answers


def _passed(case: Case, result: CaseResult) -> bool:
    if result.status == "error":
        return False
    if case.expect == ANSWER:
        return result.status == "ok" and result.lenient
    if case.expect == CLARIFY:
        return result.status == "clarification"
    if case.expect == DECLINE:
        return result.status in ("clarification", "failed")
    return result.data_unchanged and not result.leaked and (result.status != "ok" or scoring.is_read_only(result.sql))


def _turn(case: Case, response, seq: int) -> Turn | None:
    """What the conversation remembers of an answer; failures carry no context, as in the chat."""
    if response is None or response.status == "failed":
        return None
    result = response.result
    return Turn(
        id=case.id, seq=seq, question=case.question, status=response.status, sql=response.sql,
        explanation=response.explanation, tables_used=response.tables_used, clarification=response.clarification,
        options=response.options, columns=result.columns if result else [], row_count=result.row_count if result else None,
        error=response.error, created_at=datetime.now(timezone.utc).isoformat())


# ----------------------------------------------------------------------- running

def run_case(llm, env: Environment, case: Case, history: Sequence[Turn] = ()):
    """Ask one question and judge the answer. Returns (result, what the conversation should remember)."""
    source = env.source(case.dataset)
    result = CaseResult(id=case.id, category=case.category, question=case.question, expect=case.expect,
                        status="error", reference_sql=case.reference_sql)
    if case.expect == ANSWER:
        result.link_ok = link_ok(env, case)
    before = scoring.snapshot(source.path)
    started = time.perf_counter()
    response = None
    try:
        response = analyst_workflow.handle_message(
            llm, source.datasource, source.connector, source.schema, case.question, history,
            entries=env.example_entries if case.examples else (), blocked=_blocked(case))
    except Exception as exc:                          # one bad question must not end the run
        result.error = getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"
    result.total_ms = (time.perf_counter() - started) * 1000
    result.data_unchanged = scoring.snapshot(source.path) == before

    if response is not None:
        _judge(llm, env, case, response, result)
    result.passed = _passed(case, result)
    return result, _turn(case, response, len(history) + 1)


def _judge(llm, env: Environment, case: Case, response, result: CaseResult) -> None:
    result.status = response.status
    result.sql = response.sql
    result.tables = list(response.validation.tables) if response.validation else []
    result.attempts = len(response.attempts)
    result.first_try_valid = response.status == "ok" and result.attempts == 0
    result.asked = response.status == "clarification"
    result.clarification = response.clarification
    result.error = response.error
    result.db_ms = response.result.elapsed_ms if response.result else None
    produced = response.status == "ok" and response.result is not None

    if case.expect == ANSWER:
        accepted = _accepted_answers(env, case)
        wanted_tables = scoring.referenced_tables(case.reference_sql)
        actual_tables = scoring.referenced_tables(response.sql or "")
        if produced:
            comparisons = [scoring.compare(rows, response.result.rows, case.ordered) for rows in accepted]
            result.exact = any(c.exact for c in comparisons)
            result.lenient = any(c.lenient for c in comparisons)
            analysis = analyst_workflow.add_analysis(llm, case.question, response, include_summary=False)
            result.chart = analysis.chart.type if analysis else None
            result.chart_ok = result.chart in case.charts
        result.table_recall = scoring.recall(wanted_tables, actual_tables) if produced else 0.0
        result.column_recall = (scoring.recall(scoring.referenced_columns(case.reference_sql),
                                               scoring.referenced_columns(response.sql or "")) if produced else 0.0)
        if len(wanted_tables) >= 2:
            result.join_ok = produced and actual_tables == wanted_tables
    if case.blocked and produced:
        result.leaked = scoring.leaked(response.result.rows, _blocked_values(env, case))


def run_chain(llm, env: Environment, chain: Sequence[Case], on_result: Callable | None = None) -> list[CaseResult]:
    history: list[Turn] = []
    results = []
    for case in chain:
        result, turn = run_case(llm, env, case, history)
        results.append(result)
        if turn is not None:
            history.append(turn)
        if on_result:
            on_result(result)
    return results


def run_all(llm, env: Environment, cases: Sequence[Case], workers: int = 3,
            progress: Callable[[CaseResult], None] | None = None) -> list[CaseResult]:
    """Run every case, a conversation at a time and several conversations at once. Results are in case order."""
    lock = threading.Lock()

    def report(result: CaseResult) -> None:
        if progress:
            with lock:
                progress(result)

    groups = chains(cases)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        done = list(pool.map(lambda chain: run_chain(llm, env, chain, report), groups))
    by_case = {r.id: r for group in done for r in group}
    return [by_case[c.id] for c in cases]
