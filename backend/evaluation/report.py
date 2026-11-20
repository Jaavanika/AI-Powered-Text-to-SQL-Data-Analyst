"""Write the results as a Markdown report and as JSON. The same results always produce the same text."""

import json

from evaluation.cases import ANSWER, CLARIFY, DECLINE, SAFE

EXPECTED = {
    ANSWER: "a result matching the reference answer",
    CLARIFY: "a clarifying question instead of a guess",
    DECLINE: "to decline: the data does not exist",
    SAFE: "no change to the data, and nothing blocked revealed",
}


def pct(value) -> str:
    if value is None:
        return "n/a"
    text = f"{value * 100:.1f}"
    return (text[:-2] if text.endswith(".0") else text) + "%"


def seconds(value) -> str:
    return "n/a" if value is None else f"{value:.1f} s"


def milliseconds(value) -> str:
    return "n/a" if value is None else f"{value:.1f} ms"


def _metrics_table(s: dict) -> list[str]:
    rows = [
        ("Schema linking accuracy", pct(s["schema_linking"]),
         "Questions where the tables handed to the model include every table the right answer needs (checked before any model call)."),
        ("Table identification accuracy", pct(s["table_identification"]),
         "Of the tables the right answer reads, the share the generated SQL also reads."),
        ("Column identification accuracy", pct(s["column_identification"]),
         "Of the columns the right answer uses, the share the generated SQL also uses."),
        ("Join correctness", pct(s["join_correctness"]),
         "Questions needing two or more tables where the SQL reads exactly the right set of tables."),
        ("SQL execution accuracy", pct(s["validation_success"]),
         "Questions that ended in SQL that passed every check and ran (after any corrections)."),
        ("SQL validation success rate", pct(s["first_try_validation"]),
         "Questions whose first SQL passed validation and ran, with no correction needed."),
        ("Result correctness", pct(s["result_correctness"]),
         "Answers whose rows match the reference result. Extra or reordered columns are allowed; wrong or missing rows are not."),
        ("Exact result match", pct(s["exact_match"]),
         "As above, but also requiring exactly the reference's columns."),
        ("Visualization suitability", pct(s["visualization_suitability"]),
         "Answers whose automatic chart is one of the types that suit the question."),
    ]
    return ["| Metric | Result | What it measures |", "|---|---|---|",
            *[f"| {name} | **{value}** | {meaning} |" for name, value, meaning in rows]]


def _special_tables(s: dict) -> list[str]:
    a, u, i = s["ambiguity"], s["unsafe"], s["invalid_schema"]
    return [
        "## Ambiguity detection", "",
        "| Measure | Result |", "|---|---|",
        f"| Vague questions that were asked about (recall) | **{pct(a['recall'])}** |",
        f"| Clear questions that were asked about needlessly (false alarms) | **{pct(a['false_alarm_rate'])}** |",
        f"| Questions asked about that really were vague (precision) | **{pct(a['precision'])}** |",
        f"| F1 | **{pct(a['f1'])}** |",
        "", "## Safety", "",
        "| Measure | Result |", "|---|---|",
        f"| Unsafe query rejection rate (refused outright, not answered) | **{pct(u['rejected'])}** over {u['cases']} requests |",
        f"| Data unchanged after the request | **{pct(u['data_unchanged'])}** |",
        f"| Blocked values kept out of answers | **{pct(u['no_leak'])}** |",
        f"| Handled safely overall (nothing changed, nothing leaked, nothing but a read-only query run) | **{pct(u['safe'])}** |",
        f"| Questions about data that does not exist handled without inventing it | **{pct(i['handled'])}** over {i['cases']} questions |",
    ]


def render_markdown(summary: dict, results, meta: dict) -> str:
    s = summary
    lines = [
        "# Evaluation report", "",
        f"Generated {meta.get('date', '')} with model `{meta.get('model', 'unknown')}`. "
        f"{s['cases']} questions, {meta.get('workers', 1)} at a time.", "",
        "Each question has a reference SQL that is run on the same data; an answer is correct when it returns what the "
        "reference returns, not merely when it runs. "
        "The data is a private copy, and the run checks that it is unchanged afterwards.", "",
    ]
    if meta.get("settings"):
        lines += ["Settings: " + ", ".join(f"`{k}={v}`" for k, v in meta["settings"].items()) + ".", ""]
    lines += [
        "## Summary", "",
        f"**{s['passed']} of {s['cases']} questions passed ({pct(s['pass_rate'])}).** "
        f"{s['errors']} ended in an error before an answer was produced.", "",
        "## Accuracy", "", *_metrics_table(s), "",
        *_special_tables(s), "",
        "## Query execution time", "",
        "| Measure | Result |", "|---|---|",
        f"| Whole question, mean | **{seconds(s['latency']['mean_s'])}** |",
        f"| Whole question, median | {seconds(s['latency']['median_s'])} |",
        f"| Whole question, 95th percentile | {seconds(s['latency']['p95_s'])} |",
        f"| Running the SQL on the database, mean ({s['execution']['cases']} queries) | {milliseconds(s['execution']['mean_ms'])} |",
        f"| Running the SQL on the database, 95th percentile | {milliseconds(s['execution']['p95_ms'])} |",
        "", "The whole-question time includes the model calls, which dominate it.", "",
        "## By category", "", "| Category | Passed | Questions | Rate |", "|---|---|---|---|",
    ]
    for name, c in s["categories"].items():
        lines.append(f"| {name} | {c['passed']} | {c['cases']} | {pct(c['rate'])} |")

    failed = [r for r in results if not r.passed]
    lines += ["", "## Failures", ""]
    if not failed:
        lines.append("No failures: every question passed.")
    for r in failed:
        lines += [
            f"### `{r.id}` ({r.category})", "",
            f"- **Question:** {r.question}",
            f"- **Expected:** {EXPECTED[r.expect]}",
            f"- **Got:** {r.status}" + (f", after {r.attempts} correction(s)" if r.attempts else ""),
        ]
        if r.error:
            lines.append(f"- **Error:** {r.error}")
        if r.clarification:
            lines.append(f"- **Asked:** {r.clarification}")
        if r.expect == SAFE and not r.data_unchanged:
            lines.append("- **The data changed.**")
        if r.leaked:
            lines.append("- **A blocked value appeared in the answer.**")
        if r.sql:
            lines += ["", "```sql", r.sql, "```"]
        if r.reference_sql:
            lines += ["", "Reference:", "", "```sql", r.reference_sql, "```"]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def to_json(summary: dict, results, meta: dict) -> str:
    return json.dumps({"meta": meta, "summary": summary, "results": [r.to_dict() for r in results]}, indent=2)
