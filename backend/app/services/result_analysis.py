"""Profiling of a query result: column kinds, statistics and findings computed from the data.

Everything here is deterministic. The "facts" are the only claims the summary step is later
allowed to build on, so they are computed, never generated.
"""

import re
from collections.abc import Sequence

import numpy as np
import pandas as pd

from app.schemas import ColumnProfile, ResultProfile, ValueCount
from app.core.identifiers import unique_identifiers

MAX_CATEGORIES = 50          # text columns with more distinct values are free text, not labels
TOP_VALUES = 5
MAX_FACT_MEASURES = 3

_ISO_DATE = re.compile(r"^\d{4}-\d{2}(-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?)?$")
_ISO_WEEK = re.compile(r"^\d{4}-W?\d{2}$")
_YEAR = re.compile(r"^(19|20)\d{2}$")
# Column names that suggest a time period, with the plausible range of their numeric values.
_PERIOD_RANGES = {"year": (1900, 2100), "yr": (1900, 2100), "month": (1, 12), "mon": (1, 12),
                  "quarter": (1, 4), "week": (1, 53), "day": (1, 31)}
_PERIOD_NAME = re.compile(r"(?:^|_)(year|yr|month|mon|quarter|week|day)(?:$|_)")
_ID_NAME = re.compile(r"(?:^|_)id$")


def format_number(value: float) -> str:
    """Numbers as they appear in facts and prompts: integers plainly, others to 2 decimals."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _text(value, limit: int = 80) -> str:
    """A data value as one line of text. Facts end up in LLM prompts, so data must not add lines."""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value)).strip()[:limit]


def _number(value) -> bool:
    return isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, (bool, np.bool_))


def _period_hint(name: str, values: list) -> bool:
    match = _PERIOD_NAME.search(name.lower())
    if not match:
        return False
    low, high = _PERIOD_RANGES[match.group(1)]
    try:
        return all(low <= float(v) <= high for v in values)
    except (TypeError, ValueError):
        return False


def _kind(name: str, values: list) -> tuple[str, bool]:
    """(kind, is_integer) for the non-null values of a column."""
    if not values:
        return "text", False
    if all(isinstance(v, (bool, np.bool_)) for v in values):
        return "boolean", False
    if all(_number(v) for v in values):
        integers = all(float(v).is_integer() for v in values)
        if integers and _period_hint(name, values):
            return "temporal", True
        if integers and (_ID_NAME.search(name.lower()) or name.lower() == "id"):
            return "identifier", True     # a key is a label even when it repeats; it is never summed
        return "numeric", integers
    strings = [str(v) for v in values]
    if all(_ISO_DATE.match(s) or _ISO_WEEK.match(s) for s in strings):
        return "temporal", False
    if all(_YEAR.match(s) for s in strings):
        return "temporal", False
    if all(s.isdigit() for s in strings) and _period_hint(name, strings):
        return "temporal", False          # e.g. strftime('%m', ...) returns '03'
    return ("categorical" if len(set(strings)) <= MAX_CATEGORIES else "text"), False


def _clean(value):
    if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
        return None
    return value.item() if isinstance(value, np.generic) else value


def profile_result(columns: Sequence[str], rows: Sequence[Sequence], truncated: bool = False) -> ResultProfile:
    names = unique_identifiers([c or "" for c in columns], fallback="column")
    frame = pd.DataFrame([list(r) for r in rows], columns=names, dtype=object)
    profiles: list[ColumnProfile] = []

    for name, original in zip(names, columns):
        series = frame[name]
        # NaN and infinity cannot be carried in JSON or charted, so they count as missing.
        present = [v for v in (_clean(x) for x in series) if v is not None]
        kind, is_integer = _kind(name, present)
        profile = ColumnProfile(
            name=name, original_name=original or name, kind=kind, is_integer=is_integer,
            null_count=len(series) - len(present), distinct_count=len({str(v) for v in present}))
        if kind in ("numeric", "identifier") and present:
            numbers = pd.Series(present, dtype=float)
            profile.min, profile.max = float(numbers.min()), float(numbers.max())
            profile.mean, profile.median = round(float(numbers.mean()), 6), float(numbers.median())
            profile.sum = round(float(numbers.sum()), 6)
        elif kind == "temporal" and present:
            if all(_number(v) for v in present):
                profile.first, profile.last = format_number(min(present)), format_number(max(present))
            else:
                ordered = sorted(str(v) for v in present)
                profile.first, profile.last = ordered[0], ordered[-1]
        elif kind == "categorical":
            counts = pd.Series([str(v) for v in present]).value_counts()
            profile.top_values = [ValueCount(value=_text(v), count=int(c)) for v, c in counts.head(TOP_VALUES).items()]
        profiles.append(profile)

    return ResultProfile(
        row_count=len(rows), truncated=truncated, columns=profiles,
        facts=_facts(frame, profiles, truncated),
    )


def label_and_measures(profiles: Sequence[ColumnProfile]) -> tuple[ColumnProfile | None, list[ColumnProfile]]:
    """The column that labels rows (time first, then categories, then ids) and the numeric measures."""
    measures = [p for p in profiles if p.kind == "numeric"]
    for wanted in ("temporal", "categorical", "identifier", "text"):
        label = next((p for p in profiles if p.kind == wanted), None)
        if label:
            return label, measures
    return None, measures


def _facts(frame: pd.DataFrame, profiles: Sequence[ColumnProfile], truncated: bool) -> list[str]:
    n = len(frame)
    facts: list[str] = []
    if n == 0:
        return ["The query returned no rows."]
    facts.append(f"The result has {n} row{'s' if n != 1 else ''}"
                 + (" (it was cut off at the row limit, so more rows exist)." if truncated else "."))

    label, measures = label_and_measures(profiles)
    for measure in measures[:MAX_FACT_MEASURES]:
        values = pd.to_numeric(frame[measure.name], errors="coerce")
        valid = values.notna()
        if not valid.any():
            continue
        if n >= 2 and label is not None and measure.min != measure.max:
            top, bottom = values[valid].idxmax(), values[valid].idxmin()
            line = f"Highest {measure.name}: {_text(frame.loc[top, label.name])} ({format_number(values[top])})"
            if measure.min is not None and measure.min >= 0 and measure.sum:
                line += f", {values[top] / measure.sum * 100:.1f}% of the total"
            facts.append(line + ".")
            facts.append(f"Lowest {measure.name}: {_text(frame.loc[bottom, label.name])} ({format_number(values[bottom])}).")
            if label.kind == "temporal" and valid.sum() >= 2:
                periods = frame.loc[valid, label.name]
                # period numbers sort numerically (2 before 10); ISO text sorts as text
                order = (periods.astype(float) if label.is_integer else periods.astype(str)).sort_values().index
                first, last = values[order[0]], values[order[-1]]
                move = (f"{measure.name} moved from {format_number(first)} in {_text(frame.loc[order[0], label.name])} "
                        f"to {format_number(last)} in {_text(frame.loc[order[-1], label.name])}")
                if first != 0:
                    move += f" ({(last - first) / abs(first) * 100:+.1f}%)"
                facts.append(move + ".")
        if n >= 2 and measure.sum is not None and measure.min is not None and measure.min >= 0:
            facts.append(f"Total {measure.name}: {format_number(measure.sum)}; average {format_number(measure.mean)}.")
        elif n == 1:
            facts.append(f"{measure.name} is {format_number(values[valid].iloc[0])}.")
    return facts
