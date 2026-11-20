"""A PostgreSQL-shaped SchemaInfo for tests that exercise the validator and prompts without a server."""

from app.schemas import Relationship, SchemaColumn, SchemaInfo, SchemaTable


def _col(name, db_type, kind, pk=False, nullable=True):
    return SchemaColumn(name=name, original_name=name, type=kind, db_type=db_type, nullable=nullable, is_primary_key=pk)


def _table(name, columns, rows=10):
    pk = [c.name for c in columns if c.is_primary_key]
    return SchemaTable(name=name, original_name=name, row_count=rows, columns=columns, primary_key=pk)


def _fk(frm, col, to, tcol):
    return Relationship(id=f"fk:{frm}.{col}->{to}", from_table=frm, from_columns=[col], to_table=to,
                        to_columns=[tcol], source="declared", cardinality="many_to_one")


def pg_schema() -> SchemaInfo:
    tables = [
        _table("customers", [_col("customer_id", "INTEGER", "integer", True, False), _col("customer_name", "TEXT", "text"),
                             _col("email", "TEXT", "text"), _col("city", "TEXT", "text"),
                             _col("region_id", "INTEGER", "integer"), _col("signup_date", "DATE", "date")]),
        _table("products", [_col("product_id", "INTEGER", "integer", True, False), _col("product_name", "TEXT", "text"),
                            _col("category", "TEXT", "text"), _col("price", "NUMERIC(10, 2)", "float"),
                            _col("cost", "NUMERIC(10, 2)", "float")]),
        _table("orders", [_col("order_id", "INTEGER", "integer", True, False), _col("customer_id", "INTEGER", "integer"),
                          _col("product_id", "INTEGER", "integer"), _col("quantity", "INTEGER", "integer"),
                          _col("order_date", "DATE", "date"), _col("status", "TEXT", "text")]),
        _table("payments", [_col("payment_id", "INTEGER", "integer", True, False), _col("order_id", "INTEGER", "integer"),
                            _col("amount", "NUMERIC(10, 2)", "float"), _col("payment_date", "DATE", "date")]),
        # names that PostgreSQL only accepts in double quotes
        _table("Weird Table", [_col("id", "INTEGER", "integer", True, False), _col("Order Total", "NUMERIC", "float")]),
        _table("Invoices", [_col("invoice_id", "INTEGER", "integer", True, False), _col("Amount", "NUMERIC", "float"),
                            _col("DueDate", "DATE", "date")]),
    ]
    rels = [_fk("orders", "customer_id", "customers", "customer_id"),
            _fk("orders", "product_id", "products", "product_id"),
            _fk("payments", "order_id", "orders", "order_id")]
    return SchemaInfo(datasource_id="pg", dialect="postgres", tables=tables, relationships=rels)
