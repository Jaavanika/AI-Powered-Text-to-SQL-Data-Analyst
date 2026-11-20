from app.schemas import Relationship, SchemaColumn, SchemaInfo, SchemaTable
from app.services import connectors, schema_linking, schema_service
from app.services.schema_linking import link_schema, tokens


def col(name, type="text", pk=False, original=None):
    return SchemaColumn(name=name, original_name=original or name, type=type,
                        db_type=type.upper(), nullable=True, is_primary_key=pk)


def table(name, cols, rows=10, pk=None):
    return SchemaTable(name=name, original_name=name, row_count=rows, columns=cols,
                       primary_key=pk or [])


def fk(frm, col_, to, tcol):
    return Relationship(id=f"fk:{frm}.{col_}->{to}", from_table=frm, from_columns=[col_],
                        to_table=to, to_columns=[tcol], source="declared", cardinality="many_to_one")


def names(linked):
    return [t.name for t in linked.tables]


# A chain a -> b -> c -> d -> e -> f -> g, so schemas are larger than the "send everything" limit.
def chain_schema(extra_tables=()):
    letters = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]
    tables = [table(n, [col(f"{n}_id", "integer", pk=True), col("label")], pk=[f"{n}_id"]) for n in letters]
    rels = []
    for prev, nxt in zip(letters, letters[1:]):
        tables[letters.index(prev)].columns.append(col(f"{nxt}_id", "integer"))
        rels.append(fk(prev, f"{nxt}_id", nxt, f"{nxt}_id"))
    tables += list(extra_tables)
    return SchemaInfo(datasource_id="x", dialect="sqlite", tables=tables, relationships=rels)


def test_tokens_singularise_and_drop_stopwords():
    assert tokens("Show the total orders by Customers") == {"order", "customer"}
    assert tokens("sales in 2025") == {"sale"}


def test_small_schema_is_sent_whole_with_matches_flagged(client):
    schema = schema_service.build_schema(*connectors.resolve("sample", None))
    linked = link_schema(schema, "How many orders did each customer place?")
    assert set(names(linked)) == {"regions", "customers", "products", "orders", "payments"}
    assert {t.name for t in linked.tables if t.matched} >= {"orders", "customers"}
    assert linked.fallback is False
    assert linked.join_plan.status == "ok"
    assert linked.join_plan.tables[:2] == ["orders", "customers"] or linked.join_plan.tables[:2] == ["customers", "orders"]


def test_value_hints_from_the_data(client):
    datasource, connector = connectors.resolve("sample", None)
    schema = schema_service.build_schema(datasource, connector)
    values = schema_linking.distinct_values("sample", connector, schema)
    assert ("products", "category") in values and "Electronics" in values[("products", "category")]
    # only text columns are indexed
    assert ("orders", "quantity") not in values and ("orders", "order_date") not in values

    linked = link_schema(schema, "Total quantity of electronics sold in the North region", values)
    hints = {(h.table, h.column, h.value) for h in linked.value_hints}
    assert ("products", "category", "Electronics") in hints
    assert ("regions", "region_name", "North") in hints


def test_high_cardinality_text_columns_are_not_value_indexed(client):
    from tests.helpers import upload_csvs
    rows = "".join(f"person{i},{'red' if i % 2 else 'blue'}\n" for i in range(80))
    ds = upload_csvs(client, {"people": "name,colour\n" + rows})
    datasource, connector = connectors.resolve(ds["id"], "workspace-one")
    schema = schema_service.build_schema(datasource, connector)
    values = schema_linking.distinct_values(ds["id"], connector, schema)
    assert ("people", "name") not in values            # 80 distinct values
    assert sorted(values[("people", "colour")]) == ["blue", "red"]


def test_value_hints_are_cached_until_file_changes(client):
    datasource, connector = connectors.resolve("sample", None)
    schema = schema_service.build_schema(datasource, connector)
    first = schema_linking.distinct_values("sample", connector, schema)
    assert schema_linking.distinct_values("sample", connector, schema) is first


def test_large_schema_is_reduced_to_matches_plus_connecting_tables():
    schema = chain_schema()
    linked = link_schema(schema, "compare alpha and delta")
    # bravo and charlie are not mentioned but are needed to join alpha to delta
    assert set(names(linked)) == {"alpha", "bravo", "charlie", "delta"}
    assert linked.join_plan.status == "ok"
    assert {t.name for t in linked.tables if t.matched} == {"alpha", "delta"}
    assert all(r.from_table in names(linked) and r.to_table in names(linked) for r in linked.relationships)


def test_large_schema_with_no_match_falls_back_to_all_tables():
    linked = link_schema(chain_schema(), "what is the meaning of life")
    assert linked.fallback is True
    assert len(linked.tables) == 8


def test_disconnected_matches_are_reported():
    schema = chain_schema(extra_tables=[table("island", [col("label")])])
    linked = link_schema(schema, "alpha with island")
    assert linked.join_plan.status == "disconnected"
    assert "island" in linked.join_plan.unreachable


def test_key_columns_do_not_pull_in_neighbouring_tables():
    schema = chain_schema()
    linked = link_schema(schema, "show bravo")
    # alpha has a bravo_id key column, but that alone must not match alpha
    assert {t.name for t in linked.tables if t.matched} == {"bravo"}


def test_follow_up_keeps_the_tables_of_the_previous_answer():
    schema = chain_schema()
    # "only the top five" names no table, so the previous answer's tables are pinned
    linked = link_schema(schema, "now only the top five", pinned=["alpha", "delta"])
    assert set(names(linked)) == {"alpha", "bravo", "charlie", "delta"}
    assert linked.fallback is False and linked.join_plan.status == "ok"
    assert {t.name for t in linked.tables if t.matched} >= {"alpha", "delta"}
    # without the pin the same text matches nothing
    assert link_schema(schema, "now only the top five").fallback is True


def test_follow_up_also_matches_against_earlier_questions():
    linked = link_schema(chain_schema(), "and by month", context="show hotel and golf rows")
    assert {t.name for t in linked.tables if t.matched} == {"hotel", "golf"}


def test_column_matches_use_original_headers_and_synonyms():
    schema = chain_schema(extra_tables=[
        table("shipments", [col("c1", original="Delivery Date"), col("c2", "float", original="Freight Cost")])])
    linked = link_schema(schema, "average freight cost")
    assert {t.name for t in linked.tables if t.matched} == {"shipments"}
    # "income" has no direct column, but the synonym map points it at "amount"
    schema = chain_schema(extra_tables=[table("ledger", [col("amount", "float")])])
    assert {t.name for t in link_schema(schema, "total income").tables if t.matched} == {"ledger"}


def test_wide_tables_are_trimmed_but_keep_matched_and_key_columns():
    wide = table("wide", [col("wide_id", "integer", pk=True)] + [col(f"c{i}") for i in range(60)]
                 + [col("target_metric", "float")], pk=["wide_id"])
    schema = chain_schema(extra_tables=[wide])
    linked = link_schema(schema, "show the target metric from wide")
    kept = [c.name for c in next(t for t in linked.tables if t.name == "wide").columns]
    assert "target_metric" in kept and "wide_id" in kept
    assert len(kept) < 40
