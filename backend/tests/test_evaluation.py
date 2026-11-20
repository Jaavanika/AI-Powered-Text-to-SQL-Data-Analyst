"""The evaluation suite itself: its scoring, its dataset, its metrics and its report.

None of this calls the real model. A stand-in model that answers with each case's reference SQL shows the
harness scores a perfect assistant as perfect; deliberately wrong stand-ins show it notices.
"""

import json
import sqlite3
from contextlib import closing

import pytest

from app.services.llm_service import LLMError
from evaluation import metrics, report, runner, scoring
from evaluation.cases import ANSWER, CASES, CLARIFY, DECLINE, SAFE, SAMPLE, UPLOAD, by_id, chains
from tests.helpers import sql_reply

CLEAR = {"ambiguous": False, "question": None, "options": [], "reason": ""}
UNSURE = {"ambiguous": True, "question": "What do you mean?", "options": ["A", "B"], "reason": "vague"}


# ================================================================== scoring

def test_values_are_compared_by_meaning_not_by_type_or_float_noise():
    assert scoring.normalise_rows([(1, 2.0, "x")]) == scoring.normalise_rows([(1.0, 2, " x ")])
    assert scoring.normalise_rows([(166670.8400001,)]) == scoring.normalise_rows([(166670.84,)])
    assert scoring.normalise_rows([("01",)]) == scoring.normalise_rows([(1,)])           # a month as text or as a number
    assert scoring.normalise_rows([(None,)]) == [(None,)]
    assert scoring.normalise_rows([("2024-01",)]) != scoring.normalise_rows([("2024-02",)])
    assert scoring.normalise_rows([("Abc",)]) != scoring.normalise_rows([("abc",)])      # text is case-sensitive


def test_the_same_rows_in_another_order_match_unless_order_matters():
    ref, act = [(1, "a"), (2, "b")], [(2, "b"), (1, "a")]
    assert scoring.compare(ref, act).exact and scoring.compare(ref, act).lenient
    ordered = scoring.compare(ref, act, ordered=True)
    assert not ordered.exact and not ordered.lenient
    assert scoring.compare(ref, ref, ordered=True).exact


def test_extra_and_reordered_columns_are_accepted_leniently_but_not_exactly():
    ref = [("Ava", 10), ("Bo", 20)]
    act = [(10, "x", "Ava"), (20, "y", "Bo")]
    result = scoring.compare(ref, act)
    assert result.lenient and not result.exact


@pytest.mark.parametrize("act", [
    [("Ava", 10)],                                   # a row is missing
    [("Ava", 10), ("Bo", 20), ("Cy", 30)],           # a row too many
    [("Ava", 10), ("Bo", 21)],                       # a wrong value
    [("Ava",), ("Bo",)],                             # a needed column is missing
])
def test_wrong_answers_are_not_matched_even_leniently(act):
    assert not scoring.compare([("Ava", 10), ("Bo", 20)], act).lenient


def test_two_reference_columns_with_the_same_values_need_two_actual_columns():
    ref = [(1, 1), (2, 2)]
    assert scoring.compare(ref, [(1, 1), (2, 2)]).lenient
    assert not scoring.compare(ref, [(1,), (2,)]).lenient


def test_columns_are_matched_as_a_whole_row_not_value_by_value():
    """Right values in the wrong rows is a wrong answer."""
    ref = [("a", 1), ("b", 2)]
    assert not scoring.compare(ref, [("a", 2), ("b", 1)]).lenient


def test_empty_results_match_each_other_only():
    assert scoring.compare([], []).exact
    assert not scoring.compare([], [(1,)]).lenient and not scoring.compare([(1,)], []).lenient


def test_the_ordered_lenient_check_follows_the_mapped_columns():
    ref = [("a", 3), ("b", 2)]
    assert scoring.compare(ref, [(3, "a", "x"), (2, "b", "y")], ordered=True).lenient
    assert not scoring.compare(ref, [(2, "b", "y"), (3, "a", "x")], ordered=True).lenient


def test_tables_and_columns_are_read_from_the_sql_ignoring_aliases_and_ctes():
    sql = ("WITH c AS (SELECT customer_id, city FROM customers) "
           "SELECT x.city, SUM(o.quantity) AS q FROM orders o JOIN c x ON x.customer_id = o.customer_id GROUP BY x.city")
    assert scoring.referenced_tables(sql) == {"orders", "customers"}
    assert {"city", "quantity", "customer_id"} <= scoring.referenced_columns(sql)
    assert "q" not in scoring.referenced_columns(sql) and "*" not in scoring.referenced_columns("SELECT * FROM orders")
    assert scoring.referenced_tables("not sql at all (") == set()


def test_recall():
    assert scoring.recall({"a", "b"}, {"a", "b", "c"}) == 1
    assert scoring.recall({"a", "b"}, {"a"}) == 0.5
    assert scoring.recall(set(), {"a"}) == 1


def test_percentiles_interpolate_and_cope_with_nothing():
    values = [1, 2, 3, 4, 5]
    assert scoring.percentile(values, 0) == 1 and scoring.percentile(values, 50) == 3 and scoring.percentile(values, 100) == 5
    assert scoring.percentile(values, 95) == pytest.approx(4.8)
    assert scoring.percentile([], 95) == 0 and scoring.percentile([7], 95) == 7


@pytest.mark.parametrize("sql,expected", [
    ("SELECT 1", True), ("WITH a AS (SELECT 1) SELECT * FROM a", True), ("SELECT 1 UNION SELECT 2", True),
    ("DROP TABLE customers", False), ("DELETE FROM customers", False), ("UPDATE products SET price = 0", False),
    ("INSERT INTO regions VALUES (9, 'x')", False), ("SELECT 1; DROP TABLE customers", False), ("", False), ("garbage (", False),
    (None, False),
])
def test_only_a_single_read_only_statement_counts_as_read_only(sql, expected):
    assert scoring.is_read_only(sql) is expected


def test_a_leak_is_a_blocked_value_appearing_anywhere_in_the_result():
    blocked = {"ava@example.com"}
    assert scoring.leaked([("Ava", "ava@example.com")], blocked)
    assert not scoring.leaked([("Ava", "other")], blocked)
    assert not scoring.leaked([("Ava",)], set())


def test_a_snapshot_changes_when_any_data_changes(tmp_path):
    path = tmp_path / "t.sqlite"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE t (a INTEGER, b TEXT)")
        conn.executemany("INSERT INTO t VALUES (?, ?)", [(1, "x"), (2, "y")])
    before = scoring.snapshot(path)
    assert scoring.snapshot(path) == before
    for change in ("UPDATE t SET b = 'z' WHERE a = 2", "DELETE FROM t WHERE a = 1", "INSERT INTO t VALUES (3, 'w')",
                   "CREATE TABLE u (a INTEGER)"):
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute(change)
        assert scoring.snapshot(path) != before, change
        before = scoring.snapshot(path)


# =============================================================== the dataset

def test_cases_have_unique_ids_and_unique_questions():
    assert len({c.id for c in CASES}) == len(CASES)
    assert len({c.question for c in CASES}) == len(CASES)          # the stand-in model finds its case by the question


def test_every_readme_category_is_covered_often_enough():
    count = {}
    for c in CASES:
        count[c.category] = count.get(c.category, 0) + 1
    for category in ("simple selection", "filtering", "aggregation", "sorting", "single join", "multiple joins", "time analysis",
                     "comparison", "ambiguity", "invalid schema", "unsafe SQL", "uploaded data", "business definitions",
                     "follow-up questions"):
        assert count.get(category, 0) >= 3, category
    assert count["unsafe SQL"] >= 5 and count["ambiguity"] >= 4 and len(CASES) >= 50


def test_the_shape_of_every_case_is_valid():
    ids = by_id()
    for c in CASES:
        assert c.expect in (ANSWER, CLARIFY, DECLINE, SAFE) and c.dataset in (SAMPLE, UPLOAD)
        if c.expect == ANSWER:
            assert c.reference_sql and c.charts, c.id
        else:
            assert c.reference_sql is None, c.id
        if c.follows:
            assert c.follows in ids and ids[c.follows].dataset == c.dataset, c.id
    assert sum(len(chain) for chain in chains()) == len(CASES)
    assert all(chain[0].follows is None for chain in chains())


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    environment = runner.build_environment(tmp_path_factory.mktemp("evaluation"))
    yield environment
    environment.close()


def reference_rows(env, case):
    source = env.source(case.dataset)
    from app.services import query_pipeline
    validation, result = query_pipeline.check_and_execute(source.schema, source.connector, case.reference_sql)
    assert validation.valid, f"{case.id}: {validation.errors}"
    return result


@pytest.mark.parametrize("case", [c for c in CASES if c.expect == ANSWER], ids=lambda c: c.id)
def test_every_reference_query_is_valid_runs_and_returns_rows(env, case):
    """The answers must be checkable: a reference that fails or is empty would make a right answer look wrong."""
    assert len(reference_rows(env, case).rows) >= 1


@pytest.mark.parametrize("case", [c for c in CASES if c.expect == ANSWER and " DESC LIMIT" in c.reference_sql], ids=lambda c: c.id)
def test_top_n_questions_have_no_tie_at_the_cut_off(env, case):
    """A tie would make 'the top one' or 'the top five' ambiguous, and the reference an arbitrary pick.
    Run the reference with ties broken one way and the other; if the rows differ, the question has no single answer."""
    ascending = runner_rows(env, case.reference_sql.replace(" DESC LIMIT", " DESC, 1 ASC LIMIT"), case.dataset)
    descending = runner_rows(env, case.reference_sql.replace(" DESC LIMIT", " DESC, 1 DESC LIMIT"), case.dataset)
    assert scoring.compare(ascending, descending, ordered=True).exact, f"{case.id}: the cut-off falls inside a tie"


def test_the_tie_check_really_does_detect_a_tie(env):
    """Customers are tied at the top for the number of orders, so asking for just one of them has no single answer."""
    sql = ("SELECT c.customer_name, COUNT(*) FROM orders o JOIN customers c ON c.customer_id = o.customer_id "
           "GROUP BY c.customer_id, c.customer_name ORDER BY COUNT(*) DESC LIMIT 1")
    ascending = runner_rows(env, sql.replace(" DESC LIMIT", " DESC, 1 ASC LIMIT"), SAMPLE)
    descending = runner_rows(env, sql.replace(" DESC LIMIT", " DESC, 1 DESC LIMIT"), SAMPLE)
    assert not scoring.compare(ascending, descending, ordered=True).exact


def runner_rows(env, sql, dataset):
    source = env.source(dataset)
    return source.connector.execute(sql, allowed_tables={t.name for t in source.schema.tables}, blocked_columns=set(),
                                    timeout_seconds=10, max_rows=1000).rows


def test_the_revenue_anchor_is_the_completed_orders_total(env):
    case = by_id()["def_revenue"]
    assert round(reference_rows(env, case).rows[0][0], 2) == 166670.84           # not the all-orders total
    assert round(reference_rows(env, by_id()["def_profit"]).rows[0][0], 2) == 64096.44


@pytest.mark.parametrize("case", [c for c in CASES if c.expect == ANSWER], ids=lambda c: c.id)
def test_the_listed_chart_types_include_what_the_visualizer_picks_for_the_reference(env, case):
    from app.services.result_analysis import profile_result
    from app.services.visualization import select_chart
    result = reference_rows(env, case)
    chart = select_chart(profile_result(result.columns, result.rows, False), result.columns, result.rows, case.question)
    assert chart.type in case.charts, f"{case.id}: the visualizer chose {chart.type!r}, the case lists {case.charts}"


@pytest.mark.parametrize("case", [c for c in CASES if c.expect == ANSWER], ids=lambda c: c.id)
def test_schema_linking_finds_every_table_the_reference_needs(env, case):
    assert runner.link_ok(env, case), f"{case.id}: linking missed one of {scoring.referenced_tables(case.reference_sql)}"


def test_the_upload_dataset_is_really_imported(env):
    assert {t.name for t in env.source(UPLOAD).schema.tables} == {"sales"}


# ====================================================== a stand-in model

class ReferenceLLM:
    """Answers each case the way a perfect assistant would, so the harness can be checked without the real model."""

    def __init__(self, break_with=None):
        self.cases = {c.question: c for c in CASES}
        self.break_with = break_with
        self.generation_prompts: list[str] = []

    def case_for(self, messages):
        question = messages[1]["content"].rsplit("QUESTION\n", 1)[1].strip()
        return self.cases[question]

    def complete(self, messages, *, json_output=False, max_tokens=0):
        case = self.case_for(messages)
        if messages[0]["content"].startswith("You decide whether a question about a database"):
            return json.dumps(UNSURE if case.expect == CLARIFY else CLEAR)
        self.generation_prompts.append(messages[1]["content"])
        if self.break_with is not None:
            return self.break_with(case)
        if case.expect == ANSWER:
            return json.dumps(sql_reply(case.reference_sql))
        if case.expect == DECLINE:
            return json.dumps(sql_reply(None, clarification="That information is not in this database."))
        unsafe = "SELECT customer_name, email FROM customers" if case.blocked else (
            "DROP TABLE customers" if "Drop" in case.question or "DROP" in case.question else "DELETE FROM customers")
        return json.dumps(sql_reply(unsafe))


@pytest.fixture(scope="module")
def perfect_run(env):
    return runner.run_all(ReferenceLLM(), env, CASES, workers=2)


def result_of(results, case_id):
    return next(r for r in results if r.id == case_id)


def test_a_perfect_assistant_scores_perfectly(perfect_run):
    failed = [(r.id, r.status, r.error) for r in perfect_run if not r.passed]
    assert failed == []
    assert len(perfect_run) == len(CASES)


def test_a_perfect_assistant_matches_the_reference_exactly_with_the_right_joins(perfect_run):
    answers = [r for r in perfect_run if r.expect == ANSWER]
    assert all(r.exact and r.lenient and r.first_try_valid and r.attempts == 0 for r in answers)
    assert all(r.table_recall == 1 and r.column_recall == 1 for r in answers)
    assert all(r.join_ok for r in answers if r.join_ok is not None) and any(r.join_ok for r in answers)
    assert all(r.link_ok for r in answers)
    assert all(r.chart_ok for r in answers)


def test_vague_questions_get_a_clarification_and_clear_ones_do_not(perfect_run):
    assert all(r.status == "clarification" and r.asked for r in perfect_run if r.expect == CLARIFY)
    assert not any(r.asked for r in perfect_run if r.expect == ANSWER)


def test_unsafe_requests_are_stopped_and_change_nothing(perfect_run):
    unsafe = [r for r in perfect_run if r.expect == SAFE]
    assert unsafe and all(r.data_unchanged and not r.leaked and r.status == "failed" for r in unsafe)
    assert all(r.attempts >= 1 for r in unsafe)                      # the validator rejected the attempts


def test_a_follow_up_is_asked_inside_its_conversation(env):
    llm = ReferenceLLM()
    runner.run_all(llm, env, [c for c in CASES if c.id in ("follow_monthly", "follow_only_2025")], workers=1)
    follow_up = next(p for p in llm.generation_prompts if p.rstrip().endswith("Only show 2025."))
    assert "Show the number of orders per month." in follow_up


def test_definitions_are_only_active_for_the_cases_that_ask_for_them(env):
    llm = ReferenceLLM()
    runner.run_all(llm, env, [by_id()["def_revenue"], by_id()["agg_avg_price"]], workers=1)
    with_definitions = next(p for p in llm.generation_prompts if "total revenue" in p)
    without = next(p for p in llm.generation_prompts if "average product price" in p)
    assert "BUSINESS DEFINITIONS" in with_definitions and "BUSINESS DEFINITIONS" not in without


def test_timings_are_recorded(perfect_run):
    assert all(r.total_ms >= 0 for r in perfect_run)
    assert all(r.db_ms is not None and r.db_ms >= 0 for r in perfect_run if r.status == "ok")


# ------------------------------------------------------------- a wrong assistant

def test_a_wrong_assistant_is_caught(env):
    wrong = lambda case: json.dumps(sql_reply("SELECT COUNT(*) AS n FROM customers"))
    cases = [by_id()[i] for i in ("filter_cancelled", "agg_avg_price", "agg_orders_by_status", "simple_customers")]
    results = runner.run_all(ReferenceLLM(break_with=wrong), env, cases, workers=1)
    right = {r.id for r in results if r.lenient}
    assert right == {"simple_customers"}                                     # only the question that happens to match
    assert all(r.status == "ok" for r in results)                            # it ran fine; it was just wrong


def test_an_assistant_that_never_asks_misses_the_vague_questions(env):
    class Never(ReferenceLLM):
        def complete(self, messages, **kw):
            if messages[0]["content"].startswith("You decide whether"):
                return json.dumps(CLEAR)
            return json.dumps(sql_reply("SELECT product_name FROM products LIMIT 1"))
    results = runner.run_all(Never(), env, [by_id()["ambig_best_product"]], workers=1)
    assert results[0].status == "ok" and not results[0].passed and not results[0].asked


def test_an_assistant_that_invents_a_column_is_caught(env):
    liar = lambda case: json.dumps(sql_reply("SELECT AVG(satisfaction) FROM customers"))
    results = runner.run_all(ReferenceLLM(break_with=liar), env, [by_id()["invalid_satisfaction"]], workers=1)
    assert results[0].status == "failed" and results[0].passed               # the validator stopped it: declined


def test_a_model_error_is_recorded_for_that_case_and_the_run_goes_on(env):
    def sometimes(case):
        if case.id == "simple_regions":
            raise LLMError("The LLM request timed out.", 504)
        return json.dumps(sql_reply(case.reference_sql))
    cases = [by_id()[i] for i in ("simple_customers", "simple_regions", "simple_product_count")]
    results = runner.run_all(ReferenceLLM(break_with=sometimes), env, cases, workers=1)
    bad = result_of(results, "simple_regions")
    assert bad.status == "error" and "timed out" in bad.error and not bad.passed
    assert result_of(results, "simple_customers").passed and result_of(results, "simple_product_count").passed


def test_a_follow_up_whose_first_question_failed_is_still_asked(env):
    def broken_first(case):
        if case.id == "follow_orders_2025":
            raise LLMError("The LLM is rate limited. Try again shortly.", 503)
        return json.dumps(sql_reply(case.reference_sql))
    cases = [by_id()["follow_orders_2025"], by_id()["follow_cancelled"]]
    results = runner.run_all(ReferenceLLM(break_with=broken_first), env, cases, workers=1)
    assert [r.status for r in results] == ["error", "ok"]


def test_a_write_to_the_data_would_be_noticed(tmp_path, monkeypatch):
    """If anything changes the sample while a case runs, that case says so and cannot pass."""
    fresh = runner.build_environment(tmp_path)
    try:
        real = runner.analyst_workflow.handle_message

        def tamper(*args, **kwargs):
            with closing(sqlite3.connect(fresh.sample_path)) as conn, conn:
                conn.execute("UPDATE products SET price = price + 1 WHERE product_id = 1")
            return real(*args, **kwargs)

        monkeypatch.setattr(runner.analyst_workflow, "handle_message", tamper)
        result = runner.run_all(ReferenceLLM(), fresh, [by_id()["unsafe_delete"]], workers=1)[0]
        assert result.data_unchanged is False and result.passed is False
    finally:
        fresh.close()


def test_results_do_not_depend_on_the_number_of_workers(env):
    cases = [c for c in CASES if c.category in ("filtering", "ambiguity", "follow-up questions")]
    one = runner.run_all(ReferenceLLM(), env, cases, workers=1)
    four = runner.run_all(ReferenceLLM(), env, cases, workers=4)
    key = lambda rs: [(r.id, r.status, r.lenient, r.passed) for r in rs]
    assert key(one) == key(four)


def test_results_come_back_in_the_order_of_the_cases(perfect_run):
    assert [r.id for r in perfect_run] == [c.id for c in CASES]


def test_the_sample_data_is_untouched_by_a_whole_run(env, perfect_run):
    assert scoring.snapshot(env.sample_path) == env.sample_snapshot


# ================================================================= metrics

def fake(id, category, expect, **kw):
    base = dict(id=id, category=category, question=id, expect=expect, status="ok", sql="SELECT 1", tables=[], attempts=0,
                first_try_valid=True, exact=False, lenient=False, join_ok=None, table_recall=None, column_recall=None,
                link_ok=None, chart=None, chart_ok=None, asked=False, data_unchanged=True, leaked=False,
                total_ms=1000.0, db_ms=10.0, error=None, clarification=None, passed=False)
    base.update(kw)
    return runner.CaseResult(**base)


def synthetic():
    return [
        fake("a1", "filtering", ANSWER, lenient=True, exact=True, passed=True, link_ok=True, table_recall=1, column_recall=1, chart_ok=True, total_ms=1000),
        fake("a2", "filtering", ANSWER, lenient=True, exact=False, passed=True, link_ok=True, table_recall=1, column_recall=0.5, chart_ok=False, total_ms=2000, attempts=1, first_try_valid=False),
        fake("a3", "single join", ANSWER, lenient=False, passed=False, join_ok=False, link_ok=False, table_recall=0.5, column_recall=0, chart_ok=None, total_ms=3000),
        fake("a4", "single join", ANSWER, status="clarification", asked=True, join_ok=None, link_ok=True, total_ms=4000, db_ms=None, sql=None),
        fake("c1", "ambiguity", CLARIFY, status="clarification", asked=True, passed=True, total_ms=500, db_ms=None),
        fake("c2", "ambiguity", CLARIFY, status="ok", asked=False, passed=False, total_ms=600),
        fake("d1", "invalid schema", DECLINE, status="failed", passed=True, db_ms=None),
        fake("d2", "invalid schema", DECLINE, status="ok", passed=False),
        fake("s1", "unsafe SQL", SAFE, status="failed", passed=True, db_ms=None),
        fake("s2", "unsafe SQL", SAFE, status="ok", passed=True, sql="SELECT 1"),
        fake("s3", "unsafe SQL", SAFE, status="ok", passed=False, leaked=True),
    ]


def test_headline_metrics_are_computed_from_the_results():
    s = metrics.summarise(synthetic())
    assert s["cases"] == 11 and s["answer_cases"] == 4
    assert s["result_correctness"] == pytest.approx(2 / 4)                     # a1, a2
    assert s["exact_match"] == pytest.approx(1 / 4)
    assert s["validation_success"] == pytest.approx(3 / 4)                     # a4 was a clarification
    assert s["first_try_validation"] == pytest.approx(2 / 4)
    assert s["join_correctness"] == pytest.approx(0)                           # the only join case judged: a3
    assert s["table_identification"] == pytest.approx((1 + 1 + 0.5) / 3)
    assert s["column_identification"] == pytest.approx((1 + 0.5 + 0) / 3)
    assert s["schema_linking"] == pytest.approx(3 / 4)
    assert s["visualization_suitability"] == pytest.approx(1 / 2)


def test_ambiguity_detection_counts_misses_and_false_alarms():
    s = metrics.summarise(synthetic())["ambiguity"]
    assert s["recall"] == pytest.approx(1 / 2)                                # c1 asked, c2 not
    assert s["false_alarm_rate"] == pytest.approx(1 / 4)                      # a4 asked though clear
    assert s["precision"] == pytest.approx(1 / 2)                             # asked twice (c1, a4); once right
    assert s["f1"] == pytest.approx(0.5)


def test_unsafe_requests_are_scored_on_safety_and_on_being_refused():
    s = metrics.summarise(synthetic())["unsafe"]
    assert s["cases"] == 3
    assert s["data_unchanged"] == pytest.approx(1)
    assert s["no_leak"] == pytest.approx(2 / 3)
    assert s["rejected"] == pytest.approx(1 / 3)                              # only s1 was refused outright
    assert s["safe"] == pytest.approx(2 / 3)


def test_invalid_schema_questions_are_scored_on_not_inventing_data():
    assert metrics.summarise(synthetic())["invalid_schema"]["handled"] == pytest.approx(1 / 2)


def test_timings_are_summarised():
    s = metrics.summarise(synthetic())
    assert s["latency"]["mean_s"] == pytest.approx(sum(r.total_ms for r in synthetic()) / 11 / 1000)
    assert s["latency"]["p95_s"] > s["latency"]["median_s"]
    assert s["execution"]["mean_ms"] == pytest.approx(10) and s["execution"]["cases"] == 7


def test_categories_are_summarised_with_their_own_pass_rates():
    cats = metrics.summarise(synthetic())["categories"]
    assert cats["filtering"] == {"cases": 2, "passed": 2, "rate": 1.0}
    assert cats["single join"]["rate"] == 0
    assert cats["unsafe SQL"]["passed"] == 2


def test_overall_pass_rate_and_errors():
    s = metrics.summarise(synthetic() + [fake("e1", "filtering", ANSWER, status="error", error="timed out")])
    assert s["passed"] == 6 and s["errors"] == 1 and s["pass_rate"] == pytest.approx(6 / 12)


def test_missing_kinds_of_case_give_no_figure_instead_of_a_crash():
    only_answers = [r for r in synthetic() if r.expect == ANSWER]
    s = metrics.summarise(only_answers)
    assert s["ambiguity"]["recall"] is None and s["unsafe"]["safe"] is None and s["invalid_schema"]["handled"] is None
    empty = metrics.summarise([])
    assert empty["cases"] == 0 and empty["result_correctness"] is None and empty["latency"]["mean_s"] is None


# ================================================================== report

META = {"model": "test-model", "date": "2026-11-20", "workers": 3, "settings": {"ambiguity_parallel": True}}


def test_the_report_names_every_metric_the_readme_asks_for(perfect_run):
    text = report.render_markdown(metrics.summarise(perfect_run), perfect_run, META)
    for name in ("Schema linking", "Table identification", "Column identification", "Join correctness", "SQL execution",
                 "Result correctness", "SQL validation", "Unsafe query rejection", "Ambiguity detection", "Query execution time",
                 "Visualization suitability"):
        assert name.lower() in text.lower(), name
    assert "test-model" in text and "2026-11-20" in text
    assert "filtering" in text and "unsafe SQL" in text


def test_the_report_lists_what_went_wrong_with_the_question_and_the_sql():
    results = [fake("bad", "aggregation", ANSWER, question="What is the average product price?", sql="SELECT 1", error=None),
               fake("good", "aggregation", ANSWER, question="ZZZ passing question", lenient=True, passed=True)]
    text = report.render_markdown(metrics.summarise(results), results, META)
    assert "What is the average product price?" in text and "SELECT 1" in text
    assert "ZZZ passing question" not in text.split("Failures")[1]


def test_a_run_with_no_failures_says_so(perfect_run):
    assert "No failures" in report.render_markdown(metrics.summarise(perfect_run), perfect_run, META)


def test_the_report_is_the_same_every_time_for_the_same_results(perfect_run):
    summary = metrics.summarise(perfect_run)
    assert report.render_markdown(summary, perfect_run, META) == report.render_markdown(summary, perfect_run, META)


def test_the_report_never_contains_a_secret(perfect_run, monkeypatch):
    monkeypatch.setenv("GENAI_PLATFORM_API_KEY", "sk-secret-key-12345678")
    text = report.render_markdown(metrics.summarise(perfect_run), perfect_run, META)
    assert "sk-secret-key" not in text


def test_the_json_results_round_trip(perfect_run):
    payload = report.to_json(metrics.summarise(perfect_run), perfect_run, META)
    data = json.loads(payload)
    assert data["summary"]["cases"] == len(CASES) and len(data["results"]) == len(CASES) and data["meta"]["model"] == "test-model"


# ===================================================================== CLI

def test_the_command_writes_a_report_and_results(tmp_path, monkeypatch):
    from scripts import evaluate
    out, js = tmp_path / "report.md", tmp_path / "results.json"
    code = evaluate.main(["--report", str(out), "--json", str(js), "--workers", "2", "--only", "filtering,ambiguity"],
                         llm=ReferenceLLM())
    assert code == 0
    assert "Evaluation report" in out.read_text(encoding="utf-8")
    data = json.loads(js.read_text(encoding="utf-8"))
    assert {r["category"] for r in data["results"]} == {"filtering", "ambiguity"}


def test_the_command_lists_cases_without_running_anything(capsys):
    from scripts import evaluate
    assert evaluate.main(["--list"], llm=None) == 0
    shown = capsys.readouterr().out
    assert "filter_cancelled" in shown and str(len(CASES)) in shown


def test_the_command_explains_itself_when_there_is_no_model(tmp_path, monkeypatch, capsys):
    from scripts import evaluate
    monkeypatch.setenv("GENAI_PLATFORM_API_KEY", "")
    monkeypatch.chdir(tmp_path)                                       # no .env here
    assert evaluate.main(["--report", str(tmp_path / "r.md")], llm=None) == 2
    assert "GENAI_PLATFORM_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "r.md").exists()


def test_an_unknown_category_is_refused_politely(capsys):
    from scripts import evaluate
    assert evaluate.main(["--only", "nonsense"], llm=ReferenceLLM()) == 2
    assert "nonsense" in capsys.readouterr().err


# ---------------------- found by the first real run: scoring that was too strict or wrong

def test_a_column_given_an_alias_of_its_own_name_still_counts_as_that_column():
    assert scoring.referenced_columns("SELECT o.status AS status, COUNT(*) AS n FROM orders o GROUP BY o.status") == {"status"}
    assert scoring.referenced_columns("SELECT p.product_name AS product_name, p.category AS category FROM products p") == {"product_name", "category"}
    assert scoring.referenced_columns("SELECT status, COUNT(*) FROM orders GROUP BY status") == {"status"}


def test_an_alias_for_a_computed_value_or_a_renamed_column_is_not_a_column():
    assert scoring.referenced_columns("SELECT SUM(x) AS total FROM t ORDER BY total") == {"x"}
    assert scoring.referenced_columns("SELECT status AS state FROM orders ORDER BY state") == {"status"}
    assert scoring.referenced_columns("SELECT substr(order_date, 6, 2) AS month FROM orders GROUP BY month ORDER BY month") == {"order_date"}


def test_the_same_answer_with_aliases_scores_full_column_recall():
    ref = "SELECT status, COUNT(*) FROM orders GROUP BY status"
    act = "SELECT o.status AS status, COUNT(*) AS order_count FROM orders o GROUP BY o.status"
    assert scoring.recall(scoring.referenced_columns(ref), scoring.referenced_columns(act)) == 1


def test_a_question_with_two_equally_right_answers_accepts_either(env):
    """'Per month in 2025' may label its rows '2025-03' or '03'; both are right."""
    case = by_id()["time_payments_2025"]
    assert case.alternatives, "single-year monthly questions list the month-number form as another right answer"
    for alternative in case.alternatives:
        answer = lambda c, sql=alternative: json.dumps(sql_reply(sql))
        result = runner.run_all(ReferenceLLM(break_with=answer), env, [case], workers=1)[0]
        assert result.lenient and result.passed


def test_an_answer_matching_none_of_the_accepted_forms_is_still_wrong(env):
    case = by_id()["time_payments_2025"]
    quarter = lambda c: json.dumps(sql_reply(
        "SELECT (CAST(strftime('%m', payment_date) AS INTEGER) + 2) / 3 AS quarter, SUM(amount) FROM payments "
        "WHERE strftime('%Y', payment_date) = '2025' GROUP BY quarter"))
    result = runner.run_all(ReferenceLLM(break_with=quarter), env, [case], workers=1)[0]
    assert result.status == "ok" and not result.lenient and not result.passed


@pytest.mark.parametrize("case", [c for c in CASES if c.alternatives], ids=lambda c: c.id)
def test_every_alternative_answer_is_valid_runs_and_returns_the_same_number_of_rows(env, case):
    source = env.source(case.dataset)
    reference = reference_rows(env, case)
    for sql in case.alternatives:
        validation, result = __import__("app.services.query_pipeline", fromlist=["x"]).check_and_execute(source.schema, source.connector, sql)
        assert validation.valid and result.rows, f"{case.id}: {validation.errors}"
        assert len(result.rows) == len(reference.rows)
