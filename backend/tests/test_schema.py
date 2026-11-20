from tests.helpers import upload_csvs


def _schema(client, datasource_id="sample"):
    r = client.get(f"/api/datasources/{datasource_id}/schema")
    assert r.status_code == 200
    return r.json()


def test_sample_schema_tables_and_keys(client):
    schema = _schema(client)
    tables = {t["name"]: t for t in schema["tables"]}
    assert set(tables) == {"regions", "customers", "products", "orders", "payments"}
    assert tables["orders"]["primary_key"] == ["order_id"]
    assert tables["orders"]["primary_key_inferred"] is False
    assert tables["orders"]["row_count"] == 400
    cols = {c["name"]: c for c in tables["orders"]["columns"]}
    assert cols["order_id"]["is_primary_key"] is True
    assert cols["order_date"]["type"] == "date"
    assert cols["customer_id"]["nullable"] is False
    assert schema["dialect"] == "sqlite"


def test_sample_declared_foreign_keys(client):
    rels = {r["id"]: r for r in _schema(client)["relationships"]}
    assert set(rels) == {
        "fk:customers.region_id->regions",
        "fk:orders.customer_id->customers",
        "fk:orders.product_id->products",
        "fk:payments.order_id->orders",
    }
    fk = rels["fk:orders.customer_id->customers"]
    assert (fk["from_columns"], fk["to_table"], fk["to_columns"]) == (["customer_id"], "customers", ["customer_id"])
    assert fk["source"] == "declared" and fk["cardinality"] == "many_to_one"


def test_sample_has_no_junction_tables(client):
    schema = _schema(client)
    assert not any(t["is_junction"] for t in schema["tables"])
    assert schema["many_to_many"] == []


def test_uploaded_tables_get_inferred_primary_keys_and_no_relationships(client):
    ds = upload_csvs(client, {
        "customers": "customer_id,name\n1,A\n2,B\n",
        "orders": "order_id,customer_id,total\n10,1,5\n11,1,6\n12,2,7\n",
        "log": "event,at\nx,2025-01-01\nx,2025-01-02\n",
    })
    schema = _schema(client, ds["id"])
    tables = {t["name"]: t for t in schema["tables"]}
    assert tables["customers"]["primary_key"] == ["customer_id"]
    assert tables["customers"]["primary_key_inferred"] is True
    assert tables["orders"]["primary_key"] == ["order_id"]
    assert tables["log"]["primary_key"] == []
    assert schema["relationships"] == []
    # logical types and original headers come from the import, not just the SQLite storage class
    assert {c["name"]: c["type"] for c in tables["log"]["columns"]}["at"] == "date"


def test_schema_of_unknown_or_foreign_data_source_is_404(client, other_client):
    assert client.get("/api/datasources/nope/schema").status_code == 404
    ds = upload_csvs(client, {"t": "a\n1\n"})
    assert other_client.get(f"/api/datasources/{ds['id']}/schema").status_code == 404
