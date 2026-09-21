from __future__ import annotations

import pandas as pd
import pytest

from db.ddl import (
    PrimaryKeyViolationError,
    build_table_ddl,
    check_primary_key_uniqueness,
    duplicate_primary_key_values,
    generate_schema_ddl,
    infer_pg_type,
    primary_keys_from_relationships,
    safe_identifier,
    sanitize_identifier,
    sanitize_schema_columns,
    topological_order,
)
from schema_discovery.models import RelationshipCandidate


def _rel(table_a, col_a, table_b, col_b, confidence=0.9) -> RelationshipCandidate:
    return RelationshipCandidate(
        table_a=table_a, column_a=col_a, table_b=table_b, column_b=col_b,
        confidence=confidence, evidence={},
    )


class TestInferPgType:
    def test_integer(self) -> None:
        assert infer_pg_type(pd.Series([1, 2, 3], dtype="int64")) == "BIGINT"

    def test_float(self) -> None:
        assert infer_pg_type(pd.Series([1.5, 2.5])) == "DOUBLE PRECISION"

    def test_bool(self) -> None:
        assert infer_pg_type(pd.Series([True, False])) == "BOOLEAN"

    def test_datetime(self) -> None:
        series = pd.to_datetime(pd.Series(["2024-01-01", "2024-01-02"]))
        assert infer_pg_type(series) == "TIMESTAMP"

    def test_short_text_gets_varchar(self) -> None:
        assert infer_pg_type(pd.Series(["Alice", "Bob"])).startswith("VARCHAR(")

    def test_long_text_gets_text(self) -> None:
        assert infer_pg_type(pd.Series(["x" * 300, "short"])) == "TEXT"

    def test_empty_series_defaults_to_text(self) -> None:
        assert infer_pg_type(pd.Series([None, None], dtype="object")) == "TEXT"


class TestSafeIdentifier:
    def test_valid_identifier_passes(self) -> None:
        assert safe_identifier("customer_id") == "customer_id"

    def test_rejects_sql_injection_attempt(self) -> None:
        with pytest.raises(ValueError):
            safe_identifier("customers; DROP TABLE users; --")

    def test_accepts_arabic_identifier(self) -> None:
        # Postgres allows any Unicode letter in a quoted identifier, and db.loader
        # always quotes. Rejecting these used to abort the whole save with an
        # unhandled ValueError, which reached the browser only as "Failed to fetch".
        assert safe_identifier("مبيعات") == "مبيعات"
        assert safe_identifier("رقم_الطلب") == "رقم_الطلب"

    @pytest.mark.parametrize(
        "bad",
        ['has space', 'quo"te', "semi;colon", "back\\slash", "", "1leading_digit", "a-b"],
    )
    def test_still_rejects_anything_that_could_escape_quoting(self, bad: str) -> None:
        with pytest.raises(ValueError):
            safe_identifier(bad)


class TestSanitizeIdentifier:
    def test_spaces_and_punctuation_become_underscores(self) -> None:
        assert sanitize_identifier("Transaction ID") == "transaction_id"
        assert sanitize_identifier("Order #") == "order"

    def test_arabic_is_preserved_not_erased(self) -> None:
        # Previously every Arabic name collapsed to the "column" fallback,
        # silently destroying the meaning of Arabic datasets.
        assert sanitize_identifier("رقم الطلب") == "رقم_الطلب"
        assert sanitize_identifier("المدينة") == "المدينة"

    def test_unnameable_input_still_falls_back(self) -> None:
        assert sanitize_identifier("###") == "column"
        assert sanitize_identifier("") == "column"

    def test_leading_digit_is_prefixed(self) -> None:
        assert sanitize_identifier("2024 sales") == "_2024_sales"


class TestSanitizeSchemaColumns:
    def test_table_names_are_sanitized_and_rels_pks_follow(self) -> None:
        # A linked external database keeps its own table names verbatim, so the
        # save path must not assume they are already valid identifiers.
        tables = {
            "Order Items": pd.DataFrame({"Order ID": [1], "Qty": [2]}),
            "مبيعات المتجر": pd.DataFrame({"رقم الطلب": [1]}),
        }
        rels = [_rel("Order Items", "Order ID", "مبيعات المتجر", "رقم الطلب")]
        pks = {"مبيعات المتجر": "رقم الطلب"}

        out_tables, out_rels, out_pks = sanitize_schema_columns(tables, rels, pks)

        assert set(out_tables) == {"order_items", "مبيعات_المتجر"}
        assert list(out_tables["order_items"].columns) == ["order_id", "qty"]
        assert out_rels[0].table_a == "order_items"
        assert out_rels[0].column_a == "order_id"
        assert out_rels[0].table_b == "مبيعات_المتجر"
        assert out_rels[0].column_b == "رقم_الطلب"
        assert out_pks == {"مبيعات_المتجر": "رقم_الطلب"}
        # Every emitted name must now pass the stricter guard.
        for name in out_tables:
            assert safe_identifier(name) == name

    def test_primary_keys_none_stays_none(self) -> None:
        tables = {"orders": pd.DataFrame({"id": [1]})}
        _, _, out_pks = sanitize_schema_columns(tables, [], None)
        assert out_pks is None


class TestPrimaryKeysFromRelationships:
    def test_derives_pk_from_table_b_side(self) -> None:
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]
        assert primary_keys_from_relationships(rels) == {"customers": "customer_id"}

    def test_table_never_referenced_has_no_pk(self) -> None:
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]
        assert "orders" not in primary_keys_from_relationships(rels)

    def test_conflicting_candidates_highest_confidence_wins(self) -> None:
        rels = [
            _rel("a", "x", "customers", "customer_id", confidence=0.6),
            _rel("b", "y", "customers", "email", confidence=0.9),
        ]
        assert primary_keys_from_relationships(rels) == {"customers": "email"}


class TestTopologicalOrder:
    def test_dimension_before_fact(self) -> None:
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]
        order = topological_order(["orders", "customers"], rels)
        assert order.index("customers") < order.index("orders")

    def test_star_schema_multiple_dimensions(self) -> None:
        rels = [
            _rel("orders", "customer_id", "customers", "customer_id"),
            _rel("orders", "product_id", "products", "sku"),
        ]
        order = topological_order(["orders", "customers", "products"], rels)
        assert order.index("customers") < order.index("orders")
        assert order.index("products") < order.index("orders")

    def test_unrelated_table_included(self) -> None:
        assert topological_order(["standalone"], []) == ["standalone"]

    def test_cycle_does_not_infinite_loop(self) -> None:
        rels = [_rel("a", "x", "b", "y"), _rel("b", "y", "a", "x")]
        order = topological_order(["a", "b"], rels)
        assert set(order) == {"a", "b"}
        assert len(order) == 2


class TestBuildTableDdl:
    def test_marks_primary_key_column(self) -> None:
        df = pd.DataFrame({"customer_id": [1, 2, 3], "name": ["A", "B", "C"]})
        ddl = build_table_ddl("customers", df, {"customers": "customer_id"}, [])
        pk_cols = [c for c in ddl.columns if c.primary_key]
        assert len(pk_cols) == 1
        assert pk_cols[0].name == "customer_id"

    def test_adds_foreign_key_when_referenced_table_has_matching_pk(self) -> None:
        df = pd.DataFrame({"order_id": [1], "customer_id": [1]})
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]
        ddl = build_table_ddl("orders", df, {"customers": "customer_id"}, rels)
        assert len(ddl.foreign_keys) == 1
        fk = ddl.foreign_keys[0]
        assert (fk.column, fk.ref_table, fk.ref_column) == ("customer_id", "customers", "customer_id")

    def test_no_foreign_key_when_referenced_table_has_no_pk(self) -> None:
        # customers was never approved as a PK side (empty primary_keys map)
        # — the FK would reference a column with no uniqueness guarantee.
        df = pd.DataFrame({"order_id": [1], "customer_id": [1]})
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]
        ddl = build_table_ddl("orders", df, {}, rels)
        assert ddl.foreign_keys == []

    def test_to_sql_renders_primary_and_foreign_keys(self) -> None:
        df = pd.DataFrame({"order_id": [1], "customer_id": [1]})
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]
        ddl = build_table_ddl("orders", df, {"customers": "customer_id", "orders": "order_id"}, rels)
        sql = ddl.to_sql(schema="project_demo")

        assert 'CREATE TABLE "project_demo"."orders"' in sql
        assert '"order_id" BIGINT PRIMARY KEY' in sql
        assert 'FOREIGN KEY ("customer_id") REFERENCES "project_demo"."customers" ("customer_id")' in sql


class TestPrimaryKeyUniqueness:
    def test_no_duplicates_returns_empty(self) -> None:
        df = pd.DataFrame({"customer_id": ["CUST-1", "CUST-2", "CUST-3"]})
        assert duplicate_primary_key_values(df, "customer_id") == []

    def test_finds_duplicate_values(self) -> None:
        df = pd.DataFrame({"customer_id": ["CUST-1", "CUST-2", "CUST-1"]})
        assert duplicate_primary_key_values(df, "customer_id") == ["CUST-1"]

    def test_check_raises_with_table_and_column_in_message(self) -> None:
        tables = {
            "customers": pd.DataFrame({"customer_id": ["CUST-1", "CUST-1"], "name": ["A", "B"]}),
        }
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]
        ddls = generate_schema_ddl(tables, rels)

        with pytest.raises(PrimaryKeyViolationError, match="customers.*customer_id.*CUST-1"):
            check_primary_key_uniqueness(tables, ddls)

    def test_check_passes_when_pk_is_unique(self) -> None:
        tables = {
            "customers": pd.DataFrame({"customer_id": ["CUST-1", "CUST-2"], "name": ["A", "B"]}),
        }
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]
        ddls = generate_schema_ddl(tables, rels)

        check_primary_key_uniqueness(tables, ddls)  # should not raise

    def test_check_skips_tables_without_a_detected_pk(self) -> None:
        tables = {"standalone": pd.DataFrame({"id": [1, 1, 1]})}
        ddls = generate_schema_ddl(tables, [])

        check_primary_key_uniqueness(tables, ddls)  # no PK column -> nothing to check


class TestGenerateSchemaDdl:
    def test_end_to_end_star_schema(self) -> None:
        tables = {
            "orders": pd.DataFrame({"order_id": [1, 2], "customer_id": [10, 20], "total": [5.0, 10.0]}),
            "customers": pd.DataFrame({"customer_id": [10, 20], "name": ["A", "B"]}),
        }
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]

        ddls = generate_schema_ddl(tables, rels)

        names = [d.name for d in ddls]
        assert names.index("customers") < names.index("orders")
        orders_ddl = next(d for d in ddls if d.name == "orders")
        assert len(orders_ddl.foreign_keys) == 1
        customers_ddl = next(d for d in ddls if d.name == "customers")
        assert any(c.primary_key for c in customers_ddl.columns)


class TestCompositePrimaryKey:
    def test_build_table_ddl_accepts_column_list(self) -> None:
        df = pd.DataFrame({"order_id": [1, 1], "line_number": [1, 2], "product_id": ["A", "B"]})
        ddl = build_table_ddl(
            "order_items", df, {"order_items": ["order_id", "line_number"]}, [],
        )
        assert ddl.primary_key_columns == ["order_id", "line_number"]
        pk_flagged = {c.name for c in ddl.columns if c.primary_key}
        assert pk_flagged == {"order_id", "line_number"}

    def test_to_sql_renders_table_level_composite_primary_key(self) -> None:
        df = pd.DataFrame({"order_id": [1], "line_number": [1], "product_id": ["A"]})
        ddl = build_table_ddl(
            "order_items", df, {"order_items": ["order_id", "line_number"]}, [],
        )
        sql = ddl.to_sql(schema="project_demo")
        assert 'PRIMARY KEY ("order_id", "line_number")' in sql
        # Individual composite-key columns must not also get an inline
        # single-column PRIMARY KEY.
        assert '"order_id" BIGINT PRIMARY KEY' not in sql
        assert '"line_number" BIGINT PRIMARY KEY' not in sql

    def test_single_column_pk_still_renders_inline(self) -> None:
        df = pd.DataFrame({"customer_id": [1], "name": ["A"]})
        ddl = build_table_ddl("customers", df, {"customers": ["customer_id"]}, [])
        sql = ddl.to_sql()
        assert '"customer_id" BIGINT PRIMARY KEY' in sql

    def test_check_primary_key_uniqueness_detects_composite_duplicate(self) -> None:
        tables = {
            "order_items": pd.DataFrame({
                "order_id": [1, 1, 1], "line_number": [1, 1, 2], "product_id": ["A", "B", "C"],
            }),
        }
        ddls = generate_schema_ddl(
            tables, [], primary_keys={"order_items": ["order_id", "line_number"]},
        )
        with pytest.raises(PrimaryKeyViolationError, match="order_items.*order_id\\+line_number"):
            check_primary_key_uniqueness(tables, ddls)

    def test_check_primary_key_uniqueness_passes_for_unique_composite(self) -> None:
        tables = {
            "order_items": pd.DataFrame({
                "order_id": [1, 1, 2], "line_number": [1, 2, 1], "product_id": ["A", "B", "C"],
            }),
        }
        ddls = generate_schema_ddl(
            tables, [], primary_keys={"order_items": ["order_id", "line_number"]},
        )
        check_primary_key_uniqueness(tables, ddls)  # should not raise


class TestGenerateSchemaDdlExplicitPrimaryKeys:
    def test_explicit_primary_keys_override_relationship_derivation(self) -> None:
        # No approved relationships at all, but Integrity Validation confirmed
        # a primary key independently — Constraint Generation must use it.
        tables = {"products": pd.DataFrame({"sku": ["S1", "S2"], "name": ["A", "B"]})}
        ddls = generate_schema_ddl(tables, [], primary_keys={"products": ["sku"]})
        products_ddl = ddls[0]
        assert products_ddl.primary_key_columns == ["sku"]

    def test_omitting_primary_keys_falls_back_to_legacy_derivation(self) -> None:
        tables = {
            "orders": pd.DataFrame({"order_id": [1], "customer_id": [10]}),
            "customers": pd.DataFrame({"customer_id": [10], "name": ["A"]}),
        }
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]
        ddls = generate_schema_ddl(tables, rels)
        customers_ddl = next(d for d in ddls if d.name == "customers")
        assert customers_ddl.primary_key_columns == ["customer_id"]

    def test_a_table_with_no_confirmed_primary_key_gets_none(self) -> None:
        tables = {"logs": pd.DataFrame({"message": ["a", "b"]})}
        ddls = generate_schema_ddl(tables, [], primary_keys={})
        assert ddls[0].primary_key_columns == []
        assert not any(c.primary_key for c in ddls[0].columns)
