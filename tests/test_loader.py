from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import text

from db.loader import load_project_schema
from db.reflect import reflect_project_schema
from db.schema_evolution import add_new_columns, diff_new_columns
from db.session import get_engine
from schema_discovery.models import RelationshipCandidate

pytestmark = pytest.mark.integration


def _rel(table_a, col_a, table_b, col_b, confidence=0.9) -> RelationshipCandidate:
    return RelationshipCandidate(
        table_a=table_a, column_a=col_a, table_b=table_b, column_b=col_b,
        confidence=confidence, evidence={},
    )


@pytest.fixture
def test_schema():
    schema_name = "project_test_loader_stage7"
    yield schema_name
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))


class TestLoadProjectSchema:
    def test_create_load_reflect_round_trip(self, test_schema) -> None:
        tables = {
            "customers": pd.DataFrame({"customer_id": [1, 2, 3], "name": ["Alice", "Bob", "Carol"]}),
            "orders": pd.DataFrame({
                "order_id": [100, 101, 102],
                "customer_id": [1, 2, 1],
                "total": [10.5, 20.0, 15.25],
            }),
        }
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]

        load_project_schema(test_schema, tables, rels)

        metadata = reflect_project_schema(test_schema)
        orders_table = metadata.tables[f"{test_schema}.orders"]
        fk_cols = {fk.parent.name for fk in orders_table.foreign_keys}
        assert "customer_id" in fk_cols

        customers_table = metadata.tables[f"{test_schema}.customers"]
        pk_cols = {c.name for c in customers_table.primary_key.columns}
        assert pk_cols == {"customer_id"}

        engine = get_engine()
        with engine.connect() as conn:
            row_count = conn.execute(text(f'SELECT COUNT(*) FROM "{test_schema}"."orders"')).scalar()
        assert row_count == 3

    def test_fk_constraint_present_in_information_schema(self, test_schema) -> None:
        tables = {
            "customers": pd.DataFrame({"customer_id": [1, 2]}),
            "orders": pd.DataFrame({"order_id": [1], "customer_id": [1]}),
        }
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]
        load_project_schema(test_schema, tables, rels)

        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT tc.constraint_type FROM information_schema.table_constraints tc "
                    "WHERE tc.table_schema = :schema AND tc.table_name = 'orders' "
                    "AND tc.constraint_type = 'FOREIGN KEY'"
                ),
                {"schema": test_schema},
            ).fetchall()
        assert len(result) == 1

    def test_reload_replaces_existing_data(self, test_schema) -> None:
        tables = {"standalone": pd.DataFrame({"id": [1, 2], "value": ["a", "b"]})}
        load_project_schema(test_schema, tables, [])

        tables2 = {"standalone": pd.DataFrame({"id": [1], "value": ["only-one"]})}
        load_project_schema(test_schema, tables2, [])

        engine = get_engine()
        with engine.connect() as conn:
            row_count = conn.execute(text(f'SELECT COUNT(*) FROM "{test_schema}"."standalone"')).scalar()
        assert row_count == 1


class TestAtomicSave:
    def test_copy_failure_partway_rolls_back_everything(self, test_schema) -> None:
        baseline = {"customers": pd.DataFrame({"customer_id": [1, 2]})}
        load_project_schema(test_schema, baseline, [])

        # check_primary_key_uniqueness only checks PK duplicates, not FK
        # orphans, so this genuinely orphaned FK sails past the Python
        # pre-check and fails for real at COPY time — customers is created
        # and loaded successfully first, then orders' COPY hits Postgres's
        # own FK-violation error.
        tables2 = {
            "customers": pd.DataFrame({"customer_id": [10, 20]}),
            "orders": pd.DataFrame({"order_id": [1], "customer_id": [999]}),
        }
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]
        with pytest.raises(Exception):
            load_project_schema(test_schema, tables2, rels)

        engine = get_engine()
        with engine.connect() as conn:
            customer_ids = conn.execute(
                text(f'SELECT customer_id FROM "{test_schema}"."customers" ORDER BY customer_id')
            ).scalars().all()
            orders_exists = conn.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = :schema AND table_name = 'orders')"
                ),
                {"schema": test_schema},
            ).scalar()

        # The whole failed transaction (DROP + CREATE + both COPYs) rolled
        # back atomically: the pre-existing "customers" data from the
        # baseline save is untouched (not half-replaced with [10, 20]), and
        # "orders" — which never successfully existed — is still absent.
        assert customer_ids == [1, 2]
        assert orders_exists is False


class TestSchemaEvolution:
    def test_diff_detects_new_column(self, test_schema) -> None:
        load_project_schema(test_schema, {"t": pd.DataFrame({"id": [1]})}, [])
        new_cols = diff_new_columns(test_schema, "t", pd.DataFrame({"id": [1], "email": ["a@b.com"]}))
        assert new_cols == ["email"]

    def test_add_new_columns_alters_the_table(self, test_schema) -> None:
        load_project_schema(test_schema, {"t": pd.DataFrame({"id": [1]})}, [])
        added = add_new_columns(test_schema, "t", pd.DataFrame({"id": [1], "score": [4.5]}))
        assert added == ["score"]

        metadata = reflect_project_schema(test_schema)
        cols = {c.name for c in metadata.tables[f"{test_schema}.t"].columns}
        assert "score" in cols

    def test_no_new_columns_is_a_no_op(self, test_schema) -> None:
        load_project_schema(test_schema, {"t": pd.DataFrame({"id": [1]})}, [])
        assert add_new_columns(test_schema, "t", pd.DataFrame({"id": [2]})) == []
