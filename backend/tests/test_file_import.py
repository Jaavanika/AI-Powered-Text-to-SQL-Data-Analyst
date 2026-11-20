import pandas as pd

from app.services.file_import_service import clean_frame


def _types(sheet):
    return {c.name: c.type for c in sheet.columns}


def test_type_detection():
    df = pd.DataFrame({
        "id": [1, 2, 3],
        "price": [1.5, 2.25, 3.0],
        "count_with_gaps": [1.0, None, 3.0],
        "flag": [True, False, True],
        "name": ["a", "b", None],
        "day": ["2025-01-05", "2025-02-10", "2025-03-15"],
        "stamp": ["2025-01-05 10:30:00", "2025-02-10 08:00:00", None],
    })
    sheet = clean_frame("s", df)
    assert _types(sheet) == {
        "id": "integer", "price": "float", "count_with_gaps": "integer", "flag": "boolean",
        "name": "text", "day": "date", "stamp": "datetime",
    }
    assert list(sheet.df["day"]) == ["2025-01-05", "2025-02-10", "2025-03-15"]
    assert sheet.df["stamp"].iloc[1] == "2025-02-10 08:00:00"
    assert pd.isna(sheet.df["stamp"].iloc[2])


def test_mixed_date_column_stays_text():
    df = pd.DataFrame({"when": ["2025-01-05", "not a date", "2025-03-15"]})
    assert _types(clean_frame("s", df)) == {"when": "text"}


def test_text_that_looks_like_numbers_is_not_a_date():
    df = pd.DataFrame({"version": ["1.2.3", "4.5.6"]})
    assert _types(clean_frame("s", df)) == {"version": "text"}


def test_slash_dates_use_one_convention_per_column():
    month_first = clean_frame("s", pd.DataFrame({"d": ["03/04/2025", "12/25/2025"]}))
    assert _types(month_first) == {"d": "date"}
    assert list(month_first.df["d"]) == ["2025-03-04", "2025-12-25"]

    # 25/12/2025 only parses day-first, so the whole column is read day-first
    day_first = clean_frame("s", pd.DataFrame({"d": ["03/04/2025", "25/12/2025"]}))
    assert list(day_first.df["d"]) == ["2025-04-03", "2025-12-25"]

    # genuinely mixed conventions are kept as text rather than guessed
    mixed = clean_frame("s", pd.DataFrame({"d": ["03/04/2025", "25/12/2025", "12/25/2025"]}))
    assert _types(mixed) == {"d": "text"}


def test_blank_and_duplicate_headers_and_empty_rows_columns():
    df = pd.DataFrame(
        [[1, "x", None, 5], [None, None, None, None], [2, "y", None, 6]],
        columns=["Order ID", "Order ID", "Unnamed: 2", "Total ($)"],
    )
    sheet = clean_frame("s", df)
    assert [c.name for c in sheet.columns] == ["order_id", "order_id_2", "total"]
    assert [c.original_name for c in sheet.columns] == ["Order ID", "Order ID", "Total ($)"]
    assert len(sheet.df) == 2
    assert any("empty column" in n for n in sheet.warnings)
    assert any("Duplicate" in n for n in sheet.warnings)


def test_whitespace_only_text_becomes_null():
    df = pd.DataFrame({"note": ["  hello ", "   ", ""]})
    sheet = clean_frame("s", df)
    assert sheet.df["note"].iloc[0] == "hello"
    assert sheet.df["note"].isna().sum() == 2


def test_completely_empty_sheet_is_skipped():
    assert clean_frame("s", pd.DataFrame({"a": [None, None]})) is None
