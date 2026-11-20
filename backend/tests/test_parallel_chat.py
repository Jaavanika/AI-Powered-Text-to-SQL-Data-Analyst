"""The ambiguity check and SQL generation run at the same time, without changing what the user gets."""

import json
import threading
import time

import pytest

from app.config import get_settings
from app.schemas import Turn
from app.services import analyst_workflow, connectors, query_pipeline, schema_service
from app.services.llm_service import LLMError
from tests.helpers import FakeLLM, sql_reply

CLEAR = {"ambiguous": False, "question": None, "options": [], "reason": ""}
UNSURE = {"ambiguous": True, "question": "Which region?", "options": ["North", "South"], "reason": "unclear"}
GOOD = sql_reply("SELECT COUNT(*) AS n FROM customers")
CHECK_PROMPT = "You decide whether a question about a database can be answered"


class Gated:
    """A model whose two kinds of call can be held, so the test decides which one finishes first."""

    def __init__(self, check=CLEAR, generations=(GOOD,), barrier=None, hold_generation=None, check_error=None):
        self.check, self.generations, self.barrier = check, list(generations), barrier
        self.hold_generation, self.check_error = hold_generation, check_error
        self.events: list[tuple[str, str]] = []
        self.lock = threading.Lock()

    def complete(self, messages, *, json_output=False, max_tokens=0):
        kind = "check" if messages[0]["content"].startswith(CHECK_PROMPT) else "generation"
        with self.lock:
            self.events.append((kind, "start"))
        if self.barrier:
            self.barrier.wait(timeout=2)                   # only passes if the other call is in flight too
        if kind == "generation" and self.hold_generation:
            self.hold_generation.wait(timeout=10)
        if kind == "check" and self.check_error:
            raise self.check_error
        with self.lock:
            self.events.append((kind, "end"))
            reply = self.check if kind == "check" else self.generations.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply)

    def started(self, kind):
        return [e for e in self.events if e == (kind, "start")]


@pytest.fixture
def parts(client):
    datasource, connector = connectors.resolve("sample", None)
    return datasource, connector, schema_service.build_schema(datasource, connector)


def handle(parts, llm, message="how many customers", history=()):
    datasource, connector, schema = parts
    return analyst_workflow.handle_message(llm, datasource, connector, schema, message, history)


def setting(monkeypatch, name, value):
    monkeypatch.setenv(name, str(value))
    get_settings.cache_clear()


# ------------------------------------------------------------------- overlap

def test_the_check_and_the_generation_are_in_flight_at_the_same_time(parts):
    """Each call waits for the other at a barrier: a one-after-the-other flow would time out."""
    llm = Gated(barrier=threading.Barrier(2))
    response = handle(parts, llm)
    assert response.status == "ok" and response.result.rows == [[40]]


def test_the_sequential_setting_starts_the_generation_only_after_the_check(parts, monkeypatch):
    setting(monkeypatch, "AMBIGUITY_PARALLEL", "false")
    llm = Gated()
    handle(parts, llm)
    assert llm.events == [("check", "start"), ("check", "end"), ("generation", "start"), ("generation", "end")]


def test_a_clear_question_gets_the_same_answer_either_way(parts, monkeypatch):
    def same_apart_from_timing(response):
        data = response.model_dump()
        data["result"]["elapsed_ms"] = 0                       # how long the query took legitimately varies
        return data

    parallel = same_apart_from_timing(handle(parts, Gated()))
    setting(monkeypatch, "AMBIGUITY_PARALLEL", "false")
    sequential = same_apart_from_timing(handle(parts, Gated()))
    assert parallel == sequential


# ------------------------------------------------------------- when it is unclear

def test_an_unclear_question_is_answered_with_a_clarification_without_waiting_for_the_generation(parts, monkeypatch):
    hold = threading.Event()
    llm = Gated(check=UNSURE, hold_generation=hold)
    spy = []
    monkeypatch.setattr(query_pipeline, "check_and_execute", lambda *a, **k: spy.append(a) or (_ for _ in ()).throw(AssertionError("ran SQL")))
    started = time.monotonic()
    response = handle(parts, llm)
    assert time.monotonic() - started < 3                       # the held generation did not delay it
    assert response.status == "clarification" and response.options == ["North", "South"]
    hold.set()
    time.sleep(0.4)                                             # let the abandoned generation finish
    assert spy == []                                            # its SQL was never validated or run


def test_the_abandoned_generation_failing_is_not_an_error(parts):
    llm = Gated(check=UNSURE, generations=[LLMError("The LLM request timed out.", 504)])
    assert handle(parts, llm).status == "clarification"
    time.sleep(0.2)


def test_a_join_that_needs_a_choice_is_asked_about_before_any_model_call(parts, monkeypatch):
    from app.services import ambiguity_service
    from app.schemas import LinkedSchema
    monkeypatch.setattr(analyst_workflow, "join_ambiguity", lambda linked: ambiguity_service.AmbiguityResult(
        question="Which path?", options=["a", "b"], reason="two paths"))
    llm = Gated()
    response = handle(parts, llm)
    assert response.status == "clarification" and llm.events == []


# ------------------------------------------------------------------ failures

def test_a_failing_check_is_reported_and_the_generation_is_left_alone(parts):
    llm = Gated(check_error=LLMError("The LLM request timed out.", 504))
    with pytest.raises(LLMError, match="timed out"):
        handle(parts, llm)
    time.sleep(0.2)


def test_a_failing_generation_is_reported_when_the_question_is_clear(parts):
    llm = Gated(generations=[LLMError("The LLM is rate limited. Try again shortly.", 503)])
    with pytest.raises(LLMError, match="rate limited"):
        handle(parts, llm)


def test_the_correction_loop_continues_after_a_rejected_first_generation(parts):
    llm = Gated(generations=[sql_reply("SELECT nope FROM customers"), GOOD])
    response = handle(parts, llm)
    assert response.status == "ok" and len(response.attempts) == 1
    assert len(llm.started("check")) == 1 and len(llm.started("generation")) == 2


def test_a_bad_first_reply_is_retried_too(parts):
    class Bad(Gated):
        def complete(self, messages, **kw):
            if not messages[0]["content"].startswith(CHECK_PROMPT) and not self.started("generation"):
                self.events.append(("generation", "start"))
                return "this is not json"
            return super().complete(messages, **kw)
    response = handle(parts, Bad())
    assert response.status == "ok" and len(response.attempts) == 1


# ---------------------------------------------------- when no check is needed

def test_an_answer_to_a_clarification_is_not_checked_again_so_only_one_call_is_made(parts):
    llm = Gated()
    history = [Turn(id="t1", seq=1, question="sales by region", status="clarification",
                    clarification="Which region?", options=["North"], created_at="2026-11-20T10:00:00+00:00")]
    response = handle(parts, llm, "North", history)
    assert response.status == "ok" and llm.started("check") == [] and len(llm.started("generation")) == 1


def test_with_the_check_switched_off_there_is_one_call(parts, monkeypatch):
    setting(monkeypatch, "AMBIGUITY_CHECK", "false")
    llm = Gated()
    handle(parts, llm)
    assert llm.started("check") == [] and len(llm.started("generation")) == 1


# ------------------------------------------------------------- the whole app

def test_the_chat_endpoint_behaves_the_same(client, monkeypatch):
    from app.api.sql import llm_dependency
    llm = Gated(check=UNSURE)
    client.app.dependency_overrides[llm_dependency] = lambda: llm
    body = client.post("/api/chat/query", json={"datasource_id": "sample", "message": "sales by region", "include_summary": False}).json()
    assert body["status"] == "clarification" and body["clarification"] == "Which region?"
    llm.check = CLEAR
    body = client.post("/api/chat/query", json={"datasource_id": "sample", "message": "how many customers", "include_summary": False}).json()
    assert body["status"] == "ok" and body["result"]["rows"] == [[40]]


def test_the_scripted_test_model_hands_each_kind_of_call_its_own_reply():
    """With two calls in flight the order they arrive in is not fixed, so replies are matched by kind."""
    from tests.helpers import FakeLLM
    llm = FakeLLM([GOOD, CLEAR])                                 # queued in the 'wrong' order on purpose
    check = [{"role": "system", "content": CHECK_PROMPT + " without guessing."}, {"role": "user", "content": "q"}]
    write = [{"role": "system", "content": "You write SQL."}, {"role": "user", "content": "q"}]
    assert json.loads(llm.complete(check))["ambiguous"] is False
    assert json.loads(llm.complete(write))["sql"].startswith("SELECT")
