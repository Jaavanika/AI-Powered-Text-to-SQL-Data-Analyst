import io
import sqlite3
from contextlib import closing

import pandas as pd
import pytest

from app.services import registry

CSV = (
    "Order ID,Product Name,Quantity,Unit Price,Order Date,Region\n"
    "1,Widget,2,9.99,2025-01-05,North\n"
    "2,Gadget,1,24.50,2025-02-11,South\n"
    "3,Widget,5,9.99,2025-02-20,North\n"
)


def _post_csv(client, text=CSV, name="sales.csv"):
    return client.post("/api/upload/preview", files={"file": (name, text.encode(), "text/csv")})


def _xlsx_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
    return buf.getvalue()


def _rows(datasource_id, sql):
    path = registry.get_db_path(datasource_id, "workspace-one")
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(sql).fetchall()


def test_missing_workspace_header_is_rejected(client):
    r = client.get("/api/datasources", headers={"X-Workspace-Id": "x"})
    assert r.status_code == 400


def test_csv_preview_detects_columns_and_types(client):
    r = _post_csv(client)
    assert r.status_code == 200
    body = r.json()
    assert body["file_type"] == "csv"
    sheet = body["sheets"][0]
    assert sheet["suggested_table_name"] == "sales"
    assert sheet["row_count"] == 3
    types = {c["name"]: c["type"] for c in sheet["columns"]}
    assert types == {
        "order_id": "integer", "product_name": "text", "quantity": "integer",
        "unit_price": "float", "order_date": "date", "region": "text",
    }
    assert sheet["columns"][0]["original_name"] == "Order ID"
    assert len(sheet["preview_rows"]) == 3


def test_csv_import_creates_queryable_table(client):
    upload_id = _post_csv(client).json()["upload_id"]
    r = client.post("/api/upload/import", json={"upload_id": upload_id, "datasource_name": "My sales"})
    assert r.status_code == 201
    ds = r.json()
    assert ds["name"] == "My sales" and ds["kind"] == "upload"
    assert ds["tables"][0]["name"] == "sales" and ds["tables"][0]["row_count"] == 3
    assert _rows(ds["id"], "SELECT SUM(quantity * unit_price) FROM sales")[0][0] == pytest.approx(
        2 * 9.99 + 24.50 + 5 * 9.99)
    assert _rows(ds["id"], "SELECT MIN(order_date) FROM sales")[0][0] == "2025-01-05"


def test_import_consumes_the_staged_upload(client):
    upload_id = _post_csv(client).json()["upload_id"]
    assert client.post("/api/upload/import", json={"upload_id": upload_id}).status_code == 201
    assert client.post("/api/upload/import", json={"upload_id": upload_id}).status_code == 404


def test_semicolon_csv_and_latin1_encoding(client):
    text = "Name;Amount\nJosé;1,5\nZoë;2\n"
    r = client.post("/api/upload/preview", files={"file": ("x.csv", text.encode("cp1252"), "text/csv")})
    assert r.status_code == 200
    names = [c["name"] for c in r.json()["sheets"][0]["columns"]]
    assert names == ["name", "amount"]
    assert r.json()["sheets"][0]["preview_rows"][0]["name"] == "José"


def test_excel_multi_sheet_selection(client):
    data = _xlsx_bytes({
        "Customers": pd.DataFrame({"customer_id": [1, 2], "name": ["A", "B"]}),
        "Orders": pd.DataFrame({"order_id": [10, 11, 12], "customer_id": [1, 1, 2]}),
        "Empty": pd.DataFrame({"x": []}),
    })
    r = client.post("/api/upload/preview", files={"file": ("business.xlsx", data, "application/octet-stream")})
    assert r.status_code == 200
    body = r.json()
    assert [s["sheet_name"] for s in body["sheets"]] == ["Customers", "Orders"]
    assert any("Empty" in w for w in body["warnings"])

    r = client.post("/api/upload/import", json={
        "upload_id": body["upload_id"],
        "tables": [{"sheet_name": "Orders", "table_name": "Sales Orders"}],
    })
    assert r.status_code == 201
    assert [t["name"] for t in r.json()["tables"]] == ["sales_orders"]


def test_second_upload_can_join_an_existing_data_source(client):
    first = client.post("/api/upload/import", json={"upload_id": _post_csv(client).json()["upload_id"]}).json()
    customers = "customer_id,name\n1,A\n2,B\n"
    up = _post_csv(client, customers, "customers.csv").json()["upload_id"]
    r = client.post("/api/upload/import", json={"upload_id": up, "datasource_id": first["id"]})
    assert r.status_code == 201
    assert sorted(t["name"] for t in r.json()["tables"]) == ["customers", "sales"]
    assert len(client.get("/api/datasources").json()) == 2  # sample + one upload


def test_existing_table_conflict_and_replace(client):
    first = client.post("/api/upload/import", json={"upload_id": _post_csv(client).json()["upload_id"]}).json()
    again = _post_csv(client).json()["upload_id"]
    r = client.post("/api/upload/import", json={"upload_id": again, "datasource_id": first["id"]})
    assert r.status_code == 409
    r = client.post("/api/upload/import", json={
        "upload_id": again, "datasource_id": first["id"], "if_exists": "replace"})
    assert r.status_code == 201
    assert len(r.json()["tables"]) == 1


def test_failed_new_import_leaves_no_data_source(client):
    up = _post_csv(client).json()["upload_id"]
    r = client.post("/api/upload/import", json={"upload_id": up, "tables": [{"sheet_name": "nope"}]})
    assert r.status_code == 404
    assert len(client.get("/api/datasources").json()) == 1  # only the sample


def test_cannot_import_into_sample(client):
    up = _post_csv(client).json()["upload_id"]
    r = client.post("/api/upload/import", json={"upload_id": up, "datasource_id": "sample"})
    assert r.status_code == 403


@pytest.mark.parametrize("name,content,status", [
    ("data.txt", b"a,b\n1,2\n", 415),
    ("data.csv", b"", 422),
    ("fake.xlsx", b"this is not a zip", 422),
    ("fake.xls", b"this is not ole2", 422),
])
def test_rejected_files(client, name, content, status):
    r = client.post("/api/upload/preview", files={"file": (name, content, "application/octet-stream")})
    assert r.status_code == status


def test_size_limit(client, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "0")
    from app.config import get_settings
    get_settings.cache_clear()
    r = _post_csv(client)
    assert r.status_code == 413


def test_row_limit(client, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_ROWS", "2")
    from app.config import get_settings
    get_settings.cache_clear()
    r = _post_csv(client)
    assert r.status_code == 413
    assert "row limit" in r.json()["detail"]


def test_upload_id_path_traversal_is_rejected(client):
    r = client.post("/api/upload/import", json={"upload_id": "../../registry"})
    assert r.status_code == 404


def test_workspaces_are_isolated(client, other_client):
    ds = client.post("/api/upload/import", json={"upload_id": _post_csv(client).json()["upload_id"]}).json()
    assert other_client.get(f"/api/datasources/{ds['id']}").status_code == 404
    assert other_client.delete(f"/api/datasources/{ds['id']}").status_code == 404
    assert [d["id"] for d in other_client.get("/api/datasources").json()] == ["sample"]
    # another workspace cannot use someone else's staged upload either
    up = _post_csv(client).json()["upload_id"]
    assert other_client.post("/api/upload/import", json={"upload_id": up}).status_code == 404


def test_delete_removes_data_source_and_file(client):
    ds = client.post("/api/upload/import", json={"upload_id": _post_csv(client).json()["upload_id"]}).json()
    path = registry.get_db_path(ds["id"], "workspace-one")
    assert path.exists()
    assert client.delete(f"/api/datasources/{ds['id']}").status_code == 204
    assert not path.exists()
    assert client.get(f"/api/datasources/{ds['id']}").status_code == 404


def test_table_preview_endpoint(client):
    ds = client.post("/api/upload/import", json={"upload_id": _post_csv(client).json()["upload_id"]}).json()
    r = client.get(f"/api/datasources/{ds['id']}/tables/sales/preview", params={"limit": 2})
    assert r.status_code == 200
    assert len(r.json()["rows"]) == 2
    assert r.json()["rows"][0]["product_name"] == "Widget"
    assert client.get(f"/api/datasources/{ds['id']}/tables/nope/preview").status_code == 404
    bad = client.get(f"/api/datasources/{ds['id']}/tables/sales\"--/preview")
    assert bad.status_code == 404
