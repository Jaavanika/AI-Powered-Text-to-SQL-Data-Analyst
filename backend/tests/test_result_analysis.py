import math

import pytest

from app.services.result_analysis import format_number, profile_result


def kinds(profile):
    return {c.name: c.kind for c in profile.columns}


def test_kinds_and_statistics():
    rows = [["2024-01", "Electronics", 10.5, 3, True, 1], ["2024-02", "Furniture", 20.0, 5, False, 2],
            ["2024-03", "Electronics", None, 7, True, 3]]
    p = profile_result(["month", "category", "revenue", "qty", "flag", "product_id"], rows)
    assert kinds(p) == {"month": "temporal", "category": "categorical", "revenue": "numeric",
                        "qty": "numeric", "flag": "boolean", "product_id": "identifier"}
    revenue = next(c for c in p.columns if c.name == "revenue")
    assert (revenue.min, revenue.max, revenue.sum, revenue.mean, revenue.median) == (10.5, 20.0, 30.5, 15.25, 15.25)
    assert revenue.null_count == 1 and revenue.distinct_count == 2
    qty = next(c for c in p.columns if c.name == "qty")
    assert qty.is_integer is True
    category = next(c for c in p.columns if c.name == "category")
    assert [(v.value, v.count) for v in category.top_values] == [("Electronics", 2), ("Furniture", 1)]
    month = next(c for c in p.columns if c.name == "month")
    assert (month.first, month.last) == ("2024-01", "2024-03")


@pytest.mark.parametrize("name,values,expected", [
    ("day", ["2025-01-31", "2025-02-01"], "temporal"),
    ("ts", ["2025-01-31 10:30:00", "2025-02-01T08:00"], "temporal"),
    ("ym", ["2025-01", "2025-02"], "temporal"),
    ("week", ["2025-03", "2025-04"], "temporal"),
    ("y", ["2023", "2024"], "temporal"),                    # a year as text
    ("month", ["01", "02", "12"], "temporal"),              # strftime('%m')
    ("month", [1, 2, 12], "temporal"),
    ("year", [2023, 2024], "temporal"),
    ("quarter", [1, 2, 3, 4], "temporal"),
    ("count", [1, 2, 12], "numeric"),                       # small ints are not periods without a period-like name
    ("month", [1, 2, 99], "numeric"),                       # name says month but the values do not fit
    ("customer_id", [1, 1, 2], "identifier"),               # repeats are still an id
    ("id", [1, 2], "identifier"),
    ("price", [1.5, 2.5], "numeric"),
    ("flag", [True, False], "boolean"),
    ("zip", ["02134", "10001"], "categorical"),
])
def test_kind_detection(name, values, expected):
    rows = [[v] for v in values]
    assert kinds(profile_result([name], rows))[name] == expected


def test_free_text_with_many_distinct_values_is_text():
    rows = [[f"comment {i}"] for i in range(80)]
    assert kinds(profile_result(["comment"], rows)) == {"comment": "text"}


def test_mixed_and_all_null_columns_do_not_crash():
    p = profile_result(["mixed", "empty"], [[1, None], ["a", None], [2.5, None]])
    assert kinds(p) == {"mixed": "categorical", "empty": "text"}
    assert p.columns[1].null_count == 3 and p.columns[1].distinct_count == 0


def test_duplicate_and_blank_column_names_are_made_unique():
    p = profile_result(["COUNT(*)", "COUNT(*)", ""], [[1, 2, 3]])
    assert [c.name for c in p.columns] == ["count", "count_2", "column"]
    assert [c.original_name for c in p.columns] == ["COUNT(*)", "COUNT(*)", "column"]


def test_empty_result():
    p = profile_result(["a", "b"], [])
    assert p.row_count == 0 and p.facts == ["The query returned no rows."]
    assert [c.name for c in p.columns] == ["a", "b"]


def test_facts_for_a_category_and_a_measure():
    rows = [["Electronics", 600], ["Furniture", 300], ["Apparel", 100]]
    p = profile_result(["category", "revenue"], rows)
    assert p.facts == [
        "The result has 3 rows.",
        "Highest revenue: Electronics (600), 60.0% of the total.",
        "Lowest revenue: Apparel (100).",
        "Total revenue: 1000; average 333.33.",
    ]


def test_facts_describe_change_over_time_with_numeric_periods_in_order():
    rows = [[m, v] for m, v in [(10, 40.0), (2, 10.0), (12, 50.0), (1, 20.0)]]
    p = profile_result(["month", "sales"], rows)
    assert "sales moved from 20 in 1 to 50 in 12 (+150.0%)." in p.facts      # 12 sorts after 2, not before


def test_facts_for_a_single_row_and_for_truncated_results():
    assert "total is 40." in " ".join(profile_result(["total"], [[40]]).facts)
    p = profile_result(["a"], [[1], [2]], truncated=True)
    assert "cut off at the row limit" in p.facts[0] and p.truncated is True


def test_no_share_or_total_for_columns_with_negative_values():
    p = profile_result(["item", "delta"], [["a", -5], ["b", 10]])
    assert not any("% of the total" in f or "Total delta" in f for f in p.facts)


def test_unusual_values_never_raise():
    rows = [[math.nan, "x" * 5000, 10 ** 30, "é💥", None, float("inf")],
            [1.5, "y", -10 ** 30, "ü", None, 2.0]]
    p = profile_result(["a", "b", "c", "d", "e", "f"], rows)
    assert p.row_count == 2 and len(p.columns) == 6


def test_wide_and_long_results_are_handled():
    rows = [[i * j for j in range(30)] for i in range(1000)]
    p = profile_result([f"c{j}" for j in range(30)], rows)
    assert p.row_count == 1000 and len(p.facts) <= 12


def test_format_number():
    assert [format_number(v) for v in (5, 5.0, 5.5, 5.126, 1234567.891, 0.1 + 0.2, -3.0)] == [
        "5", "5", "5.5", "5.13", "1234567.89", "0.3", "-3"]
