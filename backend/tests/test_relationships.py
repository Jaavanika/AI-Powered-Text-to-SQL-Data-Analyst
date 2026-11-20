import pytest

from app.schemas import Relationship, SchemaColumn, SchemaInfo, SchemaTable
from app.services.relationship_service import find_join_plan
from app.services.schema_service import _flag_junctions, singular
from tests.helpers import upload_csvs


# ------------------------------------------------------------ unit: planner

def rel(frm, col, to, tcol, rid=None, source="declared"):
    return Relationship(id=rid or f"fk:{frm}.{col}->{to}", from_table=frm, from_columns=[col],
                        to_table=to, to_columns=[tcol], source=source, cardinality="many_to_one")


def schema_of(tables, rels):
    return SchemaInfo(
        datasource_id="x", dialect="sqlite", relationships=rels,
        tables=[SchemaTable(name=t, original_name=t, row_count=1, columns=[]) for t in tables])


SHOP = schema_of(
    ["customers", "orders", "products", "payments", "regions", "island"],
    [rel("orders", "customer_id", "customers", "customer_id"),
     rel("orders", "product_id", "products", "product_id"),
     rel("payments", "order_id", "orders", "order_id"),
     rel("customers", "region_id", "regions", "region_id")],
)


def test_single_table_needs_no_joins():
    plan = find_join_plan(SHOP, ["orders"])
    assert plan.status == "ok" and plan.joins == [] and plan.tables == ["orders"]


def test_join_through_an_intermediate_table():
    plan = find_join_plan(SHOP, ["customers", "products"])
    assert plan.status == "ok"
    assert plan.tables == ["customers", "orders", "products"]
    assert [(j.left_table, j.right_table) for j in plan.joins] == [
        ("customers", "orders"), ("orders", "products")]
    # the join is oriented from the table already in the plan to the new one
    assert plan.joins[0].left_columns == ["customer_id"] and plan.joins[0].right_columns == ["customer_id"]


def test_longer_path_and_three_tables():
    assert [j.right_table for j in find_join_plan(SHOP, ["regions", "payments"]).joins] == [
        "customers", "orders", "payments"]
    plan = find_join_plan(SHOP, ["customers", "products", "payments"])
    assert plan.status == "ok" and len(plan.joins) == 3
    assert set(plan.tables) == {"customers", "orders", "products", "payments"}


def test_disconnected_table_is_reported_not_guessed():
    plan = find_join_plan(SHOP, ["customers", "island"])
    assert plan.status == "disconnected"
    assert plan.unreachable == ["island"] and plan.joins == []
    assert "island" in plan.message


def test_max_hops_limits_the_search():
    assert find_join_plan(SHOP, ["regions", "payments"], max_hops=2).status == "disconnected"


def test_unknown_table_raises():
    with pytest.raises(ValueError, match="nope"):
        find_join_plan(SHOP, ["orders", "nope"])


def test_two_foreign_keys_between_the_same_tables_are_ambiguous():
    schema = schema_of(["orders", "addresses"], [
        rel("orders", "billing_address_id", "addresses", "address_id"),
        rel("orders", "shipping_address_id", "addresses", "address_id"),
    ])
    plan = find_join_plan(schema, ["orders", "addresses"])
    assert plan.status == "ambiguous"
    assert len(plan.ambiguities[0].options) == 2
    assert len(plan.joins) == 1  # a default is still provided
    assert {o[0].left_columns[0] for o in plan.ambiguities[0].options} == {
        "billing_address_id", "shipping_address_id"}


def test_two_equal_length_routes_are_ambiguous():
    schema = schema_of(["a", "b", "c", "d"], [
        rel("a", "b_id", "b", "id"), rel("b", "d_id", "d", "id"),
        rel("a", "c_id", "c", "id"), rel("c", "d_id", "d", "id"),
    ])
    assert find_join_plan(schema, ["a", "d"]).status == "ambiguous"


def test_junction_detection():
    def table(name, cols, pk=()):
        return SchemaTable(name=name, original_name=name, row_count=1, primary_key=list(pk),
                           columns=[SchemaColumn(name=c, original_name=c, type="integer",
                                                 db_type="INTEGER", nullable=False) for c in cols])
    tables = [table("students", ["student_id"]), table("courses", ["course_id"]),
              table("enrollments", ["student_id", "course_id", "grade"]),
              table("orders", ["order_id", "customer_id", "product_id", "qty", "date", "status"], pk=["order_id"]),
              table("customers", ["customer_id"]), table("products", ["product_id"])]
    rels = [rel("enrollments", "student_id", "students", "student_id"),
            rel("enrollments", "course_id", "courses", "course_id"),
            rel("orders", "customer_id", "customers", "customer_id"),
            rel("orders", "product_id", "products", "product_id")]
    pairs = _flag_junctions(tables, rels)
    flags = {t.name: t.is_junction for t in tables}
    assert flags["enrollments"] is True and flags["orders"] is False
    assert [(p.table_a, p.table_b, p.via) for p in pairs] == [("courses", "students", "enrollments")]


def test_singular():
    assert [singular(w) for w in ["customers", "categories", "addresses", "status", "boxes"]] == [
        "customer", "category", "address", "status", "box"]


# ----------------------------------------------------- API: sample database

def test_join_path_on_sample_database(client):
    r = client.post("/api/datasources/sample/join-path", json={"tables": ["customers", "products"]})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["tables"] == ["customers", "orders", "products"]


def test_join_path_rejects_unknown_tables(client):
    r = client.post("/api/datasources/sample/join-path", json={"tables": ["customers", "ghosts"]})
    assert r.status_code == 422


def test_sample_has_no_suggestions_and_is_read_only(client):
    assert client.get("/api/datasources/sample/relationships/suggestions").json() == []
    body = {"from_table": "orders", "from_column": "order_id", "to_table": "payments", "to_column": "order_id"}
    assert client.post("/api/datasources/sample/relationships", json=body).status_code == 403
    assert client.delete("/api/datasources/sample/relationships/fk:orders.customer_id->customers").status_code == 403


# ----------------------------------------------- API: uploaded data, no FKs

SHOP_CSVS = {
    "customers": "customer_id,customer_name,email\n" + "".join(f"{i},Cust {i},c{i}@x.com\n" for i in range(1, 11)),
    "products": "product_id,product_name,price\n" + "".join(f"{i},Prod {i},{i}.5\n" for i in range(1, 6)),
    "orders": "order_id,customer_id,product_id,quantity,order_date\n"
              + "".join(f"{i},{(i % 10) + 1},{(i % 5) + 1},{i % 3 + 1},2025-01-{i % 28 + 1:02d}\n" for i in range(1, 41)),
}


def _pairs(suggestions):
    return {(s["from_table"], s["from_column"], s["to_table"], s["to_column"]) for s in suggestions}


def test_suggestions_find_the_obvious_relationships_and_nothing_else(client):
    ds = upload_csvs(client, SHOP_CSVS)
    r = client.get(f"/api/datasources/{ds['id']}/relationships/suggestions")
    assert r.status_code == 200
    assert _pairs(r.json()) == {
        ("orders", "customer_id", "customers", "customer_id"),
        ("orders", "product_id", "products", "product_id"),
    }
    assert all(s["cardinality"] == "many_to_one" and s["match_ratio"] == 1.0 for s in r.json())


def test_suggestions_are_not_used_for_joins_until_confirmed(client):
    ds = upload_csvs(client, SHOP_CSVS)
    base = f"/api/datasources/{ds['id']}"
    plan = client.post(f"{base}/join-path", json={"tables": ["customers", "products"]}).json()
    assert plan["status"] == "disconnected"

    for table, column, parent, key in [("orders", "customer_id", "customers", "customer_id"),
                                       ("orders", "product_id", "products", "product_id")]:
        r = client.post(f"{base}/relationships", json={
            "from_table": table, "from_column": column, "to_table": parent, "to_column": key})
        assert r.status_code == 201, r.text
        assert r.json()["source"] == "user" and r.json()["cardinality"] == "many_to_one"

    plan = client.post(f"{base}/join-path", json={"tables": ["customers", "products"]}).json()
    assert plan["status"] == "ok" and plan["tables"] == ["customers", "orders", "products"]
    assert len(client.get(f"{base}/relationships").json()) == 2
    # once confirmed they are no longer suggested
    assert client.get(f"{base}/relationships/suggestions").json() == []


def test_confirm_validation_errors(client):
    ds = upload_csvs(client, SHOP_CSVS)
    url = f"/api/datasources/{ds['id']}/relationships"

    def post(**kw):
        body = {"from_table": "orders", "from_column": "customer_id",
                "to_table": "customers", "to_column": "customer_id", **kw}
        return client.post(url, json=body)

    assert post(to_table="ghosts").status_code == 404
    assert post(from_column="ghost").status_code == 404
    assert post(to_table="orders", from_table="orders").status_code == 422  # self reference
    assert post(from_column="quantity", to_column="customer_name").status_code == 422  # type mismatch
    # parent column must be unique: orders.customer_id repeats
    assert post(from_table="customers", from_column="customer_id",
                to_table="orders", to_column="customer_id").status_code == 422
    assert post().status_code == 201
    assert post().status_code == 409  # duplicate


def test_confirm_rejects_columns_with_no_overlap(client):
    ds = upload_csvs(client, {"a": "x\n1\n2\n", "b": "y\n100\n200\n"})
    r = client.post(f"/api/datasources/{ds['id']}/relationships", json={
        "from_table": "a", "from_column": "x", "to_table": "b", "to_column": "y"})
    assert r.status_code == 422 and "match" in r.json()["detail"]


def test_delete_user_relationship(client):
    ds = upload_csvs(client, SHOP_CSVS)
    base = f"/api/datasources/{ds['id']}"
    rid = client.post(f"{base}/relationships", json={
        "from_table": "orders", "from_column": "customer_id",
        "to_table": "customers", "to_column": "customer_id"}).json()["id"]
    assert client.delete(f"{base}/relationships/{rid}").status_code == 204
    assert client.delete(f"{base}/relationships/{rid}").status_code == 404
    assert client.get(f"{base}/relationships").json() == []


def test_relationships_are_removed_with_the_data_source(client):
    from app.services import registry
    ds = upload_csvs(client, SHOP_CSVS)
    client.post(f"/api/datasources/{ds['id']}/relationships", json={
        "from_table": "orders", "from_column": "customer_id",
        "to_table": "customers", "to_column": "customer_id"})
    assert registry.list_user_relationships(ds["id"])
    client.delete(f"/api/datasources/{ds['id']}")
    assert registry.list_user_relationships(ds["id"]) == []


# ------------------------------------------------- suggestion false positives

def test_low_overlap_is_not_suggested(client):
    # only 1 of the 9 distinct child values appears in the parent, and the child is not unique
    ds = upload_csvs(client, {
        "parents": "pid\n1\n2\n3\n",
        "children": "pid\n1\n1\n50\n51\n52\n53\n54\n55\n56\n",
    })
    assert client.get(f"/api/datasources/{ds['id']}/relationships/suggestions").json() == []


def test_non_unique_parent_is_not_suggested(client):
    ds = upload_csvs(client, {"a": "region\nN\nN\nS\n", "b": "region\nN\nS\nS\n"})
    assert client.get(f"/api/datasources/{ds['id']}/relationships/suggestions").json() == []


def test_generic_id_columns_do_not_match_each_other(client):
    ds = upload_csvs(client, {"a": "id,x\n1,a\n2,b\n3,c\n", "b": "id,y\n1,a\n2,b\n3,c\n"})
    assert client.get(f"/api/datasources/{ds['id']}/relationships/suggestions").json() == []


def test_id_column_matches_table_name_convention(client):
    ds = upload_csvs(client, {
        "customers": "id,name\n1,A\n2,B\n3,C\n",
        "orders": "order_id,customer_id\n10,1\n11,2\n12,2\n",
    })
    pairs = _pairs(client.get(f"/api/datasources/{ds['id']}/relationships/suggestions").json())
    assert pairs == {("orders", "customer_id", "customers", "id")}


def test_one_to_one_is_suggested_once(client):
    # both sides are unique, so the link qualifies in both directions but is reported once
    ds = upload_csvs(client, {
        "users": "user_id,name\n1,A\n2,B\n3,C\n4,D\n",
        "profiles": "user_id,bio\n1,w\n2,x\n3,y\n4,z\n",
    })
    suggestions = client.get(f"/api/datasources/{ds['id']}/relationships/suggestions").json()
    assert len(suggestions) == 1
    assert suggestions[0]["cardinality"] == "one_to_one"
    assert (suggestions[0]["from_table"], suggestions[0]["to_table"]) == ("profiles", "users")
