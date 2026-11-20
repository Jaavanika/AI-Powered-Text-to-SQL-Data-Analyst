"""Chart selection: choose a visualisation from the shape of a result and build its data.

The choice is rule-based and deterministic. A chart type named by the user ("as a pie chart") is
honoured when the data can support it; otherwise the best suitable chart is used and the reason
is recorded in `notes`.
"""

import re
from collections.abc import Sequence
from typing import Any

import numpy as np

from app.core.identifiers import unique_identifiers
from app.schemas import ChartSpec, ChartType, ResultProfile
from app.services.result_analysis import label_and_measures

MAX_BAR_ITEMS = 25
MAX_PIE_SLICES = 8
MAX_SERIES = 8
MAX_LINE_POINTS = 500
MAX_SCATTER_POINTS = 500
MAX_KPIS = 6
MAX_BAR_MEASURES = 3
MAX_LINE_MEASURES = 4
MIN_SCATTER_ROWS = 5
LONG_LABEL = 14

_REQUESTED: list[tuple[re.Pattern, ChartType]] = [
    (re.compile(r"\b(pie|donut|doughnut)\b", re.I), "pie"),
    (re.compile(r"\b(scatter|scatterplot)\b", re.I), "scatter"),
    (re.compile(r"\b(line\s*(chart|graph)?|trend\s*line)\b", re.I), "line"),
    (re.compile(r"\b(bar|column)\s*(chart|graph)?s?\b", re.I), "bar"),
    (re.compile(r"\b(as a table|table only|in a table|tabular)\b", re.I), "table"),
]
_SHARE_CUES = re.compile(r"\b(share|proportion|percentage|percent|distribution|breakdown|split|composition|portion)\b", re.I)


def requested_chart_type(text: str | None) -> ChartType | None:
    """A chart type named in free text, e.g. 'show revenue as a pie chart'."""
    for pattern, chart_type in _REQUESTED:
        if text and pattern.search(text):
            return chart_type
    return None


def _records(rows: Sequence[Sequence], names: Sequence[str], wanted: Sequence[str]) -> list[dict[str, Any]]:
    index = {n: i for i, n in enumerate(names)}
    out = []
    for row in rows:
        record = {}
        for name in wanted:
            value = row[index[name]]
            if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
                value = None
            record[name] = value
        out.append(record)
    return out


def _title(question: str | None, y: Sequence[str], x: str | None) -> str:
    if question and question.strip():
        text = " ".join(question.split()).rstrip("?.! ")
        return text[:90]
    return f"{', '.join(y)} by {x}" if x else ", ".join(y)


def _sort_time(records: list[dict], x: str, numeric: bool) -> list[dict]:
    return sorted(records, key=lambda r: (r[x] is None, float(r[x]) if numeric and r[x] is not None else str(r[x])))


def _pivot(records: list[dict], x: str, series: str, value: str, numeric_x: bool) -> tuple[list[dict], list[str], list[str]]:
    """Long format (x, series, value) to wide format (one record per x, one field per series)."""
    notes: list[str] = []
    totals: dict[str, float] = {}
    for r in records:
        totals[str(r[series])] = totals.get(str(r[series]), 0) + (r[value] or 0)
    names = sorted(totals, key=lambda s: -totals[s])
    if len(names) > MAX_SERIES:
        notes.append(f"Showing the {MAX_SERIES} largest of {len(names)} {series} values.")
        names = names[:MAX_SERIES]
    wide: dict[Any, dict[str, Any]] = {}
    for r in records:
        if str(r[series]) in names:
            wide.setdefault(r[x], {x: r[x]})[str(r[series])] = r[value]
    return _sort_time(list(wide.values()), x, numeric_x), names, notes


def select_chart(
    profile: ResultProfile,
    columns: Sequence[str],
    rows: Sequence[Sequence],
    question: str | None = None,
    requested: ChartType | None = None,
) -> ChartSpec | None:
    """Return the chart to draw, or None when there is nothing to draw (no rows)."""
    if profile.row_count == 0:
        return None
    names = unique_identifiers([c or "" for c in columns], fallback="column")
    n = profile.row_count
    label, measures = label_and_measures(profile.columns)
    temporal = next((c for c in profile.columns if c.kind == "temporal"), None)
    categoricals = [c for c in profile.columns if c.kind in ("categorical", "identifier", "text")]
    notes: list[str] = []
    wanted = requested or requested_chart_type(question)

    feasible = _feasible(n, label, measures, temporal, categoricals)
    chart_type: ChartType | None = None
    if wanted:
        if wanted in feasible or wanted == "table":
            chart_type = wanted
        else:
            notes.append(f"A {wanted} chart does not fit this data, so a suitable chart is shown instead.")
    if chart_type is None:
        chart_type = _auto_type(question, n, measures, temporal, categoricals, feasible)

    spec = _build(chart_type, profile, names, rows, question, measures, temporal, categoricals)
    spec.notes = notes + spec.notes
    spec.alternatives = [t for t in ("bar", "line", "pie", "scatter", "table") if t in feasible and t != spec.type]
    if "table" not in spec.alternatives and spec.type != "table":
        spec.alternatives.append("table")
    return spec


def _feasible(n, label, measures, temporal, categoricals) -> set[str]:
    feasible = {"table"}
    if not measures:
        return feasible
    if n == 1:
        feasible.add("kpi")
    if label is not None and n >= 1:
        feasible.add("bar")
    if temporal is not None and n >= 3:
        feasible.add("line")
    category_only = [c for c in categoricals if c.kind != "text"]
    first = measures[0]
    if (category_only and n >= 2 and n <= MAX_PIE_SLICES and temporal is None
            and first.min is not None and first.min >= 0 and (first.sum or 0) > 0):
        feasible.add("pie")
    if len(measures) >= 2 and n >= MIN_SCATTER_ROWS:
        feasible.add("scatter")
    return feasible


def _auto_type(question, n, measures, temporal, categoricals, feasible) -> ChartType:
    if not measures:
        return "table"
    if n == 1:
        return "kpi"
    if temporal is not None:
        return "line" if "line" in feasible else "bar"
    if "pie" in feasible and len(categoricals) == 1 and question and _SHARE_CUES.search(question):
        return "pie"
    if categoricals:
        return "bar"
    if "scatter" in feasible:
        return "scatter"
    return "table"


def _build(chart_type, profile, names, rows, question, measures, temporal, categoricals) -> ChartSpec:
    if chart_type == "table":
        return ChartSpec(type="table", title=_title(question, [], None))

    if chart_type == "kpi":
        record = _records(rows, names, names)[0]
        kpis = [{"label": m.original_name, "value": record[m.name]} for m in measures[:MAX_KPIS]]
        # A readable label (a name, a period) is the caption; a bare id is used only when it is all there is.
        caption_cols = [c for c in profile.columns if c.kind in ("categorical", "temporal", "text")]
        caption_cols = caption_cols or [c for c in profile.columns if c.kind == "identifier"]
        caption = ", ".join(str(record[c.name]) for c in caption_cols) or None
        return ChartSpec(type="kpi", title=_title(question, [m.name for m in measures], None),
                         data=kpis, caption=caption)

    if chart_type == "scatter":
        x_col, y_col = measures[0], measures[1]
        label_col = categoricals[0].name if categoricals else None
        fields = [x_col.name, y_col.name] + ([label_col] if label_col else [])
        data = [r for r in _records(rows, names, fields) if r[x_col.name] is not None and r[y_col.name] is not None]
        notes = [f"Showing the first {MAX_SCATTER_POINTS} of {len(data)} points."] if len(data) > MAX_SCATTER_POINTS else []
        return ChartSpec(type="scatter", title=_title(question, [y_col.name], x_col.name), x=x_col.name,
                         y=[y_col.name], x_kind="number", data=data[:MAX_SCATTER_POINTS], notes=notes)

    if chart_type == "pie":
        label = next(c for c in categoricals if c.kind != "text")
        measure = measures[0]
        data = _records(rows, names, [label.name, measure.name])
        notes = ([f"The pie shows {measure.name}; the other measures are in the table."]
                 if len(measures) > 1 else [])
        return ChartSpec(type="pie", title=_title(question, [measure.name], label.name),
                         x=label.name, y=[measure.name], x_kind="category", data=data, notes=notes)

    if chart_type == "line":
        x = temporal
        numeric_x = x.is_integer
        data = _records(rows, names, [c.name for c in profile.columns])
        others = [c for c in categoricals if c.kind == "categorical" and 2 <= c.distinct_count <= 25]
        notes: list[str] = []
        if others and len(measures) == 1:
            series = others[0]
            wide, y, notes = _pivot(data, x.name, series.name, measures[0].name, numeric_x)
        else:
            y = [m.name for m in measures[:MAX_LINE_MEASURES]]
            if len(measures) > MAX_LINE_MEASURES:
                notes.append(f"Showing the first {MAX_LINE_MEASURES} of {len(measures)} measures.")
            wide = _sort_time([{k: r[k] for k in [x.name] + y} for r in data], x.name, numeric_x)
        if len(wide) > MAX_LINE_POINTS:
            notes.append(f"Showing the first {MAX_LINE_POINTS} of {len(wide)} points.")
        title_measures = [measures[0].name] if others and len(measures) == 1 else y
        return ChartSpec(type="line", title=_title(question, title_measures, x.name), x=x.name, y=y,
                         x_kind="time", data=wide[:MAX_LINE_POINTS], notes=notes)

    # bar chart
    x = temporal or next((c for c in categoricals), None)
    assert x is not None
    data = _records(rows, names, [c.name for c in profile.columns])
    notes = []
    second = next((c for c in categoricals if c.name != x.name and c.kind == "categorical"
                   and 2 <= c.distinct_count <= MAX_SERIES), None)
    pivoted = bool(second and len(measures) == 1 and x.kind != "text")
    if pivoted:
        wide, y, notes = _pivot(data, x.name, second.name, measures[0].name, False)
        if x.kind == "temporal":
            wide = _sort_time(wide, x.name, x.is_integer)
        data_out = wide
    else:
        y = [m.name for m in measures[:MAX_BAR_MEASURES]]
        if len(measures) > MAX_BAR_MEASURES:
            notes.append(f"Showing the first {MAX_BAR_MEASURES} of {len(measures)} measures.")
        data_out = [{k: r[k] for k in [x.name] + y} for r in data]
        if x.kind == "temporal":
            data_out = _sort_time(data_out, x.name, x.is_integer)
    if len(data_out) > MAX_BAR_ITEMS:
        notes.append(f"Showing the first {MAX_BAR_ITEMS} of {len(data_out)} rows; the table has them all.")
        data_out = data_out[:MAX_BAR_ITEMS]
    longest = max((len(str(r[x.name])) for r in data_out), default=0)
    return ChartSpec(
        type="bar", title=_title(question, [measures[0].name] if pivoted else y, x.name), x=x.name, y=y,
        x_kind="time" if x.kind == "temporal" else "category", data=data_out, notes=notes,
        horizontal=(longest > LONG_LABEL or len(data_out) > 10) and x.kind != "temporal")
