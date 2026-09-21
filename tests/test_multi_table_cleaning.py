from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import patch

import pandas as pd

from agents.cleaning.multi_table import (
    all_succeeded,
    clean_table_deterministically,
    clean_tables,
    clean_tables_with_reconciliation,
    failed_tables,
    key_columns_for_table,
)
from schema_discovery.models import RelationshipCandidate

_PLAN_JSON = json.dumps([
    {"op": "impute", "columns": ["a"], "params": {"strategy": "median"},
     "description": "Fill the missing values in a with the median."},
    {"op": "drop_duplicate_rows", "columns": [], "params": {},
     "description": "Remove rows that are exact duplicates."},
])


def _rel(table_a, col_a, table_b, col_b, confidence=0.9) -> RelationshipCandidate:
    return RelationshipCandidate(
        table_a=table_a, column_a=col_a, table_b=table_b, column_b=col_b,
        confidence=confidence, evidence={},
    )


@contextmanager
def _patch_llm(fake_fn):
    """planner.py and coder.py do `from tools.llm_client import complete`, so
    the name to patch is where it's used, not tools.llm_client itself."""
    with patch("agents.cleaning.planner.complete", side_effect=fake_fn), \
         patch("agents.cleaning.coder.complete", side_effect=fake_fn):
        yield


def _fake_complete_all_good(purpose, messages, **kwargs):
    if purpose == "planner":
        return _PLAN_JSON
    raise AssertionError(
        f"the coder must not be called for a fully typed plan (purpose={purpose})"
    )


class TestKeyColumnsForTable:
    def test_collects_columns_from_both_sides(self) -> None:
        rels = [
            _rel("orders", "customer_id", "customers", "customer_id"),
            _rel("orders", "product_id", "products", "sku"),
        ]
        assert key_columns_for_table("orders", rels) == ["customer_id", "product_id"]
        assert key_columns_for_table("customers", rels) == ["customer_id"]
        assert key_columns_for_table("products", rels) == ["sku"]
        assert key_columns_for_table("unrelated", rels) == []

    def test_empty_or_none_relationships(self) -> None:
        assert key_columns_for_table("orders", None) == []
        assert key_columns_for_table("orders", []) == []


class TestCleanTables:
    def test_cleans_each_table_independently(self) -> None:
        tables = {
            "customers": pd.DataFrame({"a": [1.0, None, 1.0, 3.0]}),
            "orders": pd.DataFrame({"a": [5.0, None]}),
        }
        with _patch_llm(_fake_complete_all_good):
            results = clean_tables(
                tables, approved_relationships=[], planner_model="m", coder_model="m",
            )

        assert set(results) == {"customers", "orders"}
        assert all_succeeded(results)
        assert failed_tables(results) == []
        assert results["customers"].validation_report["passed"] is True

    def test_deduplication_runs_before_imputation(self) -> None:
        """Order matters, and the old pipeline had it backwards.

        Imputing first turns the blank in [1.0, None, 1.0, 3.0] into 1.0,
        *manufacturing* a third identical row; the dedupe step then deletes a
        record that was distinct in the source data, and the median is computed
        over a distribution the duplicate has skewed.

        De-duplicating first removes the one genuine duplicate, leaving
        [1.0, None, 3.0], and the median is then taken over the real distinct
        values (1.0 and 3.0 -> 2.0).

        The mock deliberately returns the steps in the *wrong* order, to prove
        the system re-orders them rather than trusting the planner.
        """
        tables = {"customers": pd.DataFrame({"a": [1.0, None, 1.0, 3.0]})}
        with _patch_llm(_fake_complete_all_good):
            results = clean_tables(
                tables, approved_relationships=[], planner_model="m", coder_model="m",
            )

        cleaned = results["customers"].clean_df
        assert len(cleaned) == 3
        assert sorted(cleaned["a"].tolist()) == [1.0, 2.0, 3.0]

    def test_broken_llm_response_no_longer_fails_the_table(self) -> None:
        """A table is never lost to a bad model response: planning degrades to
        the deterministic baseline. Previously an unusable coder reply left
        clean_df as None, which then blocked the whole project at the
        integrity gate."""
        tables = {
            "good": pd.DataFrame({"a": [1.0, 2.0, None]}),
            "bad": pd.DataFrame({"bad_marker_col": [1.0, 2.0, None]}),
        }

        def _fake_garbage(purpose, messages, **kwargs):
            return "I'm sorry, I can't help with that."

        with _patch_llm(_fake_garbage):
            results = clean_tables(
                tables, approved_relationships=[], planner_model="m", coder_model="m",
            )

        assert all_succeeded(results)
        assert results["bad"].clean_df is not None
        assert results["bad"].clean_df["bad_marker_col"].isna().sum() == 0

    def test_planner_llm_raising_falls_back_to_the_deterministic_plan(self) -> None:
        tables = {"orders": pd.DataFrame({"amount": [1.0, 2.0, None, 4.0]})}

        def _boom(purpose, messages, **kwargs):
            raise RuntimeError("rate limited")

        with _patch_llm(_boom):
            results = clean_tables(
                tables, approved_relationships=[], planner_model="m", coder_model="m",
            )

        assert results["orders"].success is True
        assert results["orders"].clean_df["amount"].isna().sum() == 0

    def test_key_columns_threaded_through_without_error(self) -> None:
        tables = {"orders": pd.DataFrame({"customer_id": [1, 2, 3], "total": [10.0, 20.0, 30.0]})}
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]

        with _patch_llm(_fake_complete_all_good):
            results = clean_tables(
                tables, approved_relationships=rels, planner_model="m", coder_model="m",
            )

        assert results["orders"].success is True

    def test_cleaning_never_rewrites_a_join_key(self) -> None:
        """Cleaning used to be able to case-fold or re-format a key column,
        orphaning rows in the related table and producing a
        ForeignKeyViolation at save time. Key-unsafe operators are now skipped
        with a recorded reason.

        The `city` column is what makes the planner consult the model at all:
        merging spelling variants is a judgement call, and a table whose only
        findings are lossless type conversions is planned deterministically
        without an LLM. Without it the mocked `harmonize_case` step never
        reaches the plan and the guard under test is never exercised.
        """
        tables = {"orders": pd.DataFrame({
            "customer_id": ["C1", "C2", "C3"],
            "city": ["Cairo", "cairo ", "CAIRO"],
            "amount": [1.0, 2.0, 3.0],
        })}
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]

        def _fake(purpose, messages, **kwargs):
            return json.dumps([
                {"op": "harmonize_case", "columns": ["customer_id"], "params": {"style": "lower"},
                 "description": "Standardise the casing of customer_id."},
            ])

        with _patch_llm(_fake):
            results = clean_tables(
                tables, approved_relationships=rels, planner_model="m", coder_model="m",
            )

        cleaned = results["orders"].clean_df
        assert cleaned["customer_id"].tolist() == ["C1", "C2", "C3"]
        assert any("join key" in line for line in results["orders"].transformation_log)


class TestDeterministicFallback:
    def test_cleans_a_dirty_table_with_no_llm_at_all(self) -> None:
        df = pd.DataFrame({
            "order_id": ["A1", "A2", "A2"],
            "price": ["$10.00", "$20.00", "$20.00"],
            "city": ["Cairo", "cairo ", "cairo "],
        })
        result = clean_table_deterministically(df, "orders", key_columns=["order_id"])

        assert result.success is True
        assert result.used_fallback is True
        assert result.clean_df["price"].tolist() == [10.0, 20.0]
        assert result.clean_df["city"].nunique() == 1
        assert result.clean_df["order_id"].tolist() == ["A1", "A2"]

    def test_already_clean_table_is_left_alone(self) -> None:
        df = pd.DataFrame({"customer_id": ["C1", "C2"], "amount": [10.0, 20.0]})
        result = clean_table_deterministically(df, "customers", key_columns=[])

        assert result.success is True
        assert result.cleaning_plan == []
        assert result.clean_df.equals(df)


class TestCleanTablesWithReconciliation:
    def test_reconciles_key_representation_drift(self) -> None:
        """The two tables arrive with the same logical id spelled differently
        (str vs int). Cleaning can no longer *introduce* that drift, but source
        data still contains it, and reconciliation must resolve it."""
        tables = {
            # `amount` keeps the two 1001 rows distinct — in a single-column
            # table a repeated foreign key *is* a duplicate row, and would be
            # correctly de-duplicated away before reconciliation ever ran.
            "orders": pd.DataFrame({
                "customer_id": ["1001", "1002", "1001"],
                "amount": [10.0, 20.0, 30.0],
            }),
            "customers": pd.DataFrame({"customer_id": [1001, 1002], "name": ["A", "B"]}),
        }
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]

        with _patch_llm(_fake_complete_all_good):
            results, reconciled, checks = clean_tables_with_reconciliation(
                tables, rels, planner_model="m", coder_model="m",
            )

        assert all_succeeded(results)
        assert len(checks) == 1
        assert checks[0].orphan_rate_after == 0.0
        assert checks[0].within_tolerance()
        assert reconciled["orders"]["customer_id"].tolist() == ["1001", "1002", "1001"]
        assert reconciled["customers"]["customer_id"].tolist() == ["1001", "1002"]

    def test_missing_table_from_failed_cleaning_does_not_crash_reconciliation(self) -> None:
        tables = {
            "orders": pd.DataFrame({"customer_id": [1001]}),
            "customers": pd.DataFrame({"customer_id": [1001, None]}),
        }
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]

        real_fallback = clean_table_deterministically

        def _fallback_fails(df, name, key_columns):
            if name == "customers":
                raise RuntimeError("simulated total failure")
            return real_fallback(df, name, key_columns)

        def _boom(purpose, messages, **kwargs):
            raise RuntimeError("planner down")

        class _ExplodingGraph:
            def invoke(self, state):
                raise RuntimeError("graph blew up")

        # Both paths have to fail for a table to be lost: the graph *and* the
        # deterministic fallback. That is the point of the fallback — one of
        # them failing is no longer enough to drop a table from the project.
        with _patch_llm(_boom), patch(
            "agents.cleaning.multi_table.clean_table_deterministically",
            side_effect=_fallback_fails,
        ), patch(
            "agents.cleaning.multi_table.build_cleaning_graph",
            return_value=_ExplodingGraph(),
        ):
            results, reconciled, checks = clean_tables_with_reconciliation(
                tables, rels, planner_model="m", coder_model="m",
            )

        assert failed_tables(results) == ["customers"]
        assert "customers" not in reconciled
        assert checks == []
