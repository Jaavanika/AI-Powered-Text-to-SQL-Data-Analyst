"""Turn per-question results into the figures README section 15 asks for.

A figure is `None`, not 0, when there is nothing to measure it on (a run with no unsafe requests has no unsafe
rejection rate), so a missing figure is never mistaken for a bad one.
"""

from collections import defaultdict

from evaluation.cases import ANSWER, CLARIFY, DECLINE, SAFE
from evaluation.scoring import percentile


def _mean(values):
    values = list(values)
    return sum(values) / len(values) if values else None


def _rate(flags):
    flags = list(flags)
    return sum(1 for f in flags if f) / len(flags) if flags else None


def summarise(results) -> dict:
    answers = [r for r in results if r.expect == ANSWER]
    vague = [r for r in results if r.expect == CLARIFY]
    unsafe = [r for r in results if r.expect == SAFE]
    invalid = [r for r in results if r.expect == DECLINE]

    judged = lambda attr, rows=answers: [getattr(r, attr) for r in rows if getattr(r, attr) is not None]

    # Ambiguity is judged on the vague questions (should be asked about) and the clear ones (should not).
    asked_right = sum(1 for r in vague if r.asked)
    asked_wrong = sum(1 for r in answers if r.asked)
    recall = _rate(r.asked for r in vague)
    precision = asked_right / (asked_right + asked_wrong) if (asked_right + asked_wrong) else None
    f1 = (2 * precision * recall / (precision + recall)) if precision is not None and recall is not None and (precision + recall) else None

    latencies = [r.total_ms / 1000 for r in results]
    executions = [r.db_ms for r in results if r.db_ms is not None]

    categories: dict[str, dict] = defaultdict(lambda: {"cases": 0, "passed": 0})
    for r in results:
        categories[r.category]["cases"] += 1
        categories[r.category]["passed"] += 1 if r.passed else 0
    by_category = {name: {**c, "rate": c["passed"] / c["cases"]} for name, c in categories.items()}

    return {
        "cases": len(results),
        "answer_cases": len(answers),
        "passed": sum(1 for r in results if r.passed),
        "errors": sum(1 for r in results if r.status == "error"),
        "pass_rate": _rate(r.passed for r in results),
        # accuracy of the answers
        "result_correctness": _rate(r.lenient for r in answers),
        "exact_match": _rate(r.exact for r in answers),
        "validation_success": _rate(r.status == "ok" for r in answers),
        "first_try_validation": _rate(r.status == "ok" and r.first_try_valid for r in answers),
        "schema_linking": _rate(judged("link_ok")),
        "table_identification": _mean(judged("table_recall")),
        "column_identification": _mean(judged("column_recall")),
        "join_correctness": _rate(judged("join_ok")),
        "visualization_suitability": _rate(judged("chart_ok")),
        "ambiguity": {
            "recall": recall, "false_alarm_rate": _rate(r.asked for r in answers),
            "precision": precision, "f1": f1,
        },
        "invalid_schema": {"cases": len(invalid), "handled": _rate(r.passed for r in invalid)},
        "unsafe": {
            "cases": len(unsafe),
            "data_unchanged": _rate(r.data_unchanged for r in unsafe),
            "no_leak": _rate(not r.leaked for r in unsafe) if unsafe else None,
            "rejected": _rate(r.status != "ok" for r in unsafe),
            "safe": _rate(r.passed for r in unsafe),
        },
        "latency": {
            "mean_s": _mean(latencies), "median_s": percentile(latencies, 50) if latencies else None,
            "p95_s": percentile(latencies, 95) if latencies else None,
        },
        "execution": {
            "cases": len(executions), "mean_ms": _mean(executions),
            "p95_ms": percentile(executions, 95) if executions else None,
        },
        "categories": by_category,
    }
