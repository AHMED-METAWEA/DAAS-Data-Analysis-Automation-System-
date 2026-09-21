from __future__ import annotations

import pandas as pd
import pytest

from reconciliation.normalize import normalize_key_series, normalize_relationship_keys
from reconciliation.orphans import (
    failing_checks,
    orphan_rate_and_sample,
    reconcile,
)
from schema_discovery.models import RelationshipCandidate


def _rel(table_a, col_a, table_b, col_b) -> RelationshipCandidate:
    return RelationshipCandidate(
        table_a=table_a, column_a=col_a, table_b=table_b, column_b=col_b,
        confidence=0.95, evidence={},
    )


class TestNormalizeKeySeries:
    def test_mixed_numeric_representations_collapse_to_the_same_value(self) -> None:
        # The exact bug the numeric-aware path exists to prevent: naive
        # str(x).strip() would leave "1001" vs "1001.0" as different values.
        as_int = pd.Series([1001], dtype="int64")
        as_float = pd.Series([1001.0], dtype="float64")
        as_str = pd.Series(["1001"], dtype="object")

        assert normalize_key_series(as_int).iloc[0] == normalize_key_series(as_float).iloc[0]
        assert normalize_key_series(as_float).iloc[0] == normalize_key_series(as_str).iloc[0]
        assert normalize_key_series(as_int).iloc[0] == "1001"

    def test_fractional_numeric_keys_are_preserved_distinctly(self) -> None:
        result = normalize_key_series(pd.Series([1001.5]))
        assert result.iloc[0] == "1001.5"

    def test_text_keys_stripped_and_casefolded(self) -> None:
        result = normalize_key_series(pd.Series([" ABC-123 ", "abc-123"]))
        assert result.iloc[0] == result.iloc[1] == "abc-123"

    def test_nulls_stay_null(self) -> None:
        result = normalize_key_series(pd.Series([1, None, 3]))
        assert result.iloc[1] is None


class TestNormalizeRelationshipKeys:
    def test_overwrites_both_sides_consistently(self) -> None:
        tables = {
            "orders": pd.DataFrame({"customer_id": [1001, 1002]}),
            "customers": pd.DataFrame({"customer_id": ["1001", "1002.0"]}),
        }
        rel = _rel("orders", "customer_id", "customers", "customer_id")
        result = normalize_relationship_keys(tables, rel)

        assert list(result["orders"]["customer_id"]) == ["1001", "1002"]
        assert list(result["customers"]["customer_id"]) == ["1001", "1002"]

    def test_does_not_mutate_original_tables(self) -> None:
        tables = {
            "orders": pd.DataFrame({"customer_id": [1001]}),
            "customers": pd.DataFrame({"customer_id": [1001]}),
        }
        rel = _rel("orders", "customer_id", "customers", "customer_id")
        normalize_relationship_keys(tables, rel)
        assert tables["orders"]["customer_id"].iloc[0] == 1001  # untouched, still int


class TestOrphanRateAndSamplePublicApi:
    def test_public_function_is_reusable_outside_this_module(self) -> None:
        # Locks in the public rename (was _orphan_rate_and_sample) — this is
        # what integrity.validation reuses instead of duplicating the logic.
        fk = pd.Series([1001, 1002, 9999])
        pk = pd.Series([1001, 1002])
        rate, sample = orphan_rate_and_sample(fk, pk)
        assert rate == pytest.approx(1 / 3)
        assert 9999 in sample


class TestOrphanChecks:
    def test_zero_orphans_for_consistently_cleaned_numeric_keys(self) -> None:
        # Before cleaning: consistent ints on both sides, no orphans.
        raw = {
            "orders": pd.DataFrame({"customer_id": [1001, 1002, 1001]}),
            "customers": pd.DataFrame({"customer_id": [1001, 1002, 1003]}),
        }
        # After independent cleaning: one side became strings, the other
        # floats — exactly the "silent orphan" scenario reconciliation guards
        # against.
        cleaned = {
            "orders": pd.DataFrame({"customer_id": ["1001", "1002", "1001"]}),
            "customers": pd.DataFrame({"customer_id": [1001.0, 1002.0, 1003.0]}),
        }
        rel = _rel("orders", "customer_id", "customers", "customer_id")

        normalized, checks = reconcile([rel], raw, cleaned)

        assert len(checks) == 1
        check = checks[0]
        assert check.orphan_rate_before == 0.0
        assert check.orphan_rate_after == 0.0
        assert check.within_tolerance()
        # And the stored representation is now consistent on both sides.
        assert list(normalized["orders"]["customer_id"]) == ["1001", "1002", "1001"]
        assert list(normalized["customers"]["customer_id"]) == ["1001", "1002", "1003"]

    def test_genuinely_new_orphan_is_detected_and_sampled(self) -> None:
        raw = {
            "orders": pd.DataFrame({"customer_id": [1001, 1002]}),
            "customers": pd.DataFrame({"customer_id": [1001, 1002]}),
        }
        # Cleaning dropped customer 1002 from the customers table (e.g. a
        # bad dedup rule) — this is a REAL orphan, not a representation
        # mismatch, and must still be caught after normalization.
        cleaned = {
            "orders": pd.DataFrame({"customer_id": [1001, 1002]}),
            "customers": pd.DataFrame({"customer_id": [1001]}),
        }
        rel = _rel("orders", "customer_id", "customers", "customer_id")

        _, checks = reconcile([rel], raw, cleaned)

        check = checks[0]
        assert check.orphan_rate_before == 0.0
        assert check.orphan_rate_after == pytest.approx(0.5)
        assert not check.within_tolerance()
        # Sample reflects the post-normalization stored value (a string,
        # since reconcile() normalizes before checking).
        assert "1002" in check.orphan_sample_after
        assert failing_checks(checks) == [check]

    def test_within_tolerance_allows_small_increase(self) -> None:
        raw = {
            "orders": pd.DataFrame({"customer_id": list(range(100))}),
            "customers": pd.DataFrame({"customer_id": list(range(100))}),
        }
        cleaned = {
            "orders": pd.DataFrame({"customer_id": list(range(100))}),
            "customers": pd.DataFrame({"customer_id": list(range(99))}),  # 1 missing = 1% orphans
        }
        rel = _rel("orders", "customer_id", "customers", "customer_id")
        _, checks = reconcile([rel], raw, cleaned)
        assert checks[0].within_tolerance(tolerance=0.02)
        assert not checks[0].within_tolerance(tolerance=0.005)

    def test_relationship_skipped_when_a_table_failed_cleaning(self) -> None:
        # cleaned_tables only contains "orders" (customers failed cleaning
        # and is absent) — reconcile must not KeyError on the missing table.
        raw = {
            "orders": pd.DataFrame({"customer_id": [1001]}),
            "customers": pd.DataFrame({"customer_id": [1001]}),
        }
        cleaned = {"orders": pd.DataFrame({"customer_id": [1001]})}
        rel = _rel("orders", "customer_id", "customers", "customer_id")

        normalized, checks = reconcile([rel], raw, cleaned)
        assert checks == []
        assert "customers" not in normalized
