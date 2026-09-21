from __future__ import annotations

import pandas as pd

from integrity.validation import (
    PkBlockingIssue,
    _pk_blocking_issues,
    run_integrity_validation,
)
from schema_discovery.models import RelationshipCandidate


def _rel(table_a, col_a, table_b, col_b, confidence=0.9) -> RelationshipCandidate:
    return RelationshipCandidate(
        table_a=table_a, column_a=col_a, table_b=table_b, column_b=col_b,
        confidence=confidence, evidence={},
    )


class TestPkResultsAndConfirmedMap:
    def test_confirmed_pk_surfaces_in_report_and_map(self) -> None:
        tables = {
            "customers": pd.DataFrame({"customer_id": ["C1", "C2", "C3"], "name": ["A", "B", "C"]}),
        }
        report = run_integrity_validation(tables, [])
        assert report.pk_results["customers"].status == "confirmed"
        assert report.confirmed_primary_keys == {"customers": ["customer_id"]}
        assert report.passed is True

    def test_standalone_table_with_no_relationships_still_gets_a_pk(self) -> None:
        tables = {"products": pd.DataFrame({"sku": ["S1", "S2"], "name": ["A", "B"]})}
        report = run_integrity_validation(tables, None)
        assert report.confirmed_primary_keys == {"products": ["sku"]}

    def test_duplicate_pk_column_reported_as_candidate_not_blocking_on_its_own(self) -> None:
        tables = {"customers": pd.DataFrame({"customer_id": ["C1", "C1", "C2"]})}
        report = run_integrity_validation(tables, [])
        assert report.pk_results["customers"].status == "candidate"
        assert "customers" not in report.confirmed_primary_keys
        # No relationship references this table, so nothing is blocking —
        # a candidate PK alone doesn't fail validation (no constraint will
        # be generated for it).
        assert report.passed is True


class TestPkBlockingSafetyNet:
    def test_recomputes_independently_of_pk_detection_result(self) -> None:
        # Feed a hand-built pk_map with a column that actually has
        # duplicates, bypassing pk_detection.detect_primary_keys entirely —
        # proves the safety net doesn't just trust whatever it's given.
        tables = {"customers": pd.DataFrame({"customer_id": ["C1", "C1", "C2"]})}
        issues = _pk_blocking_issues(tables, {"customers": ["customer_id"]})
        assert issues == [
            PkBlockingIssue(
                table="customers", columns=["customer_id"], duplicate_count=2, null_count=0,
            )
        ]

    def test_no_issue_when_pk_map_column_is_genuinely_unique(self) -> None:
        tables = {"customers": pd.DataFrame({"customer_id": ["C1", "C2"]})}
        assert _pk_blocking_issues(tables, {"customers": ["customer_id"]}) == []


class TestRelationshipChecks:
    def test_orphan_present_but_not_enforced_is_not_blocking(self) -> None:
        # customers.customer_id has a duplicate, so it never becomes a
        # CONFIRMED pk -> no FK constraint will be generated -> an orphan
        # against it can't fail a COPY, so it must not block.
        tables = {
            "orders": pd.DataFrame({"order_id": [1, 2, 3], "customer_id": ["C1", "C2", "C9"]}),
            "customers": pd.DataFrame({"customer_id": ["C1", "C1", "C2"]}),
        }
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]
        report = run_integrity_validation(tables, rels)
        check = report.relationship_checks[0]
        assert check.fk_will_be_enforced is False
        assert check.blocking is False
        assert report.passed is True

    def test_orphan_against_confirmed_pk_is_blocking(self) -> None:
        tables = {
            "orders": pd.DataFrame({"order_id": [1, 2, 3], "customer_id": ["C1", "C2", "C9"]}),
            "customers": pd.DataFrame({"customer_id": ["C1", "C2"]}),
        }
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]
        report = run_integrity_validation(tables, rels)
        check = report.relationship_checks[0]
        assert check.fk_will_be_enforced is True
        assert check.orphan_count == 1
        assert check.blocking is True
        assert report.passed is False

    def test_no_orphans_against_confirmed_pk_passes(self) -> None:
        tables = {
            "orders": pd.DataFrame({"order_id": [1, 2, 3], "customer_id": ["C1", "C2", "C1"]}),
            "customers": pd.DataFrame({"customer_id": ["C1", "C2"]}),
        }
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]
        report = run_integrity_validation(tables, rels)
        check = report.relationship_checks[0]
        assert check.blocking is False
        assert report.passed is True

    def test_one_to_many_cardinality_for_normal_star_schema(self) -> None:
        tables = {
            "orders": pd.DataFrame({"order_id": [1, 2, 3], "customer_id": ["C1", "C1", "C2"]}),
            "customers": pd.DataFrame({"customer_id": ["C1", "C2"]}),
        }
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]
        report = run_integrity_validation(tables, rels)
        assert report.relationship_checks[0].cardinality == "one_to_many"

    def test_many_to_many_relationship_flags_needs_bridge_table_without_blocking(self) -> None:
        tables = {
            "a": pd.DataFrame({"key": ["X1", "X1", "X2"]}),
            "b": pd.DataFrame({"key": ["X1", "X2", "X2"]}),
        }
        rels = [_rel("a", "key", "b", "key")]
        report = run_integrity_validation(tables, rels)
        check = report.relationship_checks[0]
        assert check.cardinality == "many_to_many"
        assert check.needs_bridge_table is True
        assert check.blocking is False
        assert report.passed is True

    def test_relationship_referencing_missing_table_is_skipped(self) -> None:
        tables = {"orders": pd.DataFrame({"order_id": [1], "customer_id": ["C1"]})}
        rels = [_rel("orders", "customer_id", "customers", "customer_id")]
        report = run_integrity_validation(tables, rels)
        assert report.relationship_checks == []


class TestDtypeAndDuplicateRowIssues:
    def test_flags_mostly_numeric_object_column(self) -> None:
        values = ["1001", "1002", "CUST-1003"] + ["1004"] * 20
        tables = {"customers": pd.DataFrame({"customer_id": values})}
        report = run_integrity_validation(tables, [])
        flagged = {(d.table, d.column) for d in report.dtype_issues}
        assert ("customers", "customer_id") in flagged

    def test_does_not_flag_fully_numeric_or_fully_text_columns(self) -> None:
        tables = {
            "customers": pd.DataFrame({
                "customer_id": ["1001", "1002", "1003"],
                "name": ["Alice", "Bob", "Carol"],
            }),
        }
        report = run_integrity_validation(tables, [])
        assert report.dtype_issues == []

    def test_duplicate_whole_row_reported_but_not_blocking(self) -> None:
        tables = {
            "customers": pd.DataFrame({
                "customer_id": ["C1", "C2", "C3"],
                "name": ["Alice", "Bob", "Alice"],
            }),
        }
        # Force a real whole-row duplicate.
        tables["events"] = pd.DataFrame({
            "event_id": ["E1", "E2", "E1"], "type": ["click", "click", "click"],
        })
        report = run_integrity_validation(tables, [])
        dup_counts = {(d.table, d.duplicate_row_count) for d in report.duplicate_row_issues}
        assert ("events", 2) in dup_counts
        # Whole-row duplicates are informational, not blocking, and don't
        # affect PK confirmation for other tables.
        assert report.passed is True


class TestFkBlockingUsesPostgresSemantics:
    """A generated FOREIGN KEY compares the RAW stored values, so the gate has to
    as well. Judging only on casefolded keys let it pass a save that Postgres was
    guaranteed to reject with a ForeignKeyViolation."""

    def test_case_mismatched_text_keys_block(self) -> None:
        tables = {
            "orders": pd.DataFrame({
                "order_id": ["O1", "O2"],
                "product_id": ["sku-1", "sku-2"],  # lowercased by this table's plan
            }),
            "products": pd.DataFrame({
                "product_id": ["SKU-1", "SKU-2"],  # left as-is by its own plan
                "name": ["A", "B"],
            }),
        }
        rels = [_rel("orders", "product_id", "products", "product_id")]
        report = run_integrity_validation(tables, rels)

        check = report.relationship_checks[0]
        assert check.fk_will_be_enforced is True
        assert check.blocking is True
        assert check.orphan_count == 2
        assert report.passed is False

    def test_whitespace_mismatched_text_keys_block(self) -> None:
        tables = {
            "orders": pd.DataFrame({"order_id": ["O1"], "product_id": ["sku-1 "]}),
            "products": pd.DataFrame({"product_id": ["sku-1"], "name": ["A"]}),
        }
        report = run_integrity_validation(
            tables, [_rel("orders", "product_id", "products", "product_id")]
        )
        assert report.relationship_checks[0].blocking is True
        assert report.passed is False

    def test_identical_text_keys_do_not_block(self) -> None:
        tables = {
            "orders": pd.DataFrame({"order_id": ["O1"], "product_id": ["sku-1"]}),
            "products": pd.DataFrame({"product_id": ["sku-1"], "name": ["A"]}),
        }
        report = run_integrity_validation(
            tables, [_rel("orders", "product_id", "products", "product_id")]
        )
        assert report.relationship_checks[0].orphan_count == 0
        assert report.passed is True

    def test_numeric_int_float_spellings_still_do_not_block(self) -> None:
        """The int/float/str spellings of one id are a representation detail the
        DDL type mapping settles — the normalized model stays correct there, so
        the stricter check must not fire."""
        tables = {
            "orders": pd.DataFrame({"order_id": ["O1", "O2"], "product_id": [1001.0, 1002.0]}),
            "products": pd.DataFrame({"product_id": [1001, 1002], "name": ["A", "B"]}),
        }
        report = run_integrity_validation(
            tables, [_rel("orders", "product_id", "products", "product_id")]
        )
        check = report.relationship_checks[0]
        assert check.orphan_count == 0
        assert check.blocking is False
        assert report.passed is True

    def test_null_foreign_keys_are_not_orphans(self) -> None:
        tables = {
            "orders": pd.DataFrame({"order_id": ["O1", "O2"], "product_id": ["sku-1", None]}),
            "products": pd.DataFrame({"product_id": ["sku-1"], "name": ["A"]}),
        }
        report = run_integrity_validation(
            tables, [_rel("orders", "product_id", "products", "product_id")]
        )
        assert report.relationship_checks[0].orphan_count == 0
        assert report.passed is True
