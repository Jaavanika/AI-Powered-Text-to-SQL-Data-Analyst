from app.core.identifiers import sanitize_identifier, unique_identifiers


def test_basic_snake_case():
    assert sanitize_identifier("Order Date (UTC)") == "order_date_utc"


def test_accents_and_symbols():
    assert sanitize_identifier("Café Prix €") == "cafe_prix"


def test_leading_digit_and_empty():
    assert sanitize_identifier("2025 sales", fallback="col") == "col_2025_sales"
    assert sanitize_identifier("###", fallback="col") == "col"


def test_injection_attempt_is_neutralised():
    assert sanitize_identifier('x"; DROP TABLE users; --') == "x_drop_table_users"


def test_reserved_sqlite_prefix():
    assert sanitize_identifier("sqlite_master") == "t_sqlite_master"


def test_length_cap():
    assert len(sanitize_identifier("a" * 200)) == 63


def test_unique_identifiers():
    assert unique_identifiers(["Name", "name", "NAME!", ""]) == ["name", "name_2", "name_3", "col"]
