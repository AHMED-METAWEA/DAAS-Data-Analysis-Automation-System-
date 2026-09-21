"""Join keys must be canonicalised on the path that actually saves.

``reconcile`` normalises both sides of every approved relationship, but it only
ran inside the "clean remaining tables" endpoint. A multi-table project whose
tables were each cleaned individually never reached it, so a plan that
standardised casing on one side and not the other left keys that a generated
FOREIGN KEY rejects at COPY time — surfacing as a ForeignKeyViolation from
Postgres rather than a blocked save. ``_tables_for_storage`` is the shared
choke point both /integrity and /save now read.
"""

from __future__ import annotations

import pandas as pd

from backend.app.api.v1.pipeline import _tables_for_storage
from backend.app.services.pipeline_sessions import PipelineSession, TableCleaningState
from integrity.validation import run_integrity_validation
from schema_discovery.models import RelationshipCandidate


def _rel(table_a, col_a, table_b, col_b) -> RelationshipCandidate:
    return RelationshipCandidate(
        table_a=table_a, column_a=col_a, table_b=table_b, column_b=col_b,
        confidence=0.95, evidence={},
    )


def _session(tables: dict[str, pd.DataFrame]) -> PipelineSession:
    s = PipelineSession(id="s", project_id="p", raw_tables=tables, primary_table="orders")
    s.cleaning = {name: TableCleaningState(cleaned_df=df) for name, df in tables.items()}
    return s


def _mismatched() -> dict[str, pd.DataFrame]:
    return {
        "orders": pd.DataFrame({
            "order_id": ["O1", "O2"],
            "product_id": ["sku-1", "sku-2"],  # this table's plan lowercased them
        }),
        "products": pd.DataFrame({
            "product_id": ["SKU-1", "SKU-2"],  # its own plan did not
            "name": ["A", "B"],
        }),
    }


def test_both_sides_of_the_relationship_are_normalized() -> None:
    session = _session(_mismatched())
    rels = [_rel("orders", "product_id", "products", "product_id")]

    tables = _tables_for_storage(session, rels)

    assert tables["orders"]["product_id"].tolist() == ["sku-1", "sku-2"]
    assert tables["products"]["product_id"].tolist() == ["sku-1", "sku-2"]


def test_result_is_written_back_so_integrity_and_save_agree() -> None:
    session = _session(_mismatched())
    rels = [_rel("orders", "product_id", "products", "product_id")]

    _tables_for_storage(session, rels)

    # /save reads cleaned_df straight off the session; if the normalization only
    # existed in the returned copy the two endpoints would disagree.
    assert session.cleaning["products"].cleaned_df["product_id"].tolist() == ["sku-1", "sku-2"]


def test_integrity_passes_on_the_reconciled_tables() -> None:
    session = _session(_mismatched())
    rels = [_rel("orders", "product_id", "products", "product_id")]

    # Unreconciled, the gate correctly refuses: Postgres would reject the COPY.
    assert run_integrity_validation(_mismatched(), rels).passed is False

    after = run_integrity_validation(_tables_for_storage(session, rels), rels)
    assert after.passed is True


def test_is_idempotent() -> None:
    session = _session(_mismatched())
    rels = [_rel("orders", "product_id", "products", "product_id")]

    once = _tables_for_storage(session, rels)["products"]["product_id"].tolist()
    twice = _tables_for_storage(session, rels)["products"]["product_id"].tolist()
    assert once == twice


def test_relationship_naming_an_absent_table_is_skipped() -> None:
    session = _session({"orders": pd.DataFrame({"order_id": ["O1"], "product_id": ["sku-1"]})})
    tables = _tables_for_storage(session, [_rel("orders", "product_id", "products", "product_id")])
    assert list(tables) == ["orders"]


def test_uncleaned_tables_are_excluded() -> None:
    session = _session(_mismatched())
    session.cleaning["products"].cleaned_df = None
    tables = _tables_for_storage(session, [])
    assert list(tables) == ["orders"]
