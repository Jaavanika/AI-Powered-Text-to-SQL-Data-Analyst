import math

import pytest

from app.services.result_analysis import profile_result
from app.services.visualization import requested_chart_type, select_chart

MONTHS = [f"2024-{m:02d}" for m in range(1, 13)]


def chart(columns, rows, question=None, requested=None):
    profile = profile_result(columns, rows)
    return select_chart(profile, columns, rows, question, requested)


def test_no_rows_means_no_chart():
    assert chart(["a"], []) is None


def test_single_value_is_a_kpi_card():
    spec = chart(["total_revenue"], [[1234.5]])
    assert spec.type == "kpi" and spec.data == [{"label": "total_revenue", "value": 1234.5}]
    assert spec.caption is None


def test_single_row_with_a_label_is_a_kpi_with_a_caption():
    spec = chart(["product_name", "revenue", "units"], [["Laptop", 9000.5, 7]])
    assert spec.type == "kpi" and spec.caption == "Laptop"
    assert [d["label"] for d in spec.data] == ["revenue", "units"]


def test_a_kpi_caption_prefers_the_name_over_the_id():
    spec = chart(["product_id", "product_name", "revenue"], [[1, "Laptop Pro 14", 49362.0]])
    assert spec.type == "kpi" and spec.caption == "Laptop Pro 14"
    assert chart(["product_id", "revenue"], [[7, 10.0]]).caption == "7"       # an id is better than nothing


def test_time_series_is_a_line_chart_sorted_by_time():
    rows = [[m, i * 10.0] for i, m in reversed(list(enumerate(MONTHS)))]          # given in reverse
    spec = chart(["month", "sales"], rows)
    assert spec.type == "line" and spec.x == "month" and spec.y == ["sales"] and spec.x_kind == "time"
    assert [r["month"] for r in spec.data] == MONTHS


def test_numeric_periods_sort_numerically():
    spec = chart(["month", "n"], [[10, 1], [2, 2], [1, 3], [12, 4]])
    assert spec.type == "line" and [r["month"] for r in spec.data] == [1, 2, 10, 12]


def test_two_time_points_are_too_few_for_a_line():
    assert chart(["year", "sales"], [[2024, 10], [2025, 20]]).type == "bar"


def test_category_and_measure_is_a_bar_chart_in_the_query_order():
    spec = chart(["category", "revenue"], [["Electronics", 9], ["Furniture", 5], ["Apparel", 2]])
    assert spec.type == "bar" and spec.x == "category" and spec.y == ["revenue"] and spec.horizontal is False
    assert [r["category"] for r in spec.data] == ["Electronics", "Furniture", "Apparel"]


def test_long_labels_or_many_categories_go_horizontal():
    assert chart(["name", "n"], [["A very long product name", 1], ["Another long one", 2]]).horizontal is True
    assert chart(["c", "n"], [[f"c{i}", i] for i in range(12)]).horizontal is True


def test_several_measures_become_grouped_bars_up_to_three():
    spec = chart(["region", "a", "b", "c", "d"], [["N", 1, 2, 3, 4], ["S", 5, 6, 7, 8]])
    assert spec.type == "bar" and spec.y == ["a", "b", "c"]
    assert any("first 3 of 4 measures" in n for n in spec.notes)


def test_share_questions_get_a_pie_otherwise_a_bar():
    rows = [["N", 50], ["S", 30], ["E", 20]]
    assert chart(["region", "sales"], rows, "What is the share of sales by region?").type == "pie"
    assert chart(["region", "sales"], rows, "Sales by region").type == "bar"


def test_a_share_question_with_a_count_and_a_share_column_still_gets_a_pie():
    rows = [["Electronics", 346, 0.28], ["Furniture", 322, 0.26], ["Apparel", 278, 0.23]]
    spec = chart(["category", "category_qty", "share"], rows, "Show the share of quantity by category")
    assert spec.type == "pie" and spec.y == ["category_qty"]
    assert "other measures are in the table" in spec.notes[0]


def test_pie_is_not_used_for_many_slices_or_negative_values():
    many = [[f"c{i}", 1] for i in range(12)]
    assert chart(["c", "n"], many, "show the breakdown").type == "bar"
    assert chart(["c", "n"], [["a", 5], ["b", -2]], "show the breakdown").type == "bar"


def test_requested_type_is_honoured_when_possible():
    rows = [["N", 50], ["S", 30]]
    assert chart(["region", "sales"], rows, "sales by region as a pie chart").type == "pie"
    assert chart(["region", "sales"], rows, requested="table").type == "table"
    assert chart(["region", "sales"], rows, requested="pie").type == "pie"


def test_infeasible_request_falls_back_and_says_why():
    spec = chart(["month", "sales"], [[m, 1.0 * i] for i, m in enumerate(MONTHS)], requested="pie")
    assert spec.type == "line" and "pie chart does not fit" in spec.notes[0]
    spec = chart(["region", "sales"], [["N", 5], ["S", 6]], "show it as a line chart")
    assert spec.type == "bar" and "line chart does not fit" in spec.notes[0]


def test_two_numeric_columns_make_a_scatter_plot():
    rows = [[i, i * 2.5] for i in range(10)]
    spec = chart(["price", "units"], rows)
    assert spec.type == "scatter" and spec.x == "price" and spec.y == ["units"] and spec.x_kind == "number"
    assert len(spec.data) == 10


def test_scatter_needs_enough_rows_and_can_be_requested_with_labels():
    assert chart(["a", "b"], [[1, 2], [2, 3], [3, 5]]).type == "table"
    rows = [[f"p{i}", i, i * 3.0] for i in range(8)]
    assert chart(["product", "price", "units"], rows).type == "bar"
    spec = chart(["product", "price", "units"], rows, "price vs units as a scatter plot")
    assert spec.type == "scatter" and spec.data[0]["product"] == "p0"


def test_time_plus_category_plus_measure_is_a_multi_series_line():
    rows = [[m, cat, 10.0 + i] for i, m in enumerate(MONTHS[:4]) for cat in ("Electronics", "Furniture")]
    spec = chart(["month", "category", "revenue"], rows)
    assert spec.type == "line" and set(spec.y) == {"Electronics", "Furniture"}
    assert len(spec.data) == 4 and set(spec.data[0]) == {"month", "Electronics", "Furniture"}


def test_too_many_series_are_capped_to_the_largest():
    rows = [[m, f"s{i}", float(i)] for m in MONTHS[:3] for i in range(12)]
    spec = chart(["month", "series", "v"], rows)
    assert len(spec.y) == 8 and spec.y[0] == "s11" and any("8 largest of 12" in n for n in spec.notes)
    assert spec.title == "v by month"                       # not "s11, s10, s9, ... by month"


def test_two_categories_and_a_measure_make_a_grouped_bar():
    rows = [["N", "Electronics", 5], ["N", "Furniture", 3], ["S", "Electronics", 4], ["S", "Furniture", 6]]
    spec = chart(["region", "category", "revenue"], rows)
    assert spec.type == "bar" and spec.x == "region" and set(spec.y) == {"Electronics", "Furniture"}
    assert len(spec.data) == 2


def test_results_without_a_numeric_measure_are_just_a_table():
    spec = chart(["name", "city"], [["Ann", "Oslo"], ["Bob", "Rome"]])
    assert spec.type == "table"


def test_long_bar_charts_are_capped():
    spec = chart(["c", "n"], [[f"c{i}", i] for i in range(60)])
    assert spec.type == "bar" and len(spec.data) == 25 and any("first 25 of 60" in n for n in spec.notes)


def test_alternatives_list_other_feasible_charts_and_always_the_table():
    spec = chart(["region", "sales"], [["N", 5], ["S", 3], ["E", 2]])
    assert spec.type == "bar" and "pie" in spec.alternatives and "table" in spec.alternatives
    assert "bar" not in spec.alternatives and "scatter" not in spec.alternatives
    assert "table" not in chart(["name"], [["a"]]).alternatives


def test_nan_and_inf_become_null_in_chart_data():
    spec = chart(["region", "sales"], [["N", math.nan], ["S", 4.0], ["E", math.inf]])
    assert [r["sales"] for r in spec.data] == [None, 4.0, None]


def test_duplicate_column_names_use_the_unique_names_as_keys():
    spec = chart(["region", "COUNT(*)", "COUNT(*)"], [["N", 1, 2], ["S", 3, 4]])
    assert spec.y == ["count", "count_2"] and spec.data[0] == {"region": "N", "count": 1, "count_2": 2}


def test_title_comes_from_the_question_or_the_columns():
    assert chart(["r", "n"], [["a", 1], ["b", 2]], "Sales by region?").title == "Sales by region"
    assert chart(["r", "n"], [["a", 1], ["b", 2]]).title == "n by r"


@pytest.mark.parametrize("text,expected", [
    ("show it as a pie chart", "pie"), ("donut please", "pie"), ("a bar chart of sales", "bar"),
    ("column chart", "bar"), ("trend line of revenue", "line"), ("line graph", "line"),
    ("scatter plot of price vs units", "scatter"), ("show it as a table", "table"),
    ("total revenue by region", None), ("the pipeline barrier", None), (None, None), ("", None),
])
def test_requested_chart_type(text, expected):
    assert requested_chart_type(text) == expected
